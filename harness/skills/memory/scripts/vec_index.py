#!/usr/bin/env python3
# vec_index.py — sqlite-vec wrapper + embedding queue management.
#
# Stores per-entry embeddings in <vault>/_meta/vec-index.db via the
# sqlite-vec SQLite extension. Provides upsert/delete/query ops + a
# JSONL-based queue that save.py / evolve.py append to (queue drain
# happens via this module's `drain_queue` function — called from the
# idle-time hook or manually via `python3 vec_index.py drain`).
#
# Two-tier architecture:
#   1. Queue layer (vault-local; always works): save.py / evolve.py
#      append JSONL entries to <vault>/_meta/embedding-queue.jsonl.
#      No external deps; never blocks file write.
#   2. Index layer (sqlite-vec required): drain_queue() reads queue
#      entries, calls embed.py, upserts into vec-index.db. Graceful-
#      skip if sqlite-vec / embedding mode unavailable — queue stays
#      pending until next drain.
#
# Graceful-skip pattern:
#   - sqlite-vec not installed → all index ops are no-ops + log warning;
#     queue entries stay pending.
#   - Embedding unavailable (no local model) → drain skips that queue
#     entry + leaves it for next drain.
#   - File-write side (save.py / evolve.py) is NEVER blocked by either.
#
# Dimension-mismatch handling (v0.9.2, plan #18 task 2):
#   - The vec-index virtual table is created at EMBEDDING_DIM (currently
#     1024 from embed.py; was 384 in v0.x).
#   - On open, _open_index() introspects the existing virtual-table
#     schema and compares its dim to EMBEDDING_DIM. If mismatched
#     (e.g. operator upgraded the toolkit on top of an old 384-d
#     index), _open_index() prints a clear "vec-index dim mismatch;
#     rebuild required" message to stderr and returns None (graceful-
#     skip — never blocks the prompt). Caller treats the same as
#     "sqlite-vec unavailable".
#   - Operator runs `python3 vec_index.py rebuild --vault-path <path>`
#     to drop + recreate the index at the current dim. The embedding
#     queue is preserved across rebuild; the operator's existing vault
#     entries are NOT auto-re-enqueued (operators can manually re-save
#     each entry or wait for a future `reindex` subcommand that walks
#     the vault + enqueues all .md files).

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# embed.py is in the same scripts/ dir.
_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from embed import EMBEDDING_DIM, EmbeddingUnavailable, embed_text  # type: ignore
except ImportError as e:  # pragma: no cover
    print(f"ERROR: cannot import embed module: {e}", file=sys.stderr)
    raise


_META_DIR = "_meta"
_INDEX_FILENAME = "vec-index.db"
_QUEUE_FILENAME = "embedding-queue.jsonl"

# Regex to parse the FLOAT[N] dim from a vec0 virtual-table CREATE
# statement as returned by SELECT sql FROM sqlite_master.
_DIM_REGEX = re.compile(r"FLOAT\[(\d+)\]", re.IGNORECASE)


def _meta_dir(vault: Path) -> Path:
    return vault / _META_DIR


def _index_path(vault: Path) -> Path:
    return _meta_dir(vault) / _INDEX_FILENAME


def _queue_path(vault: Path) -> Path:
    return _meta_dir(vault) / _QUEUE_FILENAME


def _try_import_sqlite_vec() -> bool:
    """Try to import sqlite-vec + verify the local SQLite build supports
    extension loading. Returns True if both available; False otherwise.

    Graceful-skip pattern: if sqlite-vec isn't installed OR the Python
    sqlite3 module doesn't support `enable_load_extension` (Apple's
    macOS system Python disables this feature), all index operations
    are no-ops. Queue entries stay pending for a future drain in an
    environment that supports extension loading.

    Workaround for operators on Apple's system Python: install a
    Python from Homebrew (`brew install python`) or pyenv, which both
    have `enable_load_extension` enabled. Documented in the parent
    design's Tech Debt #1.
    """
    try:
        import sqlite_vec  # type: ignore  # noqa: F401
    except ImportError:
        return False
    # Verify sqlite3 supports extension loading on this Python build.
    if not hasattr(sqlite3.Connection, "enable_load_extension"):
        return False
    return True


def _detect_index_dim(conn: sqlite3.Connection) -> int | None:
    """Detect the embedding dimension of the existing `entries` virtual
    table by parsing the CREATE statement stored in sqlite_master.

    Returns the parsed dim, or None if the `entries` table doesn't
    exist yet (fresh DB) or the dim can't be parsed (defensive — should
    not happen for any vec_index.py-created table since the CREATE
    statement is always FLOAT[N]).
    """
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='entries'"
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    m = _DIM_REGEX.search(row[0])
    if not m:
        return None
    return int(m.group(1))


def _open_index(vault: Path) -> sqlite3.Connection | None:
    """Open the vec-index DB and load the sqlite-vec extension.

    Returns None in any of these cases (all treated as "skip index op +
    leave queue pending"):
      - sqlite-vec module not installed
      - SQLite build doesn't support extension loading (e.g. macOS
        system Python)
      - extension load fails at runtime (rare; some sqlite3 builds
        advertise enable_load_extension but refuse at call time)
      - existing `entries` virtual table has a dim != EMBEDDING_DIM
        (operator upgraded the toolkit on top of an older-dim index;
        a clear stderr warning + rebuild instruction is printed)
    """
    if not _try_import_sqlite_vec():
        return None
    _meta_dir(vault).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_index_path(vault))
    try:
        conn.enable_load_extension(True)
        import sqlite_vec  # type: ignore
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (AttributeError, sqlite3.OperationalError):
        # Defensive: a Python build can have enable_load_extension as a
        # method but fail at runtime (some sqlite3 builds report it as
        # unsupported via OperationalError). Treat the same as missing.
        conn.close()
        return None
    # Dim-mismatch detection (plan #18 task 2): if an `entries` virtual
    # table already exists with a dimension different from EMBEDDING_DIM,
    # the CREATE TABLE IF NOT EXISTS below would be a no-op and we'd
    # silently proceed against an incompatible schema. Catch + warn +
    # graceful-skip.
    existing_dim = _detect_index_dim(conn)
    if existing_dim is not None and existing_dim != EMBEDDING_DIM:
        print(
            f"[vec_index] dim mismatch: existing index at "
            f"{_index_path(vault)} is {existing_dim}-d but current code "
            f"expects {EMBEDDING_DIM}-d. Rebuild required: "
            f"python3 vec_index.py rebuild --vault-path {vault}",
            file=sys.stderr,
        )
        conn.close()
        return None
    # Ensure schema exists.
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS entries USING vec0("
        f"  embedding FLOAT[{EMBEDDING_DIM}]"
        f")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entry_meta ("
        "  rowid INTEGER PRIMARY KEY,"
        "  path TEXT UNIQUE NOT NULL,"
        "  updated_at TEXT NOT NULL,"
        "  indexed_at INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    _migrate_pre_v37(conn)
    conn.commit()
    return conn


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if `column` exists in `table` per PRAGMA table_info."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    for row in cursor.fetchall():
        # PRAGMA table_info returns rows of (cid, name, type, notnull, default, pk)
        if row[1] == column:
            return True
    return False


def _migrate_pre_v37(conn: sqlite3.Connection) -> bool:
    """V4 #37 schema migration: add `indexed_at INTEGER NOT NULL DEFAULT 0` to
    entry_meta if absent.

    Pre-#37 indexes have entry_meta without the column. ALTER TABLE is gentler
    than rebuild — preserves embeddings until they're refreshed via the
    natural drift → enqueue → drain flow. Existing rows get indexed_at=0,
    which makes them appear "drifted" against any source file (mtime > 0)
    until re-embed completes.

    Idempotent: detects via PRAGMA table_info; no-op if column already present.

    Returns True if migration ran; False if no-op (already migrated).

    Caller is responsible for `conn.commit()` after this returns.
    """
    if _has_column(conn, "entry_meta", "indexed_at"):
        return False
    conn.execute("ALTER TABLE entry_meta ADD COLUMN indexed_at INTEGER NOT NULL DEFAULT 0")
    print(
        "[vec_index] migrated pre-v4.2 entry_meta schema to v37 (drift-detection enabled). "
        "Existing rows have indexed_at=0; they'll re-embed on next drain via natural drift signal.",
        file=sys.stderr,
    )
    return True


def upsert_entry(vault_path: Path | str, entry_relative: str, embedding: list[float]) -> bool:
    """Insert or update an entry's embedding in the vec-index.

    Returns True if upserted; False if sqlite-vec unavailable (no-op).
    """
    vault = Path(vault_path)
    conn = _open_index(vault)
    if conn is None:
        return False
    try:
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"embedding dimension {len(embedding)} != expected {EMBEDDING_DIM}"
            )
        # Find existing rowid by path (if any).
        cursor = conn.execute(
            "SELECT rowid FROM entry_meta WHERE path = ?", (entry_relative,)
        )
        row = cursor.fetchone()
        emb_blob = json.dumps(embedding)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        now_epoch = int(time.time())  # V4 #37: cheap integer for drift-comparison
        if row:
            rowid = row[0]
            conn.execute(
                "UPDATE entries SET embedding = ? WHERE rowid = ?",
                (emb_blob, rowid),
            )
            conn.execute(
                "UPDATE entry_meta SET updated_at = ?, indexed_at = ? WHERE rowid = ?",
                (now_iso, now_epoch, rowid),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO entries(embedding) VALUES (?)", (emb_blob,)
            )
            rowid = cursor.lastrowid
            conn.execute(
                "INSERT INTO entry_meta(rowid, path, updated_at, indexed_at) VALUES (?, ?, ?, ?)",
                (rowid, entry_relative, now_iso, now_epoch),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_entry(vault_path: Path | str, entry_relative: str) -> bool:
    """Remove an entry from the vec-index by relative path.

    Returns True if deleted (or never existed); False if sqlite-vec
    unavailable (no-op).
    """
    vault = Path(vault_path)
    conn = _open_index(vault)
    if conn is None:
        return False
    try:
        cursor = conn.execute(
            "SELECT rowid FROM entry_meta WHERE path = ?", (entry_relative,)
        )
        row = cursor.fetchone()
        if row:
            rowid = row[0]
            conn.execute("DELETE FROM entries WHERE rowid = ?", (rowid,))
            conn.execute("DELETE FROM entry_meta WHERE rowid = ?", (rowid,))
            conn.commit()
        return True
    finally:
        conn.close()


def index_size(vault_path: Path | str) -> int | None:
    """Return number of entries in the index, or None if sqlite-vec unavailable."""
    vault = Path(vault_path)
    conn = _open_index(vault)
    if conn is None:
        return None
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM entry_meta")
        return cursor.fetchone()[0]
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# V4 #37: drift detection primitives
# -----------------------------------------------------------------------------

# Tolerance window for mtime-vs-indexed_at comparison. Filesystem timestamp
# granularity varies (HFS+ truncates to 1s; APFS is ns; GDrive's local cache
# may further coarsen). 1-second slop avoids false-positive drift on
# same-second writes (the common case when an upsert happens in the same
# second as the source file was saved).
_DRIFT_MTIME_TOLERANCE_SECONDS = 1.0


def _vault_projects_dir(vault: Path) -> Path:
    """Return <vault>/projects/ (post-V4 #26 canonical) if present, else
    <vault>/personal-projects/ (legacy fallback). Mirrors the same helper
    in harness_memory.py; duplicated here to avoid cross-script import
    coupling within the memory skill scripts dir.
    """
    new = vault / "projects"
    if new.is_dir():
        return new
    legacy = vault / "personal-projects"
    if legacy.is_dir():
        return legacy
    return new  # neither exists; return new (preferred) for caller's mkdir


def _resolve_entry_path(vault: Path, entry_relative: str) -> Path:
    """Resolve `entry_relative` to an absolute path under the vault.

    Entries are stored with paths relative to the vault root (e.g.
    `personal-private/_always-load/coding-style.md` or
    `projects/agentm/decisions/2026-03-01-key-decisions.md`). Just join.
    """
    return vault / entry_relative


def is_entry_drifted(
    vault_path: Path | str,
    entry_relative: str,
    db_path: Path | str | None = None,
) -> bool:
    """Return True if the entry's source `.md` file has drifted from its
    indexed embedding.

    Drift signals:
      - source file's mtime > the row's `indexed_at + tolerance`
      - row doesn't exist in entry_meta (effective drift = "not indexed")
      - sqlite-vec unavailable → returns False (no signal; caller defaults
        to "not drifted" since we can't know)

    Pure read; no writes. Caller decides what to do with the drift signal
    (enqueue for re-embed; surface to operator; skip vec-score; etc.).

    Source-file resolution: `<vault>/<entry_relative>`. The entry_relative
    is what's stored in entry_meta.path — the path that save.py writes at
    embed time.

    Tolerance: 1-second slop (filesystem granularity protection — see
    _DRIFT_MTIME_TOLERANCE_SECONDS).

    Per V4 #37 design / plan #21 task 3.
    """
    vault = Path(vault_path)
    # Source file path under vault — entry_relative is relative-to-vault-root.
    src = _resolve_entry_path(vault, entry_relative)
    if not src.is_file():
        # Entry's source file missing — treat as "drifted" so caller can
        # decide (e.g. enqueue delete from index). Documented behavior.
        return True
    try:
        src_mtime = src.stat().st_mtime
    except OSError:
        return True  # stat failed — defensive; treat as drift

    # Open the vec-index to read indexed_at. _open_index returns None if
    # sqlite-vec unavailable; in that case we can't tell, return False
    # (matches the "graceful-skip silent" pattern: no signal = no action).
    conn = _open_index(vault)
    if conn is None:
        return False
    try:
        cursor = conn.execute(
            "SELECT indexed_at FROM entry_meta WHERE path = ?", (entry_relative,)
        )
        row = cursor.fetchone()
        if row is None:
            # Entry not yet indexed → "drifted" (no row to compare against;
            # caller should enqueue for first-time embed).
            return True
        indexed_at = row[0]
        return src_mtime > (indexed_at + _DRIFT_MTIME_TOLERANCE_SECONDS)
    finally:
        conn.close()


def find_drifted_entries(vault_path: Path | str) -> dict:
    """Walk the vault + return a categorized inventory of every entry's
    drift status. Honors V4 #26 dual-path resolver (projects/ + personal-
    projects/ fallback).

    Returns dict with three lists of vault-relative entry paths:
        {
            "drifted":     [<rel_path>, ...],  # mtime > indexed_at + tolerance
            "up_to_date":  [<rel_path>, ...],  # row exists + mtime <= indexed_at
            "not_indexed": [<rel_path>, ...],  # no row in entry_meta
        }

    Walks:
      - <vault>/personal-private/**/*.md
      - <vault>/projects/<slug>/**/*.md (or legacy personal-projects/ fallback)
      - <vault>/_idea-incubator/**/*.md

    Excludes:
      - <vault>/_meta/   (operator-curated narrative; not memory-skill content)
      - **/_archive/    (historical; never indexed)
      - PLAN.archive.*.md (post-V4 #26 archived plans; never indexed)

    Performance target: <1ms per entry (one os.stat + one sqlite lookup).
    Caller (full-sync subcommand) reports summary; --rebuild enqueues all
    drifted + not_indexed entries for re-embed via embedding-queue.jsonl.

    Graceful-skip: if sqlite-vec unavailable, returns
    `{"drifted": [], "up_to_date": [], "not_indexed": <all walkable files>}` —
    every entry appears "not indexed" because we can't query the index.
    """
    vault = Path(vault_path)
    if not vault.is_dir():
        return {"drifted": [], "up_to_date": [], "not_indexed": []}

    # Collect walk targets.
    walk_roots: list[Path] = []
    private = vault / "personal-private"
    if private.is_dir():
        walk_roots.append(private)
    projects = _vault_projects_dir(vault)
    if projects.is_dir():
        walk_roots.append(projects)
    incubator = vault / "_idea-incubator"
    if incubator.is_dir():
        walk_roots.append(incubator)

    # Try to open index. If unavailable, every entry appears not_indexed.
    conn = _open_index(vault)
    indexed_at_by_path: dict[str, int] = {}
    if conn is not None:
        try:
            cursor = conn.execute("SELECT path, indexed_at FROM entry_meta")
            indexed_at_by_path = {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            conn.close()

    drifted: list[str] = []
    up_to_date: list[str] = []
    not_indexed: list[str] = []

    for root in walk_roots:
        for md in sorted(root.rglob("*.md")):
            # Excludes.
            if any(p == "_archive" for p in md.parts):
                continue
            if md.name.startswith("PLAN.archive."):
                continue
            rel = md.relative_to(vault)
            rel_str = str(rel).replace("\\", "/")  # POSIX-style path for sqlite key

            try:
                src_mtime = md.stat().st_mtime
            except OSError:
                drifted.append(rel_str)
                continue

            indexed_at = indexed_at_by_path.get(rel_str)
            if indexed_at is None:
                not_indexed.append(rel_str)
            elif src_mtime > (indexed_at + _DRIFT_MTIME_TOLERANCE_SECONDS):
                drifted.append(rel_str)
            else:
                up_to_date.append(rel_str)

    return {
        "drifted": drifted,
        "up_to_date": up_to_date,
        "not_indexed": not_indexed,
    }


def rebuild_index(vault_path: Path | str) -> dict:
    """Drop + recreate the vec-index virtual table at current EMBEDDING_DIM.

    Used after a toolkit upgrade that changes EMBEDDING_DIM (e.g. v0.9.0
    → v0.9.2 bumped 384 → 1024 for the BGE-large default). Detection
    + warning happens automatically in _open_index(); this function
    is the operator-driven remediation.

    Behavior:
      - Drops `entries` virtual table + `entry_meta` table.
      - Recreates both at the current EMBEDDING_DIM.
      - Preserves the embedding queue file (`<vault>/_meta/embedding-
        queue.jsonl`). Any pending queue entries can be drained on the
        next `drain` invocation.
      - Does NOT auto-walk the vault to re-enqueue existing entries
        (that's a future `reindex` subcommand). Operators who want a
        fully-populated index after rebuild can manually re-save each
        entry, or wait for the planned reindex feature.

    Returns a stats dict:
      {
        "old_dim": int | None,
        "new_dim": int,
        "entries_dropped": int,
        "queue_preserved": bool,
      }
    Or, if sqlite-vec is unavailable:
      {"skipped": True, "note": "sqlite-vec unavailable"}
    """
    vault = Path(vault_path)
    if not _try_import_sqlite_vec():
        return {"skipped": True, "note": "sqlite-vec unavailable"}
    _meta_dir(vault).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_index_path(vault))
    try:
        conn.enable_load_extension(True)
        import sqlite_vec  # type: ignore
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (AttributeError, sqlite3.OperationalError):
        conn.close()
        return {"skipped": True, "note": "extension load failed"}

    # Detect old dim (informational; mismatch is the WHOLE POINT of
    # rebuild, so we don't bail on it).
    old_dim = _detect_index_dim(conn)

    # Count entries before drop (best-effort; entry_meta may not exist
    # on truly-fresh DBs).
    entries_count = 0
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM entry_meta")
        entries_count = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        pass

    # Drop existing tables. DROP VIRTUAL TABLE syntax handles vec0
    # virtual tables; DROP TABLE IF EXISTS handles the regular table.
    conn.execute("DROP TABLE IF EXISTS entries")
    conn.execute("DROP TABLE IF EXISTS entry_meta")
    conn.commit()

    # Recreate at current EMBEDDING_DIM.
    conn.execute(
        f"CREATE VIRTUAL TABLE entries USING vec0("
        f"  embedding FLOAT[{EMBEDDING_DIM}]"
        f")"
    )
    conn.execute(
        "CREATE TABLE entry_meta ("
        "  rowid INTEGER PRIMARY KEY,"
        "  path TEXT UNIQUE NOT NULL,"
        "  updated_at TEXT NOT NULL,"
        "  indexed_at INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    conn.commit()
    conn.close()

    return {
        "old_dim": old_dim,
        "new_dim": EMBEDDING_DIM,
        "entries_dropped": entries_count,
        "queue_preserved": _queue_path(vault).exists(),
    }


def enqueue(vault_path: Path | str, entry_relative: str, op: str, *, text: str = "") -> None:
    """Append an entry to the embedding queue.

    The queue is JSONL; each line is `{"op": "upsert"|"delete", "path":
    "<relative>", "text": "<embed-text-or-empty>", "enqueued_at": "..."}`.
    save.py + evolve.py call this synchronously after the file write;
    drain_queue() processes the queue later (idle-time hook or manual
    /memory reindex).

    The `text` field is the content to embed for upsert ops (typically
    title + tags + first paragraph). For delete ops, text is ignored.

    This function is sync + fast + never raises on sqlite-vec absence —
    it's the file-write side of the architecture; the slow async work
    happens in drain_queue.
    """
    vault = Path(vault_path)
    _meta_dir(vault).mkdir(parents=True, exist_ok=True)
    record = {
        "op": op,
        "path": entry_relative,
        "text": text,
        "enqueued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # Append + LF newline (consistent with the vault's on-disk LF convention).
    line = (json.dumps(record) + "\n").encode("utf-8")
    with open(_queue_path(vault), "ab") as f:
        f.write(line)


def drain_queue(vault_path: Path | str, *, mode: str | None = None) -> dict:
    """Drain the embedding queue: read entries, embed text, upsert/delete index.

    Returns a stats dict: {"processed": N, "skipped": N, "errors": N, "remaining": N}.

    Graceful-skip semantics:
      - If sqlite-vec missing OR dim-mismatch detected: all queue
        entries are skipped (stay pending); returns stats with skipped
        == queue_size.
      - If embedding unavailable for an entry: that entry is left in
        the queue for a future drain; other entries continue processing.
      - The queue file is rewritten with unprocessed entries at the end
        (idempotent: re-running drain on a stable queue + dep state
        produces the same final state).
    """
    vault = Path(vault_path)
    queue_file = _queue_path(vault)
    stats = {"processed": 0, "skipped": 0, "errors": 0, "remaining": 0}
    if not queue_file.exists():
        return stats

    # Read all queue entries.
    lines = queue_file.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(ln) for ln in lines if ln.strip()]

    # Use _open_index probe rather than just _try_import_sqlite_vec so
    # we catch the dim-mismatch case too — _open_index() prints the
    # rebuild-required warning if a mismatched index exists.
    probe = _open_index(vault)
    if probe is None:
        # sqlite-vec missing OR dim mismatch — all entries stay pending.
        stats["skipped"] = len(entries)
        stats["remaining"] = len(entries)
        return stats
    probe.close()

    unprocessed: list[dict] = []
    for record in entries:
        op = record.get("op")
        rel_path = record.get("path")
        text = record.get("text", "")
        if op == "delete":
            try:
                delete_entry(vault, rel_path)
                stats["processed"] += 1
            except Exception:  # pragma: no cover
                stats["errors"] += 1
                unprocessed.append(record)
            continue
        if op == "upsert":
            try:
                embedding = embed_text(text, mode=mode)
            except EmbeddingUnavailable:
                # Embedding mode unavailable; keep entry queued.
                stats["skipped"] += 1
                unprocessed.append(record)
                continue
            except Exception:  # pragma: no cover
                stats["errors"] += 1
                unprocessed.append(record)
                continue
            try:
                upsert_entry(vault, rel_path, embedding)
                stats["processed"] += 1
            except Exception:  # pragma: no cover
                stats["errors"] += 1
                unprocessed.append(record)
            continue
        # Unknown op — skip.
        stats["errors"] += 1
        unprocessed.append(record)

    # Rewrite queue with only unprocessed entries.
    if unprocessed:
        content = "\n".join(json.dumps(r) for r in unprocessed) + "\n"
        queue_file.write_bytes(content.encode("utf-8"))
    else:
        # All entries processed — remove the queue file.
        queue_file.unlink()

    stats["remaining"] = len(unprocessed)
    return stats


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-vec-index",
        description=(
            "MemoryVault vec-index management. Subcommands: drain "
            "(process embedding queue), size (report index count), "
            "rebuild (drop + recreate at current EMBEDDING_DIM, used "
            "after toolkit upgrades that change embedding dimension; "
            "see ADR 0001's 2026-05-20 amendment for the v0.9.2 "
            "384 → 1024 bump)."
        ),
    )
    parser.add_argument(
        "--vault-path",
        required=False,
        help="MemoryVault root (overrides MEMORY_VAULT_PATH env var)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    drain_p = sub.add_parser("drain", help="process embedding queue + upsert into index")
    drain_p.add_argument(
        "--mode",
        choices=["local", "stub"],
        default=None,
        help="embedding mode override (default: local; see embed.py for details)",
    )
    sub.add_parser("size", help="report vec-index entry count")
    sub.add_parser(
        "rebuild",
        help=(
            "drop + recreate vec-index at current EMBEDDING_DIM (use "
            "after upgrading the toolkit when a dim-mismatch warning "
            "appears)"
        ),
    )
    return parser.parse_args(argv)


def _resolve_vault_path(arg_vault_path: str | None) -> Path:
    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raise FileNotFoundError(
        "No vault path resolved. Set --vault-path or MEMORY_VAULT_PATH."
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        vault = _resolve_vault_path(args.vault_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.cmd == "drain":
        stats = drain_queue(vault, mode=args.mode)
        print(json.dumps(stats))
        return 0
    if args.cmd == "size":
        size = index_size(vault)
        if size is None:
            print(json.dumps({"size": None, "note": "sqlite-vec unavailable"}))
            return 2  # Distinct exit for graceful-skip.
        print(json.dumps({"size": size}))
        return 0
    if args.cmd == "rebuild":
        stats = rebuild_index(vault)
        if stats.get("skipped"):
            print(json.dumps(stats))
            return 2  # Distinct exit for graceful-skip.
        print(json.dumps(stats))
        return 0
    return 1  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
