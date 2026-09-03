#!/usr/bin/env python3
"""graph_snapshot.py — persisted, incrementally-rebuilt typed-edge graph.

PLAN-auto-org-write-time-linking task 2. `graph.py`'s `extract_edges()` is a
pure, stateless, in-memory extractor with exactly one caller today
(`consolidate.py`, fresh per run) — there is no derived, persisted snapshot
anywhere. This module adds one, as a device-local SQLite store under
`~/.agentm/memory/_meta/` ("SQLite on cloud sync corrupts", so it never lives
in the vault).

The two consumers this snapshot was built for — the write-time linker and the
weekly link-improvement sweep — were removed along with the vector stack they
rode on (see `wiki/designs/agentm-rescope-week1-experiment.md`). It is kept
because `consolidate.py` and the orphan reporting still read it, and because
the walk it maintains is the only cheap "who links to X" answer in the tree.

`consolidate.py`'s existing in-memory `graph.extract_edges_for_paths()` call
is left as-is (confirmed at /work time, per the plan's own note) — it scores
recurrence over a caller-supplied, situational `episodic_paths` subset, which
doesn't map cleanly onto "the whole persisted graph."

Rebuild has two modes:
  - `rebuild(vault)` (no `paths`): walks the vault, re-extracts only files
    whose mtime is newer than what's stored, and drops nodes whose source
    file is gone. This is the full-sweep shape.
  - `rebuild(vault, paths=[...])`: skips the walk entirely and re-extracts
    exactly the given paths — a single just-saved note, no full-vault cost.

Public API:
  rebuild(vault_path, *, paths=None) -> RebuildStats
  incoming(vault_path, path) -> list[str]      # source paths that link to `path`
  outgoing(vault_path, path) -> list[Edge]      # `path`'s own outgoing edges
  orphans(vault_path) -> list[str]              # known nodes with zero edges either direction
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import graph  # noqa: E402

_SNAPSHOT_FILENAME = "graph-snapshot.db"

# Device-local store root: SQLite on cloud-sync is a known corruption pattern,
# so the snapshot never lives in the vault. Mirrors the V5-1 storage-seam
# Tier.LOCAL_INDEX "never sync" contract. These three helpers were imported
# from `vec_index.py` until that module was removed; none of them was ever
# vector-specific, so they moved here rather than going with it.
_LOCAL_INDEX_ROOT = Path.home() / ".agentm" / "memory" / "_meta"

# The metadata columns extracted from an entry's frontmatter.
_META_COLUMNS: tuple[str, ...] = (
    "kind", "status", "slug", "project", "created", "tags", "group_name", "fingerprint",
)


def _local_index_dir(vault: Path) -> Path:
    """Device-local dir for this vault's derived stores.

    Named by vault path so multiple vaults don't collide. Uses a short hash
    suffix to survive vault-root renames without breaking the namespace.
    """
    key = f"{vault.resolve().name}-{hashlib.sha256(str(vault.resolve()).encode()).hexdigest()[:8]}"
    return _LOCAL_INDEX_ROOT / key


def _vault_rel(path: Path, vault: Path) -> str:
    """Vault-relative key; a root-space path (the `Projects/` sibling of the
    memory root) is keyed relative to the vault root instead."""
    try:
        rel = path.relative_to(vault)
    except ValueError:
        rel = path.relative_to(vault.parent)
    return str(rel).replace("\\", "/")


def _root_projects_dir(vault):
    """The vault-root `Projects/` space, discovered never conjured (filing-v2
    2b). Flat layout: `<memory-root>/Projects`. Nested layout — the memory
    root sits inside an Obsidian vault, witnessed by `.obsidian/` at the
    parent and none at the memory root itself: the sibling
    `<vault-root>/Projects`. A memory root at the top of its own vault has no
    sibling, whatever directory named `Projects` sits beside it (its parent
    is the operator's home or a sync folder, where one is common and is not
    the vault's). None when no root space exists."""
    vault = Path(vault)
    flat = vault / "Projects"
    if flat.is_dir():
        return flat
    parent = vault.parent
    if (parent / ".obsidian").is_dir() and not (vault / ".obsidian").is_dir():
        sibling = parent / "Projects"
        if sibling.is_dir():
            return sibling
    return None


def _vault_projects_dir(vault: Path) -> Path:
    """Return <vault>/projects/ (post-V4 #26 canonical) if present, else
    <vault>/personal-projects/ (legacy fallback). Mirrors the same helper
    in harness_memory.py; duplicated here to avoid cross-script import
    coupling within the memory skill scripts dir.
    """
    new = vault / "desk/projects"
    if new.is_dir():
        return new
    legacy = vault / "personal-projects"
    if legacy.is_dir():
        return legacy
    return new  # neither exists; return new (preferred) for caller's mkdir


def _extract_meta_from_file(file_path: Path) -> dict:
    """Extract the metadata columns from a memory entry's frontmatter.

    Stdlib-only inline parse (avoid importing yaml, ADR 0001). Best-effort:
    an unreadable or frontmatter-less file returns a dict of `None`s (never
    raises) so a metadata-extraction failure never blocks its caller.

    `project` is derived, not read directly — the frontmatter has no
    `project:` field (see `save.py`'s `FRONTMATTER_FIELD_ORDER`); a `group:`
    value of the vault's `projects/<slug>/...` shape yields `project=<slug>`,
    everything else (personal/, incubator/, …) yields `project=None`.
    """
    empty = {c: None for c in _META_COLUMNS}
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return empty
    if not text.startswith("---\n"):
        return empty
    end = text.find("\n---\n", 4)
    if end == -1:
        return empty
    fm_text = text[4:end]

    meta = dict(empty)
    meta["slug"] = file_path.stem
    group_value: str | None = None
    tags: list[str] = []
    for line in fm_text.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key == "kind":
            meta["kind"] = val
        elif key == "status":
            meta["status"] = val
        elif key == "slug":
            meta["slug"] = val
        elif key == "created":
            meta["created"] = val
        elif key == "group":
            group_value = val
        elif key == "fingerprint":
            meta["fingerprint"] = val
        elif key == "tags":
            if val.startswith("[") and val.endswith("]"):
                tags = [t.strip().strip("'\"") for t in val[1:-1].split(",") if t.strip()]

    meta["group_name"] = group_value
    meta["tags"] = json.dumps(tags)
    # The slug is the first segment AFTER the projects space, on either
    # generation. (The previous fixed index returned the literal "projects"
    # for `desk/projects/<slug>` — the same miss recall.py fixed earlier.)
    for prefix in ("desk/projects/", "Projects/", "projects/"):
        if group_value and group_value.startswith(prefix):
            slug = group_value[len(prefix):].split("/", 1)[0]
            meta["project"] = slug or None
            break
    return meta

# A path that's staged-not-reviewed (_inbox) or historical
# (_archive, PLAN.archive.*.md) is never a graph node either.
_EXCLUDED_DIR_NAMES = frozenset({"_archive", "_inbox"})


@dataclass(frozen=True)
class RebuildStats:
    files_touched: int
    edges_written: int
    nodes_removed: int
    touched_paths: list[str]


def _snapshot_path(vault: Path) -> Path:
    return _local_index_dir(vault) / _SNAPSHOT_FILENAME


def _open(vault: Path) -> sqlite3.Connection:
    snapshot_path = _snapshot_path(vault)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(snapshot_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS nodes ("
        "  path TEXT PRIMARY KEY,"
        "  slug TEXT NOT NULL,"
        "  mtime REAL NOT NULL,"
        "  updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_slug ON nodes(slug)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS edges ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  source_path TEXT NOT NULL,"
        "  target TEXT NOT NULL,"
        "  edge_type TEXT NOT NULL"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target)")
    conn.commit()
    return conn


def _walk_vault_paths(vault: Path) -> list[str]:
    """Walk roots: personal/,
    projects/<slug>/ (or legacy personal-projects/), _idea-incubator/;
    excludes _archive/, _inbox/ (at any depth) and PLAN.archive.*.md.
    Returns vault-relative POSIX path strings.
    """
    walk_roots: list[Path] = []
    private = vault / "memory"
    if private.is_dir():
        walk_roots.append(private)
    # Filing-v2 2b: the vault-root `Projects/` generation is a SIBLING of the
    # memory root; during the merge window both spaces may hold projects.
    for projects in (_vault_projects_dir(vault), _root_projects_dir(vault)):
        if projects is not None and projects.is_dir() and projects not in walk_roots:
            walk_roots.append(projects)
    incubator = vault / "_idea-incubator"
    if incubator.is_dir():
        walk_roots.append(incubator)

    out: list[str] = []
    for root in walk_roots:
        for md in sorted(root.rglob("*.md")):
            if any(p in _EXCLUDED_DIR_NAMES for p in md.parts):
                continue
            if md.name.startswith("PLAN.archive."):
                continue
            out.append(_vault_rel(md, vault))
    return out


def _slug_for(vault: Path, rel_path: str) -> str:
    return _extract_meta_from_file(vault / rel_path)["slug"] or Path(rel_path).stem


def _reindex_one(conn: sqlite3.Connection, vault: Path, rel_path: str, mtime: float) -> int:
    """Delete `rel_path`'s existing edges + node row, re-extract, re-insert.
    Returns the number of edges written. Caller commits."""
    conn.execute("DELETE FROM edges WHERE source_path = ?", (rel_path,))
    try:
        content = (vault / rel_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    edges = graph.extract_edges(rel_path, content)
    for e in edges:
        conn.execute(
            "INSERT INTO edges(source_path, target, edge_type) VALUES (?, ?, ?)",
            (e.source_path, e.target, e.edge_type),
        )
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slug = _slug_for(vault, rel_path)
    conn.execute(
        "INSERT INTO nodes(path, slug, mtime, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET slug = excluded.slug, mtime = excluded.mtime, "
        "updated_at = excluded.updated_at",
        (rel_path, slug, mtime, now_iso),
    )
    return len(edges)


def _remove_one(conn: sqlite3.Connection, rel_path: str) -> None:
    conn.execute("DELETE FROM edges WHERE source_path = ?", (rel_path,))
    conn.execute("DELETE FROM nodes WHERE path = ?", (rel_path,))


def rebuild(vault_path: Path | str, *, paths: list[str] | None = None) -> RebuildStats:
    """Rebuild the snapshot.

    `paths=None` (default): full-vault mode. Walks the vault, re-extracts
    only files whose mtime is newer than the stored node (or that aren't
    stored yet), and drops any stored node whose source file no longer
    exists anywhere in the walk.

    `paths=[...]`: targeted mode. No walk — re-extracts exactly the given
    vault-relative paths (a path that no longer exists on disk is treated
    as a deletion, mirroring the stale-upsert-becomes-
    delete convention). This is the cheap path the write-time linker uses
    for a single just-saved note.

    `RebuildStats.touched_paths` is the list of paths actually re-extracted
    this call (new or changed since the prior rebuild; excludes removed
    paths) — the "arrived or changed since the last cycle" signal the
    weekly link-improvement sweep (task 4) needs, without a second,
    independent staleness-tracking mechanism duplicating this one.
    """
    vault = Path(vault_path)
    conn = _open(vault)
    try:
        files_touched = 0
        edges_written = 0
        nodes_removed = 0
        touched_paths: list[str] = []

        if paths is not None:
            for rel_path in paths:
                full = vault / rel_path
                if not full.is_file():
                    cur = conn.execute("SELECT 1 FROM nodes WHERE path = ?", (rel_path,))
                    if cur.fetchone():
                        _remove_one(conn, rel_path)
                        nodes_removed += 1
                    continue
                mtime = full.stat().st_mtime
                edges_written += _reindex_one(conn, vault, rel_path, mtime)
                files_touched += 1
                touched_paths.append(rel_path)
            conn.commit()
            return RebuildStats(
                files_touched=files_touched,
                edges_written=edges_written,
                nodes_removed=nodes_removed,
                touched_paths=touched_paths,
            )

        # Full-vault mode.
        walked = _walk_vault_paths(vault)
        walked_set = set(walked)
        stored = {
            row[0]: row[1]
            for row in conn.execute("SELECT path, mtime FROM nodes").fetchall()
        }

        for rel_path in walked:
            mtime = (vault / rel_path).stat().st_mtime
            stored_mtime = stored.get(rel_path)
            if stored_mtime is not None and mtime <= stored_mtime:
                continue  # unchanged — skip re-extraction
            edges_written += _reindex_one(conn, vault, rel_path, mtime)
            files_touched += 1
            touched_paths.append(rel_path)

        for rel_path in stored:
            if rel_path not in walked_set:
                _remove_one(conn, rel_path)
                nodes_removed += 1

        conn.commit()
        return RebuildStats(
            files_touched=files_touched,
            edges_written=edges_written,
            nodes_removed=nodes_removed,
            touched_paths=touched_paths,
        )
    finally:
        conn.close()


def incoming(vault_path: Path | str, path: str) -> list[str]:
    """Vault-relative source paths of edges whose target resolves to `path`
    (matched by `path`'s own slug, or by its full path — most wikilinks in
    this vault target a bare slug, per graph.py's raw wikilink capture)."""
    vault = Path(vault_path)
    conn = _open(vault)
    try:
        row = conn.execute("SELECT slug FROM nodes WHERE path = ?", (path,)).fetchone()
        slug = row[0] if row else Path(path).stem
        cursor = conn.execute(
            "SELECT DISTINCT source_path FROM edges WHERE target = ? OR target = ? "
            "ORDER BY source_path",
            (slug, path),
        )
        return [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()


def outgoing(vault_path: Path | str, path: str) -> list[graph.Edge]:
    """`path`'s own outgoing edges, as extracted and stored at last rebuild."""
    vault = Path(vault_path)
    conn = _open(vault)
    try:
        cursor = conn.execute(
            "SELECT source_path, target, edge_type FROM edges WHERE source_path = ? "
            "ORDER BY id",
            (path,),
        )
        return [graph.Edge(source_path=r[0], target=r[1], edge_type=r[2]) for r in cursor.fetchall()]
    finally:
        conn.close()


def orphans(vault_path: Path | str) -> list[str]:
    """Vault-relative paths of every known node with zero edges in either
    direction — no outgoing wikilink, and nothing else's wikilink resolves
    to this node's slug or path."""
    vault = Path(vault_path)
    conn = _open(vault)
    try:
        nodes = conn.execute("SELECT path, slug FROM nodes ORDER BY path").fetchall()
        out: list[str] = []
        for path, slug in nodes:
            has_outgoing = conn.execute(
                "SELECT 1 FROM edges WHERE source_path = ? LIMIT 1", (path,)
            ).fetchone()
            if has_outgoing:
                continue
            has_incoming = conn.execute(
                "SELECT 1 FROM edges WHERE target = ? OR target = ? LIMIT 1",
                (slug, path),
            ).fetchone()
            if has_incoming:
                continue
            out.append(path)
        return out
    finally:
        conn.close()
