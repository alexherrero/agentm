#!/usr/bin/env python3
"""lifecycle.py — V6-1 per-note lifecycle state (durable per-note tier + decay).

PLAN-wave-e-v6-index task 3 (agentm-memory-index.md). FABLE-confirmed ADAPT
call for V6-1 (design-doc.md § v6-25-external-thinking-audit): lifecycle is
explicit `supersedes:`/`superseded_by:` link-chains + git history + recency —
not a numeric per-note confidence score (that stays at the write-gate,
`harness_memory.py`'s existing `confidence`, unrelated to this module).

Two tiers:
  - "durable"  — never decays. Two categories are decay-exempt regardless of
    the `lifecycle_tier` frontmatter field (FABLE R1's named gate #2 pair):
      * error-history  — proxied by `kind: failure-incident` (the reserved
        kind save.py already scrubs PII from).
      * architecture-decisions — proxied by any entry whose vault-relative
        path has a `decisions/` directory segment.
    An entry explicitly tagged `lifecycle_tier: durable` is also exempt.
  - "volatile" — the default when `lifecycle_tier` is absent or `volatile`.
    Decays exponentially from last genuine recall access (or `created` if
    never accessed). Access-driven reset (FABLE R1, adopted-bounded): only a
    genuine recall hit resets the clock, and only for volatile-tier notes —
    a lint walk, an index rebuild, or a dreaming pass touching the file must
    NEVER reset it. This module exposes no hook those callers use; only
    recall.py's prompt_submit() calls `record_recall_access()`, mirroring
    heat_policy.record_hit()'s exact call-site discipline.

Persistence: a sidecar (`.lifecycle.json` at vault root), not frontmatter —
frontmatter carries the durable `lifecycle_tier` classification; the sidecar
carries the volatile last-access clock, mirroring heat_policy.py's split
(frontmatter = durable classification, `.heat.json` = ephemeral counters).

Public API (called by recall.py):
  lifecycle_tier_for(fm, rel_path)             — "durable" | "volatile"
  is_decay_exempt(fm, rel_path)                — bool
  compute_decay_score(vault, slug, fm, rel_path, *, now=None) -> float
  record_recall_access(vault, slug, fm, rel_path, *, today=None) -> None
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path, PurePosixPath

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Sidecar filename at vault root (hidden, not an Obsidian note — mirrors
# heat_policy.py's HEAT_SIDECAR_NAME convention).
LIFECYCLE_SIDECAR_NAME = ".lifecycle.json"

# Decay-exempt "error-history" proxy: the reserved `kind` value save.py
# already mandates a PII scrub for. Named explicitly in FABLE R1's gate #2.
DECAY_EXEMPT_KINDS = frozenset({"failure-incident"})

# Decay-exempt "architecture-decisions" proxy: any entry under a `decisions/`
# directory segment, matching the real vault convention observed across
# `projects/*/decisions/*.md` (task 1's re-audit dossier sampled this exact
# directory). Path-based rather than kind-based because no dedicated
# `kind: decision` value exists in this codebase today (ADRs retired into
# living-design amendment logs per the operator's global convention).
_DECISIONS_DIR_SEGMENT = "decisions"

# Exponential decay half-life for volatile-tier notes (days). A note with no
# access for one half-life decays to 0.5; two half-lives to 0.25; etc.
# Tunable default — not measured against real usage yet (V6-20 eval slice for
# this task covers "field populated + queryable + red-tests", not a tuned
# half-life; revisit once real access patterns exist to tune against).
DECAY_HALF_LIFE_DAYS = 30.0


def lifecycle_tier_for(fm: dict[str, str], rel_path: str | Path) -> str:
    """Return "durable" or "volatile" for an entry.

    Explicit `lifecycle_tier: durable` in frontmatter, OR either decay-exempt
    proxy (kind or decisions/ path segment), classifies as durable. Absent
    field + no proxy match defaults to volatile.
    """
    if is_decay_exempt(fm, rel_path):
        return "durable"
    return "volatile" if fm.get("lifecycle_tier", "volatile") != "durable" else "durable"


def is_decay_exempt(fm: dict[str, str], rel_path: str | Path) -> bool:
    """True if this entry never decays, regardless of access pattern.

    Three independent routes, per FABLE R1's gate #2 pairing plus the
    explicit-tag escape hatch:
      1. `lifecycle_tier: durable` frontmatter tag.
      2. `kind: failure-incident` (error-history proxy).
      3. a `decisions/` directory segment in the vault-relative path
         (architecture-decisions proxy).
    """
    if fm.get("lifecycle_tier") == "durable":
        return True
    if fm.get("kind") in DECAY_EXEMPT_KINDS:
        return True
    parts = PurePosixPath(str(rel_path)).parts
    if _DECISIONS_DIR_SEGMENT in parts:
        return True
    return False


def _load_sidecar(vault: Path) -> dict:
    path = vault / LIFECYCLE_SIDECAR_NAME
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("version") == 1:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "entries": {}}


def _save_sidecar(vault: Path, data: dict) -> None:
    try:
        from vault_lock import atomic_write  # type: ignore
    except ImportError:
        path = vault / LIFECYCLE_SIDECAR_NAME
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return
    path = vault / LIFECYCLE_SIDECAR_NAME
    content = json.dumps(data, indent=2, sort_keys=True)
    atomic_write(path, content)


def record_recall_access(
    vault: Path,
    slug: str,
    fm: dict[str, str],
    rel_path: str | Path,
    *,
    today: str | None = None,
) -> None:
    """Record one genuine recall access for `slug` — resets the decay clock.

    Only called from recall.py's prompt_submit() (mirrors heat_policy's
    record_hit() call-site discipline exactly: a lint walk, an index rebuild,
    or a dreaming/consolidation pass touching the file must never call this).

    No-op for decay-exempt entries (durable tiers ignore access entirely —
    FABLE R1's explicit bound). Best-effort: exceptions are swallowed, since
    lifecycle tracking must never block the recall pipeline.

    `today` is injectable for tests (ISO date string YYYY-MM-DD).
    """
    if is_decay_exempt(fm, rel_path):
        return
    try:
        if today is None:
            import datetime
            today = datetime.date.today().isoformat()
        try:
            from vault_lock import vault_mutex  # type: ignore
            ctx = vault_mutex(vault)
        except ImportError:
            from contextlib import nullcontext
            ctx = nullcontext()

        with ctx:
            data = _load_sidecar(vault)
            entries = data.setdefault("entries", {})
            entries[slug] = {"last_access": today}
            _save_sidecar(vault, data)
    except Exception:  # noqa: BLE001 — lifecycle tracking is best-effort
        pass


def _days_between(earlier_iso: str, later_iso: str) -> float:
    import datetime
    fmt = "%Y-%m-%d"
    a = datetime.datetime.strptime(earlier_iso[:10], fmt)
    b = datetime.datetime.strptime(later_iso[:10], fmt)
    return (b - a).total_seconds() / 86400.0


def compute_decay_score(
    vault: Path,
    slug: str,
    fm: dict[str, str],
    rel_path: str | Path,
    *,
    now: str | None = None,
) -> float:
    """Return a decay score in (0.0, 1.0] — 1.0 means fully fresh/no decay.

    Decay-exempt entries always score 1.0 (durable tiers ignore access —
    FABLE R1). Volatile entries decay exponentially from the last genuine
    recall access (sidecar `last_access`), falling back to frontmatter
    `updated` (then `created`, if `updated` is absent) with half-life
    `DECAY_HALF_LIFE_DAYS`.

    `updated`, not `created`, is the right fallback anchor: an entry that
    was substantively edited today is fresh regardless of how old its
    original creation date is — falling back to `created` would penalize a
    frequently-maintained reference doc for staleness it doesn't have,
    purely because it has never yet had a genuine recall access recorded
    (a cold-start bias task 6's own eval caught: it silently demoted an
    accurate, same-day-edited hit out of the top-5 entirely).

    `now` is injectable for tests (ISO date string YYYY-MM-DD).
    """
    if is_decay_exempt(fm, rel_path):
        return 1.0
    if now is None:
        import datetime
        now = datetime.date.today().isoformat()

    data = _load_sidecar(vault)
    entry = data.get("entries", {}).get(slug, {})
    anchor = entry.get("last_access") or fm.get("updated") or fm.get("created")
    if not anchor:
        return 1.0  # No basis to compute decay — treat as fresh.
    try:
        elapsed_days = max(0.0, _days_between(anchor, now))
    except ValueError:
        return 1.0  # Malformed date — fail open to fresh rather than crash.
    return math.pow(0.5, elapsed_days / DECAY_HALF_LIFE_DAYS)
