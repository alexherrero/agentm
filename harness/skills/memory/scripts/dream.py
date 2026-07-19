#!/usr/bin/env python3
"""dream.py — the thin manual `/dream` pass (AG Wave E dreaming plan task 2).

A one-shot, operator-invoked run of the full dream pipeline — corpus stats
(deterministic) → dedup → contradiction triage → compression →
crystallization → insight-generation → qualification → digest+staging —
against a vault (or, in tests, a seeded fixture corpus).

Per the vault design's v1 locked call #1 ("stage ALL source-touching
dispositions in v1; auto-write only the additive derived layer"), this
module is deliberately split by write behavior:

  - dedup / contradiction-triage / compression PROPOSE dispositions
    (`Proposal` — a stage, a kind, the paths touched, and the mutations that
    WOULD apply) but never write them. Task 1's revert-log
    (`revert_log.RevertLog`) is not invoked here — this task's own
    boundary stops at "propose"; task 3 (`_dream-staging/` inbox contract)
    is what formalizes operator-confirm → `RevertLog.record_and_apply`.
    The digest names each proposal's prospective revert pointer (the
    `run_id`/`stage` it will journal under once confirmed) so an operator —
    or task 3's confirm flow — knows exactly where a later revert would
    reach.
  - insight-generation is the one additive, non-source-touching stage —
    it writes new `status: candidate` files directly (no staging needed;
    "candidate" never becomes authoritative until separately accepted).
  - digest+staging writes the run's digest (every proposal + its revert
    pointer) and each mutation-bearing proposal's raw proposed content to
    `_dream-staging/<run_id>/` — read-only material for a human, or a later
    task 3 confirm step, to act on.

Deliberately thin (v1 locked call #6: "dogfoods the passes, calibrates
thresholds"): dedup uses stdlib `difflib` text-similarity rather than an
embedding model (keeps this pass dependency-light and independent of
whichever V6-index/embedding work lands separately); contradiction-triage
and compression use simple frontmatter conventions (`slug`, `supersedes`)
rather than a semantic engine; qualification defaults every insight
candidate's rung to "retrieval" (this pass only re-surfaces relationships
already present in the corpus — it does not attempt the "a V6-3 pass over
the pre-pass snapshot can't reproduce it" discovery test). These are
calibration-era simplifications, not the v2 engine-graduated shape — see
`wiki/designs/agentm-experience-and-dreaming.md` and the vault's
`research-dream-mode-design.md`.

Public surface:

    run_dream(vault_path, *, run_id=None) -> DreamDigest
        Runs the full pass once against `vault_path`. Never mutates an
        existing entry. Returns the digest (proposals, insight candidates,
        corpus stats, the written digest file's path).

    Proposal / InsightCandidate / DreamDigest
        The pass's result types (see each dataclass's docstring).

CLI: `python3 dream.py --vault-path <path> [--run-id <id>]`.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from vault_lock import atomic_write  # noqa: E402

__all__ = [
    "run_dream",
    "run_dream_and_auto_apply",
    "Proposal",
    "InsightCandidate",
    "DreamDigest",
    "main",
]

# Calibration-era thresholds (v1 locked call #6) — expected to move once
# task 2's dogfood evidence exists (vault design v2 graduation table).
DEDUP_SIMILARITY_THRESHOLD = 0.92
COMPRESSION_CHAIN_MIN_LENGTH = 3

# Reserved top-level vault dirs a dream pass never reads as source entries —
# mirrors vault_lint.py's _EXCLUDE_DIRS plus dreaming's own output dirs (a
# pass must not dream about its own prior output).
_EXCLUDE_DIRS = frozenset(
    {"_idea-incubator", "_meta", "_harness", "_inbox", "_dream-staging", "_archive", "_dream", ".obsidian"}
)


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------

@dataclass
class Proposal:
    """One proposed, NOT-YET-APPLIED disposition from a source-touching
    stage. `mutations` is `[(path, new_content_or_None), ...]` in the exact
    shape `revert_log.RevertLog.record_and_apply` accepts — task 3's confirm
    flow is expected to pass it straight through once the operator accepts."""

    stage: str  # "dedup" | "contradiction_triage" | "compression"
    kind: str  # "merge" | "keep_both" | "compress"
    paths: list
    summary: str
    mutations: list = field(default_factory=list)


@dataclass
class InsightCandidate:
    """One additively-written derived insight — always `status: candidate`,
    never authoritative until a separate, later acceptance."""

    path: Path
    content: str


@dataclass
class DreamDigest:
    run_id: str
    corpus_stats: dict
    proposals: list
    insight_candidates: list
    digest_path: Optional[Path] = None
    tidying_previews: list = field(default_factory=list)


# -----------------------------------------------------------------------------
# Minimal frontmatter helpers (mirrors the repo's existing per-script idiom —
# see heat_policy.py / recall.py's own `_parse_frontmatter`; not centralized
# anywhere in this codebase today, so this module follows the same pattern
# rather than introducing a new shared dependency).
# -----------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> tuple[dict, str]:
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
# Corpus reading
# -----------------------------------------------------------------------------

def _iter_entries(vault_path: Path) -> list:
    entries = []
    for p in sorted(vault_path.rglob("*.md")):
        rel_parts = p.relative_to(vault_path).parts[:-1]
        if any(part in _EXCLUDE_DIRS for part in rel_parts):
            continue
        entries.append(p)
    return entries


def _load(entries: list) -> dict:
    """path -> (frontmatter dict, body str, raw content str)."""
    loaded = {}
    for p in entries:
        raw = p.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(raw)
        loaded[p] = (fm, body, raw)
    return loaded


# -----------------------------------------------------------------------------
# Stage 0 — corpus stats (deterministic)
# -----------------------------------------------------------------------------

def _stage_corpus_stats(entries: list) -> dict:
    return {
        "entry_count": len(entries),
        "total_bytes": sum(p.stat().st_size for p in entries),
    }


# -----------------------------------------------------------------------------
# Stage 1 — dedup
# -----------------------------------------------------------------------------

def _stage_dedup(entries: list, loaded: dict) -> list:
    proposals = []
    matched = set()
    for i, a in enumerate(entries):
        if a in matched:
            continue
        _, body_a, raw_a = loaded[a]
        for b in entries[i + 1:]:
            if b in matched:
                continue
            _, body_b, raw_b = loaded[b]
            ratio = difflib.SequenceMatcher(None, body_a, body_b).ratio()
            if ratio < DEDUP_SIMILARITY_THRESHOLD:
                continue
            merged_body = body_a.rstrip("\n") + "\n" + body_b.rstrip("\n") + "\n"
            merged_content = raw_a[: raw_a.rfind(body_a)] + merged_body if body_a in raw_a else merged_body
            superseded_content = _patch_frontmatter(raw_b, {"status": "superseded", "supersedes": str(a)})
            proposals.append(
                Proposal(
                    stage="dedup",
                    kind="merge",
                    paths=[str(a), str(b)],
                    summary=f"{a.name} and {b.name} are {ratio:.0%} similar (>= {DEDUP_SIMILARITY_THRESHOLD:.0%}) — propose merge",
                    mutations=[(a, merged_content), (b, superseded_content)],
                )
            )
            matched.add(b)
    return proposals


# -----------------------------------------------------------------------------
# Stage 2 — contradiction triage
# -----------------------------------------------------------------------------

def _stage_contradiction_triage(entries: list, loaded: dict) -> list:
    by_slug: dict = {}
    for p in entries:
        fm, _, _ = loaded[p]
        slug = fm.get("slug")
        if not slug:
            continue
        by_slug.setdefault(slug, []).append(p)

    proposals = []
    for slug, paths in by_slug.items():
        if len(paths) < 2:
            continue
        bodies = {p: loaded[p][1] for p in paths}
        if len(set(bodies.values())) < 2:
            continue  # identical bodies — dedup's job, not a contradiction
        proposals.append(
            Proposal(
                stage="contradiction_triage",
                kind="keep_both",
                paths=[str(p) for p in paths],
                summary=(
                    f"{len(paths)} entries share slug {slug!r} with differing content — "
                    "flagged for operator triage, no auto-resolution in v1"
                ),
                mutations=[],  # advisory only — v1 never auto-resolves a contradiction
            )
        )
    return proposals


# -----------------------------------------------------------------------------
# Stage 3 — compression (supersession-chain compaction)
# -----------------------------------------------------------------------------

def _find_supersession_chains(entries: list, loaded: dict) -> list:
    """A chain is entries linked head<-...<-tail via `supersedes:` back-links
    (each entry's `supersedes:` names the path it replaces). Returns chains
    of length >= COMPRESSION_CHAIN_MIN_LENGTH, head-first."""
    supersedes_of = {}
    for p in entries:
        fm, _, _ = loaded[p]
        target = fm.get("supersedes")
        if target:
            supersedes_of[p] = Path(target)

    superseded_targets = set(supersedes_of.values())
    heads = [p for p in supersedes_of if p not in superseded_targets]

    chains = []
    for head in heads:
        chain = [head]
        cur = supersedes_of.get(head)
        while cur is not None:
            match = next((e for e in entries if e == cur or str(e) == str(cur)), None)
            if match is None or match in chain:
                break
            chain.append(match)
            cur = supersedes_of.get(match)
        if len(chain) >= COMPRESSION_CHAIN_MIN_LENGTH:
            chains.append(chain)
    return chains


def _stage_compression(entries: list, loaded: dict) -> list:
    proposals = []
    for chain in _find_supersession_chains(entries, loaded):
        head = chain[0]
        rest = chain[1:]
        _, head_body, head_raw = loaded[head]
        derived_from = ", ".join(str(p) for p in rest)
        compacted_body = head_body.rstrip("\n") + f"\nderived_from: [{derived_from}]\n"
        compacted_content = (
            head_raw[: head_raw.rfind(head_body)] + compacted_body if head_body in head_raw else compacted_body
        )
        mutations = [(head, compacted_content)]
        for p in rest:
            # Never-delete-sources (the vault design's own invariant): mark
            # compacted-into rather than removing the file.
            _, _, raw = loaded[p]
            mutations.append((p, _patch_frontmatter(raw, {"compacted_into": str(head)})))
        proposals.append(
            Proposal(
                stage="compression",
                kind="compress",
                paths=[str(p) for p in chain],
                summary=f"supersession chain of {len(chain)} entries headed by {head.name} — propose compaction",
                mutations=mutations,
            )
        )
    return proposals


# -----------------------------------------------------------------------------
# Stage — tidying (auto-organization part 1, task 3): a non-exempt entry
# past 5 years without a genuine recall access stages a move to its tier's
# `_archive/` — never a delete, both the old and new path are captured in
# one mutation pair (record_and_apply journals a pre-image of each), so
# reverting a tidying entry restores the original file and removes the
# archived copy. An entry crossing 4.5 years gets a one-cycle preview line
# in the digest (informational only, no mutation) before the actual move —
# a heads-up, not a gate. A genuine recall resets the clock to zero: this
# reads the exact same `.lifecycle.json` anchor `lifecycle.py`'s own decay
# scoring uses, via the shared `lifecycle.days_since_last_genuine_access`
# seam, so "cold" here means exactly what "decayed" means everywhere else
# in the memory engine, never a second, independently-drifting notion of
# staleness. Task 4 (the artifact shelf) extends this same stage with a
# second, non-memory lane rather than adding a separate one.
# -----------------------------------------------------------------------------

_ARCHIVE_THRESHOLD_DAYS = 1825.0  # 5 years
_ARCHIVE_PREVIEW_DAYS = 1642.5  # 4.5 years — one cycle's heads-up before the move


def _archived_path(rel_path: Path) -> Path:
    """Where a tidying-stage archive move for `rel_path` lands: `_archive/`
    inserted right after the owning tier root, kind-subfolder structure
    preserved past that point. Two tier roots are real, already-live vault
    conventions this mirrors exactly:

      personal/<kind>/...            -> personal/_archive/<kind>/...
      projects/<project>/<kind>/...  -> projects/<project>/_archive/<kind>/...

    `personal/_archive/preferences/*.md` already exists in this exact
    shape. The `projects/` case is scoped PER-PROJECT (`_archive` inserted
    after the project segment, not after `projects/` itself) deliberately:
    `projects/_archive/<project>/` is a different, already-existing
    feature — whole-*project* retirement (the 2026-07-12 amendment) — and
    reusing that namespace for a still-active project's individual
    archived notes would collide with it. Anything outside those two tier
    roots — including a bare-root file with no parent directory at all,
    the shape this module's own test fixtures use — falls back to
    inserting `_archive` right at the root, never after the filename
    itself (a naive "after the first segment" rule would turn `foo.md`
    into the nonsensical `foo.md/_archive`)."""
    parts = rel_path.parts
    if len(parts) >= 2 and parts[0] == "personal":
        tier_len = 1
    elif len(parts) >= 3 and parts[0] == "projects":
        tier_len = 2
    else:
        tier_len = 0
    return Path(*parts[:tier_len], "_archive", *parts[tier_len:])


# -----------------------------------------------------------------------------
# The artifact shelf (task 4) — the tidying stage's second lane, for
# "the other non-memory documents" the design names: any entry this same
# corpus walk finds with no `kind:` frontmatter field at all, i.e. never
# written through save.py's locked memory-entry contract (REQUIRED
# FRONTMATTER_FIELD_ORDER makes `kind` mandatory for a real memory — its
# absence IS the signal, not a second, invented classification).
#
# Operator ruling (2026-07-18, this plan's own task 4): "touch" for an
# artifact reuses the EXACT SAME mechanism task 3 already reuses for
# memories — a genuine recall.py hit, tracked via
# lifecycle.days_since_last_genuine_access — rather than a new "any
# injection into a conversation" tracker (which nothing in this codebase
# implements yet, and would need cross-repo instrumentation spanning
# skills/hooks living in the separate crickets repo — disproportionate to
# one task). Same mechanism, narrower population, shorter threshold,
# `_shelf/` destination instead of `_archive/`.
#
# Unlike the archive lane, the shelf is bidirectional: `_shelf/` is NOT in
# `_EXCLUDE_DIRS` (unlike `_archive/`), so a previously-shelved artifact is
# re-walked every cycle — if it's been touched since shelving (elapsed
# drops back below the threshold), this stage proposes moving it back to
# its original folder on the very next cycle, exactly as the design's own
# "one use brings it back" line specifies.
# -----------------------------------------------------------------------------

_SHELF_THRESHOLD_DAYS = 365.0  # 1 year


def _shelved_path(rel_path: Path) -> Path:
    """Mirrors `_archived_path`'s tier-root insertion, using `_shelf`
    instead of `_archive` — same two real tier-root conventions, same
    bare-root fallback."""
    parts = rel_path.parts
    if len(parts) >= 2 and parts[0] == "personal":
        tier_len = 1
    elif len(parts) >= 3 and parts[0] == "projects":
        tier_len = 2
    else:
        tier_len = 0
    return Path(*parts[:tier_len], "_shelf", *parts[tier_len:])


def _unshelved_path(rel_path: Path) -> Path:
    """Inverse of `_shelved_path` — strips the (single, by construction)
    `_shelf` segment, restoring the artifact to its original tier-relative
    location."""
    parts = list(rel_path.parts)
    parts.remove("_shelf")
    return Path(*parts)


def _stage_tidying(vault_path: Path, entries: list, loaded: dict, *, now: str | None = None) -> tuple:
    """Returns (proposals, preview_lines). See module section docstrings
    above (memory archive, then the artifact shelf). `now` is injectable
    for tests (ISO date string YYYY-MM-DD)."""
    import lifecycle  # noqa: E402  (lazy: keeps run_dream()'s own import graph unchanged)

    if now is None:
        import datetime
        now = datetime.date.today().isoformat()

    proposals = []
    preview_lines = []

    for path in entries:
        fm, _body, raw = loaded[path]
        rel = path.relative_to(vault_path)
        slug = fm.get("slug") or path.stem
        is_artifact = "kind" not in fm

        elapsed = lifecycle.days_since_last_genuine_access(vault_path, slug, fm, rel, now=now)
        if elapsed is None:
            continue  # decay-exempt, or no basis to compute — never tidied

        if is_artifact:
            currently_shelved = "_shelf" in rel.parts
            if currently_shelved:
                if elapsed < _SHELF_THRESHOLD_DAYS:
                    dest_rel = _unshelved_path(rel)
                    proposals.append(
                        Proposal(
                            stage="tidying",
                            kind="unshelve",
                            paths=[str(rel)],
                            summary=(
                                f"{rel} — touched {elapsed:.0f} days ago, since being shelved — "
                                f"propose return to {dest_rel}"
                            ),
                            mutations=[(path, None), (vault_path / dest_rel, raw)],
                        )
                    )
                # else: still cold — stays on the shelf, no action.
            elif elapsed > _SHELF_THRESHOLD_DAYS:
                dest_rel = _shelved_path(rel)
                proposals.append(
                    Proposal(
                        stage="tidying",
                        kind="shelve",
                        paths=[str(rel)],
                        summary=(
                            f"{rel} — {elapsed:.0f} days untouched, past the 1y shelf threshold — "
                            f"propose move to {dest_rel}"
                        ),
                        mutations=[(path, None), (vault_path / dest_rel, raw)],
                    )
                )
            continue  # artifacts don't participate in the memory-archive lane below

        if elapsed > _ARCHIVE_THRESHOLD_DAYS:
            dest_rel = _archived_path(rel)
            proposals.append(
                Proposal(
                    stage="tidying",
                    kind="archive",
                    paths=[str(rel)],
                    summary=(
                        f"{rel} — {elapsed:.0f} days ({elapsed / 365.25:.1f}y) since last genuine "
                        f"recall access, past the 5y archive threshold — propose move to {dest_rel}"
                    ),
                    mutations=[(path, None), (vault_path / dest_rel, raw)],
                )
            )
        elif elapsed > _ARCHIVE_PREVIEW_DAYS:
            preview_lines.append(
                f"{rel} — {elapsed:.0f} days ({elapsed / 365.25:.1f}y) silent, crosses the 5y "
                "archive threshold within roughly the next cycle"
            )

    return proposals, preview_lines


# -----------------------------------------------------------------------------
# Stage — weekly link-improvement sweep (auto-org part 2 task 4). Connects
# notes that arrived or changed since the last cycle to older related
# content, using task 1's vector index + task 2's persisted graph snapshot.
# -----------------------------------------------------------------------------

# Cap on proposals this stage generates per cycle — bounds worst-case blast
# radius the same way tidying/compression already do (plan constraint:
# "capped"). Matches dream_confirm.DEFAULT_AUTO_APPLY_BATCH_CAP's own
# number, the standing precedent for a per-cycle auto-org action bound.
_LINK_IMPROVEMENT_BATCH_CAP = 25


_LINK_SWEEP_CURSOR_REL = "_meta/link-sweep-cursor.json"


def _link_sweep_cursor_path(vault_path: Path) -> Path:
    return vault_path / _LINK_SWEEP_CURSOR_REL


def _read_link_sweep_cursor(vault_path: Path) -> float:
    """Epoch seconds of the last `_stage_link_improvement` run, or `0.0` if
    it's never run (everything counts as "changed").

    Deliberately its OWN, separate cursor — NOT `graph_snapshot.rebuild()`'s
    `touched_paths` (a review caught a real bug in an earlier version that
    used it: `write_time_linker.apply()` already calls `graph_snapshot.
    rebuild(vault, paths=[rel_path])` for every note it write-time-links,
    which records that note's post-write mtime into the SAME snapshot DB —
    so by the time the weekly sweep ran its own full-vault rebuild, any
    note that already got an ordinary write-time link (the common case —
    precisely the notes most likely to have qualifying neighbors) had its
    mtime already "consumed" and never showed up as newly touched. This
    cursor is independent of anything graph_snapshot tracks internally."""
    try:
        data = json.loads(_link_sweep_cursor_path(vault_path).read_text(encoding="utf-8"))
        return float(data.get("last_run_epoch", 0.0))
    except (OSError, ValueError, TypeError):
        return 0.0


def _write_link_sweep_cursor(vault_path: Path, epoch: float) -> None:
    atomic_write(_link_sweep_cursor_path(vault_path), json.dumps({"last_run_epoch": epoch}))


def cheap_model_tier_available() -> bool:
    """Whether a budget-capped cheap-model yes/no call is available for the
    weekly sweep's ambiguous middle band. Always `False` today.

    Confirmed by research before this stage was built: no synchronous
    "ask a cheap model X, get yes/no back, capped by a budget" primitive
    exists anywhere in this codebase. The job-manifest schema has a
    `budget_tokens` field, but it's parsed and never consumed (zero
    callers). The only place this codebase shells out to `claude`
    programmatically spawns full async background sessions — the wrong
    shape for a synchronous per-candidate call. Every prior instance of
    this exact problem (a cron/hook context needing LLM judgment) —
    `adapt_skills.py`'s deterministic-Pass-1/sub-agent-Pass-2 split,
    `orchestration_idle.py`'s explicit "a hook fires outside the agent
    loop and cannot dispatch a sub-agent", `forward_learning.py`'s
    deliberately-deterministic-only v1 — resolved it by staying
    deterministic and deferring the LLM-judged pass, never by building a
    new primitive this codebase has consistently avoided.

    This function is the seam a future build wires to a real budget +
    call primitive. Until then it's a named, tracked gap (not silently
    dropped): every ambiguous candidate the weekly sweep finds falls
    through to "left unlinked," matching the design's own explicit
    "budget exhausted / tier unavailable" fallback — unconditionally
    today rather than conditionally on a budget that has nothing to
    spend from yet.
    """
    return False


def _stage_link_improvement(vault_path: Path, entries: list, loaded: dict, *, now: str | None = None) -> list:
    """Returns proposals (`stage="link_improvement"`, `kind="link"`).

    For notes that arrived or changed since the last cycle — tracked via
    this function's OWN cursor (`_read_link_sweep_cursor`/
    `_write_link_sweep_cursor`; see its docstring for why this is
    deliberately not `graph_snapshot.rebuild()`'s own `touched_paths`) —
    queries task 1's vector index for older related notes. A candidate
    above `write_time_linker.CONFIDENT_SIMILARITY_THRESHOLD` gets a
    both-directions link proposal; this function's caller (`run_dream`)
    runs the normal stage->confirm->revert-log pipeline on it like any
    other proposal. Also rebuilds task 2's graph snapshot in full-vault
    mode as a side effect (keeping it current for its OTHER consumers —
    the connectivity meter, task 7 — even though this function no longer
    reads its `touched_paths`).

    The ambiguous middle band (between `write_time_linker.
    LINK_SIMILARITY_FLOOR` and `CONFIDENT_SIMILARITY_THRESHOLD`) — the
    design's own reserved slot for a budget-capped cheap-model yes/no —
    checks `cheap_model_tier_available()` (always `False` today; see its
    own docstring for the full reasoning) and falls through to "left
    unlinked" for every candidate in that band.

    Same-cycle mutation-collision guard: if note A and note B (both in this
    cycle's changed set) are both related to the same older note C, only
    the first one to claim C gets to add C's reciprocal link this cycle —
    a `touched_this_sweep` set makes sure no path is ever a mutation target
    in more than one proposal in a single call, which would otherwise let
    a later proposal's captured pre-mutation content silently overwrite an
    earlier proposal's addition once both apply. The missed reciprocal
    link (C not linked to B this cycle) is deferred, not lost — C stays a
    normal candidate for a future cycle once something else changes it.

    `now` is injectable for tests, matching `_stage_tidying`'s own
    exemption-check convention below.
    """
    import embed  # noqa: E402  (lazy: keeps run_dream()'s own import graph unchanged)
    import graph_snapshot  # noqa: E402
    import lifecycle  # noqa: E402
    import vec_index  # noqa: E402
    import write_time_linker  # noqa: E402

    if now is None:
        import datetime
        now = datetime.date.today().isoformat()

    sweep_started_at = time.time()
    cursor = _read_link_sweep_cursor(vault_path)
    graph_snapshot.rebuild(vault_path)  # keep the snapshot current for its other consumers

    by_rel = {}
    for p in entries:
        rel = str(p.relative_to(vault_path)).replace("\\", "/")
        by_rel[rel] = p

    changed_paths = [
        rel for rel, p in by_rel.items()
        if p in loaded and p.stat().st_mtime > cursor
    ]
    max_seen_mtime = max((by_rel[rel].stat().st_mtime for rel in changed_paths), default=0.0)

    proposals: list = []
    touched_this_sweep: set = set()

    for rel_path in changed_paths:
        if len(proposals) >= _LINK_IMPROVEMENT_BATCH_CAP:
            break
        path = by_rel.get(rel_path)
        if path is None or path not in loaded or path in touched_this_sweep:
            continue

        fm, body, raw = loaded[path]
        slug = fm.get("slug") or path.stem
        rel_obj = path.relative_to(vault_path)
        # Same exemption gate as _stage_tidying: durable/failure-incident/
        # decision/always-load/pinned notes are "never candidates for
        # anything here" per the design's Signals section.
        if lifecycle.days_since_last_genuine_access(vault_path, slug, fm, rel_obj, now=now) is None:
            continue

        embed_input = f"{slug} [{fm.get('tags', '')}]\n\n{body[:500]}"
        try:
            embedding = embed.embed_text(embed_input)
        except embed.EmbeddingUnavailable:
            continue
        except Exception:
            continue

        # Query at the LOWER floor (not the confident threshold) so the
        # ambiguous middle band is genuinely visible to the code below,
        # rather than silently invisible because the query itself already
        # excluded it — the seam has to see a candidate to (not) call the
        # cheap model on it.
        neighbors = vec_index.nearest(
            vault_path, embedding,
            k=write_time_linker.MAX_RELATED_LINKS + 1,
            similarity_floor=write_time_linker.LINK_SIMILARITY_FLOOR,
        )
        qualifying = []
        for neighbor_rel, sim in neighbors:
            if neighbor_rel == rel_path:
                continue
            neighbor_path = by_rel.get(neighbor_rel)
            if neighbor_path is None or neighbor_path not in loaded or neighbor_path in touched_this_sweep:
                continue
            if sim < write_time_linker.CONFIDENT_SIMILARITY_THRESHOLD:
                # Ambiguous middle band. Never fail open to an unbudgeted
                # call (plan's Locked design call) -- check the seam, and
                # today it's always unavailable (see this function's own
                # docstring), so every ambiguous candidate falls through to
                # "left unlinked" without ever reaching a model call.
                #
                # Deliberately does NOT remember this pair as "considered"
                # anywhere: similarity isn't guaranteed symmetric within one
                # sweep (this note's embedding is freshly recomputed above;
                # the neighbor's is whatever's already stored, from a
                # possibly-different embed-input formula or an earlier,
                # since-changed version of that note) — a review caught a
                # real bug where marking a pair "seen" here silently
                # dropped a genuinely confident match from the OTHER
                # direction later in the same sweep. touched_this_sweep
                # (below) is the only cross-proposal guard this function
                # needs: it stops the same PATH being a mutation target
                # twice, which is the actual hazard (a second proposal's
                # captured pre-mutation content silently overwriting the
                # first's addition) -- it doesn't need a redundant "did we
                # already look at this exact pair" tracker on top.
                if not cheap_model_tier_available():
                    continue
                continue  # pragma: no cover -- unreachable until the tier ships
            qualifying.append((neighbor_rel, neighbor_path))
            if len(qualifying) >= write_time_linker.MAX_RELATED_LINKS:
                break
        if not qualifying:
            continue

        neighbor_slugs = []
        for _neighbor_rel, neighbor_path in qualifying:
            neighbor_fm = loaded[neighbor_path][0]
            neighbor_slugs.append(neighbor_fm.get("slug") or neighbor_path.stem)

        mutations = []
        new_content = write_time_linker.merge_related_slugs(raw, neighbor_slugs)
        if new_content is not None:
            mutations.append((path, new_content))
            touched_this_sweep.add(path)

        for (neighbor_rel, neighbor_path), neighbor_slug in zip(qualifying, neighbor_slugs):
            neighbor_raw = loaded[neighbor_path][2]
            neighbor_new_content = write_time_linker.merge_related_slugs(neighbor_raw, [slug])
            if neighbor_new_content is not None:
                mutations.append((neighbor_path, neighbor_new_content))
                touched_this_sweep.add(neighbor_path)

        if not mutations:
            continue  # already linked both ways -- nothing to propose

        neighbor_rels = [nr for nr, _np in qualifying]
        proposals.append(
            Proposal(
                stage="link_improvement",
                kind="link",
                paths=[rel_path, *neighbor_rels],
                summary=(
                    f"{rel_path} <-> {', '.join(neighbor_rels)} — related "
                    f"(similarity clears the confident threshold)"
                ),
                mutations=mutations,
            )
        )

    # Advance the cursor to when THIS sweep started (not `time.time()` now)
    # -- anything modified DURING this run's own execution stays eligible
    # for the next cycle too, rather than silently slipping past both.
    # Floored at the max mtime actually seen among this cycle's changed
    # entries: guards against the cursor ever retreating behind an entry
    # it just processed (defensive against clock skew between the mtime
    # clock and time.time(), and keeps this robust for tests that set an
    # explicit future mtime rather than relying on real elapsed wall-clock
    # time between fixture writes).
    _write_link_sweep_cursor(vault_path, max(sweep_started_at, max_seen_mtime))

    return proposals


# -----------------------------------------------------------------------------
# Stage 4 — crystallization (thin: a textual summary folded into the digest,
# not a new file — phase-close crystallization is a separate, out-of-scope
# [PENDING-IMPL] elsewhere in the Experience design).
# -----------------------------------------------------------------------------

def _stage_crystallization(corpus_stats: dict, proposals: list) -> str:
    by_stage: dict = {}
    for p in proposals:
        by_stage[p.stage] = by_stage.get(p.stage, 0) + 1
    parts = [
        f"{corpus_stats['entry_count']} entries scanned ({corpus_stats['total_bytes']} bytes)."
    ]
    if by_stage:
        parts.append(
            "Proposed: " + ", ".join(f"{n} {stage}" for stage, n in sorted(by_stage.items())) + "."
        )
    else:
        parts.append("No dispositions proposed this run.")
    return " ".join(parts)


# -----------------------------------------------------------------------------
# Stage 5 — insight generation (additive, auto-written, always candidate)
# -----------------------------------------------------------------------------

def _stage_insight_generation(
    vault_path: Path, run_id: str, crystallized_summary: str, proposals: list
) -> list:
    if not proposals:
        return []  # nothing worth an insight over — v1 stays conservative

    derived_from = sorted({p for prop in proposals for p in prop.paths})
    content = (
        "---\n"
        "kind: insight\n"
        "status: candidate\n"
        f"dream_run: {run_id}\n"
        f"derived_from: [{', '.join(derived_from)}]\n"
        "---\n"
        f"# Dream insight — run {run_id}\n\n"
        f"{crystallized_summary}\n"
    )
    path = vault_path / "_dream" / "insights" / f"{run_id}.md"
    return [InsightCandidate(path=path, content=content)]


# -----------------------------------------------------------------------------
# Stage 6 — qualification (thin default; see module docstring)
# -----------------------------------------------------------------------------

def _stage_qualification(insight_candidates: list) -> None:
    for candidate in insight_candidates:
        candidate.content = _patch_frontmatter(candidate.content, {"rung": "retrieval"})


# -----------------------------------------------------------------------------
# Stage 7 — digest + staging
# -----------------------------------------------------------------------------

def _render_digest(digest: DreamDigest, *, auto_applied=None, tidying_anomaly=None) -> str:
    """`auto_applied` (an optional `dream_confirm.AutoAppliedBatch`) marks
    which proposals this run already applied automatically — the
    dreaming pipeline's confirm-free "expire" action (2026-07-11 operator
    ruling: compression-stage proposals only; dedup/contradiction-triage
    always still show as staged, awaiting an explicit
    `dream_confirm.confirm()` call). `run_dream()` itself calls this with
    `auto_applied=None` (nothing has auto-applied yet at that point in the
    pipeline); `run_dream_and_auto_apply()` re-renders with the real batch
    once it's known, so the on-disk `digest.md` never misreports an
    already-applied item as still awaiting confirmation.

    `tidying_anomaly` (an optional `dream_confirm.AnomalyCheckResult`,
    task 6) flags a tripped anomaly breaker: this cycle's tidying-stage
    proposal count was several times the recent usual, so none of it
    auto-applied this cycle (it stays pending, exactly like dedup/
    contradiction-triage) — surfaced here as the digest's "console" line
    for the operator."""
    lines = [
        f"# Dream digest — run {digest.run_id}",
        "",
        f"Corpus: {digest.corpus_stats['entry_count']} entries, {digest.corpus_stats['total_bytes']} bytes.",
        "",
    ]
    if digest.insight_candidates:
        lines.append("## Insight candidates (written, status: candidate)")
        for c in digest.insight_candidates:
            lines.append(f"- `{c.path}`")
        lines.append("")

    if digest.tidying_previews:
        lines.append("## Archive preview (crosses the 5y threshold next cycle — no action yet)")
        lines.append("")
        for line in digest.tidying_previews:
            lines.append(f"- {line}")
        lines.append("")

    if tidying_anomaly is not None and tidying_anomaly.tripped:
        lines.append("## ⚠ ANOMALY BREAKER TRIPPED — tidying auto-apply suppressed this cycle")
        lines.append("")
        lines.append(
            f"{tidying_anomaly.current_count} tidying-stage proposal(s) this run, "
            f"vs. a recent baseline of {tidying_anomaly.baseline:.1f} "
            f"(threshold {tidying_anomaly.threshold:.1f}) — applying nothing from this "
            "stage this cycle rather than an abnormal batch. Every tidying proposal "
            "stays pending; review the run's proposals.json and confirm manually if "
            "the volume is genuinely expected, or investigate before the next cycle."
        )
        lines.append("")

    auto_applied_by_index = {}
    if auto_applied is not None:
        auto_applied_by_index = {item["index"]: item for item in auto_applied.items}
        lines.append("## Auto-expired this run (applied automatically — no confirm required)")
        lines.append("")
        if not auto_applied.items:
            lines.append(
                f"None this run (stages watched: {', '.join(sorted(auto_applied.stages)) or 'none'}; "
                f"batch cap {auto_applied.batch_cap})."
            )
        else:
            lines.append(
                f"{len(auto_applied.items)} proposal(s) auto-applied "
                f"(batch cap {auto_applied.batch_cap}; stages: {', '.join(sorted(auto_applied.stages))}):"
            )
            lines.append("")
            for item in auto_applied.items:
                lines.append(f"- #{item['index']} {item['stage']}/{item['kind']}: {item['summary']}")
                lines.append(
                    f"  revert: `RevertLog(vault_path).revert({digest.run_id!r}, {item['entry_id']!r})` "
                    f"— entry `{item['entry_id']}`"
                )
            lines.append("")
            lines.append(
                f"Full record: `_dream-staging/{digest.run_id}/auto-expired.json` "
                "(also mirrored at `_meta/dream-auto-expired-latest.json`)."
            )
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
                f"- **AUTO-APPLIED** (expire — no confirm required): entry `{item['entry_id']}`; "
                f"undo via `RevertLog.revert({digest.run_id!r}, {item['entry_id']!r})`"
            )
        elif p.mutations:
            lines.append(
                "- staged — NOT applied; operator confirmation required "
                f"(`dream_confirm.confirm(vault_path, {digest.run_id!r}, {i}, revert_log)`)"
            )
            lines.append(
                f"- revert pointer (on confirm): run `{digest.run_id}`, stage `{p.stage}` "
                f"— apply via `revert_log.RevertLog.record_and_apply({digest.run_id!r}, {p.stage!r}, mutations)`, "
                f"undo via `RevertLog.revert({digest.run_id!r}, entry_id)`"
            )
            lines.append(f"- proposal file: `{i:02d}-{p.stage}-{p.kind}.proposal.md`")
        else:
            lines.append("- advisory only — no mutation proposed")
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


def _render_manifest(digest: DreamDigest, staged_at: float) -> str:
    """The machine-readable sibling of digest.md — task 3's confirm flow
    reads this (not the human-facing digest/proposal markdown, which embeds
    content in prose/code-fences and is not reliably re-parseable) to get
    each proposal's exact mutation data back out."""
    return json.dumps(
        {
            "run_id": digest.run_id,
            "staged_at": staged_at,
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


def _stage_digest_and_staging(vault_path: Path, digest: DreamDigest) -> Path:
    staging_dir = vault_path / "_dream-staging" / digest.run_id
    digest_path = staging_dir / "digest.md"
    atomic_write(digest_path, _render_digest(digest))
    atomic_write(staging_dir / "proposals.json", _render_manifest(digest, time.time()))
    for i, p in enumerate(digest.proposals, start=1):
        if not p.mutations:
            continue
        proposal_path = staging_dir / f"{i:02d}-{p.stage}-{p.kind}.proposal.md"
        atomic_write(proposal_path, _render_proposal_file(i, p))
    return digest_path


# -----------------------------------------------------------------------------
# The pass
# -----------------------------------------------------------------------------

def run_dream(vault_path: Path, *, run_id: str | None = None) -> DreamDigest:
    """Run the full thin `/dream` pass once against `vault_path`. Never
    mutates an existing entry — dedup/contradiction/compression stages only
    PROPOSE (see module docstring). Insight candidates are the one
    exception: written for real, immediately, always `status: candidate`."""
    vault_path = Path(vault_path)
    run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    entries = _iter_entries(vault_path)
    loaded = _load(entries)

    corpus_stats = _stage_corpus_stats(entries)
    proposals = []
    proposals.extend(_stage_dedup(entries, loaded))
    proposals.extend(_stage_contradiction_triage(entries, loaded))
    proposals.extend(_stage_compression(entries, loaded))
    tidying_proposals, tidying_previews = _stage_tidying(vault_path, entries, loaded)
    proposals.extend(tidying_proposals)
    proposals.extend(_stage_link_improvement(vault_path, entries, loaded))

    crystallized_summary = _stage_crystallization(corpus_stats, proposals)
    insight_candidates = _stage_insight_generation(vault_path, run_id, crystallized_summary, proposals)
    _stage_qualification(insight_candidates)

    for candidate in insight_candidates:
        atomic_write(candidate.path, candidate.content)

    digest = DreamDigest(
        run_id=run_id,
        corpus_stats=corpus_stats,
        proposals=proposals,
        insight_candidates=insight_candidates,
        tidying_previews=tidying_previews,
    )
    digest.digest_path = _stage_digest_and_staging(vault_path, digest)
    return digest


# -----------------------------------------------------------------------------
# Auto-apply — the "expire" action's confirm-free apply path (2026-07-11
# operator ruling: see wiki/designs/agentm-experience-and-dreaming.md's
# amendment log). `run_dream()` above is untouched — it still only ever
# proposes; this wrapper is the additive layer that immediately confirms
# the compression-stage proposals through `dream_confirm.auto_apply_batch`
# once `run_dream()` has staged them. `dedup` and `contradiction_triage`
# proposals are left exactly as `run_dream()` staged them — pending,
# awaiting an explicit operator `dream_confirm.confirm()` call.
# -----------------------------------------------------------------------------

def run_dream_and_auto_apply(
    vault_path: Path,
    *,
    run_id: str | None = None,
    revert_log=None,
    batch_cap: int | None = None,
    log_root: Path | str | None = None,
    lock_root: Path | str | None = None,
):
    """Run `run_dream()` (unchanged), then auto-apply its compression-stage,
    tidying-stage, and link-improvement-stage proposals through
    `dream_confirm.auto_apply_batch` — no operator confirm required for
    those (see `dream_confirm.AUTO_APPLY_STAGES`'s own docstring for each
    stage's justification). Dedup and contradiction-triage proposals stay
    staged in `_dream-staging/<run_id>/`, exactly as `run_dream()` left
    them.

    Before applying, the tidying-stage proposal count runs through
    `dream_confirm.check_tidying_anomaly` (task 6's anomaly breaker,
    scoped to tidying specifically — the rest of the guard suite lands
    with part 3). A tripped check excludes `"tidying"` from this cycle's
    auto-apply stages (compression is unaffected); every tidying proposal
    stays pending instead, and the digest carries a visible flag.

    Re-renders `digest.md` with the auto-applied batch (and any tripped
    anomaly) reflected (see `_render_digest`'s `auto_applied`/
    `tidying_anomaly` params), and writes the machine-readable per-run
    `_dream-staging/<run_id>/auto-expired.json` plus the stable, run-id-
    free `_meta/dream-auto-expired-latest.json` pointer — the latter is
    what a later reader (e.g. a console/dashboard surface) reads without
    needing to already know the run id, and it is overwritten every cycle
    (including a zero-item one) so it never goes stale.

    `revert_log` defaults to a fresh `RevertLog(vault_path, log_root=
    log_root, lock_root=lock_root)` (the CLI's own default; `log_root`/
    `lock_root` default to `None`, i.e. `RevertLog`'s own real
    `~/.cache/agentm/dream/revert-log/` — a test passes a scratch dir for
    either, or injects a whole `revert_log` instance directly, exactly
    like `dream_confirm`'s own existing tests do). Ignored if `revert_log`
    is given explicitly. `batch_cap` defaults to
    `dream_confirm.DEFAULT_AUTO_APPLY_BATCH_CAP` — the global mutation
    budget the design names, which already applies across every auto-
    apply stage combined (compression + tidying), on top of this per-
    stage anomaly check; no separate cap needed for task 6.

    Returns `(digest, auto_applied_batch)`.
    """
    import dream_confirm  # noqa: E402  (lazy: keeps run_dream()'s own import graph unchanged)
    from revert_log import RevertLog  # noqa: E402

    vault_path = Path(vault_path)
    digest = run_dream(vault_path, run_id=run_id)

    if revert_log is None:
        revert_log = RevertLog(vault_path, log_root=log_root, lock_root=lock_root)
    cap = batch_cap if batch_cap is not None else dream_confirm.DEFAULT_AUTO_APPLY_BATCH_CAP

    tidying_count = sum(1 for p in digest.proposals if p.stage == "tidying")
    anomaly = dream_confirm.check_tidying_anomaly(vault_path, tidying_count)
    stages = dream_confirm.AUTO_APPLY_STAGES
    if anomaly.tripped:
        stages = frozenset(stages - {"tidying"})

    batch = dream_confirm.auto_apply_batch(vault_path, digest.run_id, revert_log, batch_cap=cap, stages=stages)

    staging_dir = vault_path / "_dream-staging" / digest.run_id
    atomic_write(
        staging_dir / "digest.md",
        _render_digest(digest, auto_applied=batch, tidying_anomaly=anomaly),
    )

    payload = dream_confirm.render_auto_applied_json(batch)
    atomic_write(staging_dir / "auto-expired.json", payload)
    atomic_write(vault_path / "_meta" / "dream-auto-expired-latest.json", payload)

    if anomaly.tripped:
        atomic_write(
            vault_path / "_meta" / "dream-anomaly-latest.json",
            json.dumps({
                "run_id": digest.run_id,
                "stage": "tidying",
                "current_count": anomaly.current_count,
                "baseline": anomaly.baseline,
                "threshold": anomaly.threshold,
            }, indent=2),
        )

    return digest, batch


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _resolve_vault_path(arg_vault_path: str | None) -> Path | None:
    import os

    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    return Path(env_path).expanduser() if env_path else None


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the thin manual /dream pass.")
    parser.add_argument("--vault-path", help="MemoryVault root (overrides MEMORY_VAULT_PATH env var)")
    parser.add_argument("--run-id", help="Override the generated run id")
    parser.add_argument(
        "--batch-cap", type=int, default=None,
        help="max compression-stage ('expire') proposals to auto-apply this run "
             "(default: dream_confirm.DEFAULT_AUTO_APPLY_BATCH_CAP)",
    )
    parser.add_argument(
        "--no-auto-apply", action="store_true",
        help="propose only (run_dream's old behavior) -- skip the confirm-free "
             "expire auto-apply step entirely; every proposal, including "
             "compression, stays pending for a manual dream_confirm.confirm() call",
    )
    parser.add_argument(
        "--log-root", default=None,
        help="override RevertLog's journal directory (default: "
             "~/.cache/agentm/dream/revert-log/, XDG_CACHE_HOME-honoring). "
             "Mainly for tests -- never needed in normal use.",
    )
    parser.add_argument(
        "--lock-root", default=None,
        help="override the revert-log's lock directory (default: vault_lock's "
             "own default). Mainly for tests -- never needed in normal use.",
    )
    args = parser.parse_args(argv)

    vault = _resolve_vault_path(args.vault_path)
    if vault is None or not vault.exists():
        print("ERROR: no vault path resolved (set --vault-path or MEMORY_VAULT_PATH)", file=sys.stderr)
        return 1

    if args.no_auto_apply:
        digest = run_dream(vault, run_id=args.run_id)
        print(
            f"dream run {digest.run_id}: {len(digest.proposals)} proposal(s), "
            f"{len(digest.insight_candidates)} insight candidate(s) — digest at {digest.digest_path} "
            "(--no-auto-apply: nothing auto-applied, all proposals pending)"
        )
        return 0

    digest, batch = run_dream_and_auto_apply(
        vault, run_id=args.run_id, batch_cap=args.batch_cap,
        log_root=args.log_root, lock_root=args.lock_root,
    )
    print(
        f"dream run {digest.run_id}: {len(digest.proposals)} proposal(s), "
        f"{len(digest.insight_candidates)} insight candidate(s), "
        f"{len(batch.items)} auto-applied (expire) — digest at {digest.digest_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
