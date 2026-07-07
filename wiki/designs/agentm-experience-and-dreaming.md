---
title: experience — design
status: launched
kind: design
scope: feature
area: agentm/experience
governs: [harness/skills/memory/scripts/heat_policy.py, harness/skills/memory/scripts/reflect.py, harness/hooks/]
parent: agentm-hld.md
seeded: 2026-06-20
approved: 2026-06-21
---

> [!NOTE]
> **LAUNCHED (lifted 2026-06-24, AG Phase 3; originally approved 2026-06-21) · locked 2026-06-28 (final AG design sweep).** child-design — the Experience pillar, parent [agentm HLD](agentm-hld.md); the store this lifecycle tends is the [Memory System design](agentm-memory-system.md). `status: launched` (lifted into tracked `wiki/designs/` 2026-06-24, AG Phase 3); the designed pieces (forward learning · dreaming · crystallization) carry `[PENDING-IMPL]` markers. **The runner they'll schedule on shipped 2026-07-06** (see the [Runner design](agentm-runner.md)) — this pillar's own consumers are still unbuilt on their own merits, not blocked on the runner's existence.

# AgentM Experience & Dreaming

## Objective

Experience is **how agentm gets better over time** — it turns the agent's own past sessions, and the wider world, into sharper judgment, so the next session starts further ahead than the last. It is what makes the memory *grow* rather than just persist.

## Overview

Experience runs in **two directions**:

- **Backward** — learning from its own past. Every finished session leaves something behind that sharpens the next. **Built today.**
- **Forward** — learning from the world. On a schedule, when configured, the agent reaches approved sources, brings back ideas to improve itself, and surfaces them to you. **Largely designed.**

![Experience feeds agentm's memory from two directions — backward (reflection, built) from past sessions, forward (scheduled learning, designed) from the world — and dreaming (designed) consolidates the whole corpus into insights; the runner (built) drives the scheduled passes, and the memory sharpens Opinions over time](diagrams/agentm-experience.svg)
## Design

### Backward experience

- **Reflection** *(built)*. After a session ends, a pass mines the transcript for durable entries, each **classified by `kind`** (preference · workflow · fix · …; see the [Memory System](agentm-memory-system.md) for the taxonomy). It routes each candidate by confidence into **three lanes**: **HIGH** → auto-saved; **MEDIUM** → the inbox or surfaced for review; **LOW** → the inbox to triage later. The routing *mode* is set by `MEMORY_REVIEW_MODE` (auto / silent / interactive); the confidence tiers themselves are hardcoded, not a tunable threshold. A Stop-event hook runs it at session end; an idle hook catches sessions that crashed before it could fire. Reflection also seeds **idea incubation** (a skeleton an idea-researcher sub-agent later fills).
- **Heat-based curation** *(built)*. Which memories load *every* session changes with use: a heat policy promotes an on-demand entry into the always-load set once it's been hit enough times across enough distinct sessions, and demotes cold ones — down to a safety floor, leaving pinned entries alone. Built and tested; it keeps the always-load set earning its token cost.
- **Crystallization** *(designed)*. At the close of a completed exploration, distil it into a structured digest — question · investigation · findings · lessons · open threads — instead of leaving raw transcript fragments behind. It's the phase-close counterpart to dreaming's whole-corpus pass; the digest schema is V6 work. **`[PENDING-IMPL]`** — build the phase-close crystallization trigger + the digest schema (documenter); today reflection captures fragments but doesn't yet crystallize a finished exploration.

### Forward experience — designed

Backward learning can only sharpen what the agent has already seen; forward learning brings in what it *hasn't* — a periodic, opt-in pass that reaches approved sources and surfaces what's worth knowing.

- **Approved-source learning.** On a schedule (operator-configured), the agent pulls from a list of **approved sources** — feeds, named repositories, the web — and mines them for ideas that could improve it: techniques, tools, patterns, conventions. What it finds is **surfaced to you** to accept or pass on; nothing is adopted silently. The source list and the cadence are operator-controlled — opt-in by design.
- **The import watchlist (adapt-don't-import)** is the one piece that exists today: a deterministic rubric (`adapt_skills.py`) enriches candidate external *skills*, a judge sub-agent (`adapt-evaluator`) classifies them HIGH/MEDIUM/LOW, and a review CLI (`watchlist_review.py`) lets you promote / dismiss / defer. The broader loop generalizes this same shape — find → screen → surface — beyond skills to ideas, patterns, and references.
- **The runner (the background-job executor) — built 2026-07-06.** Backward learning rides session events; forward learning has no session to ride, so it runs on the **runner** — fired by the host's scheduler (see the [Runner design](agentm-runner.md)). The runner is also the home for other periodic upkeep (dreaming, index maintenance). It's a shared substrate primitive — the Maintainer and Researcher persona loops wait on it too.

**`[PENDING-IMPL]`** — the runner it needs now exists; build the approved-source pipeline itself, then flip this section to as-built (documenter). Today only the import watchlist ships.

### Dreaming — designed

Memory's "sleep": a whole-corpus consolidation pass — dedup, contradiction triage, compression, insight-generation — producing a derived insights layer. The design is locked. Its prerequisite, the revert-log primitive, exists (`harness/skills/memory/scripts/revert_log.py`, built 2026-07-07), and the pipeline itself now has a thin, operator-invoked implementation: `harness/skills/memory/scripts/dream.py` (`run_dream(vault_path, *, run_id=None) -> DreamDigest`, built 2026-07-07). It runs dedup (stdlib `difflib`, threshold 0.92), contradiction triage (same-`slug`-differing-body, advisory only), and compression (`supersedes:`-chain compaction, never deleting a source) as **proposal-only** stages — no source file is mutated by a run — and writes every disposition to a staged digest (`_dream-staging/<run_id>/digest.md`) carrying a prospective revert pointer for the confirm step to apply. Insight-generation is the one stage that writes directly, always as an additive `status: candidate` file under `_dream/insights/<run_id>.md`, per the design's own carve-out for non-source-touching writes. What's still missing: the confirm step that actually calls `revert_log` to apply a proposal (task 3), and the scheduled, unattended pass that runs this on the runner instead of by hand (task 4) — until those land, `/dream` is manual-invoke-only and every disposition stays proposed, not applied.

**`[PENDING-IMPL]`** — the revert-log primitive and the thin manual `/dream` pipeline now exist; build the confirm step (wiring `/dream`'s proposals through `revert_log`) and the scheduled runner pass, then flip this section to as-built (documenter).

### Where the learning lands

What's learned routes into the **general memory** the same way anything else does — as **`kind`-classified entries**: a learned preference, workflow, fix, or domain-reference becomes a typed entry, classified by reflection on the way in (see the [Memory System](agentm-memory-system.md)). Two other destinations sit alongside it:

- **The idea incubator / watchlist** — an ingested idea, article, or candidate skill (forward learning) lands here **for later review**, not adopted silently.
- **The Opinion supplement** — the part that's a *standard* sharpens an opinion's learned layer over time (the *accumulate* loop — designed; the [Opinions design](agentm-opinions-and-gates.md)).

Everything routes through the existing memory engine — no new store; heat curation runs continuously over the always-load set.

## Dependencies

- **Writes to Memory** — every direction lands in the [Memory System](agentm-memory-system.md), and Experience is the **growth engine** that grows it into the interconnected knowledge base (its *How it grows* section).
- **Sharpens Opinions** — the learned supplement is what makes an opinion's vault layer grow over time (the [Opinions design](agentm-opinions-and-gates.md)); the precise *accumulate* loop is designed, not specified.
- **The runner is shared substrate, built 2026-07-06** — the Maintainer + Researcher persona loops ([Personas design](agentm-personas.md)) run on it too, once each of those is built.
- **crickets touch is light** — the memory-engine sub-agents (`memory-idea-researcher`, `adapt-evaluator`) run read-only inside the harness; reflection mines sessions that *used* crickets tools but depends on none; forward learning reaches *out* to sources. Experience keeps working on a bare agentm.

## Risks & open questions

- **Forward learning and dreaming are designed, not built** — the two directions are lopsided today (backward shipped, forward is a sketch). Marked `[PENDING-IMPL]` above. **The runner they'll schedule on is built (2026-07-06)** — no longer part of this gap.
- **The accumulate → Opinions loop is unspecified** — which experience signals sharpen which opinion, how often, and what keeps a bad signal from corrupting a standard.
- **Dreaming's prerequisite and its thin manual pipeline are now both built** — the revert-log primitive (`harness/skills/memory/scripts/revert_log.py`, task 1) and manual `/dream` (`harness/skills/memory/scripts/dream.py`, task 2) both ship; `/dream`'s proposals aren't wired to `revert_log` yet, so the remaining gap is the confirm step (task 3) and the scheduled pass built on top of it (task 4).
- **Re-audit triggers:** flip forward-learning / dreaming to as-built as each ships; specify the accumulate loop when forward learning lands.

## References

- `harness/skills/memory/scripts/` — `heat_policy.py` (promote/demote/pin), `reflect.py` (transcript mining), `ideas_incubator.py`, `adapt_skills.py`, `watchlist_review.py`, `revert_log.py` (dreaming's append-only undo journal; tested by `scripts/test_revert_log.py`), `dream.py` (thin manual `/dream` — proposal-only dedup/contradiction-triage/compression + direct insight-candidate writes; tested by `scripts/test_dream.py`)
- `harness/hooks/` — `memory-reflect-stop`, `memory-reflect-idle` (the backward triggers)
- `harness/agents/` — `memory-idea-researcher`, `adapt-evaluator` (read-only memory-engine sub-agents)
- vault `decisions/research-dream-mode-design.md` — the locked-but-unbuilt dreaming design
- `~/.claude/CLAUDE.md` (opusplan) + the heat policy — the token-cost lever behind curation

## Amendment log

**2026-07-07 — thin manual `/dream` landed (`PLAN-wave-e-dreaming` task 2).** `harness/skills/memory/scripts/dream.py` ships a one-shot, operator-invoked run of the dream pipeline (`run_dream(vault_path, *, run_id=None) -> DreamDigest`): corpus stats → dedup → contradiction triage → compression → crystallization → insight-generation → qualification → digest+staging, tested by `scripts/test_dream.py` (12 passing tests). Dedup, contradiction-triage, and compression only **propose** dispositions — a `Proposal` dataclass listing stage/kind/paths/summary/mutations — and never write to an existing entry; every proposal lands in a staged digest (`_dream-staging/<run_id>/digest.md`) with a prospective revert pointer for a later confirm step (task 3, not yet built) to apply through task 1's `RevertLog`. Insight-generation is the sole exception, writing new `status: candidate` files directly to `_dream/insights/<run_id>.md` — additive, non-source-touching, so it doesn't need staging per the design's own carve-out. This is a deliberately thin, calibration-era pass: dedup uses `difflib` text-similarity (not embeddings), contradiction triage is a same-`slug`-differing-body check with zero auto-resolution, compression only compacts `supersedes:`-chains of length ≥ 3 and never deletes a superseded file, and qualification defaults every insight's `rung` to `retrieval`. Both dreaming prerequisites now exist (the revert-log primitive and the manual pipeline); what remains before Dreaming can flip to as-built is the confirm step that wires `/dream`'s proposals through `revert_log` (task 3) and the scheduled runner pass (task 4). *Re-audit trigger:* flip Dreaming's `[PENDING-IMPL]` to as-built once tasks 3 and 4 ship.

**2026-07-07 — dreaming's prerequisite landed: the revert-log primitive (`PLAN-wave-e-dreaming` task 1).** `harness/skills/memory/scripts/revert_log.py` ships an append-only, per-run undo journal for the dreaming pipeline's content-touching writes (dedup/merge/supersede/compress), tested by `scripts/test_revert_log.py` (11 passing tests). Public API: `RevertLog(vault_path, *, log_root=None, lock_root=None, timeout=10.0, stale=10.0)`, `RevertLog.record_and_apply(run_id, stage, mutations) -> entry_id`, `RevertLog.revert(run_id, entry_id=None)`. The journal lives on a local, non-synced path (`~/.cache/agentm/dream/revert-log/<run_id>.jsonl`, `XDG_CACHE_HOME`-honoring) — never in the synced vault — and reuses the existing `vault_lock.py` primitives rather than inventing new locking. This is only the prerequisite: the Dreaming section's `[PENDING-IMPL]` marker stays as-is, since `/dream` and the scheduled consolidation pass are still unbuilt. *Re-audit trigger:* flip Dreaming's `[PENDING-IMPL]` to as-built once `/dream` and the scheduled pass ship on top of this primitive.

**2026-07-06 — the runner's own `[PENDING-IMPL]` flipped to as-built (see the [Runner design](agentm-runner.md)).** Reconciled this pillar's prose to match: removed "runner" from the shared "designed, not built" framing (the LAUNCHED note, the diagram caption, the runner bullet, Risks, the re-audit triggers) since it no longer applies — forward learning and dreaming remain designed, not built, on their own merits, not because the runner they'd schedule on is missing. This is the re-audit the 2026-06-28 entry below named.

**2026-06-28 — lock-down sweep (operator review).** Sized the diagram (`width`/`height`); confirmed the content (two-directional learning · the built-vs-designed split · the growth-engine framing) and the newest-first log. Locked as a v5–v8 guidepost. *(The deferred memoryvault split will later fold its reflection-sidecar, idea-ledger, crash-recovery-marker, and internet-skill-discovery units in here — see MEMORYVAULT-SPLIT-MAP.)*

**2026-06-28 — scheduler → runner rename (Bucket-B / critique W1).** Renamed the agentm background-job primitive **scheduler → runner** through the current-state prose — the LAUNCHED note, the diagram caption, the cron bullet, Risks, and the re-audit triggers — and pointed it at the new [Runner design](agentm-runner.md), which now owns it; the host's scheduler stays the cron that fires it. The historical 2026-06-21 entry keeps its original wording. *Re-audit trigger:* flip the runner's `[PENDING-IMPL]` to as-built when it ships.

**2026-06-21 — authored, reviewed, and finalized.**

Migrated from the agentm HLD and conformed to the abbreviated-design template. Documents the Experience pillar — how agentm gets better over time — as a lifecycle in **two directions**: **backward** (reflection, `kind`-classified, tri-modal routing under `MEMORY_REVIEW_MODE`; heat-based curation — both built) and **forward** (scheduled, opt-in approved-source + deep-research learning, surfaced never adopted silently — designed; only the import watchlist ships), plus **dreaming** (whole-corpus consolidation, gated on a revert-log) and **crystallization** (phase-close distillation) — both designed. What's learned lands in three places: the general `kind`-classified memory, the idea incubator/watchlist, and the Opinion supplement.

Operator-reviewed across several rounds and grounded against the live code before finalization (which corrected a misnamed routing knob to `MEMORY_REVIEW_MODE` and confirmed every built-vs-designed call). Named Experience as the **growth engine** that grows the Memory System into an interconnected knowledge base. **Designed-for / `[PENDING-IMPL]`:** the approved-source pipeline, the scheduler, dreaming + its revert-log prerequisite, and crystallization. **Re-audit triggers:** flip each to as-built as it ships; specify the Experience → Opinions accumulate loop when forward learning lands.
