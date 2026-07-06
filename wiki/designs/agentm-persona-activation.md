---
title: persona activation — design
status: launched
kind: design
scope: feature
area: agentm/personas
governs:
  - scripts/persona_resolve.py
  - scripts/persona_compile.py
parent: agentm-hld.md
seeded: 2026-06-26
approved: 2026-06-26
---

> **Persona activation is how agentm puts on a persona — the Engineer, the Reviewer, the Planner — and gives it the right model, standards, and tools for the job.** **Built 2026-07-06** (`scripts/persona_resolve.py` + `scripts/persona_compile.py`) — parent [agentm HLD](agentm-hld).

# AgentM Persona Activation Design

## Objective

A persona is a hat the agent wears for a job — the Engineer, the Reviewer, the Planner — each borrowing tools from the toolbox without owning them. The [persona tier](persona-tier) design already built the parts a persona is made of: the persona file format, the check that validates it, the always-on **brain** (the memory engine that sits under every persona), and a first Planner persona. The step that puts a persona on while the agent is running — picking it, loading it, and giving it its model, standards, and tools — is this design, and it shipped 2026-07-06.

**As-built:** `persona_resolve.py`'s `adopt(name, mode)` runs select → gate → load → resolve-bindings → compose (steps 1-5 of the six below); `persona_compile.py` + `install.sh`'s persona-walk realize step 6 (the per-host launch) for the sub-agent mode. The Planner persona can now actually be activated. Several neighboring designs still lean on pieces of their own: [model + effort routing](agentm-model-effort-routing)'s own tier-scale build is separate (this design's `tier:` binding calls its declared resolver contract regardless), and automatic switching stays research-open (below).

## Overview

Activation is a short pipeline with one job — turn a named persona into a running one — built to the shape of the resolvers agentm already has: pure, one-way, and graceful when a binding is absent.

![The activation pipeline: a persona is selected (explicit invocation or a workflow step; auto-detection is research-open), passes the check-personas gate, loads its manifest on demand, resolves its three bindings through their own resolvers (tier via model-effort routing, opinions via opinion_resolve, capabilities via capability_resolver), composes stance + tools + Memory beneath, and runs in its mode; on the sub-agent path it compiles down to a host agent-def or SKILL.md wrap](diagrams/agentm-persona-activation.svg)

*A persona is selected, gated, loaded on demand, has its three bindings resolved through their own resolvers, composed with Memory beneath, and run in its mode. On the sub-agent path it compiles down to the host's native dispatch.*

## Design

### Selection — three paths, two ship

A persona is chosen for a task one of three ways. v1 ships the first two; the third stays research-open (operator ruling).

- **Explicit invocation.** The operator launches a named persona in a mode it declares — a session wearing the hat, a `/loop`, a `/goal` runner, or a dispatched sub-agent. Fully tractable; the v1 floor.
- **Workflow-step adoption.** A crickets phase command puts the fitting persona on for its step — `/work` wears the Engineer, `/review` wears the Reviewer. This rides the phase command: the phase spec names the persona to adopt, so it is deterministic and makes adoption feel native.
- **Auto-detection from the task** *(research-open)*. A mid-conversation cue selects a persona — a design question surfaces the Designer, a failing diagnosis the Troubleshooter. Reliable mid-conversation intent classification to swap stance is an unsolved problem: there is no trigger grammar, confidence gate, false-switch ceiling, or per-turn cost budget for it yet. The design carries the cue as the `triggers:` field and binds it only on the sub-agent path, where the host does the routing; automatic in-session stance-swap stays research-open.

### Selection policy

Five calls settle how selection behaves once more than the happy path is in play:

- **The phase spec is the source of truth for a workflow step.** A crickets phase names the persona to wear; a manifest's `triggers:` field feeds only the sub-agent routing path, so the same field never becomes a second, competing selector.
- **An explicit choice wins.** Precedence runs explicit invocation > workflow-step > auto-detection, so an operator's chosen stance is never silently overridden and auto-detection is the lowest tier. A workflow-step adoption on the sub-agent path is per-dispatch — a cold Reviewer dispatched by `/review` does not contend with the stance worn in the interactive session.
- **There is no default opinionated persona.** When nothing is selected, the working stance is the brain alone (Memory plus the bare agent); wearing an opinionated persona is always an explicit or workflow-step act.
- **Operator-initiated switching ships; automatic switching does not.** Re-invoking adoption in a session re-runs the pipeline and swaps the stance — an explicit operator act. Only cue-driven automatic switching stays research-open, because the hard part is reading intent, not the swap itself.
- **One opinionated stance at a time, with the brain beneath.** An agent wears one stance plus the brain; a persona that needs another — a Tech-Lead needing a Reviewer — dispatches it as a sub-agent. Stacking two stances would make the `tier:` and `opinions:` bindings ambiguous and break single-threaded coherence.

### Adoption — the pipeline

`adopt(persona, mode)` runs six steps:

1. **Select** — resolve the persona name to its manifest in `personas/` (an explicit name, or the persona the phase step declares).
2. **Gate** — the manifest must pass `check-personas` (its `requires:` resolve to substrate). A manifest that fails the gate is never adopted.
3. **Load on demand** — read the manifest body now, reusing the on-demand memory-load path. `brain` is always-on through the recall hook; every other persona is dormant until adopted.
4. **Resolve bindings** — fold the three axes through their resolvers (below).
5. **Compose** — the stance (the manifest body) + the resolved tools (from `enhances:`) + **Memory beneath** become the working persona. An absent `enhances:` tool degrades to absence; the persona still runs on a bare agentm.
6. **Run** — realize the mode: injected context for an interactive session, a dispatched sub-agent (cold or warm), a scheduled `/loop`, or a `/goal` runner. The Reviewer runs **cold** on the sub-agent path, for adversarial independence.

**Memory beneath is automatic.** Every persona is Memory plus a stance — recall at the start, reflect at the end — so the brain composes under every adoption as a built-in pipeline step. The manifest carries no Memory axis; the pipeline supplies it.

### The manifest — five built fields, four new axes

A persona stays an ordinary agent-def: an opinionated prose body, an optional `model:` and read-only `tools:`, and its declared deps. The gate validates three invariants today (`kind`, `requires:` ⊆ substrate, no always-load); activation adds four declared axes, each wired into the gate as part of this build.

```yaml
---
kind: persona            # the primitive — gate-checked (must be "persona")
name: reviewer           # the dispatch key / filename
requires: [...]          # hard deps — gate-checked: must resolve to substrate
enhances: [code-review]  # soft composition — any capability; an unmet one degrades
always_load: false       # the gate rejects always_load: true for every persona
# --- added by activation ---
tier: T2                 # T0 Mechanical … T4 Deep — declared here, the scale owned by model-effort routing
opinions: [good]         # the Opinion surfaces it leans on — resolved via opinion_resolve
modes: [sub-agent, interactive]   # the launch modes it supports — subset of {sub-agent, interactive, loop, goal}
triggers: [review-phase] # automatic-adoption signals — a workflow-step name, or a cue compiled to the host description
---
The stance — the opinionated prompt body.
```

The persona's working **state** is not in the manifest; it comes from Memory beneath.

### Binding resolution — through their own resolvers, one-way

Each binding resolves through the resolver that owns it; activation applies the result and never reaches back. All three resolvers are pure and never raise, so a missing binding degrades gracefully and adoption still completes.

- **`tier:`** → the [model + effort routing](agentm-model-effort-routing) scale (defined in that design, not in agent memory). The tiers are a five-rung model-and-effort ladder: **T0 Mechanical** (cheapest model, low effort — rote edits, log-scraping), **T1 Execute** (`opusplan`, medium effort — long autonomous build stretches), **T2 Author** (strongest model, high effort — planning, design, holding the technical bar), **T3 Architect** (strongest, max effort — cross-system design and roadmap calls), and **T4 Deep** (strongest, max effort plus orchestration — research and adversarial audit). Activation resolves the persona's declared tier to a concrete model + effort and applies it; the persona declares its tier, the routing design owns the scale.
- **`opinions:`** → `opinion_resolve(name)` from the [opinion registry](agentm-opinion-registry), folding the coded base ⊕ the learned supplement, always-latest. The persona-adoption seam is exactly the consumer the registry named as activation-blocked.
- **`enhances:` / `requires:`** → `capability_resolver.py`. `requires:` is hard (gate-floored to substrate); `enhances:` is soft (absent → graceful degrade).

The arrows run one way — persona → opinions, persona → capabilities, persona → tier scale. Nothing depends back on the persona.

### How it launches on each host

Activation uses each host's own way of launching an agent, so it adds no per-host machinery of its own. The work splits two ways (**hybrid**): for a dispatched sub-agent, the installer turns each persona into a host-native agent definition at install time, so the host's built-in routing picks it up; for an interactive session, a `/loop`, or a `/goal` run, a persona is put on at run time by injecting it into the session. A small, pure helper — `persona_resolve`, modeled on `capability_resolver` and `opinion_resolve` — does the install-time conversion.

- **Claude Code** — the sub-agent path compiles a persona to an agent-def: `name` → the Task tool's `subagent_type`, `triggers:` → the `description` (the host's auto-route trigger, which the host enforces), `tier:` → the `model:` + effort frontmatter, the `enhances:`-resolved tools → the `tools:` allowlist. The interactive, loop, and goal modes ride injected context + slash commands, where model choice is advisory only (a host can nudge `/model`). The always-on `brain` recall fires through hooks.
- **Antigravity** — no first-class sub-agent slot, so a persona compiles to a `SKILL.md` wrap (the existing sub-agent-as-skill pattern); the parent agent reasons over the skill description and delegates through the runtime `start_subagent` tool. Hooks have no Antigravity surface, so the brain's hook-driven recall is Claude-Code-only.

`install.sh` learns to dispatch `personas/` — today its agent dispatch walks `harness/agents/` only. The per-host consequence to state plainly: detection-via-`triggers:` is enforceable on the sub-agent/description path on both hosts; in-session stance-swap is advisory on both.

### Enforcement

Extend `check-personas.py` (the built gate — exit 0/1/2, in `check-all.sh` + CI across Linux/macOS/Windows) to validate the four new axes without breaking the three built invariants:

- `modes:` ⊆ `{sub-agent, interactive, loop, goal}`; `tier:` ∈ `{T0…T4}`; `triggers:` well-formed (a workflow-step name or a cue string); `opinions:` entries are strings.
- **Shape only for `opinions:` and `enhances:`** — resolution stays a runtime, graceful concern (mirroring `opinion_available`), so an unmet binding degrades at runtime and never fails the build.
- Keep the **one-way-arrow invariant**: a persona's `requires:` stays ⊆ substrate — this is what keeps agentm from hard-depending on crickets.
- Keep the **no-always-load invariant**: the gate rejects `always_load: true` for every persona; `brain` is always-on through the session-start recall hook.

### The boundary

- **vs the [opinion registry](agentm-opinion-registry)** — the registry owns `opinion_resolve` (base ⊕ supplement); activation calls it at adoption to bind a persona's `opinions:`. The registry's CLI + composition-edge consumers work before activation exists; only its persona-axis consumers wait on this.
- **vs the capability resolver** — `capability_resolver.py` owns name → installed-capability matching; activation composes the resolved tools into the running persona.
- **vs [model + effort routing](agentm-model-effort-routing)** — that design owns the T0–T4 scale + per-host rendering; activation applies the declared tier at adoption.
- **vs [persona tier](persona-tier)** — that design owns the primitive and the gate (the persona-vs-tool discriminator is owned by [personas](agentm-personas)); this design is its build-part 3 (load-on-demand + the surfacing/activation path), the one part V5-11 did not ship.
- **vs the host dispatch** — the host owns `Task` / `start_subagent` + description-routing; activation compiles a persona down to the host's agent-def or `SKILL.md` and rides those rails. A persona is a layer above a host sub-agent — surfaced through one on the cold-dispatch path, while remaining a durable classification that outlives any single dispatch.

## Dependencies

- **calls the three resolvers** — `capability_resolver.py` (built), plus the [opinion registry](agentm-opinion-registry)'s `opinion_resolve` and the [model + effort routing](agentm-model-effort-routing) scale (both designed, building in the same wave). All are pure, one-way, and never raise.
- **extends the built gate** — `check-personas.py` gains the four new-axis checks; the primitive + the gate stay owned by [persona tier](persona-tier).
- **rides the host dispatch** — Claude Code agent-defs (`Task` `subagent_type` + `description` routing) and Antigravity's `SKILL.md` + `start_subagent`; `install.sh` learns `personas/`.
- **composes Memory beneath** — the always-on `brain` ([memory system](agentm-memory-system)) sits under every adoption.
- **points up at** [persona tier](persona-tier) (build-part 3) and the [agentm HLD](agentm-hld) §Personas; the crickets side of the wiring is [composition](https://github.com/alexherrero/crickets/wiki/crickets-composition).

## Migrations

- **Done.** Repointed [persona tier](persona-tier)'s build-part-3 `[PENDING-IMPL]` to name this design as its specification (2026-07-06); the `persona-activation` seeding row + the real `governs:` glob are stamped in the area-taxonomy (no new area — it joins persona-tier there).
- **Partly done at build:** extended `check-personas.py` for the four axes; wrote the adoption path (`persona_resolve.py` / `adopt`); taught `install.sh` to dispatch `personas/`. **Still open:** renaming the persona file `personas/rememberer.md` → `personas/brain.md` (with its test fixture and the reference pages that name it — this design's own prose already calls it "the brain" throughout) and adding the four new axes to the two existing manifests (`rememberer`/brain, `team-coordinator`) — neither is a docs change, so neither happened in this wiki-authorship pass; both stay open code work.
- **Status honesty — done.** Persona-tier's build-part-3 and the neighbor `[PENDING-IMPL]` markers flip to as-built in this same pass. The Planner seed and the ≥4-deep board depth still stay hand-maintained until the github-projects Planner (TPM) build lands (Wave D) — activation makes the Planner *activatable*, it does not make board-depth maintenance automatic.

## Risks & open questions

- **Buildability illusion.** Activation is the third link in the github-projects ≥4-deep stack (board depth → Planner → activation → opinion registry); each dependent reads "designed" while leaning on this unbuilt seam. Until it ships, the board depth decays and is hand-maintained; no dependent should claim "done" on an unactivatable seed.
- **Auto-detection is an open design problem.** Mid-conversation stance-swap has no trigger grammar, confidence gate, false-switch ceiling, or cost budget — so it stays research-open, split from the explicit + workflow-step path; marking it `[PENDING-IMPL]` would re-introduce the hazard.
- **Per-host asymmetry.** Detection-via-`triggers:` is enforceable only on the sub-agent/description path; in-session stance-swap and model choice are advisory on both hosts. A reader should not assume symmetric enforcement in interactive sessions.
- **Coupled bet.** The roster, automatic adoption, and tier-enforcement all stand on this one seam — sequence them as one Wave-B leader feeding Wave-D adopters; none ships independently.
- **Resist the persona-zoo.** Activation makes adding a persona cheap; add the next one only when a real cross-capability concern with no single-plugin home appears.
- **Re-audit triggers:** flip persona-tier build-part-3 + the neighbor `[PENDING-IMPL]` markers at ship; design the in-session detection mechanism before promoting auto-detection off research-open; re-pin the host-seam details if a host changes its dispatch surface.

## Locked design calls

- **Selection ships explicit + workflow-step; auto-detection stays research-open** (operator ruling) — the `triggers:` field is authored and bound on the sub-agent/description path; automatic in-session stance-swap stays research-open.
- **Selection policy (operator)** — the phase spec is authoritative for a workflow step (`triggers:` feeds only sub-agent routing); precedence is explicit > workflow-step > auto-detection; no default opinionated persona (the brain alone); operator-initiated switching ships, automatic switching stays research-open; one opinionated stance at a time with the brain beneath (multi-persona by dispatch). See §Selection policy.
- **Memory beneath is automatic** — every persona is Memory + a stance; the brain composes under every adoption as a pipeline step the manifest never declares.
- **Bindings resolve through their own resolvers, one-way and never-raise** — activation applies the result; a missing binding degrades, it does not fail adoption.
- **Activation is persona-tier's build-part 3** — it owns the load-and-surface path; the primitive and the gate stay with persona-tier.
- **No per-host runtime adapter** — a persona compiles down to the host's native agent-def / `SKILL.md`; the installer learns to walk `personas/` and emit each persona in the host's form.
- **The adoption logic is hybrid** (operator ruling) — the sub-agent path compiles at install time (riding the host's description-routing); a runtime `adopt()` serves the interactive / loop / goal modes that inject context. `persona_resolve` stays pure, mirroring `capability_resolver` / `opinion_resolve`.

## References

- **The resolver precedents (built):** `scripts/capability_resolver.py` (the one-way `enhances:` resolver) · `scripts/governs_resolver.py` (the markdown-data, never-raise shape)
- **The gate to extend:** `scripts/check-personas.py` (the built persona validator — `kind` / `requires` ⊆ substrate / no-always-load)
- **The host dispatch to ride:** `harness/agents/*.md` (the agent-defs `install.sh` dispatches; `subagent_type` / `description` / `model:` are the Claude-Code dispatch form the compile step produces) · `install.sh` (the agent-dispatch path + the Antigravity `SKILL.md` wrap)
- **The seeds that run today:** `personas/brain.md` (the degenerate always-on persona) · `personas/team-coordinator.md` (the Planner (TPM) seed + its built capability scripts)
- **Up:** [persona tier](persona-tier) (build-part 3) · [personas](agentm-personas) (the roster + adoption) · [agentm HLD](agentm-hld) §Personas
- **Calls / composes:** [opinion registry](agentm-opinion-registry) · [model + effort routing](agentm-model-effort-routing) · [memory system](agentm-memory-system) (Memory beneath) · [composition](https://github.com/alexherrero/crickets/wiki/crickets-composition) (the crickets wiring)

## Amendment log

*Newest first. Collapses to one ≤2-paragraph entry at finalization; git holds the granular history.*

- **2026-07-06 — built (AG Wave B leader 4/5).** `scripts/persona_resolve.py` (the `adopt()` pipeline: select → gate → load → resolve-bindings → compose) + `scripts/persona_compile.py` (per-host launch: Claude Code agent-def, Antigravity `SKILL.md`) + `install.sh`'s persona-walk block ship. `check-personas.py` extended for the four manifest axes. `governs:` now points at both new scripts. **Design-fidelity correction found during build:** `requires:` is NOT re-resolved through `capability_resolver.py` as this design's Dependencies section originally implied — it's validated at the gate only (an agentm `scripts/` stem, ADR 0016 DC-4's existing invariant); only `enhances:` binds through the capability resolver. Two items stay open, deliberately out of this build's scope: the `rememberer.md` → `brain.md` rename, and adding the four axes to the two existing persona manifests.
- **2026-06-28 — lock-down sweep (operator review).** All standing fixes clean (diagram sized; no mermaid; no ADR mentions; log already newest-first). Confirmed the selection policy (explicit + workflow-step; auto-detection research-open) and the resolver-shape build (pure · one-way · never-raise; per-host agent-launch). No content change. Locked as a v5–v8 guidepost.

- **2026-06-26 — authored, reviewed, and finalized.** The fourth Bucket-A substrate sub-design (after runner, reporting, and the opinion registry), specifying persona-tier's build-part 3 — the step that puts a persona on at runtime: it picks a persona, loads its file on demand, sets its model + standards + tools through their own resolvers, composes the brain beneath, and runs it in its mode. Built to the resolver shape agentm already has (pure, one-way, never-raise); uses each host's own agent-launch (Claude Code agent-def + `Task`; Antigravity `SKILL.md` + `start_subagent`); extends the built `check-personas` gate for four new manifest axes (`tier:` / `opinions:` / `modes:` / `triggers:`). **Operator calls:** selection = explicit + workflow-step (auto-detection research-open) plus the five-part selection policy (phase-authoritative · explicit-wins · brain-only default · operator-switching-only · one-stance-with-the-brain-beneath); Memory beneath automatic; bindings one-way + never-raise; activation is build-part 3 (the primitive + gate stay with persona-tier); no per-host adapter; hybrid adoption (install-time compile + runtime `adopt()`); the always-on persona is named **the brain**. *Re-audit:* flip persona-tier build-part-3 + neighbor `[PENDING-IMPL]` at ship; design in-session detection before promoting auto-detection.
