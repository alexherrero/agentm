#!/usr/bin/env python3
"""consolidate.py — V6-4 consolidation: episodic -> semantic tier transitions.

PLAN-wave-e-v6-index task 7 (agentm-memory-index.md). FABLE-confirmed ADAPT
call for V6-4 (design-doc.md § v6-25-external-thinking-audit touch-point
row): adopt the 4-tier taxonomy (episodic/semantic/procedural + the
directional invariant — promotion only flows episodic -> semantic ->
procedural, never backward), reject LLM-judge promotion in favor of
deterministic RECURRENCE-GATED promotion (ROADMAP-AgentMemoryV6.md V6-4:
"promote when the same fact appears in N episodes, not time-gated").

Recurrence signal (deterministic, zero-LLM, reusing task 4's own output
rather than inventing a second mechanism): V6-2's typed-edge graph already
extracts, per episodic-tier entry, which other entries/concepts it
references. When the SAME target is referenced by >= MIN_RECURRENCE
distinct episodic entries, that recurrence — not a time window, not an
LLM's judgment — is the promotion trigger.

Hard prerequisites (this task wires to them; it does not rebuild either):
  - The revert-log primitive (`revert_log.py`, PLAN-wave-e-dreaming task 1,
    merged into main 2026-07-07). Every consolidation write goes through
    `RevertLog.record_and_apply` — never a direct `save_entry`/`Path.write`
    call, which would bypass the undo floor a corpus-scale mutation must
    have (FABLE-confirmed prerequisite named explicitly in this plan).
  - The crystallization digest schema (`crystallize.py`, PLAN-wave-e-
    experience task 2, merged into main 2026-07-07) — authored there,
    reused here, not redefined. A recurrence-promotion's five fields map
    onto the "completed exploration" schema by treating "what recurring
    pattern was found" as the exploration:
      question       — what recurring reference triggered this promotion
      investigation  — which episodic entries were examined
      findings       — the recurring target + how many entries agree
      lessons        — the promotion action taken (episodic -> semantic)
      open_threads   — left empty in v1 (no further judgment made)

Never-delete invariant: promotion adds a new semantic-tier entry ABOVE the
episodic sources — it never deletes, supersedes, or mutates them. The new
entry's `derived_from:` frontmatter (save.py, this task) names the sources.

Directional invariant, minimal enforcement in v1: promotion only proposes
episodic -> semantic (the ADR-confirmed direction); semantic -> procedural
is V6-16 (a separate, later, procedural-memory/skill-induction item) and is
explicitly out of scope here.

Public API:
  find_recurring_targets(vault, episodic_paths, *, min_recurrence=MIN_RECURRENCE)
      -> dict[str, list[str]]
  consolidate_target(vault, revert_log, run_id, target, source_paths, *,
                      group="personal") -> str  # returns the revert-log entry_id
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import graph  # noqa: E402
import save  # noqa: E402
from crystallize import CrystallizationDigest, DIGEST_KIND, _render_body  # noqa: E402

# Recurrence floor: the same target must be referenced by at least this many
# distinct episodic entries before promotion is considered. 3 is a named,
# undogmatic v0 default (no source parameterizes a specific N) — a
# calibration-era judgment call, same posture as chunking's window size and
# the decay half-life; open to recalibration once real corpus growth exists.
MIN_RECURRENCE = 3


def find_recurring_targets(
    vault: Path,
    episodic_paths: list[str],
    *,
    min_recurrence: int = MIN_RECURRENCE,
) -> dict[str, list[str]]:
    """Deterministic recurrence detection over V6-2's typed-edge output.

    Extracts edges from every path in `episodic_paths` (task 4's
    `graph.extract_edges_for_paths`) and groups them by target. A target
    referenced by `min_recurrence` or more DISTINCT source entries is a
    promotion candidate.

    Returns {target: [sorted source_paths]} for qualifying targets only —
    empty dict if nothing recurs enough to qualify. Deterministic: same
    inputs always produce the same output (sorted source lists), no
    randomness, no LLM call anywhere in this path.
    """
    edges = graph.extract_edges_for_paths(vault, episodic_paths)
    by_target: dict[str, set[str]] = {}
    for e in edges:
        if not e.is_edge:
            continue
        by_target.setdefault(e.target, set()).add(e.source_path)

    return {
        target: sorted(sources)
        for target, sources in by_target.items()
        if len(sources) >= min_recurrence
    }


def _consolidated_slug(target: str) -> str:
    """Deterministic slug for a target's consolidated semantic entry —
    stable across runs so re-running consolidation on the same target
    collides (a `save_entry`-shaped FileExistsError) rather than silently
    duplicating, matching the existing kind-collision contract."""
    stem = Path(target).stem
    safe = "".join(c if c.isalnum() or c == "-" else "-" for c in stem.lower())
    safe = "-".join(filter(None, safe.split("-")))
    return f"consolidated-{safe}"


def consolidate_target(
    vault: Path,
    revert_log,
    run_id: str,
    target: str,
    source_paths: list[str],
    *,
    group: str = "personal",
) -> str:
    """Promote a recurring `target` to a semantic-tier consolidated entry.

    Writes THROUGH `revert_log.record_and_apply` — the only path this
    module ever uses to touch the vault. Never calls `save_entry` or any
    other direct-write helper (those write outside the revert-log's
    journal, which this task's own verification explicitly forbids for
    every consolidation write).

    The new entry: `kind: crystallized` (reusing task 2's shared digest
    schema, not redefining it), `lifecycle_tier: durable` (a consolidated
    semantic-tier fact is durable — decay-exempt, per tension #6's
    authority-by-volatility projection: durable conventions read as
    authoritative), `derived_from: [source_paths]` (the provenance edge;
    sources are never deleted or modified). Returns the revert-log's
    `entry_id` for this promotion (so it can be reverted as one stage).

    Raises ValueError if `source_paths` doesn't meet `MIN_RECURRENCE` — a
    defensive check independent of the caller having already filtered via
    `find_recurring_targets`, so this function is safe to call directly
    without relying on caller discipline.
    """
    if len(source_paths) < MIN_RECURRENCE:
        raise ValueError(
            f"consolidate_target requires >= {MIN_RECURRENCE} sources for "
            f"target {target!r}, got {len(source_paths)}"
        )

    slug = _consolidated_slug(target)
    digest = CrystallizationDigest(
        question=f"What recurring reference to {target!r} appears across episodic entries?",
        investigation=(
            f"{len(source_paths)} episodic entries reference {target!r}:\n"
            + "\n".join(f"- {p}" for p in source_paths)
        ),
        findings=(
            f"{target!r} recurs across {len(source_paths)} distinct entries "
            f"(recurrence floor: {MIN_RECURRENCE}), a deterministic signal "
            "that this is durable, not incidental."
        ),
        lessons=(
            f"Promoted episodic -> semantic (V6-4). The consolidated entry is "
            f"durable (decay-exempt) and carries a derived_from provenance "
            f"edge back to its {len(source_paths)} sources; none of those "
            f"sources were deleted or modified."
        ),
        open_threads="",
    )
    body = _render_body(digest)

    fm = save._build_frontmatter(
        kind=DIGEST_KIND,
        group=group,
        slug=slug,
        tags=[],
        always_load=False,
        supersedes=None,
        lifecycle_tier="durable",
        derived_from=source_paths,
    )
    # NOT body.rstrip("\n") — when the last field (open_threads) is empty,
    # _render_body's trailing blank-line-then-nothing is exactly the
    # structure parse_digest's regex requires ("## Open threads\n\n" before
    # end-of-string); rstrip collapsed it to a single newline and broke the
    # round-trip (caught by this task's own digest round-trip test).
    content = fm + "\n" + body if body.endswith("\n") else fm + "\n" + body + "\n"

    target_path = Path(vault) / group / DIGEST_KIND / f"{slug}.md"
    if target_path.exists():
        raise FileExistsError(
            f"consolidated entry already exists at {target_path} — "
            "target already promoted; not overwriting"
        )

    return revert_log.record_and_apply(run_id, "consolidate", [(target_path, content)])
