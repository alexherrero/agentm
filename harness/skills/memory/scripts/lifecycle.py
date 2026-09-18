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
    Decays on the contract's stepped curve from last genuine recall access (or
    `created` if never accessed). Access-driven reset (FABLE R1, adopted-bounded): only a
    genuine recall hit resets the clock, and only for volatile-tier notes —
    a lint walk, an index rebuild, or a dreaming pass touching the file must
    NEVER reset it. This module exposes no hook those callers use; only
    recall.py's prompt_submit() calls `record_recall_access()`, mirroring
    heat_policy.record_hit()'s exact call-site discipline.

Persistence: a sidecar (`.lifecycle.json` at vault root), not frontmatter —
frontmatter carries the durable `lifecycle_tier` classification; the sidecar
carries the volatile last-access clock, mirroring heat_policy.py's split
(frontmatter = durable classification, `.heat.json` = ephemeral counters).

Public API (called by recall.py, and — as of the auto-organization tidying
stage, task 3 — dream.py):
  lifecycle_tier_for(fm, rel_path)             — "durable" | "volatile"
  is_decay_exempt(fm, rel_path)                — bool
  compute_decay_score(vault, slug, fm, rel_path, *, now=None) -> float
  record_recall_access(vault, slug, fm, rel_path, *, today=None) -> None
  days_since_last_genuine_access(vault, slug, fm, rel_path, *, now=None)
                                                — float | None
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import storage_rules  # noqa: E402  (the filing contract, for space exemptions)

# Sidecar filename at vault root (hidden, not an Obsidian note — mirrors
# heat_policy.py's HEAT_SIDECAR_NAME convention).
LIFECYCLE_SIDECAR_NAME = ".lifecycle.json"

import vault_layout  # noqa: E402  (same-dir; the sidecar's home moved to the engine dir in plan 05)

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

# The curve with no contract behind it — the bands this module and the daemon
# both carried as constants before the contract's `decay_*` lines were read.
# Kept so a vault whose contract predates those lines ranks exactly as it did,
# and mirrored byte for byte by `note.defaultBands` in Go.
#
# Each pair is (elapsed days at or below, score), checked in order.
_DEFAULT_BANDS: tuple[tuple[float, float], ...] = (
    (182.0, 1.0),
    (365.0, 0.5),
    (1095.0, 0.125),
    (1825.0, 0.0625),
)

# What a memory scores past the last band when the contract names no weight.
# A floor, not a waypoint: the curve never reaches zero.
DECAY_FLOOR = 0.0625

# The contract keys the curve is read from, in the order the bands fall.
_BAND_KEYS = (
    "decay_full_days",
    "decay_half_days",
    "decay_eighth_days",
    "decay_floor_days",
)
_BAND_SCORES = (1.0, 0.5, 0.125)


def decay_bands() -> tuple[tuple[float, float], ...]:
    """The one curve, read from the filing contract's `decay_*` thresholds.

    Before this, the daemon ran stepped bands hardcoded in Go and this module
    ran a thirty-day exponential, so the same note ranked two different ways
    depending on which arm answered — and the contract's five `decay_*` lines
    were read by neither. One curve, and it is the operator's.

    A contract that does not describe an ascending curve falls back to
    `_DEFAULT_BANDS` **wholesale**, never band by band: a curve read half from
    the contract and half from a constant is exactly the drift this reads the
    contract to end, and it would be invisible.
    """
    try:
        thresholds = storage_rules.rules().thresholds()
    except Exception:
        return _DEFAULT_BANDS
    days: list[float] = []
    previous = 0.0
    for key in _BAND_KEYS:
        try:
            value = float(thresholds[key])
        except (KeyError, TypeError, ValueError):
            return _DEFAULT_BANDS
        if value <= previous:
            return _DEFAULT_BANDS
        previous = value
        days.append(value)
    try:
        floor_weight = float(thresholds.get("decay_floor_weight", 0) or 0)
    except (TypeError, ValueError):
        floor_weight = 0.0
    if floor_weight <= 0:
        floor_weight = DECAY_FLOOR
    return tuple(zip(days, (*_BAND_SCORES, floor_weight)))


def _score_from_bands(elapsed_days: float) -> float:
    """Pure elapsed-days -> score against the live curve. No I/O, no exempt
    check — compute_decay_score applies those before ever calling this."""
    bands = decay_bands()
    for threshold, score in bands:
        if elapsed_days <= threshold:
            return score
    return bands[-1][1]


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
    # Path(rel_path), not PurePosixPath(str(rel_path)) -- the latter silently
    # broke on a real Path object on Windows: str(WindowsPath(...)) renders
    # backslash-separated, and PurePosixPath treats a backslash as an
    # ordinary filename character, not a separator, collapsing the whole
    # relative path into ONE segment -- so "decisions" was never found in
    # `parts`, and a decisions/ entry silently lost its exemption on Windows.
    # A plain Path() handles both a forward-slash string literal (every
    # existing caller/test) and a real Path object (this module's own
    # production call sites, e.g. dream.py's `path.relative_to(vault_path)`)
    # correctly on every platform -- PureWindowsPath accepts "/" exactly
    # like PurePosixPath does, so there is no cross-platform downside.
    parts = Path(rel_path).parts
    if _DECISIONS_DIR_SEGMENT in parts:
        return True
    return False


# The sidecar's shapes. Version 1 keyed an entry on a note's slug — its
# basename, or the `slug:` in its frontmatter. That is not an identity: 26 keys
# in the live file matched more than one file in the vault, `progress` matching
# eight of them, so eight notes shared one clock and a recall of any of them
# read as a recall of all eight. Version 2 keys on the note's path from the
# vault root, which is what the daemon already writes for every row it holds,
# and carries the note's fingerprint so a file that moves can be followed to its
# new path instead of losing its clock.
SIDECAR_VERSION = 2
_SIDECAR_VERSIONS = (1, 2)


def sidecar_key(vault: Path, rel_path) -> str:
    """The key an entry is stored under: the note's path from the vault root."""
    return vault_layout.vault_rel(rel_path, vault)


def fingerprint_of(path) -> str:
    """A note's body hash — what a moved file is recognised by.

    The body, not the whole file: frontmatter is rewritten by enrichment, the
    backfills and the operator, and a fingerprint that changed every time a tag
    was added would follow nothing. Empty string when the file cannot be read,
    which reads as "no fingerprint" everywhere it is used.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    body = raw
    if raw.startswith("---"):
        parts = raw.split("\n---", 1)
        if len(parts) == 2:
            body = parts[1]
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()[:16]


def _load_sidecar(vault: Path) -> dict:
    path = vault_layout.sidecar_path(vault, LIFECYCLE_SIDECAR_NAME)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("version") in _SIDECAR_VERSIONS:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": SIDECAR_VERSION, "entries": {}}


def _lookup(data: dict, vault: Path, slug: str, rel_path) -> dict:
    """One entry, by path, falling back to the slug a version-1 file keyed on.

    The fallback is what lets a vault whose sidecar predates the re-keying keep
    every clock it had until the migration runs — and it is deliberately only a
    *read*: nothing writes a slug key any more, so the old keys drain as notes
    are recalled rather than being carried forward.
    """
    entries = data.get("entries") or {}
    entry = entries.get(sidecar_key(vault, rel_path))
    if entry is not None:
        return entry
    if data.get("version") == 1 and slug:
        return entries.get(slug) or {}
    return {}


def _save_sidecar(vault: Path, data: dict) -> None:
    try:
        from vault_lock import atomic_write  # type: ignore
    except ImportError:
        path = vault_layout.sidecar_path(vault, LIFECYCLE_SIDECAR_NAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return
    path = vault_layout.sidecar_path(vault, LIFECYCLE_SIDECAR_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    """Record one genuine recall access — resets the decay clock.

    One note; `record_recall_accesses` is what the recall path calls, with
    every note one recall served. Kept because a single-note reset is what a
    command like `/memory revive` wants, and because it is the shape the
    call-site discipline was written about.

    No-op for decay-exempt entries (durable tiers ignore access entirely —
    FABLE R1's explicit bound). Best-effort: exceptions are swallowed, since
    lifecycle tracking must never block the recall pipeline.

    `today` is injectable for tests (ISO date string YYYY-MM-DD).
    """
    record_recall_accesses(vault, [(rel_path, fm)], today=today)


def record_recall_accesses(vault: Path, served, *, today: str | None = None) -> None:
    """Record a genuine recall access for every note one recall actually served.

    `served` is an iterable of `(rel_path, frontmatter)`.

    Batched because the sidecar is one file: recording five hits one at a time
    read and rewrote it five times, under the vault mutex each time, on a path
    with a 300ms budget.

    **Served, not merely found.** The design's words are "the clock resets on
    any hit served to any surface", and a hit the token budget dropped before
    the prompt was assembled was not served. The recall path used to reset the
    clock for every candidate it ranked, which quietly meant a note could be
    kept alive by never being shown to anyone.

    Only the recall path calls this — a lint walk, an index rebuild or a
    dreaming pass touching a file must never reach it.
    """
    pairs = [(rel, fm) for rel, fm in served if not is_decay_exempt(fm, rel)]
    if not pairs:
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
            for rel, _fm in pairs:
                key = sidecar_key(vault, rel)
                entry = dict(entries.get(key) or {})
                entry["last_access"] = today
                fp = fingerprint_of(Path(vault) / rel)
                if fp:
                    entry["fingerprint"] = fp
                entries[key] = entry
            data["version"] = SIDECAR_VERSION
            _save_sidecar(vault, data)
    except Exception:  # noqa: BLE001 — lifecycle tracking is best-effort
        pass


def rekey(vault: Path, old_rel, new_rel) -> bool:
    """Follow a note's clock to where the note went.

    Called by the night's reconcile step when it pairs a path that vanished
    with the file carrying its body — the one repair that keeps a hand move in
    Obsidian or Finder from silently resetting a memory's age to nothing.

    Returns whether an entry moved. Best-effort, like every other write here.
    """
    try:
        try:
            from vault_lock import vault_mutex  # type: ignore
            ctx = vault_mutex(vault)
        except ImportError:
            from contextlib import nullcontext
            ctx = nullcontext()
        with ctx:
            data = _load_sidecar(vault)
            entries = data.setdefault("entries", {})
            old_key = sidecar_key(vault, old_rel)
            new_key = sidecar_key(vault, new_rel)
            if old_key == new_key or old_key not in entries:
                return False
            entry = entries.pop(old_key)
            # The destination wins if it somehow already has a clock: a note
            # that is at the new path now is the note whose clock that is.
            entries.setdefault(new_key, entry)
            data["version"] = SIDECAR_VERSION
            _save_sidecar(vault, data)
            return True
    except Exception:  # noqa: BLE001
        return False


def _days_between(earlier_iso: str, later_iso: str) -> float:
    import datetime
    fmt = "%Y-%m-%d"
    a = datetime.datetime.strptime(earlier_iso[:10], fmt)
    b = datetime.datetime.strptime(later_iso[:10], fmt)
    return (b - a).total_seconds() / 86400.0


def days_since_last_genuine_access(
    vault: Path,
    slug: str,
    fm: dict[str, str],
    rel_path: str | Path,
    *,
    now: str | None = None,
) -> float | None:
    """Elapsed days since the same anchor `compute_decay_score` uses (last
    genuine recall access, falling back to `updated` then `created`) — the
    public seam a second consumer (dream.py's tidying stage, auto-
    organization part 1 task 3) uses to decide "how cold is this entry"
    without duplicating the anchor-resolution chain.

    Returns None for a decay-exempt entry (durable tiers have no
    meaningful "silence" — nothing should read a numeric age off one) or
    when there's no anchor at all / it's malformed (the same "no basis to
    compute decay" case `compute_decay_score` treats as fully fresh).
    """
    if is_decay_exempt(fm, rel_path):
        return None
    if now is None:
        import datetime
        now = datetime.date.today().isoformat()
    return _resolve_elapsed_days(vault, slug, fm, now, rel_path)


def _in_contract_exempt_space(rel_path: str | Path) -> bool:
    """Whether this note lives in a space the memory contract does not govern.

    Nothing there decays. Decay is a memory-lifecycle concept — it models a fact
    going cold because nobody has needed it — and these are documents. A 2016
    lesson is not less true for being ten years old, and ranking it as though it
    were would be applying a memory's physics to a thing that is not one.
    """
    try:
        exempt = storage_rules.rules().contract_exempt_spaces()
    except Exception:
        return False
    return storage_rules.in_space(str(rel_path), exempt)


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
    FABLE R1). Volatile entries decay on the contract's stepped curve from the
    last genuine recall access (sidecar `last_access`), falling back to
    frontmatter `updated` (then `created`, if `updated` is absent).

    The thirty-day exponential this used to run retired in the axis-per-space
    landing, with the shadow-mode stepped copy that sat beside it waiting for
    exactly this promotion. There is one curve now, `decay_bands()`, and the
    daemon reads the same lines from the same file.

    `updated`, not `created`, is the right fallback anchor: an entry that
    was substantively edited today is fresh regardless of how old its
    original creation date is — falling back to `created` would penalize a
    frequently-maintained reference doc for staleness it doesn't have,
    purely because it has never yet had a genuine recall access recorded
    (a cold-start bias task 6's own eval caught: it silently demoted an
    accurate, same-day-edited hit out of the top-5 entirely).

    `now` is injectable for tests (ISO date string YYYY-MM-DD).
    """
    if is_decay_exempt(fm, rel_path) or _in_contract_exempt_space(rel_path):
        return 1.0
    if now is None:
        import datetime
        now = datetime.date.today().isoformat()

    elapsed_days = _resolve_elapsed_days(vault, slug, fm, now, rel_path)
    if elapsed_days is None:
        # No anchor, or a malformed one: no basis to compute decay. Fresh
        # rather than old — a note the system knows nothing about should not be
        # buried for it.
        return 1.0
    return _score_from_bands(elapsed_days)


def _resolve_elapsed_days(vault: Path, slug: str, fm: dict[str, str], now: str,
                          rel_path=None) -> float | None:
    """The last_access / updated / created anchor chain, as elapsed days.

    The access record is read by the note's path, falling back to the slug a
    version-1 sidecar keyed on. Returns None when there is no anchor or it is
    malformed, which the curve treats as "no basis to compute decay".
    """
    data = _load_sidecar(vault)
    entry = _lookup(data, vault, slug, rel_path if rel_path is not None else slug)
    anchor = entry.get("last_access") or fm.get("updated") or fm.get("created")
    if not anchor:
        return None
    try:
        return max(0.0, _days_between(anchor, now))
    except ValueError:
        return None
