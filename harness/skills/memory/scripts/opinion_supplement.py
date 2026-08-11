#!/usr/bin/env python3
"""opinion_supplement.py — the accumulate loop's Stages 2-3 (recurrence gate,
contradiction check, composition), per the ten locked design calls in
`wiki/designs/agentm-experience-and-dreaming.md`'s accumulate-loop section.

Stage 1 (`opinion_routing.py` + `reflect.route_candidates`'s supplement
branch, shipped v9.1.0) writes standard-shaped candidates into a per-opinion
lane, `<vault>/personal/_opinions/<opinion>/<slug>.md`, `status: proposed`,
with no gate at all — the corruption ceiling was zero by construction
because nothing served the lane's content back to the agent yet. This module
is what turns a lane into a served supplement, deterministically, with no
model call (the orchestration chains that run this cannot dispatch a
sub-agent — `orchestration_idle.py:24` — the same blocker crystallization's
distillation hit).

Three stacked guards, run in this order for every lane:

  1. **Recurrence gate** (locked call 4). Entries in one opinion's lane are
     clustered by same-opinion `difflib` similarity at
     `RECURRENCE_SIMILARITY_THRESHOLD`; a cluster's session ids (its own
     `sessions:` frontmatter, unioned across the group) must reach
     `RECURRENCE_SESSION_THRESHOLD` distinct sessions before the group is
     even considered for promotion. A single occurrence stays parked.
  2. **Contradiction check** (locked call 5). A group that clears the
     recurrence gate is checked against the opinion's coded base
     (`<repo_root>/opinions/<name>.md`) for a direct normative reversal on a
     shared work-domain anchor. A hit parks the group instead of promoting
     it, and records a proposed base change — the served supplement is
     never allowed to silently override a shipped standard.
  3. **Composition** (locked call 7). `personal/_opinions/<name>.md` is
     regenerated wholly from the lane's current promoted set every time
     anything changes — idempotent and self-healing, never an append.

Every mutation this module computes is returned as plain `(Path,
str | None)` pairs — the exact shape `dream.Proposal.mutations` and
`revert_log.RevertLog.record_and_apply` already use — so `dream.py`'s
`_stage_opinion_supplement()` wraps this module's output in a `Proposal`
and stages it through the SAME propose-then-confirm mechanism every other
stage uses. This module itself never writes a vault entry directly and
never imports `dream` (a leaf module, like `lifecycle.py` — `dream.py`
imports leaf modules, never the reverse).

Stdlib-only. No model call anywhere in this module.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import opinion_routing  # noqa: E402  (reuse _WORK_DOMAIN's vocabulary as the contradiction check's shared-anchor test)

__all__ = [
    "RECURRENCE_SIMILARITY_THRESHOLD",
    "RECURRENCE_SESSION_THRESHOLD",
    "LANE_DEPTH_WARNING_THRESHOLD",
    "STATUS_PROPOSED",
    "STATUS_PROMOTED",
    "STATUS_PARKED",
    "STATUS_SUPERSEDED",
    "BASE_PROPOSALS_FILENAME",
    "LaneCycleResult",
    "lane_dirs",
    "process_lane",
    "read_base_proposals",
    "lane_health",
]

# ── Calibration-era constants (locked calls 4, 6, 10) ───────────────────────
#
# Neither number below is measured — both are chosen for consistency with an
# existing constant, exactly as this design's own text says. Re-audit once
# real lane volume exists (the design's own carried-forward trigger).

# The write-time linker's own confident band, inherited from that module
# before it was removed with the vector stack; not dedup's near-verbatim
# 0.92 (dream.DEDUP_SIMILARITY_THRESHOLD) — the same lesson
# mined from two different sessions is paraphrased, not duplicated, so the
# near-verbatim threshold would under-merge it. Re-audit trigger (locked
# call 4): tighten if unrelated standards merge as real lane volume arrives;
# loosen if the gate never fires while a lane grows.
RECURRENCE_SIMILARITY_THRESHOLD = 0.85

# Two DISTINCT sessions, not two entries — the spec's own anecdote test (one
# incident is an anecdote; the same lesson surfacing from two different
# sessions is a pattern). A Stage-1-era entry with no `sessions:` value
# counts as zero and can join a group without satisfying this gate alone.
RECURRENCE_SESSION_THRESHOLD = 2

# Re-audit trigger (locked call 10): if any opinion's lane exceeds roughly
# this many still-untriaged (proposed/parked) entries, the recurrence
# threshold is too loose for whatever is feeding it. Advisory only — this
# module never blocks on it, the health check surfaces it.
LANE_DEPTH_WARNING_THRESHOLD = 20

STATUS_PROPOSED = "proposed"
STATUS_PROMOTED = "promoted"
STATUS_PARKED = "parked"
STATUS_SUPERSEDED = "superseded"

# Locked call 3 (extend-never-override guard, channel 2 of 3): the stable,
# always-current record of every suspected supplement/base contradiction —
# read by the cycle digest, the health check, and the console. Written by
# `dream.py`'s `_stage_opinion_supplement()` wrapper directly (atomic_write,
# not through revert_log/confirm) every cycle, since it is observability
# about the corpus, not vault content served to the agent — the same
# "-latest.json" convention as `dream-auto-expired-latest.json` /
# `sampled-audit-latest.json`.
BASE_PROPOSALS_FILENAME = "opinion-base-proposals.json"

# Entries in these statuses are still eligible for clustering/promotion.
# `promoted` entries are deliberately excluded from re-clustering (a v1
# scoping choice, not a locked call): once served, a lane entry's identity
# is decided and composition already reflects it. A later occurrence of the
# same lesson lands as a fresh `proposed` entry and is free to cluster with
# any OTHER still-active entry, but does not re-open an already-served one
# — narrower than the design text strictly requires, chosen so a served
# entry's provenance never silently changes without its own fresh confirm.
_ACTIVE_STATUSES = (STATUS_PROPOSED, STATUS_PARKED)


@dataclass
class LaneCycleResult:
    """One opinion's worth of this cycle's work, bundled as ONE unit so a
    single `dream_confirm.confirm()` call either lands the whole thing or
    none of it — the only way multiple lesson-groups promoting in the same
    cycle can never race the served file (see this module's own docstring
    for why a per-group proposal would be able to regress it)."""

    opinion: str
    mutations: list  # [(Path, str | None), ...] — lane patches + the composed file, together
    summary: str
    base_change_proposals: list = field(default_factory=list)  # dicts for _meta/opinion-base-proposals.json


# -----------------------------------------------------------------------------
# Frontmatter (self-contained — this scripts/ dir's established per-module
# idiom; see dream.py's own comment on why it isn't centralized).
# -----------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> tuple:
    """(frontmatter dict, body str). Values wrapped in `[...]` parse as
    lists (the `sessions:` / `refs:` flow-list shape); everything else is a
    bare string. Mirrors `opinion_resolver.py`'s own parser exactly, since
    that module already established this shape for `composes:`/`serves:`."""
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
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fm[key] = [x.strip() for x in inner.split(",") if x.strip()]
        else:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            fm[key] = value
    return fm, body


def _render_frontmatter(fm: dict) -> str:
    lines = ["---"]
    for key, value in fm.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _patch_frontmatter(content: str, updates: dict) -> str:
    """Patch (or add) frontmatter keys, preserving every untouched key and
    the body verbatim. Mirrors dream.py's own `_patch_frontmatter` shape."""
    fm, body = _parse_frontmatter(content)
    fm.update(updates)
    return _render_frontmatter(fm) + "\n" + body


def _as_list(fm: dict, key: str) -> list:
    v = fm.get(key)
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


# -----------------------------------------------------------------------------
# The coded base (read-only; the repo's own opinions/<name>.md, NOT the
# vault). Fixed relative to this shipped module, unlike the vault — no
# runtime resolution hazard the way an absolute vault path would be.
# -----------------------------------------------------------------------------

def _repo_root(root: Optional[Path] = None) -> Path:
    if root is not None:
        return Path(root)
    # <root>/harness/skills/memory/scripts/opinion_supplement.py -> <root>
    return Path(__file__).resolve().parents[4]


def _read_coded_base(opinion: str, *, root: Optional[Path] = None) -> Optional[str]:
    """The opinion's shipped base prose, or None if this name has no coded
    base at all (a lane dir with no matching `opinions/<name>.md` — never
    happens for anything Stage 1's classifier routes to, since every one of
    `opinion_routing.ROUTABLE_OPINIONS` ships a base file, but a hand-made
    lane could still name something else)."""
    path = _repo_root(root) / "opinions" / f"{opinion}.md"
    if not path.is_file():
        return None
    try:
        _, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return body.strip() or None


# -----------------------------------------------------------------------------
# Lane discovery + loading
# -----------------------------------------------------------------------------

def lane_dirs(vault_path: Path) -> list:
    """Every per-opinion lane directory, `<vault>/personal/_opinions/<name>/`
    — NOT the composed `<name>.md` served files, which sit beside them."""
    base = Path(vault_path) / "personal" / "_opinions"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir())


def _load_lane(lane_dir: Path) -> dict:
    """path -> (frontmatter dict, body str, raw content str), flat (Stage 1
    writes one file per candidate directly in the lane dir, no nesting)."""
    loaded = {}
    for p in sorted(lane_dir.glob("*.md")):
        raw = p.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(raw)
        loaded[p] = (fm, body, raw)
    return loaded


def _normalize_body(body: str) -> str:
    """The comparable rule text: title + body, with Stage 1's own trailing
    "## Mining metadata" (and any "## Supporting excerpts" after it)
    stripped — those sections carry per-occurrence instrumentation
    (rationale/confidence/excerpts) that legitimately differs between two
    mentions of the SAME lesson, so including them in a similarity or
    contradiction comparison would suppress a real match."""
    marker = body.find("\n## Mining metadata")
    if marker != -1:
        body = body[:marker]
    return body.strip()


# -----------------------------------------------------------------------------
# Recurrence gate — same-opinion similarity clustering over the lane
# (dedup's own merge-into-earliest shape, reused: see dream._stage_dedup).
# -----------------------------------------------------------------------------

def _earliest(paths: list, loaded: dict) -> Path:
    """Sort key: `created` frontmatter ascending (Stage-1's own ISO-8601 UTC
    strings sort lexicographically), tie-broken by filename for determinism
    when `created` is absent or identical."""
    return min(paths, key=lambda p: (loaded[p][0].get("created") or "", p.name))


def _similarity_clusters(paths: list, loaded: dict, *, threshold: float) -> list:
    """Disjoint hub-grouping over `paths`, same O(n^2) `difflib` pairwise
    shape as `dream._stage_dedup` (a `matched` set so no entry joins two
    clusters) but compared on `_normalize_body` rather than the raw body,
    and returning plain `[hub, *members]` groups rather than `Proposal`
    objects — this module builds its own mutations, it doesn't reuse
    dedup's merged-body-concatenation shape verbatim (see module docstring).
    Entries with no similar neighbor are NOT included — the caller treats
    every unclustered path as its own singleton group."""
    matched = set()
    groups = []
    for i, a in enumerate(paths):
        if a in matched:
            continue
        norm_a = _normalize_body(loaded[a][1])
        group = [a]
        for b in paths[i + 1:]:
            if b in matched:
                continue
            norm_b = _normalize_body(loaded[b][1])
            ratio = difflib.SequenceMatcher(None, norm_a, norm_b).ratio()
            if ratio >= threshold:
                group.append(b)
                matched.add(b)
        if len(group) > 1:
            matched.add(a)
            groups.append(group)
    # Singletons: every path never claimed by a multi-member group.
    for p in paths:
        if p not in matched:
            groups.append([p])
    return groups


# -----------------------------------------------------------------------------
# Contradiction check — narrow, high-precision (locked call 5).
# -----------------------------------------------------------------------------

_POSITIVE_POLARITY = re.compile(r"\b(always|must|required?|require[sd]?)\b", re.IGNORECASE)
_NEGATIVE_POLARITY = re.compile(r"\b(never|don'?t|do not|forbidden|forbid(?:s)?)\b", re.IGNORECASE)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str) -> list:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _polarity(sentence: str) -> Optional[str]:
    """"positive" (always/must/required), "negative" (never/don't/forbidden),
    or None — a sentence carrying BOTH (or neither) is ambiguous and is
    never treated as a polarity signal, erring toward missing a
    contradiction rather than flagging a false one (the design's own
    "narrow and high-precision" framing)."""
    pos = bool(_POSITIVE_POLARITY.search(sentence))
    neg = bool(_NEGATIVE_POLARITY.search(sentence))
    if pos and not neg:
        return "positive"
    if neg and not pos:
        return "negative"
    return None


def _shared_anchor(a: str, b: str) -> bool:
    """True iff both sentences name the same work-domain anchor (reusing
    opinion_routing._WORK_DOMAIN's own vocabulary — the same terms Stage 1
    already uses to decide a candidate is "about doing work")."""
    anchors_a = {m.lower() for m in opinion_routing._WORK_DOMAIN.findall(a)}
    anchors_b = {m.lower() for m in opinion_routing._WORK_DOMAIN.findall(b)}
    return bool(anchors_a & anchors_b)


def _contradicts_base(entry_body: str, base_body: str) -> Optional[str]:
    """The base sentence a supplement entry directly reverses, or None.
    Direct normative negation on a shared anchor: opposite polarity +
    overlapping work-domain anchor, checked across every (base, entry)
    sentence pair. Deliberately narrow (locked call 5) — the real guarantee
    is structural (nothing ever writes into `opinions/*.md`), this check
    only decides whether a group parks instead of promoting."""
    base_sentences = _sentences(base_body)
    entry_sentences = _sentences(entry_body)
    for base_s in base_sentences:
        base_pol = _polarity(base_s)
        if base_pol is None:
            continue
        for entry_s in entry_sentences:
            entry_pol = _polarity(entry_s)
            if entry_pol is None or entry_pol == base_pol:
                continue
            if _shared_anchor(base_s, entry_s):
                return base_s
    return None


# -----------------------------------------------------------------------------
# Composition (locked call 7) — pure render, no I/O.
# -----------------------------------------------------------------------------

def _provenance_footer(fm: dict) -> str:
    sessions = _as_list(fm, "sessions")
    refs = _as_list(fm, "refs")
    promoted_at = fm.get("promoted", "")
    bits = [f"{len(sessions)} session(s)"]
    if refs:
        bits.append(f"{len(refs)} ref(s)")
    return f"_Promoted {promoted_at} — {', '.join(bits)}._"


def _compose_served_file(opinion: str, promoted_entries: list) -> str:
    """`promoted_entries`: [(fm, body), ...] for every entry that is (or is
    about to become, this cycle) `status: promoted`, oldest-first. Returns
    the FULL file content (frontmatter + body) — a pure function of this
    input, matching "regenerates wholly from the lane, every cycle"."""
    ordered = sorted(promoted_entries, key=lambda e: e[0].get("promoted") or e[0].get("created") or "")
    sections = []
    for fm, body in ordered:
        title_and_body = _normalize_body(body)
        sections.append(f"{title_and_body}\n\n{_provenance_footer(fm)}")
    body_text = (
        "_The base above is authoritative. The entries below are learned, "
        "not shipped — they extend it, they never override it._\n\n"
        + "\n\n".join(sections) + "\n"
    )
    fm_text = "---\nkind: opinion-supplement\nstatus: promoted\n---\n\n"
    return fm_text + body_text


# -----------------------------------------------------------------------------
# The per-opinion cycle
# -----------------------------------------------------------------------------

def process_lane(
    vault_path: Path, opinion: str, *, now: Optional[str] = None, root: Optional[Path] = None
) -> Optional[LaneCycleResult]:
    """Run the recurrence gate, contradiction check, and composition for one
    opinion's lane. Returns None when there is nothing to propose this
    cycle (no group newly crosses the recurrence threshold, no merge
    consolidation happened, and the served file already matches the
    current promoted set) — the same "only propose on an actual change"
    convention every other dreaming stage follows."""
    vault_path = Path(vault_path)
    lane_dir = vault_path / "personal" / "_opinions" / opinion
    if not lane_dir.is_dir():
        return None
    loaded = _load_lane(lane_dir)

    active = [p for p, (fm, _, _) in loaded.items() if fm.get("status", STATUS_PROPOSED) in _ACTIVE_STATUSES]
    already_promoted = [p for p, (fm, _, _) in loaded.items() if fm.get("status") == STATUS_PROMOTED]

    # An empty (or all-promoted, nothing-active) lane still has to reach
    # the composition step below: a lane whose active entries were all
    # cleared out from under an existing served file must still self-heal
    # that file away. Only the clustering/gate loop itself is skippable.
    base_body = _read_coded_base(opinion, root=root) if active else None
    groups = _similarity_clusters(active, loaded, threshold=RECURRENCE_SIMILARITY_THRESHOLD) if active else []

    mutations = []
    base_change_proposals = []
    newly_promoted = []  # (fm, body) pairs to fold into composition
    any_change = False

    for group in groups:
        survivor = _earliest(group, loaded)
        members = [p for p in group if p != survivor]

        survivor_fm, survivor_body, _ = loaded[survivor]
        sessions = list(dict.fromkeys(_as_list(survivor_fm, "sessions")))
        refs = list(dict.fromkeys(_as_list(survivor_fm, "refs")))
        merged_body_parts = [_normalize_body(survivor_body)]

        for m in members:
            m_fm, m_body, m_raw = loaded[m]
            for s in _as_list(m_fm, "sessions"):
                if s not in sessions:
                    sessions.append(s)
            for r in _as_list(m_fm, "refs"):
                if r not in refs:
                    refs.append(r)
            merged_body_parts.append(_normalize_body(m_body))
            mutations.append((m, _patch_frontmatter(m_raw, {
                "status": STATUS_SUPERSEDED, "supersedes": str(survivor),
            })))

        # Always the normalized (title+body-only) form, whether this group
        # merged 2+ members or is a lone survivor — the contradiction check
        # below must never see a raw "## Mining metadata"/excerpts block,
        # which can contain quoted transcript text that has nothing to do
        # with the standard actually being proposed. Exact-duplicate parts
        # collapse (dict.fromkeys, order-preserving) — two mentions of the
        # same lesson are typically paraphrased, but reflect can genuinely
        # mine byte-identical title+body twice, and a served entry should
        # never show the same sentence back to back.
        merged_body = "\n\n".join(dict.fromkeys(merged_body_parts)) + "\n"
        if len(members) > 0:
            any_change = True

        eligible = len(set(sessions)) >= RECURRENCE_SESSION_THRESHOLD
        contradiction = _contradicts_base(merged_body, base_body) if (eligible and base_body) else None

        survivor_updates = {}
        if sessions:
            survivor_updates["sessions"] = sessions
        if refs:
            survivor_updates["refs"] = refs

        if eligible and base_body is not None and contradiction is None:
            survivor_updates["status"] = STATUS_PROMOTED
            survivor_updates["promoted"] = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            new_fm = dict(survivor_fm)
            new_fm.update(survivor_updates)
            newly_promoted.append((new_fm, merged_body))
        else:
            survivor_updates["status"] = STATUS_PARKED
            if contradiction:
                base_change_proposals.append({
                    "opinion": opinion,
                    "entry": str(survivor),
                    "base_sentence": contradiction,
                    "entry_excerpt": _normalize_body(merged_body)[:300],
                })

        if survivor_updates:
            survivor_raw = loaded[survivor][2]
            if len(members) > 0:
                # A real merge happened — the survivor's OWN lane file has
                # to carry the merged body too (not just patched
                # frontmatter), or a LATER cycle re-deriving "already
                # promoted" content straight from disk would silently drop
                # every absorbed member's contribution and could never
                # reproduce what was actually served. This does discard the
                # survivor's own Stage-1 "## Mining metadata" block — the
                # same trade-off dream._stage_dedup's own merge already
                # makes for ordinary vault content.
                new_fm = dict(_parse_frontmatter(survivor_raw)[0])
                new_fm.update(survivor_updates)
                patched = _render_frontmatter(new_fm) + "\n\n" + merged_body
            else:
                patched = _patch_frontmatter(survivor_raw, survivor_updates)
            if patched != survivor_raw:
                mutations.append((survivor, patched))
                any_change = True

    # Composition: fold this cycle's newly-promoted survivors in with
    # whatever was ALREADY on disk as promoted (from a prior, already-
    # confirmed cycle) — never another proposal from the SAME cycle, which
    # is exactly what keeps a single opinion's whole cycle safe to bundle
    # as one proposal (see LaneCycleResult's own docstring).
    all_promoted = [(loaded[p][0], loaded[p][1]) for p in already_promoted] + newly_promoted
    served_path = vault_path / "personal" / "_opinions" / f"{opinion}.md"

    if all_promoted:
        new_served_content = _compose_served_file(opinion, all_promoted)
        current_served = served_path.read_text(encoding="utf-8") if served_path.is_file() else None
        if current_served != new_served_content:
            mutations.append((served_path, new_served_content))
            any_change = True
    elif served_path.is_file():
        # The promoted set emptied out (every promoted entry superseded or
        # unpromoted by hand) — self-healing: no served file at all.
        mutations.append((served_path, None))
        any_change = True

    # `any_change` alone would discard a cycle that has nothing NEW to
    # mutate but DOES have a still-live base-change proposal to report (a
    # contradiction that was already flagged last cycle and remains
    # unresolved) — that report must survive even on an otherwise-quiet
    # cycle, or the aggregate `_meta/opinion-base-proposals.json` file
    # would silently lose a real, still-open finding the moment its own
    # group stops changing.
    if not any_change and not base_change_proposals:
        return None

    n_new = len(newly_promoted)
    n_consolidated = sum(len(g) - 1 for g in groups if len(g) > 1)
    summary = (
        f"opinion '{opinion}': {n_new} new promotion(s), "
        f"{n_consolidated} lane entrie(s) consolidated"
    )
    return LaneCycleResult(
        opinion=opinion, mutations=mutations, summary=summary,
        base_change_proposals=base_change_proposals,
    )


# -----------------------------------------------------------------------------
# Health (locked call 10) — a read-only, current-on-disk snapshot. Computed
# independently of whether this cycle proposed anything, so the health
# check always has a current reading even on a no-op cycle.
# -----------------------------------------------------------------------------

def read_base_proposals(vault_path: Path) -> list:
    """Every currently-recorded base-change proposal, across every opinion —
    `[]` on a missing or unreadable file (fail-safe, never raises; a fresh
    vault or one that predates this stage simply has none yet)."""
    path = Path(vault_path) / "_meta" / BASE_PROPOSALS_FILENAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def lane_health(vault_path: Path, opinion: str) -> dict:
    """A read-only, current-on-disk snapshot — independent of whether this
    cycle proposed anything, so the health check always has a current
    reading even on a no-op cycle."""
    vault_path = Path(vault_path)
    base_proposal_count = sum(
        1 for p in read_base_proposals(vault_path) if p.get("opinion") == opinion
    )
    lane_dir = vault_path / "personal" / "_opinions" / opinion
    if not lane_dir.is_dir():
        return {
            "opinion": opinion, "lane_depth": 0, "promoted_count": 0,
            "parked_count": 0, "provenance_coverage": 0.0,
            "base_proposal_count": base_proposal_count,
        }
    loaded = _load_lane(lane_dir)
    statuses = [fm.get("status", STATUS_PROPOSED) for fm, _, _ in loaded.values()]
    promoted = [fm for fm, _, _ in loaded.values() if fm.get("status") == STATUS_PROMOTED]
    parked = [s for s in statuses if s == STATUS_PARKED]
    proposed = [s for s in statuses if s == STATUS_PROPOSED]
    with_provenance = sum(1 for fm in promoted if _as_list(fm, "refs"))
    return {
        "opinion": opinion,
        "lane_depth": len(parked) + len(proposed),
        "promoted_count": len(promoted),
        "parked_count": len(parked),
        "provenance_coverage": (with_provenance / len(promoted)) if promoted else 0.0,
        "base_proposal_count": base_proposal_count,
    }
