#!/usr/bin/env python3
"""recall_counter.py — per-recall JSONL event ledger (L1, ledger ruling 6).

The Morning Brief's "retrieved" count needs a real per-recall signal;
nothing counted individual recalls before this (heat_policy.py's `.heat.json`
sidecar is a mutable rolled-up per-slug counter, not an append-only event
log). Privacy-shaped by design: logs the query as a hash, never raw text,
plus the slugs actually surfaced and how many. Mirrors
`inbox_digest.append_digest_history`'s JSONL-append idiom.

The sole call site is `recall.py`'s `prompt_submit()`, right after token-
budget truncation decides the final `loaded_slugs` -- a lint walk, index
rebuild, or dreaming pass must never reach this, same discipline as
`heat_policy.record_hit()` and `lifecycle.record_recall_access()`.

Rows expire (recall-ledger-retention): `record_recall` runs a cheap probe
after each append and sweeps rows past `RETENTION_DAYS`. Since recall-trace
widened each row with `hits[].path`, a row names the vault directory a note
lived in, and nothing used to remove one -- so a path outlived the note it
described. Retention bounds that to the window where the evidence is still
useful. It bounds how LONG vault structure is disclosed, not WHO can read it;
the trace design's trust-boundary trigger stands unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Retention policy (recall-ledger-retention). Rows age out; the byte ceiling is
# a backstop for a burst that fills the window early, not the common path.
# Sized against the live ledger: 116 recalls/day at ~250 bytes/row projects to
# ~28 KB/day, so 90 days holds ~2.5 MB (~6.3 MB under the trace design's more
# pessimistic ~70 KB/day estimate) -- both far below the ceiling. The ceiling
# sits BELOW the trace design's ~50 MB re-audit trigger on purpose: enforcement
# should engage before the threshold that says "go redesign this," not after.
RETENTION_DAYS = 90.0
MAX_BYTES = 25 * 1024 * 1024


def default_history_path() -> Path:
    """The ledger path, overridable via `$AGENTM_RECALL_HISTORY`.

    `recall.py`'s `prompt_submit()` calls `record_recall` with no
    `history_path`, so anything exercising that function writes the operator's
    REAL ledger. Three test suites did exactly that; PR #390 fixed them by
    mocking `record_recall`, and that mocking is the primary guard -- this
    override does not replace it.

    The override is the standing escape hatch underneath it. Mocking protects
    the call sites someone remembered to mock, which was enough while the
    ledger was append-only (stray rows landed and sat there). It is a thinner
    guarantee now that `record_recall` can prune, because a future unmocked
    caller no longer just adds a row -- it read-modify-writes the file and can
    drop real ones. Redirecting the path protects whatever the mocks miss.

    Mirrors the `$MEMORY_VAULT_PATH` / `$AGENTM_TELEMETRY_DIR` /
    `$XDG_CACHE_HOME` escape hatches this codebase already uses.
    """
    override = os.environ.get("AGENTM_RECALL_HISTORY")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "agentm" / "telemetry" / "recall-history.jsonl"


def _hash_query(query_text: str) -> str:
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()[:16]


def record_recall(query_text: str, hit_slugs: list[str], *,
                   hits: "list[dict] | None" = None,
                   drops: "dict | None" = None,
                   now: "datetime | None" = None,
                   history_path: "Path | None" = None) -> dict:
    """Append one recall event. Best-effort: a write failure never raises --
    callers treat this the same as heat_policy/lifecycle's other best-effort
    recording, never blocking the recall pipeline itself.

    `hits` (recall-trace, Loose Ends Release 8) is the optional per-slug
    evidence array recall.py's query() already computes -- sim/keyword/
    combined/rank and, when available, lifecycle_tier/decay_score. Omitted
    (None) by default so a caller not yet passing it produces the exact row
    shape this ledger has always written; the key is left off the row
    entirely rather than written as `[]`, so a reader can tell "no trace
    recorded" apart from "recorded, zero hits" (there is no such thing as a
    recall event with hit_slugs non-empty but zero hits, so `[]` would only
    ever mean the former in practice -- omitting the key says so directly
    instead of relying on that inference)."""
    now = now if now is not None else datetime.now(timezone.utc)
    path = history_path if history_path is not None else default_history_path()
    row = {
        "ts": now.isoformat(),
        "query_hash": _hash_query(query_text),
        "hit_slugs": list(hit_slugs),
        "hit_count": len(hit_slugs),
    }
    if hits is not None:
        row["hits"] = list(hits)
    if drops:
        # Why an empty recall was empty (online-recall task 3). `hit_count: 0`
        # with `hits: []` cannot separate a retrieval miss from over-filtering,
        # and those have opposite fixes. Integers only — how many rows the
        # daemon returned, and how many each stage dropped. Deliberately *not*
        # the extracted terms: those are prompt vocabulary, and this file's
        # contract is a hashed query, never raw text.
        row["drops"] = {k: int(v) for k, v in sorted(drops.items())}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass
    else:
        _maybe_prune(path, now=now)
    return row


def _maybe_prune(path: Path, *, now: datetime) -> None:
    """Run the retention sweep if the ledger has aged past the window or
    crossed the byte ceiling. Best-effort in the same sense as the append
    above: nothing here may raise into the recall pipeline.

    Enforcement lives on the write path deliberately, not on a scheduler.
    `dream.py`'s job template is weekly, ships `dry_run: true`, and is not
    seeded at install; `dream_confirm.cleanup_applied_batches()` -- this
    repo's only other retention routine -- has no production caller at all.
    A policy hung off a scheduler that may never run is a policy that
    silently isn't enforced, and this codebase has shipped that failure
    twice (session reflection dead 57 days under green CI; the
    crystallization trigger shipped inert). `record_recall` is the one path
    that provably executes, because it is the path that writes the rows.
    """
    try:
        if _needs_prune(path, now=now):
            prune_history(now=now, history_path=path)
    except Exception:  # noqa: BLE001 -- never block recall on housekeeping
        pass


def _needs_prune(path: Path, *, now: datetime,
                 retention_days: float = RETENTION_DAYS,
                 max_bytes: int = MAX_BYTES) -> bool:
    """Cheap "is a sweep warranted?" probe -- O(1), not a read of the file.

    The ledger is append-only and chronological, so line 1 is the oldest row:
    parsing just its `ts` answers the question without reading the rest.
    Measured on the real 185 KB production ledger, `stat` + first-line read
    costs 11.9 microseconds, against a `prompt_submit()` that already runs
    vector search, BM25, and a file read per hit. Nothing to gain by gating
    something that cheap, so the check is unconditional.

    An unreadable head row counts as "prune." It can only be torn-write
    debris, `prune_history` drops rows it can't parse, and so the state
    self-heals in one pass -- rather than leaving a ledger that can never
    age-prune because its oldest row is garbage.
    """
    try:
        if path.stat().st_size >= max_bytes:
            return True
        with open(path, "rb") as fh:
            first = fh.readline()
    except OSError:
        return False
    if not first.strip():
        return False
    ts = _row_ts(first)
    if ts is None:
        return True
    return ts < now - timedelta(days=retention_days)


def _row_ts(line: "str | bytes") -> "datetime | None":
    """Parse a ledger line's `ts`, or None if the line is unusable.

    Tolerates every shape a corrupt or foreign line can take -- invalid JSON,
    valid JSON that isn't an object (a bare list or scalar, so `["ts"]` raises
    TypeError), a missing key, an unparseable timestamp. A naive timestamp is
    read as UTC, matching what `record_recall` writes.
    """
    try:
        row = json.loads(line)
        ts = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, KeyError, ValueError):
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def prune_history(*, now: "datetime | None" = None,
                  retention_days: float = RETENTION_DAYS,
                  history_path: "Path | None" = None) -> dict:
    """Drop ledger rows older than `retention_days`; keep the rest.

    Retention is by age because age subsumes redaction. A row naming a note
    deleted six months ago disappears because the ROW is six months old -- no
    vault lookup, no chasing a target that moves. Resolving `hits[].path`
    against the live vault instead would be actively wrong: `heat_policy`'s
    promote/demote and `dream`'s tidying stage MOVE notes without recording
    old->new, so "path no longer resolves" is mostly a note that is alive one
    directory over, and purging on delete would destroy the trace for the
    notes most worth tracing.

    Rows that can't be parsed, or carry no usable `ts`, are dropped. No reader
    can interpret them -- `trace()` and `count_since()` already skip such lines
    -- and keeping them means torn-write debris accumulates with no way out.

    Rewrites through `vault_lock.atomic_write` (temp -> fsync -> rename). A row
    appended between the read and the rename lands on the replaced inode and is
    lost; that window is sub-millisecond, prunes fire on the order of monthly
    at observed volume, and this ledger is already best-effort. No mutex is
    taken: it would only serialize prune against prune, and two concurrent
    prunes each write a complete valid file, so one simply wins.

    Returns `{"kept": rows now in the file, "dropped": rows removed,
    "pruned": whether the file was rewritten}`. When the rewrite can't happen,
    nothing is removed and the counts say so rather than reporting an intent.
    """
    now = now if now is not None else datetime.now(timezone.utc)
    path = history_path if history_path is not None else default_history_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"kept": 0, "dropped": 0, "pruned": False}

    cutoff = now - timedelta(days=retention_days)
    kept: list[str] = []
    dropped = 0
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        ts = _row_ts(stripped)
        if ts is None or ts < cutoff:
            dropped += 1
            continue
        kept.append(stripped)

    if dropped == 0:
        return {"kept": len(kept), "dropped": 0, "pruned": False}

    # Lazy, and inside the guard: `vault_lock` is a vendored sibling in this
    # scripts/ dir (DC-9), so an import failure must degrade to "ledger keeps
    # growing" -- the status quo -- rather than break recall's counter at
    # import time. A non-atomic fallback write is NOT an option here: a torn
    # rewrite would lose the whole ledger, which is worse than not pruning.
    try:
        import sys
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from vault_lock import atomic_write  # noqa: E402
        atomic_write(path, "".join(line + "\n" for line in kept))
    except (ImportError, OSError):
        return {"kept": len(kept) + dropped, "dropped": 0, "pruned": False}

    return {"kept": len(kept), "dropped": dropped, "pruned": True}


def count_since(*, now: "datetime | None" = None, lookback_seconds: int,
                 history_path: "Path | None" = None) -> dict:
    """Summarize recall events within `lookback_seconds` of `now`: total
    recall calls and total hits surfaced across them. Malformed lines and
    unparseable timestamps are skipped, never raised."""
    now = now if now is not None else datetime.now(timezone.utc)
    path = history_path if history_path is not None else default_history_path()
    if not path.is_file():
        return {"recall_count": 0, "hit_count": 0}

    from datetime import timedelta
    cutoff = now - timedelta(seconds=lookback_seconds)
    recall_count = 0
    hit_count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if ts < cutoff:
            continue
        recall_count += 1
        hit_count += int(row.get("hit_count", 0))
    return {"recall_count": recall_count, "hit_count": hit_count}
