#!/usr/bin/env python3
"""inbox_triage.py — the long-promised `_inbox/` bulk-review pass
(`/memory inbox --bulk-review`).

Reflection (`reflect.py`) routes every MEDIUM/LOW-confidence candidate to
`personal/_inbox/<slug>.md` and stops there — nothing in this codebase has
ever moved an entry OUT of `status: inbox` again. A bulk-review command was
named as required follow-up as far back as the original MemoryVault design
docs (`wiki/designs/memoryvault/parts/reflection-and-recovery.md`,
`discovery-mining.md`) and repeated in this skill's own SKILL.md and a code
comment in `reflect.py` — this module is that follow-up.

It proposes exactly one of three dispositions per still-untriaged inbox
entry (operator ruling, 2026-07-11):

  - **promote** — an entry reinforced by repeated occurrence (a high
    `mining_occurrences` count) or a real content match across MULTIPLE
    inbox entries (a similarity cluster of 3+) graduates to the same
    canonical destination `reflect.py`'s own HIGH-confidence path already
    writes to (`_save_candidate_canonical` → `save.save_entry()`'s
    `<vault>/<group>/<kind>/<slug>.md` convention + frontmatter shape —
    reused here via `save._build_frontmatter`, not reinvented).
  - **merge** — a near-duplicate PAIR (similarity above threshold but not
    part of a larger reinforcing cluster) is proposed for merge, reusing
    `dream.py`'s own `_stage_dedup` difflib comparison AND its merge
    mutation shape verbatim against the inbox pool, instead of a second
    similarity implementation.
  - **expire** — stale, unreinforced, untouched past a TTL: proposed for
    in-place archival (frontmatter `status: expired`, never a physical
    delete or move — the same "retire it, never delete a source"
    convention `dream.py`'s own compression stage already uses).

Every proposal stages through the EXACT SAME `_dream-staging/<run_id>/`
contract dreaming already built (`dream_confirm.py`'s manifest schema is
reused byte-for-byte — `list_pending` / `confirm` / `expire_stale` /
`auto_apply_batch` / `render_auto_applied_json` all run UNMODIFIED against
an inbox-triage run; nothing in `dream_confirm.py` changes for this
module to exist).

**The confirm-gate rule, and its retirement (operator ruling, 2026-07-11,
two passes same day):** this module originally shipped with every
disposition proposed against the PRE-EXISTING BACKLOG — 1,565 notes that
existed before this mechanism's first-ever run — confirm-gated, full stop,
regardless of kind; only post-cutover **expire** proposals auto-applied
(reusing `dream_confirm.AUTO_APPLY_STAGES`/`auto_apply_batch()`'s pattern
of a stage set passed explicitly per call, never touching dream.py's own
`AUTO_APPLY_STAGES` default). That first pass ran exactly as designed: the
operator personally reviewed and confirmed all 635 staged proposals across
1,565 entries, zero errors. On the strength of that supervised pass, the
operator then directed that confirm-gating retire going forward — **every
disposition (promote/merge/expire) now auto-applies by default**,
regardless of whether the source entry predates or postdates the cutover
stamp. See the amendment log in
`wiki/designs/agentm-experience-and-dreaming.md` for the full history of
both rulings.

The **cutover marker** (`_meta/inbox-triage-cutover.json`, stamped once on
this mechanism's first-ever run and never overwritten again) and the
per-entry pre-existing/post-cutover classification
(`_is_pre_existing_backlog`, keyed off an entry's `created` frontmatter
timestamp — absent on the entire original backlog, since `reflect.py`'s
inbox writer only grew the field alongside this module) both still exist,
but are now purely INFORMATIONAL: they label which era a proposal's
source entry belongs to (surfaced in an expire proposal's stage name and
its digest summary line) without gating whether anything auto-applies.
See `ensure_cutover_marker` / `_is_pre_existing_backlog` below.

Public surface:

    run_inbox_triage(vault_path, *, run_id=None, now=None,
                      expire_ttl_days=DEFAULT_EXPIRE_TTL_DAYS,
                      promote_occurrence_threshold=DEFAULT_PROMOTE_OCCURRENCE_THRESHOLD)
        -> InboxTriageDigest
        Scans the inbox pool once, proposes dispositions, stages them.
        Never mutates a source file — proposal-only, exactly like
        `dream.run_dream()`.

    run_inbox_triage_and_auto_apply(vault_path, *, ..., revert_log=None,
                                     batch_cap=None, log_root=None,
                                     lock_root=None)
        -> (InboxTriageDigest, dream_confirm.AutoAppliedBatch)
        Runs the scan above, then auto-applies EVERY pending proposal —
        promote, merge, and expire alike, pre-existing-backlog or
        post-cutover alike (`ALL_AUTO_APPLY_STAGES`) — through
        `dream_confirm.auto_apply_batch`.

    review_inbox_triage(vault_path, run_id, revert_log, *, interactive=True,
                         stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
                         lock_root=None)
        -> dict
        Interactive confirm/reject/skip walk over a run's still-pending
        proposals — the CLI's review loop, factored out so tests can drive
        it without a subprocess (mirrors `watchlist_review.review_watchlist`'s
        shape).

    reject_proposal(vault_path, proposal, *, now=None) -> dict
        Marks every path a proposal touches `status: triage_rejected` — a
        direct frontmatter patch outside the revert-log (rejecting isn't
        one of the three dispositions, it's the operator declining to
        pursue one; matches `watchlist_review.py`'s own promote/dismiss/
        defer precedent of annotating directly rather than through a
        journal). This is also what stops a rejected entry from being
        re-proposed by a later fresh scan.

CLI: `python3 inbox_triage.py --vault-path <path> --bulk-review`
(`/memory inbox --bulk-review` — see SKILL.md).

Stdlib-only except for this skill's own sibling modules (`dream.py`,
`dream_confirm.py`, `revert_log.py`, `save.py`, `vault_lock.py`). See
`wiki/designs/agentm-experience-and-dreaming.md`'s amendment log.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import dream  # noqa: E402  (reuse _stage_dedup's difflib comparison + merge shape)
import dream_confirm  # noqa: E402  (reuse list_pending/confirm/auto_apply_batch/render_auto_applied_json)
import save as save_module  # noqa: E402  (reuse _build_frontmatter -- the canonical-destination convention)
from revert_log import RevertLog  # noqa: E402
from vault_lock import atomic_write  # noqa: E402

__all__ = [
    "run_inbox_triage",
    "run_inbox_triage_and_auto_apply",
    "review_inbox_triage",
    "reject_proposal",
    "ensure_cutover_marker",
    "Proposal",
    "InboxTriageDigest",
    "PROMOTE_STAGE",
    "MERGE_STAGE",
    "AUTO_APPLY_ELIGIBLE_STAGE",
    "BACKLOG_EXPIRE_STAGE",
    "ALL_AUTO_APPLY_STAGES",
    "main",
]

# ── calibration-era constants (same "ship simple, tune from real use"
# posture dream.py's own DEDUP_SIMILARITY_THRESHOLD/COMPRESSION_CHAIN_MIN_
# LENGTH document -- expected to move once real bulk-review cycles exist) ──

# How long an inbox entry can sit untouched, since its own `created`
# timestamp, before it's proposed for archival. NOT the same TTL as
# `dream_confirm.DEFAULT_TTL_DAYS` (that one governs how long a STAGED
# PROPOSAL can sit unconfirmed before it goes stale) -- this is a
# completely separate lifecycle stage, how old the underlying entry itself
# must be before we even propose archiving it.
DEFAULT_EXPIRE_TTL_DAYS = 90.0

# A single inbox entry's own `mining_occurrences` count at or above this
# is "reinforced by repeated occurrence" on its own, no cluster needed.
# Mirrors dream.py's COMPRESSION_CHAIN_MIN_LENGTH (3) -- the same "three
# is the reinforcement floor" judgment call, reused for consistency rather
# than picked fresh.
DEFAULT_PROMOTE_OCCURRENCE_THRESHOLD = 3

# Stage-name vocabulary. `dream_confirm.auto_apply_batch()` filters purely
# on `proposal.stage in stages` -- passing a stage set explicitly (never
# touching the shared `dream_confirm.AUTO_APPLY_STAGES` frozenset, which
# stays `{"compression"}` for dream.py's own general-corpus runs) keeps
# this module's own auto-apply eligibility fully independent of dream.py's.
#
# All four stage names below auto-apply by default now (operator ruling,
# 2026-07-11, second pass -- the confirm-gate this vocabulary originally
# encoded is retired; see the module docstring's amendment note).
# `AUTO_APPLY_ELIGIBLE_STAGE` / `BACKLOG_EXPIRE_STAGE` keep their distinct
# names purely as INFORMATIONAL era metadata on an expire proposal (which
# side of the cutover stamp its source entry falls on) -- the distinction
# is no longer behavioral; both are in `ALL_AUTO_APPLY_STAGES` below.
PROMOTE_STAGE = "inbox_promote"
MERGE_STAGE = "inbox_merge"
COLLAPSE_STAGE = "inbox_collapse"                # fingerprint-exact family collapse (auto-org part 3 task 3)
AUTO_APPLY_ELIGIBLE_STAGE = "inbox_expire"       # post-cutover expire (era label only)
BACKLOG_EXPIRE_STAGE = "inbox_expire_backlog"    # pre-existing-backlog expire (era label only)

# Every stage this module ever proposes auto-applies by default now --
# promote/merge/expire alike, regardless of the source entry's era. This
# is the stage set `run_inbox_triage_and_auto_apply` passes to
# `dream_confirm.auto_apply_batch(stages=...)`. `inbox_collapse` joined
# (auto-org part 3 task 3) on the plan's own Locked design call
# ("Fingerprint-exact collapses are deterministic") + the design's fresh
# ruling extending auto-apply to dedup under these bounds: a collapse only
# ever MARKS copies superseded (never deletes), every mutation rides
# revert_log.record_and_apply, and membership in an exact family is a
# deterministic content-hash fact, not a judgment call. Fuzzy merges are
# the judgment-call case — and those never reach a proposal without a
# model verdict (see _build_merge_and_promote_clusters).
ALL_AUTO_APPLY_STAGES = frozenset({PROMOTE_STAGE, MERGE_STAGE, COLLAPSE_STAGE, AUTO_APPLY_ELIGIBLE_STAGE, BACKLOG_EXPIRE_STAGE})

_META_DIR_NAME = "_meta"
_CUTOVER_MARKER_NAME = "inbox-triage-cutover.json"
_AUTO_EXPIRED_LATEST_NAME = "inbox-triage-auto-expired-latest.json"
# The needs-your-eye list (auto-org part 3 tasks 3+5): ambiguous dedup/merge
# candidates neither exact-matched nor confidently fuzzy-verdicted. One
# underlying JSON list; the console / digest / morning-brief surfaces all
# read this file (task 5). Overwritten every run — a candidate the operator
# resolves simply stops reappearing on the next cycle's recompute.
_NEEDS_YOUR_EYE_NAME = "needs-your-eye.json"


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------

@dataclass
class Proposal:
    """One proposed, NOT-YET-APPLIED disposition -- same shape as
    `dream.Proposal` (stage/kind/paths/summary/mutations) so it serializes
    into the exact manifest schema `dream_confirm.py` already reads; a
    separate dataclass rather than reusing `dream.Proposal`'s identity
    directly, since our own stage vocabulary (`inbox_promote` / `inbox_merge`
    / `inbox_expire` / `inbox_expire_backlog`) differs from dream.py's."""

    stage: str
    kind: str  # "promote" | "merge" | "archive"
    paths: list
    summary: str
    mutations: list = field(default_factory=list)


@dataclass
class InboxTriageDigest:
    run_id: str
    cutover_at: str
    corpus_stats: dict
    proposals: list
    digest_path: Optional[Path] = None
    # Ambiguous dedup/merge candidates (auto-org part 3 task 3): fuzzy-
    # similar pairs whose required model verdict was unavailable or unsure.
    # Never proposals — they stay `status: inbox` untouched, exempt from
    # this run's occurrence-promote and expire passes, and surface via the
    # digest + `_meta/needs-your-eye.json`. Items: {"paths": [...],
    # "reason": str}.
    needs_your_eye: list = field(default_factory=list)


# -----------------------------------------------------------------------------
# Cutover marker -- the backlog/new boundary
# -----------------------------------------------------------------------------

def _cutover_marker_path(vault_path: Path) -> Path:
    return Path(vault_path) / _META_DIR_NAME / _CUTOVER_MARKER_NAME


def ensure_cutover_marker(vault_path: Path, *, now: float | None = None) -> str:
    """Return the cutover timestamp (ISO-8601 UTC, second precision),
    stamping it on this mechanism's first-ever call against `vault_path`
    (the marker file doesn't exist yet) and simply reading it back on
    every later call. Once written, NEVER overwritten -- this is the
    permanent, unambiguous boundary between "existing backlog" (confirm-
    gated forever, regardless of disposition) and "created after this
    mechanism first ran" (eligible for the same auto-apply-on-expire
    treatment `dream_confirm.AUTO_APPLY_STAGES` already grants
    `compression`). `now` is injectable so tests never touch real
    wall-clock time."""
    path = _cutover_marker_path(vault_path)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["cutover_at"]
    now = now if now is not None else time.time()
    cutover_at = datetime.fromtimestamp(now, tz=timezone.utc).replace(microsecond=0).isoformat()
    atomic_write(path, json.dumps({"cutover_at": cutover_at}, indent=2))
    return cutover_at


def _is_pre_existing_backlog(fm: dict, cutover_at: str) -> bool:
    """True if this entry predates (or is exactly at) the cutover stamp --
    including every entry with NO `created` field at all, which today
    means every one of the pre-existing 1,565-note backlog: `reflect.py`
    never wrote the field before this module shipped, so its absence
    alone is conclusive (only an entry written by the amended
    `_save_candidate_to_inbox` can ever carry `created`, and that amendment
    landed in the same change as this module). ISO-8601 UTC timestamps in
    the fixed `_utcnow_iso()` shape sort lexicographically, so a plain
    string comparison is exact -- no datetime parsing, no exception path.

    INFORMATIONAL ONLY as of 2026-07-11's second ruling -- this used to
    gate auto-apply eligibility (pre-existing backlog stayed confirm-gated
    forever); it no longer does. Its sole remaining caller,
    `_build_expire_proposal`, uses the return value purely to pick an
    expire proposal's era-label stage name (`AUTO_APPLY_ELIGIBLE_STAGE` vs
    `BACKLOG_EXPIRE_STAGE`) and summary text -- both stages are in
    `ALL_AUTO_APPLY_STAGES` and auto-apply identically."""
    created = fm.get("created")
    if not created:
        return True
    return created <= cutover_at


def _is_past_ttl(fm: dict, *, now_dt: datetime, ttl_days: float) -> bool:
    """True if this entry's own age (since its `created` timestamp)
    exceeds `ttl_days`. Entries with no `created` field (the pre-existing
    backlog) are always treated as past-TTL -- they have no recorded
    creation time, but by construction (see `_is_pre_existing_backlog`)
    they all predate this module, so they are unambiguously old. A
    `created` value that fails to parse is treated conservatively as
    NOT past TTL (no expire proposal is safer than guessing at malformed
    data)."""
    created = fm.get("created")
    if not created:
        return True
    try:
        created_dt = datetime.fromisoformat(created)
    except ValueError:
        return False
    if created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)
    age_days = (now_dt - created_dt).total_seconds() / 86400.0
    return age_days > ttl_days


def _utcnow_iso(now: float | None = None) -> str:
    now = now if now is not None else time.time()
    return datetime.fromtimestamp(now, tz=timezone.utc).replace(microsecond=0).isoformat()


def _rel(path: Path, vault_path: Path) -> str:
    """Vault-relative path as a forward-slash string, regardless of host
    OS -- `Path.relative_to()` alone renders backslash-separated on
    Windows, which would leak into frontmatter values (`derived_from`,
    `promoted_to`) and digest prose as a platform-dependent string. Mirrors
    `save.py`'s own `str(target.relative_to(vault)).replace(os.sep, "/")`
    convention for its vec-index enqueue path."""
    return str(Path(path).relative_to(vault_path)).replace(os.sep, "/")


# -----------------------------------------------------------------------------
# Minimal frontmatter helpers -- same per-module idiom `dream.py` documents
# ("not centralized anywhere in this codebase today, so this module follows
# the same pattern rather than introducing a new shared dependency").
# -----------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> tuple:
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content
    fm_text = content[4:end]
    body = content[end + 5:]
    fm: dict = {}
    for line in fm_text.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        fm[key] = value
    return fm, body


def _patch_frontmatter(content: str, updates: dict) -> str:
    if not content.startswith("---\n"):
        lines = ["---"] + [f"{k}: {v}" for k, v in updates.items()] + ["---"]
        return "\n".join(lines) + "\n" + content

    end = content.find("\n---\n", 4)
    if end == -1:
        return content

    fm_text = content[4:end]
    body = content[end + 5:]
    lines = fm_text.split("\n")
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        if ":" in line:
            key = line.partition(":")[0].strip()
            if key in remaining:
                new_lines.append(f"{key}: {remaining.pop(key)}")
                continue
        new_lines.append(line)
    for k, v in remaining.items():
        new_lines.append(f"{k}: {v}")
    return "---\n" + "\n".join(new_lines) + "\n---\n" + body


# -----------------------------------------------------------------------------
# Inbox pool reading
# -----------------------------------------------------------------------------

def _inbox_dir(vault_path: Path) -> Path:
    return Path(vault_path) / "personal" / "_inbox"


def _iter_inbox_files(vault_path: Path) -> list:
    inbox_dir = _inbox_dir(vault_path)
    if not inbox_dir.exists():
        return []
    return sorted(p for p in inbox_dir.glob("*.md") if p.is_file())


def _load(paths: list) -> dict:
    """path -> (frontmatter dict, body str, raw content str)."""
    loaded = {}
    for p in paths:
        raw = p.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(raw)
        loaded[p] = (fm, body, raw)
    return loaded


def _still_untriaged(vault_path: Path):
    """Every `_inbox/*.md` file still carrying `status: inbox` -- entries a
    PRIOR run's confirm (promoted/superseded/expired) or a reject
    (triage_rejected) already resolved are never re-proposed by a fresh
    scan. Returns (paths, loaded)."""
    all_paths = _iter_inbox_files(vault_path)
    all_loaded = _load(all_paths)
    paths = [p for p in all_paths if all_loaded[p][0].get("status", "inbox") == "inbox"]
    loaded = {p: all_loaded[p] for p in paths}
    return paths, loaded


# -----------------------------------------------------------------------------
# Promote
# -----------------------------------------------------------------------------

def _build_promote_proposal(vault_path: Path, cluster_paths: list, loaded: dict, *, reason: str, now: float | None = None):
    """`cluster_paths` is one or more inbox entries that earned a promote
    disposition together -- a single high-occurrence entry, or a 3+
    similarity cluster `_build_merge_proposals` identified. The first path
    is the representative ("hub") whose body becomes the canonical entry's
    body; every path in the cluster (hub included) gets patched
    `status: promoted`.

    Reuses `save._build_frontmatter` -- the EXACT frontmatter convention
    `save.save_entry()` (and `reflect.py`'s own `_save_candidate_canonical`
    HIGH-confidence path) already writes canonical entries with -- rather
    than hand-rolling a second frontmatter shape. Does NOT call
    `save.save_entry()` itself: that performs a real, immediate write, and
    a proposal must never mutate anything until confirmed (dream.py's own
    propose/confirm split). The canonical write happens later, through
    `revert_log.record_and_apply`, exactly like every other staged
    mutation in this pipeline.

    Returns `None` (skip -- no proposal this run) if the canonical
    destination already exists, mirroring `save_entry()`'s own
    `FileExistsError` collision guard: a hand-built mutation bypasses that
    guard (it writes via `atomic_write`, not `save_entry()`), so the
    collision check has to happen here instead."""
    hub = cluster_paths[0]
    fm, body, _raw = loaded[hub]
    kind = fm.get("kind") or "idea"
    slug = fm.get("slug") or hub.stem
    canonical_path = Path(vault_path) / "personal" / kind / f"{slug}.md"
    if canonical_path.exists():
        return None

    # Strip the "## Mining metadata" (+ "## Supporting excerpts") block
    # `reflect.py`'s `_save_candidate_to_inbox` appends -- the promoted
    # canonical entry gets the same bare body `_save_candidate_canonical`
    # would have written for a HIGH-confidence candidate, not the triage
    # instrumentation.
    clean_body = body.split("\n## Mining metadata", 1)[0].rstrip("\n") + "\n"

    derived_from = sorted(_rel(p, vault_path) for p in cluster_paths)
    fm_block = save_module._build_frontmatter(
        kind=kind, group="personal", slug=slug, tags=[], always_load=False,
        supersedes=None, derived_from=derived_from,
    )
    canonical_content = fm_block + "\n" + clean_body

    now_iso = _utcnow_iso(now)
    mutations = [(canonical_path, canonical_content)]
    for p in cluster_paths:
        _p_fm, _p_body, p_raw = loaded[p]
        patched = _patch_frontmatter(p_raw, {
            "status": "promoted",
            "promoted_to": _rel(canonical_path, vault_path),
            "promoted_at": now_iso,
        })
        mutations.append((p, patched))

    plural = "y" if len(cluster_paths) == 1 else "ies"
    summary = (
        f"{len(cluster_paths)} inbox entr{plural} ({reason}) -- propose promote to "
        f"{_rel(canonical_path, vault_path)}"
    )
    return Proposal(
        stage=PROMOTE_STAGE, kind="promote",
        paths=[str(p) for p in cluster_paths], summary=summary, mutations=mutations,
    )


# -----------------------------------------------------------------------------
# Merge -- reuses dream.py's own dedup stage verbatim
# -----------------------------------------------------------------------------

def judge_fuzzy_merge(hub_body: str, other_body: str) -> str:
    """The cheap-model yes/no/unsure verdict for a FUZZY (similar-not-
    identical) merge candidate — "yes" (merge), "no" (genuinely distinct,
    keep both), or "unsure" (needs-your-eye).

    Only ever invoked when `dream.cheap_model_tier_available()` returns
    True — which it never does today (see that seam's docstring: no
    synchronous budgeted model-call primitive exists in this codebase), so
    this function is unreachable in production until the tier ships. Tests
    patch both the seam and this judge. Raises rather than guessing: a
    fuzzy merge without a real verdict must fail closed to needs-your-eye
    (the plan's Locked design call — "no exception either direction"),
    never fall through to a silent heuristic.
    """
    raise RuntimeError(
        "judge_fuzzy_merge called with no cheap-model primitive available — "
        "fuzzy-merge verdicts are unreachable until the tier ships"
    )


@dataclass
class ClusterScan:
    """`_build_merge_and_promote_clusters`' result (auto-org part 3 task 3
    widened the old 2-tuple): deterministic exact-family collapses, verdict-
    approved fuzzy merges, promote clusters, and the ambiguous leftovers."""

    collapse_proposals: list = field(default_factory=list)
    merge_proposals: list = field(default_factory=list)
    promote_clusters: list = field(default_factory=list)
    needs_your_eye: list = field(default_factory=list)  # {"paths": [...], "reason": str}


def _build_merge_and_promote_clusters(entries: list, loaded: dict) -> ClusterScan:
    """Two passes over the inbox pool (auto-org part 3 task 3).

    **Pass 1 — fingerprint-exact families (deterministic).** Bucket the
    pool by `dedup_guard.live_content_fingerprint` (the same hash the
    write-time guard matches on, recomputed from each file's current
    body). A bucket of two or more is a suffix family: content-identical
    modulo formatting, however many `_1`/`_2` copies deep. The whole
    family collapses into the canonical EARLIEST note in one disposition
    (`inbox_collapse`): every copy is marked `status: superseded` +
    `supersedes: <canonical>` — marked, never deleted, so part 1's tidying
    lanes pick the copies up on later cycles — and the surviving note's
    content is left exactly as it is (the copies are the same content;
    there is nothing to absorb). Deterministic by the plan's Locked design
    call, so it auto-applies. Family members are claimed here and never
    reach pass 2.

    **Pass 2 — difflib near-duplicates over the remainder.** Same
    `dream._stage_dedup` comparison as always (threshold
    `dream.DEDUP_SIMILARITY_THRESHOLD`, disjoint hub-grouping per its own
    `matched` set). A hub matched by 2+ others stays a PROMOTE cluster
    (the operator's "same insight captured three-plus times" signal —
    promotion, not deduplication, untouched by this task). A pairwise
    near-duplicate is now a FUZZY MERGE candidate: by the Locked design
    call it requires a cheap-model verdict before applying — "yes" emits
    the merge proposal (dream's own merge-into-earlier / mark-later-
    superseded mutations, verbatim), "no" keeps both notes in the normal
    flow, "unsure" OR an unavailable tier routes the pair to the
    needs-your-eye list (left `status: inbox`, untouched, never forced).
    Before this task, pairwise difflib merges auto-applied unverdicted —
    that path is deliberately gone.
    """
    import dedup_guard  # same skill dir (lazy: mirrors dream's own import style)

    scan = ClusterScan()

    # Pass 1 — exact families.
    by_fingerprint: dict = defaultdict(list)
    for path in entries:
        fp = dedup_guard.live_content_fingerprint(path)
        if fp is not None:
            by_fingerprint[fp].append(path)

    exact_family_members: set = set()
    for fp, family in by_fingerprint.items():
        if len(family) < 2:
            continue
        canonical = _earliest_note(family, loaded)
        copies = [p for p in family if p != canonical]
        mutations = [
            (copy, _patch_frontmatter(loaded[copy][2], {
                "status": "superseded",
                "supersedes": str(canonical),
            }))
            for copy in copies
        ]
        scan.collapse_proposals.append(Proposal(
            stage=COLLAPSE_STAGE, kind="collapse",
            paths=[str(canonical)] + [str(c) for c in copies],
            summary=(
                f"{canonical.name} + {len(copies)} content-identical cop"
                f"{'y' if len(copies) == 1 else 'ies'} (fingerprint {fp[:12]}…) — "
                f"collapse into the earliest, mark the rest superseded"
            ),
            mutations=mutations,
        ))
        exact_family_members.update(family)

    # Pass 2 — difflib near-duplicates over what's left.
    remaining = [p for p in entries if p not in exact_family_members]
    dedup_proposals = dream._stage_dedup(remaining, loaded)
    by_hub: dict = defaultdict(list)
    for p in dedup_proposals:
        by_hub[Path(p.paths[0])].append(p)

    for hub, group in by_hub.items():
        if len(group) >= 2:
            cluster = [hub] + [Path(p.paths[1]) for p in group]
            scan.promote_clusters.append(cluster)
            continue
        p = group[0]
        pair_paths = [str(x) for x in p.paths]
        if not dream.cheap_model_tier_available():
            scan.needs_your_eye.append({
                "paths": pair_paths,
                "reason": (
                    "fuzzy near-duplicate (similar, not fingerprint-identical) — "
                    "a merge needs a cheap-model verdict and the tier is unavailable"
                ),
            })
            continue
        verdict = judge_fuzzy_merge(loaded[Path(p.paths[0])][1], loaded[Path(p.paths[1])][1])
        if verdict == "yes":
            scan.merge_proposals.append(Proposal(
                stage=MERGE_STAGE, kind="merge",
                paths=list(p.paths), summary=p.summary, mutations=list(p.mutations),
            ))
        elif verdict == "unsure":
            scan.needs_your_eye.append({
                "paths": pair_paths,
                "reason": "fuzzy near-duplicate — the cheap-model judge was unsure",
            })
        # "no": genuinely distinct — both notes stay in the normal flow.

    return scan


def _earliest_note(family: list, loaded: dict) -> Path:
    """The canonical survivor of an exact family: earliest by `created`
    frontmatter; sorted-path order breaks ties and covers the legacy
    backlog shape with no `created` at all (matching `_iter_inbox_files`'
    own ordering)."""
    def sort_key(p: Path):
        created = (loaded[p][0].get("created") or "").strip()
        return (0 if created else 1, created, str(p))
    return sorted(family, key=sort_key)[0]


# -----------------------------------------------------------------------------
# Expire
# -----------------------------------------------------------------------------

def _build_expire_proposal(vault_path: Path, path: Path, fm: dict, raw: str, *,
                            cutover_at: str, now_dt: datetime, now: float | None, ttl_days: float):
    if not _is_past_ttl(fm, now_dt=now_dt, ttl_days=ttl_days):
        return None
    is_backlog = _is_pre_existing_backlog(fm, cutover_at)
    stage = BACKLOG_EXPIRE_STAGE if is_backlog else AUTO_APPLY_ELIGIBLE_STAGE
    patched = _patch_frontmatter(raw, {"status": "expired", "expired_at": _utcnow_iso(now)})
    rel = _rel(path, vault_path)
    era = "pre-existing backlog" if is_backlog else "created after the triage cutover"
    summary = f"{rel} untouched past {ttl_days:.0f}d TTL ({era}) -- propose archival"
    return Proposal(stage=stage, kind="archive", paths=[str(path)], summary=summary, mutations=[(path, patched)])


# -----------------------------------------------------------------------------
# The scan
# -----------------------------------------------------------------------------

def run_inbox_triage(
    vault_path: Path, *, run_id: str | None = None, now: float | None = None,
    expire_ttl_days: float = DEFAULT_EXPIRE_TTL_DAYS,
    promote_occurrence_threshold: int = DEFAULT_PROMOTE_OCCURRENCE_THRESHOLD,
) -> InboxTriageDigest:
    """One pass over the still-untriaged `_inbox/` pool. Never mutates a
    source file -- every disposition below is PROPOSE-only, staged to
    `_dream-staging/<run_id>/`, exactly like `dream.run_dream()`. Each
    entry gets at most one proposal per run (a promote/merge cluster claims
    every path it touches before the occurrence-based promote pass or the
    expire pass considers them)."""
    vault_path = Path(vault_path)
    now = now if now is not None else time.time()
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    run_id = run_id or f"inbox-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now))}-{uuid.uuid4().hex[:8]}"

    cutover_at = ensure_cutover_marker(vault_path, now=now)

    entries, loaded = _still_untriaged(vault_path)
    claimed: set = set()
    proposals: list = []

    scan = _build_merge_and_promote_clusters(entries, loaded)

    # Exact-family collapses first (deterministic — auto-org part 3 task 3):
    # each claims its whole family.
    for prop in scan.collapse_proposals:
        proposals.append(prop)
        claimed.update(Path(p) for p in prop.paths)

    for cluster in scan.promote_clusters:
        prop = _build_promote_proposal(vault_path, cluster, loaded, reason="repeated near-duplicate capture", now=now)
        if prop is not None:
            proposals.append(prop)
            claimed.update(Path(p) for p in prop.paths)

    for prop in scan.merge_proposals:
        paths = [Path(p) for p in prop.paths]
        if claimed.intersection(paths):
            # Provably unreachable given _build_merge_and_promote_clusters'
            # own partitioning (see its docstring) -- kept as a cheap,
            # defensive guard against a future refactor breaking that
            # invariant silently.
            continue
        proposals.append(prop)
        claimed.update(paths)

    # Needs-your-eye pairs stay `status: inbox`, untouched — but their
    # paths are claimed so this same run's occurrence-promote and expire
    # passes can't touch them either (an ambiguous pair expiring in the
    # very cycle that flagged it would violate "left in the inbox
    # untouched and flagged"). They wait for the operator; the list is
    # recomputed every run, so a resolved pair just stops reappearing.
    for item in scan.needs_your_eye:
        claimed.update(Path(p) for p in item["paths"])

    # Occurrence-based promote: single entries reinforced on their own,
    # not part of any similarity cluster.
    for path in entries:
        if path in claimed:
            continue
        fm, _body, _raw = loaded[path]
        # Two occurrence counters exist: `mining_occurrences` (reflect's
        # miner) and `occurrences` (the write-time dedup guard's reinforce
        # bump, auto-org part 3 task 2). Both mean "this content recurred";
        # the promote threshold honors whichever is higher rather than
        # privileging one writer's counter (part 3 task 3 unification).
        def _int_field(name: str) -> int:
            try:
                return int(fm.get(name, "0") or 0)
            except ValueError:
                return 0
        occurrences = max(_int_field("mining_occurrences"), _int_field("occurrences"))
        if occurrences >= promote_occurrence_threshold:
            prop = _build_promote_proposal(
                vault_path, [path], loaded,
                reason=f"{occurrences} mining occurrences", now=now,
            )
            if prop is not None:
                proposals.append(prop)
                claimed.add(path)

    # Expire: stale, unreinforced (everything left over -- by construction,
    # nothing that reached here earned a promote or merge disposition).
    for path in entries:
        if path in claimed:
            continue
        fm, _body, raw = loaded[path]
        prop = _build_expire_proposal(
            vault_path, path, fm, raw,
            cutover_at=cutover_at, now_dt=now_dt, now=now, ttl_days=expire_ttl_days,
        )
        if prop is not None:
            proposals.append(prop)
            claimed.add(path)

    corpus_stats = {
        "entry_count": len(entries),
        "total_bytes": sum(p.stat().st_size for p in entries),
    }
    digest = InboxTriageDigest(
        run_id=run_id, cutover_at=cutover_at, corpus_stats=corpus_stats,
        proposals=proposals, needs_your_eye=scan.needs_your_eye,
    )
    digest.digest_path = _stage_digest_and_staging(vault_path, digest, staged_at=now)
    _write_needs_your_eye(vault_path, scan.needs_your_eye, run_id=run_id, now=now)
    return digest


def _needs_your_eye_path(vault_path: Path) -> Path:
    return Path(vault_path) / _META_DIR_NAME / _NEEDS_YOUR_EYE_NAME


def _write_needs_your_eye(vault_path: Path, items: list, *, run_id: str, now: float | None) -> None:
    """Overwrite `_meta/needs-your-eye.json` with this run's ambiguous
    candidates — the ONE underlying list the console / digest / morning-
    brief surfaces read (task 5). Written every run, including an empty
    one, so the surfaces never show a stale flag after the operator
    resolves a pair."""
    payload = {
        "run_id": run_id,
        "detected_at": _utcnow_iso(now),
        "items": items,
    }
    path = _needs_your_eye_path(vault_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, indent=2))


# -----------------------------------------------------------------------------
# Digest + staging (mirrors dream.py's own render/stage shape)
# -----------------------------------------------------------------------------

def _staging_dir(vault_path: Path, run_id: str) -> Path:
    return Path(vault_path) / "_dream-staging" / run_id


def _render_digest(digest: InboxTriageDigest, *, auto_applied=None) -> str:
    lines = [
        f"# Inbox triage digest — run {digest.run_id}",
        "",
        f"Cutover: {digest.cutover_at} (entries with no `created` field, or with "
        "`created` at or before this stamp, are the pre-existing backlog -- an "
        "informational era label only; every disposition, promote/merge/expire "
        "alike, auto-applies regardless of era as of 2026-07-11).",
        f"Inbox pool scanned: {digest.corpus_stats['entry_count']} still-untriaged "
        f"entries, {digest.corpus_stats['total_bytes']} bytes.",
        "",
    ]

    if digest.needs_your_eye:
        lines.append("## Needs your eye (ambiguous — nothing applied, nothing forced)")
        lines.append("")
        lines.append(
            "These pairs are similar but not content-identical, and a fuzzy "
            "merge never applies without a confident verdict. They stay in "
            "the inbox untouched (exempt from this run's promote/expire "
            "passes) until you resolve them — merge by hand, edit one apart, "
            "or leave them; a resolved pair drops off this list on the next "
            "cycle."
        )
        lines.append("")
        for item in digest.needs_your_eye:
            names = ", ".join(Path(p).name for p in item["paths"])
            lines.append(f"- {names} — {item['reason']}")
        lines.append("")

    auto_applied_by_index = {}
    if auto_applied is not None:
        auto_applied_by_index = {item["index"]: item for item in auto_applied.items}
        lines.append("## Auto-applied this run (no confirm required)")
        lines.append("")
        if not auto_applied.items:
            lines.append(
                f"None this run (stages watched: {', '.join(sorted(auto_applied.stages)) or 'none'}; "
                f"batch cap {auto_applied.batch_cap})."
            )
        else:
            lines.append(f"{len(auto_applied.items)} proposal(s) auto-applied:")
            lines.append("")
            for item in auto_applied.items:
                lines.append(f"- #{item['index']} {item['stage']}/{item['kind']}: {item['summary']}")
                lines.append(
                    f"  revert: `RevertLog(vault_path).revert({digest.run_id!r}, {item['entry_id']!r})` "
                    f"— entry `{item['entry_id']}`"
                )
            lines.append("")
        lines.append("")

    if not digest.proposals:
        lines.append("## Proposals")
        lines.append("")
        lines.append("None this run.")
        return "\n".join(lines) + "\n"

    lines.append("## Proposals")
    lines.append("")
    for i, p in enumerate(digest.proposals, start=1):
        lines.append(f"### {i}. {p.stage} — {p.kind}")
        lines.append(f"- paths: {', '.join(p.paths)}")
        lines.append(f"- {p.summary}")
        if i in auto_applied_by_index:
            item = auto_applied_by_index[i]
            lines.append(
                f"- **AUTO-APPLIED** (no confirm required): entry `{item['entry_id']}`; "
                f"undo via `RevertLog.revert({digest.run_id!r}, {item['entry_id']!r})`"
            )
        else:
            lines.append(
                "- staged — not yet applied this cycle (scan-only run, or this "
                "cycle's batch cap was reached); it will auto-apply on a later "
                "run, or confirm it now via "
                f"(`dream_confirm.confirm(vault_path, {digest.run_id!r}, {i}, revert_log)`, or "
                "`/memory inbox --bulk-review --run-id ... --confirm " + str(i) + "`)"
            )
            lines.append(f"- proposal file: `{i:02d}-{p.stage}-{p.kind}.proposal.md`")
        lines.append("")
    return "\n".join(lines) + "\n"


def _render_proposal_file(index: int, p: Proposal) -> str:
    lines = [f"# Proposal {index}: {p.stage} / {p.kind}", "", p.summary, ""]
    for path, new_content in p.mutations:
        lines.append(f"## {path}")
        lines.append("```")
        lines.append("<deleted>" if new_content is None else new_content)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _render_manifest(digest: InboxTriageDigest, staged_at: float) -> str:
    """The machine-readable sibling of digest.md -- `dream_confirm.py`'s
    `_load_manifest` reads this UNMODIFIED (same schema dream.py's own
    `_render_manifest` produces: run_id/staged_at/proposals[index/stage/
    kind/paths/summary/mutations])."""
    return json.dumps(
        {
            "run_id": digest.run_id,
            "staged_at": staged_at,
            "cutover_at": digest.cutover_at,
            "proposals": [
                {
                    "index": i,
                    "stage": p.stage,
                    "kind": p.kind,
                    "paths": p.paths,
                    "summary": p.summary,
                    "mutations": [[str(path), content] for path, content in p.mutations],
                }
                for i, p in enumerate(digest.proposals, start=1)
            ],
        },
        indent=2,
    )


def _stage_digest_and_staging(vault_path: Path, digest: InboxTriageDigest, *, staged_at: float | None = None) -> Path:
    staged_at = staged_at if staged_at is not None else time.time()
    staging_dir = _staging_dir(vault_path, digest.run_id)
    digest_path = staging_dir / "digest.md"
    atomic_write(digest_path, _render_digest(digest))
    atomic_write(staging_dir / "proposals.json", _render_manifest(digest, staged_at))
    for i, p in enumerate(digest.proposals, start=1):
        proposal_path = staging_dir / f"{i:02d}-{p.stage}-{p.kind}.proposal.md"
        atomic_write(proposal_path, _render_proposal_file(i, p))
    return digest_path


# -----------------------------------------------------------------------------
# Auto-apply -- every disposition (promote/merge/expire), through
# dream_confirm's own auto_apply_batch (no change to dream_confirm.py or
# its AUTO_APPLY_STAGES default -- our own stage set is passed explicitly
# per call).
# -----------------------------------------------------------------------------

def run_inbox_triage_and_auto_apply(
    vault_path: Path, *, run_id: str | None = None, now: float | None = None,
    expire_ttl_days: float = DEFAULT_EXPIRE_TTL_DAYS,
    promote_occurrence_threshold: int = DEFAULT_PROMOTE_OCCURRENCE_THRESHOLD,
    revert_log=None, batch_cap: int | None = None,
    log_root: Path | str | None = None, lock_root: Path | str | None = None,
):
    """Runs `run_inbox_triage()` (unchanged), then auto-applies EVERY
    pending proposal -- promote, merge, and expire alike, pre-existing-
    backlog or post-cutover alike (`ALL_AUTO_APPLY_STAGES`) -- through
    `dream_confirm.auto_apply_batch`. The confirm-gate this function
    originally enforced (only post-cutover expire auto-applied; everything
    else stayed pending for an explicit confirm) retired 2026-07-11,
    second pass, once the operator personally reviewed and confirmed the
    entire 635-proposal/1,565-entry first-run backlog with zero errors. A
    proposal can still be left pending after this call -- not because of
    its disposition or the source entry's era, but only if the run's
    `batch_cap` is smaller than the number of eligible proposals; the
    remainder stays pending for the next cycle or an explicit manual
    `confirm()`.

    Returns `(digest, auto_applied_batch)`."""
    vault_path = Path(vault_path)
    digest = run_inbox_triage(
        vault_path, run_id=run_id, now=now,
        expire_ttl_days=expire_ttl_days,
        promote_occurrence_threshold=promote_occurrence_threshold,
    )

    if revert_log is None:
        revert_log = RevertLog(vault_path, log_root=log_root, lock_root=lock_root)
    cap = batch_cap if batch_cap is not None else dream_confirm.DEFAULT_AUTO_APPLY_BATCH_CAP
    batch = dream_confirm.auto_apply_batch(
        vault_path, digest.run_id, revert_log,
        stages=ALL_AUTO_APPLY_STAGES,
        batch_cap=cap, now=now, lock_root=lock_root,
    )

    staging_dir = _staging_dir(vault_path, digest.run_id)
    atomic_write(staging_dir / "digest.md", _render_digest(digest, auto_applied=batch))

    payload = dream_confirm.render_auto_applied_json(batch)
    atomic_write(staging_dir / "auto-expired.json", payload)
    atomic_write(vault_path / _META_DIR_NAME / _AUTO_EXPIRED_LATEST_NAME, payload)

    return digest, batch


# -----------------------------------------------------------------------------
# Review -- the interactive confirm/reject/skip loop
# (mirrors watchlist_review.review_watchlist's shape; adapted for our
# stage/confirm/revert-log-backed proposals instead of watchlist's direct
# frontmatter annotations)
# -----------------------------------------------------------------------------

def _current_status(path: Path):
    if not path.exists():
        return None
    fm, _body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    return fm.get("status")


def reject_proposal(vault_path: Path, proposal, *, now: float | None = None) -> dict:
    """Mark every path `proposal` touches `status: triage_rejected` (+ a
    timestamp) -- a direct frontmatter patch, matching
    `watchlist_review.py`'s own promote/dismiss/defer precedent of
    annotating outside the revert-log. Rejecting is the operator declining
    to pursue a disposition, not one of the three dispositions itself, so
    it doesn't need `record_and_apply`'s undo coverage. This is also what
    keeps a rejected entry from being re-proposed: `_still_untriaged()`
    only considers `status: inbox` entries, and `triage_rejected` isn't
    that."""
    now_iso = _utcnow_iso(now)
    touched = []
    for path_str in proposal.paths:
        path = Path(path_str)
        if not path.exists():
            continue
        patched = _patch_frontmatter(
            path.read_text(encoding="utf-8"),
            {"status": "triage_rejected", "triage_rejected_at": now_iso},
        )
        atomic_write(path, patched)
        touched.append(str(path))
    return {"action": "rejected", "paths": touched}


def _prompt_proposal_action(p, *, stdin=sys.stdin, stdout=sys.stdout) -> str:
    print("", file=stdout)
    print("─" * 72, file=stdout)
    print(f"Inbox-triage proposal #{p.index}: {p.stage} / {p.kind}", file=stdout)
    print(f"  paths:   {', '.join(p.paths)}", file=stdout)
    print(f"  summary: {p.summary}", file=stdout)
    print("─" * 72, file=stdout)
    print("Action: [c]onfirm / [r]eject / [s]kip (default: skip)", file=stdout)
    stdout.flush()
    try:
        choice = stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "skip"
    if choice in ("c", "confirm"):
        return "confirm"
    if choice in ("r", "reject"):
        return "reject"
    if not choice or choice in ("s", "skip"):
        return "skip"
    print(f"  (unknown choice {choice!r}; defaulting to skip)", file=stdout)
    return "skip"


def review_inbox_triage(
    vault_path: Path, run_id: str, revert_log: RevertLog, *,
    interactive: bool = True, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
    lock_root: Path | str | None = None,
) -> dict:
    """Walk `run_id`'s still-pending proposals; prompt for confirm/reject/
    skip; perform the action. Non-TTY stdin defaults every prompt to skip
    (never silent action -- same contract `watchlist_review.review_watchlist`
    already established)."""
    vault_path = Path(vault_path)
    pending = [p for p in dream_confirm.list_pending(vault_path, run_id) if p.status == "pending"]
    # Drop anything a DIFFERENT, overlapping proposal (or a manual reject)
    # already resolved out from under this one -- checked against the LIVE
    # on-disk file, not the staged copy captured at scan time.
    pending = [
        p for p in pending
        if all(_current_status(Path(path)) == "inbox" for path in p.paths)
    ]
    stats = {"total": len(pending), "confirmed": 0, "rejected": 0, "skipped": 0, "errors": 0}
    if not pending:
        print("[inbox-triage] no pending proposals", file=stderr)
        return stats
    if interactive and not stdin.isatty():
        print(
            "[inbox-triage] interactive review requested but stdin is not a TTY; "
            "defaulting all prompts to skip (never silent action)",
            file=stderr,
        )
        interactive = False
    for p in pending:
        action = _prompt_proposal_action(p, stdin=stdin, stdout=stdout) if interactive else "skip"
        if action == "confirm":
            try:
                dream_confirm.confirm(vault_path, run_id, p.index, revert_log, lock_root=lock_root)
                stats["confirmed"] += 1
            except dream_confirm.DreamConfirmError as e:
                print(f"[inbox-triage] confirm failed for #{p.index}: {e}", file=stderr)
                stats["errors"] += 1
        elif action == "reject":
            reject_proposal(vault_path, p)
            stats["rejected"] += 1
        else:
            stats["skipped"] += 1
    return stats


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _resolve_vault_path(arg_vault_path: str | None) -> Path | None:
    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    return Path(env_path).expanduser() if env_path else None


def _most_recent_run_id(vault_path: Path) -> str | None:
    staging_root = Path(vault_path) / "_dream-staging"
    if not staging_root.exists():
        return None
    candidates = sorted(p.name for p in staging_root.iterdir() if p.is_dir() and p.name.startswith("inbox-"))
    return candidates[-1] if candidates else None


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bulk-triage the MemoryVault _inbox/ backlog -- promote (reinforced), "
            "merge (near-duplicate), or expire (stale) each still-untriaged entry. "
            "Every proposal auto-applies by default -- promote/merge/expire alike, "
            "regardless of whether the entry predates the triage cutover -- pass "
            "--no-auto-apply to propose only. See `/memory inbox --bulk-review` in "
            "SKILL.md."
        )
    )
    parser.add_argument("--vault-path", help="MemoryVault root (overrides MEMORY_VAULT_PATH env var)")
    parser.add_argument(
        "--bulk-review", action="store_true",
        help="scan (+ auto-apply), then interactively review remaining pending "
             "proposals -- the default action when no other flag is given",
    )
    parser.add_argument("--run-id", default=None, help="resume/inspect an existing run instead of a fresh scan")
    parser.add_argument("--list", action="store_true", help="print pending proposals as JSON, no prompts")
    parser.add_argument("--confirm", type=int, metavar="INDEX", help="confirm one proposal by index (requires --run-id)")
    parser.add_argument("--reject", type=int, metavar="INDEX", help="reject one proposal by index (requires --run-id)")
    parser.add_argument(
        "--non-interactive", action="store_true",
        help="scan (+ auto-apply) only; skip the interactive review loop",
    )
    parser.add_argument(
        "--no-auto-apply", action="store_true",
        help="propose only -- skip the confirm-free auto-apply step for every disposition",
    )
    parser.add_argument("--batch-cap", type=int, default=None)
    parser.add_argument("--expire-ttl-days", type=float, default=DEFAULT_EXPIRE_TTL_DAYS)
    parser.add_argument("--promote-occurrence-threshold", type=int, default=DEFAULT_PROMOTE_OCCURRENCE_THRESHOLD)
    parser.add_argument("--log-root", default=None, help="override RevertLog's journal directory (tests only)")
    parser.add_argument("--lock-root", default=None, help="override the vault-mutex lock directory (tests only)")
    args = parser.parse_args(argv)

    vault = _resolve_vault_path(args.vault_path)
    if vault is None or not vault.exists():
        print("ERROR: no vault path resolved (set --vault-path or MEMORY_VAULT_PATH)", file=sys.stderr)
        return 1

    revert_log = RevertLog(vault, log_root=args.log_root, lock_root=args.lock_root)

    if args.confirm is not None:
        if not args.run_id:
            print("ERROR: --confirm requires --run-id", file=sys.stderr)
            return 1
        try:
            entry_id = dream_confirm.confirm(vault, args.run_id, args.confirm, revert_log, lock_root=args.lock_root)
        except dream_confirm.DreamConfirmError as e:
            print(json.dumps({"action": "error", "reason": str(e)}, indent=2))
            return 1
        print(json.dumps({"action": "confirmed", "entry_id": entry_id}, indent=2))
        return 0

    if args.reject is not None:
        if not args.run_id:
            print("ERROR: --reject requires --run-id", file=sys.stderr)
            return 1
        pending = dream_confirm.list_pending(vault, args.run_id)
        match = next((p for p in pending if p.index == args.reject), None)
        if match is None:
            print(json.dumps({"action": "error", "reason": f"no proposal {args.reject} in run {args.run_id}"}, indent=2))
            return 1
        print(json.dumps(reject_proposal(vault, match), indent=2))
        return 0

    if args.list:
        run_id = args.run_id or _most_recent_run_id(vault)
        if run_id is None:
            print(json.dumps([], indent=2))
            return 0
        pending = dream_confirm.list_pending(vault, run_id)
        print(json.dumps([
            {"index": p.index, "stage": p.stage, "kind": p.kind, "paths": p.paths,
             "summary": p.summary, "status": p.status}
            for p in pending
        ], indent=2))
        return 0

    # Default / --bulk-review: scan (unless resuming --run-id) + auto-apply
    # + interactive review.
    if args.run_id:
        run_id = args.run_id
    elif args.no_auto_apply:
        digest = run_inbox_triage(
            vault, expire_ttl_days=args.expire_ttl_days,
            promote_occurrence_threshold=args.promote_occurrence_threshold,
        )
        run_id = digest.run_id
        print(
            f"inbox-triage run {run_id}: {len(digest.proposals)} proposal(s) -- "
            f"digest at {digest.digest_path} "
            "(--no-auto-apply: nothing auto-applied, all proposals pending)"
        )
    else:
        digest, batch = run_inbox_triage_and_auto_apply(
            vault, batch_cap=args.batch_cap,
            expire_ttl_days=args.expire_ttl_days,
            promote_occurrence_threshold=args.promote_occurrence_threshold,
            log_root=args.log_root, lock_root=args.lock_root,
        )
        run_id = digest.run_id
        print(
            f"inbox-triage run {run_id}: {len(digest.proposals)} proposal(s), "
            f"{len(batch.items)} auto-applied -- digest at {digest.digest_path}"
        )

    if args.non_interactive:
        return 0

    stats = review_inbox_triage(vault, run_id, revert_log, lock_root=args.lock_root)
    print(json.dumps(stats, indent=2))
    return 0 if stats["errors"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
