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
> **LAUNCHED (lifted 2026-06-24, AG Phase 3; originally approved 2026-06-21).** child-design — the Experience pillar, parent [agentm HLD](agentm-hld.md); the store this lifecycle tends is the [Memory System design](agentm-memory-system.md). `status: launched` (lifted into tracked `wiki/designs/` 2026-06-24, AG Phase 3); the designed pieces (forward learning · scheduler · dreaming · crystallization) carry `[PENDING-IMPL]` markers.

# AgentM Experience & Dreaming

## Objective

Experience is **how agentm gets better over time** — it turns the agent's own past sessions, and the wider world, into sharper judgment, so the next session starts further ahead than the last. It is what makes the memory *grow* rather than just persist.

## Overview

Experience runs in **two directions**:

- **Backward** — learning from its own past. Every finished session leaves something behind that sharpens the next. **Built today.**
- **Forward** — learning from the world. On a schedule, when configured, the agent reaches approved sources, brings back ideas to improve itself, and surfaces them to you. **Largely designed.**

![Experience feeds agentm's memory from two directions — backward (reflection, built) from past sessions, forward (scheduled learning, designed) from the world — and dreaming (designed) consolidates the whole corpus into insights; the scheduler (designed) drives the scheduled passes, and the memory sharpens Opinions over time](diagrams/agentm-experience.svg)
## Design

### Backward experience

- **Reflection** *(built)*. After a session ends, a pass mines the transcript for durable entries — **classified by `kind`** (preference · workflow · fix · …; see the [Memory System](agentm-memory-system.md) for the taxonomy) — and routes each candidate by confidence into **three lanes**: **HIGH** → auto-saved; **MEDIUM** → the inbox or surfaced for review; **LOW** → the inbox to triage later. The routing *mode* is set by `MEMORY_REVIEW_MODE` (auto / silent / interactive); the confidence tiers themselves are hardcoded, not a tunable threshold. A Stop-event hook runs it at session end; an idle hook catches sessions that crashed before it could fire. Reflection also seeds **idea incubation** (a skeleton an idea-researcher sub-agent later fills).
- **Heat-based curation** *(built)*. Which memories load *every* session changes with use: a heat policy promotes an on-demand entry into the always-load set once it's been hit enough times across enough distinct sessions, and demotes cold ones — down to a safety floor, leaving pinned entries alone. Built and tested; it keeps the always-load set earning its token cost.
- **Crystallization** *(designed)*. At the close of a completed exploration, distil it into a structured digest — question · investigation · findings · lessons · open threads — instead of leaving raw transcript fragments behind. It's the phase-close counterpart to dreaming's whole-corpus pass; the digest schema is V6 work. **`[PENDING-IMPL]`** — build the phase-close crystallization trigger + the digest schema (documenter); today reflection captures fragments but doesn't yet crystallize a finished exploration.

### Forward experience — designed

Backward learning can only sharpen what the agent has already seen; forward learning brings in what it *hasn't* — a periodic, opt-in pass that reaches approved sources and surfaces what's worth knowing.

- **Approved-source learning.** On a schedule (operator-configured), the agent pulls from a list of **approved sources** — feeds, named repositories, the web — and mines them for ideas that could improve it: techniques, tools, patterns, conventions. What it finds is **surfaced to you** to accept or pass on; nothing is adopted silently. The source list and the cadence are operator-controlled — opt-in by design.
- **The import watchlist (adapt-don't-import)** is the one piece that exists today: a deterministic rubric (`adapt_skills.py`) enriches candidate external *skills*, a judge sub-agent (`adapt-evaluator`) classifies them HIGH/MEDIUM/LOW, and a review CLI (`watchlist_review.py`) lets you promote / dismiss / defer. The broader loop generalizes this same shape — find → screen → surface — beyond skills to ideas, patterns, and references.
- **The scheduler (the cron element).** Backward learning rides session events; forward learning has no session to ride, so it needs a scheduled trigger. The scheduler is also the home for other periodic upkeep (dreaming, index maintenance). It's a shared substrate primitive — the Maintainer and Researcher persona loops wait on it too.

**`[PENDING-IMPL]`** — build the approved-source pipeline + the scheduler, then flip this section to as-built (documenter); today only the import watchlist ships.

### Dreaming — designed

Memory's "sleep": a scheduled, whole-corpus consolidation pass — dedup, contradiction triage, compression, insight-generation — producing a derived insights layer. The design is locked; there's no implementation, and its own prerequisite (a revert-log primitive) is unbuilt. The planned path is a thin manual `/dream` first, scheduled later (on the scheduler above).

**`[PENDING-IMPL]`** — build the revert-log primitive, then `/dream`, then the scheduled pass; flip to as-built as each lands (documenter).

### Where the learning lands

What's learned routes into the **general memory** the same way anything else does — as **`kind`-classified entries**: a learned preference, workflow, fix, or domain-reference becomes a typed entry, classified by reflection on the way in (see the [Memory System](agentm-memory-system.md)). Two other destinations sit alongside it:

- **The idea incubator / watchlist** — an ingested idea, article, or candidate skill (forward learning) lands here **for later review**, not adopted silently.
- **The Opinion supplement** — the part that's a *standard* sharpens an opinion's learned layer over time (the *accumulate* loop — designed; the [Opinions design](agentm-opinions-and-gates.md)).

Everything routes through the existing memory engine — no new store; heat curation runs continuously over the always-load set.

## Dependencies

- **Writes to Memory** — every direction lands in the [Memory System](agentm-memory-system.md), and Experience is the **growth engine** that grows it into the interconnected knowledge base (its *How it grows* section).
- **Sharpens Opinions** — the learned supplement is what makes an opinion's vault layer grow over time (the [Opinions design](agentm-opinions-and-gates.md)); the precise *accumulate* loop is designed, not specified.
- **The scheduler is shared substrate** — once built, the Maintainer + Researcher persona loops ([Personas design](agentm-personas.md)) run on it too.
- **crickets touch is light** — the memory-engine sub-agents (`memory-idea-researcher`, `adapt-evaluator`) run read-only inside the harness; reflection mines sessions that *used* crickets tools but depends on none; forward learning reaches *out* to sources. Experience keeps working on a bare agentm.

## Risks & open questions

- **Forward learning, the scheduler, and dreaming are designed, not built** — the two directions are lopsided today (backward shipped, forward is a sketch). Marked `[PENDING-IMPL]` above.
- **The accumulate → Opinions loop is unspecified** — which experience signals sharpen which opinion, how often, and what keeps a bad signal from corrupting a standard.
- **Dreaming's prerequisite** — a revert-log primitive must exist before whole-corpus consolidation is safe to run.
- **Re-audit triggers:** flip forward-learning / scheduler / dreaming to as-built as each ships; specify the accumulate loop when forward learning lands.

## References

- `harness/skills/memory/scripts/` — `heat_policy.py` (promote/demote/pin), `reflect.py` (transcript mining), `ideas_incubator.py`, `adapt_skills.py`, `watchlist_review.py`
- `harness/hooks/` — `memory-reflect-stop`, `memory-reflect-idle` (the backward triggers)
- `harness/agents/` — `memory-idea-researcher`, `adapt-evaluator` (read-only memory-engine sub-agents)
- vault `decisions/research-dream-mode-design.md` — the locked-but-unbuilt dreaming design
- `~/.claude/CLAUDE.md` (opusplan) + the heat policy — the token-cost lever behind curation

## Amendment log

**2026-06-21 — authored, reviewed, and finalized.**

Migrated from the agentm HLD and conformed to the abbreviated-design template. Documents the Experience pillar — how agentm gets better over time — as a lifecycle in **two directions**: **backward** (reflection, `kind`-classified, tri-modal routing under `MEMORY_REVIEW_MODE`; heat-based curation — both built) and **forward** (scheduled, opt-in approved-source + deep-research learning, surfaced never adopted silently — designed; only the import watchlist ships), plus **dreaming** (whole-corpus consolidation, gated on a revert-log) and **crystallization** (phase-close distillation) — both designed. What's learned lands in three places: the general `kind`-classified memory, the idea incubator/watchlist, and the Opinion supplement.

Operator-reviewed across several rounds and grounded against the live code before finalization (which corrected a misnamed routing knob to `MEMORY_REVIEW_MODE` and confirmed every built-vs-designed call). Named Experience as the **growth engine** that grows the Memory System into an interconnected knowledge base. **Designed-for / `[PENDING-IMPL]`:** the approved-source pipeline, the scheduler, dreaming + its revert-log prerequisite, and crystallization. **Re-audit triggers:** flip each to as-built as it ships; specify the Experience → Opinions accumulate loop when forward learning lands.
