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
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def default_history_path() -> Path:
    return Path.home() / ".cache" / "agentm" / "telemetry" / "recall-history.jsonl"


def _hash_query(query_text: str) -> str:
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()[:16]


def record_recall(query_text: str, hit_slugs: list[str], *,
                   now: "datetime | None" = None,
                   history_path: "Path | None" = None) -> dict:
    """Append one recall event. Best-effort: a write failure never raises --
    callers treat this the same as heat_policy/lifecycle's other best-effort
    recording, never blocking the recall pipeline itself."""
    now = now if now is not None else datetime.now(timezone.utc)
    path = history_path if history_path is not None else default_history_path()
    row = {
        "ts": now.isoformat(),
        "query_hash": _hash_query(query_text),
        "hit_slugs": list(hit_slugs),
        "hit_count": len(hit_slugs),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass
    return row


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
