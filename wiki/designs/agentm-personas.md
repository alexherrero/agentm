---
title: personas — design
status: launched
kind: design
scope: feature
area: agentm/personas
governs: [personas/, scripts/check-personas.py]
parent: agentm-hld.md
seeded: 2026-06-20
approved: 2026-06-21
---

> [!NOTE]
> **LAUNCHED (lifted 2026-06-24, AG Phase 3; originally approved 2026-06-21).** Abbreviated-design child — the Personas pillar, parent [agentm HLD](agentm-hld.md); follows the abbreviated-design template. Operator-approved after the edit pass + technical additions. Two `[PENDING-IMPL]` markers await implementation (documenter); `status: launched` (lifted into tracked `wiki/designs/` 2026-06-24, AG Phase 3).

# AgentM Personas Design

## Objective - What is a Persona?
A persona is a **stance** agentm takes for a job — it is composed of capabilities, leans on the Opinions it needs, and stands on the memory beneath it. This combination is given a name (label) for ease of use. Personas allow specialization through experience, for the same person (agentm).

## Overview — the model

A persona is defined by four things it **declares**:

1. **Its stance** — the cross-capability judgment it makes.
2. **What it composes** — the capabilities (crickets tools) it wields, named by `enhances:`. It hard-requires only the substrate.
3. **Which Opinions it leans on** — the named [Opinion surfaces](agentm-opinions-and-gates.md) it consults.
4. **Its launch modes** — the subset of run-modes that fit it (not every persona supports all four).

A persona is **stateful**, but its state isn't in the manifest — it's drawn from the memory beneath it. The manifest is the stance + composition; the experience and artifacts come from Memory.

## Design

### Memory — the pseudo-persona

`rememberer` is a **pseudo-persona that sits under every other persona**, giving each the memory it uses to act (recall at the start, reflection at the end). Every other persona is, in effect, **Memory + a stance** — which is why even a bare agentm "has a persona."

### Persona, tool, and the retired "role"

The persona-vs-tool discriminator is **cross-capability judgment**: a persona makes a call spanning more than one capability and leans on opinions to make it; a tool does one thing and makes no such judgment. For example, the crickets read-only helpers — `explorer`, `evaluator` — are **tools**; and the stance-bearing ones are personas.

There is **no separate "role" tier**. A role *is* a persona (the stateful stance, an agentm concept); crickets provides the **tools** and the **packages** that bundle them. A package named after the persona that wields it — a "worker" bundle — can look like a role, but it's only correlated tools under a name. 

### Adoption — how a persona is put on

The persona is the *who*; how it's put on is a separate axis, with two paths.

**Explicitly launched** in a mode the persona declares (the same persona, defined once, runs in any mode it supports):

**Automatically adopted** — put on when the work calls for it: by a **crickets workflow** (the work phase wears the Engineer, the review phase the Reviewer), or by **detection** (a design question surfaces → the Designer; a failure to diagnose → the Troubleshooter). This is what makes personas feel native — you don't summon the Reviewer; the review phase wears it. A manifest can declare which automatic triggers it answers to.

**Adoption pathways** — personas run in one of the following modes.
- **Sub-agent** — dispatched, scoped, returns a result. Best for read-only fan-out or a bounded task. *(A sub-agent launch can run **cold** — with fresh context even though the persona is stateful; the Reviewer must, for adversarial independence.)*
- **Interactive session** — a session *wearing the hat*; you converse with it in that stance. Best for design/architecture and diagnosis.
- **Loop** — runs its job on a cadence (`/loop`). Best for coordination and upkeep.
- **Goal** — given a goal, works autonomously toward it (`/goal`). Best for open-ended pursuit.

Not every persona supports every mode — For example, the Reviewer is sub-agent-only (cold independence), the Operator is loop/sub-agent, the Architect is interactive/goal. The declared set is part of the definition.

### The manifest — what a persona declares

![How a persona is composed: a manifest (stance + enhances + opinions + modes/triggers) composes crickets tools via capability_resolver and leans on opinions, standing on Memory; invoked manually (sub-agent/interactive/loop/goal) or automatically (workflow step / detection)](diagrams/agentm-persona-composition.svg)

A persona is a file in `personas/` (`shape: persona`) — a light manifest plus a prose body. The manifest is what `check-personas.py` validates and what the runtime composes from. It declares:

- **stance** — the cross-capability judgment, in the body (prose).
- **`enhances:`** — the capabilities (crickets tools) it composes, by name (soft).
- **`requires:`** — hard dependencies, **substrate only** (gate-enforced).
- **opinions** — the Opinion surfaces it leans on.
- **modes** — the launch modes it supports (sub-agent / interactive / loop / goal).
- **triggers** — the automatic-adoption signals it answers to (a workflow step, or a detection cue).
- **tier** — the model + reasoning-effort tier it runs at (T0 Mechanical … T4 Deep), from the [model + effort routing](agentm-model-effort-routing.md) scale. The per-persona tier is the roster's rightmost column; the scale + the Claude/Gemini equivalents + the enforcement live in that design. One-way: the persona declares its tier, the routing design owns the scale.

The state a persona acts on is **not** in the manifest — it comes from Memory beneath. *(The field schema is partly designed; the contract + quick-reference are `wiki/designs/persona-tier.md` + `wiki/reference/persona-tier-schema.md`.)* **`[PENDING-IMPL]`** — replace the path references with a direct link to the schema section once the manifest schema is finalized (documenter).

### How a persona is composed, and invoked

**Composed.** At adoption, the manifest's `enhances:` names resolve through `capability_resolver.py` (the [composition](https://github.com/alexherrero/crickets/wiki/crickets-composition) runtime): the stance, the resolved tools, and the Memory beneath compose into the working persona. A named tool that's absent degrades gracefully — the persona still runs on a bare agentm.

**Invoked.** The two paths from §Adoption are realized by the **activation plumbing** — how a manifest's stance + composition reach a *running* agent: injected context for an interactive session, a dispatched sub-agent (cold or warm), a scheduled `/loop`, or a `/goal` runner. **Automatic** adoption fires when a crickets workflow step, or a mid-conversation detection cue, matches a manifest trigger. *(This plumbing is the unbuilt core: today only `rememberer` + the `team-coordinator` Planner seed run; the resolver, the gate, and the `kind: persona` primitive are built — the dispatch/detection wiring is design.)* **`[PENDING-IMPL]`** — refresh the built-vs-designed line and cite the dispatch/detection implementation once the activation plumbing ships (documenter).

### The persona gate

`check-personas.py` keeps the tier honest at build time: **`requires ⊆ substrate`** (a persona hard-requires only substrate, never a crickets capability — composition is the soft `enhances:` path) and **no always-load** (a persona carries no always-load weight). These keep a persona from quietly becoming a layer everything depends on, and keep it cheap. The gate is build-time *enforcement* of the manifest contract — the contract itself is defined in the [persona-tier design](persona-tier.md) (which folds the former ADR 0016), not here.

## Dependencies

- **Composes crickets tools by name** through the soft `enhances:` path — the runtime resolver is agentm's `capability_resolver.py` (the [composition design](https://github.com/alexherrero/crickets/wiki/crickets-composition)); when a named tool is absent the persona degrades gracefully and still works on a bare agentm.
- **Leans on** the [Opinions](agentm-opinions-and-gates.md) surfaces (the "Leans on" column) — *how* a persona retrieves an opinion is the request-by-name registry there (designed, not built).
- **Points up at** the [agentm HLD](agentm-hld.md) §Personas; **builds on** `wiki/designs/persona-tier.md` (the locked tier design — the inverted-dependency tier; it folds the former ADR 0016).

## The roster

*(Confirmed roster; all but Memory and the Planner seed are designs today.)*

| Persona | Stance (the judgment it makes) | Composes | Leans on | Modes | Tier |
|---|---|---|---|---|---|
| *Memory* (pseudo) | keep the record true, beneath all | the memory engine | — | always-on | T0 |
| **Planner** (TPM) | turn intent into a plan and a board | development-lifecycle (plan) · github-projects | how we engineer | loop · sub-agent | T2–T3 |
| **Architect** | shape the broad picture *across* systems | design/architecture · research | how we engineer | interactive · goal | T3 |
| **Designer** | design a *single* system in depth | design · research | how we engineer | interactive · goal | T2 |
| **Tech-Lead** | hold the technical bar across the work | development-lifecycle · code-review | done · good | interactive · sub-agent | T2 |
| **Engineer** (worker) | build the thing | development-lifecycle (work) · code-review | done · efficient | goal · interactive | T1 |
| **Reviewer** | assume it's broken, find the flaw | code-review (adversarial) | good | sub-agent | T4 |
| **Operator** | run things and report — no changes | queue-status · maintenance (read) | — | loop · sub-agent | T0 |
| **Troubleshooter / SRE** | diagnose failures in complex systems | code-review · maintenance · diagnostics | good · how we engineer | interactive · sub-agent | T3 |
| **Researcher** | go learn what we don't know | research · forward-experience | what's worth knowing | goal · loop | T4 |
| **Maintainer** | keep the house clean (deps, docs, drift) | maintenance · wiki | done | loop | T1 |

- **Architect vs. Designer — scope is the differentiator.** Architect zooms *out* (the broad picture across systems); Designer zooms *in* (one system). They are the top two rungs of the [sizing ladder](agentm-opinions-and-gates.md) — plan → design (Designer) → architecture (Architect).
- **Operator** is change-free (observe + report), distinct from the **Maintainer** (makes upkeep changes).
- **Tech-Lead** holds the bar and is the one most likely to *dispatch* others as sub-agents; the **Engineer** builds.
- **Tier** — the rightmost column is each persona's model+effort tier (T0…T4), bound from [model + effort routing](agentm-model-effort-routing.md). A mode-spanning persona escalates one tier for a harder sub-task (e.g. the Planner: planning at T2 → a roadmap call at T3).

## Risks & open questions

- **Mostly designed.** Today only `rememberer` (pseudo) + `team-coordinator` (the Planner seed) exist. The full roster, the **launch-mode activation plumbing** (how a stance reaches a running agent — injected context vs. sub-agent dispatch vs. session-switching), the **automatic-detection triggers**, and the **opinion-retrieval wiring** are design.
- **Re-audit triggers:** confirm the roster as personas ship; flip the adoption plumbing to as-built as it lands; reconcile `Coordinator-Roles.md` + the crickets agent-defs at the role-retirement landing.

## References

- `personas/` — `rememberer.md` (the pseudo-persona), `team-coordinator.md` (today's Planner seed)
- `scripts/check-personas.py` — the `requires ⊆ substrate` + no-always-load gate
- `scripts/capability_resolver.py` — the `enhances:` runtime resolver a persona composes through
- [persona-tier](persona-tier.md) (the locked tier design — folds the former ADR 0016; its "arbitrates" discriminator is superseded by "cross-capability judgment")
- design-doc §4 (classification spine) + §9.6 (persona-vs-role — **resolved here**)

## Amendment log

- **2026-06-24 — added the `tier:` model+effort axis (pointer to the routing design).** A persona now declares a **`tier:`** (model × reasoning-effort, T0…T4) — a manifest field + a Tier column on the roster — bound from the new cross-cutting [model + effort routing](agentm-model-effort-routing.md) design (which owns the scale, the Claude/Gemini equivalents, and the enforcement). One-way: personas declare the tier, the routing design defines the scale; no scale/rationale is duplicated here. **Re-audit trigger:** wire the `tier:` field into `check-personas.py` + the activation plumbing (apply the tier at adoption) when the routing design is built.
- **2026-06-23 — roster reconciled to two capability renames (propagation).** The "Leans on" column updated for renames that landed elsewhere: `developer-workflows` → `development-lifecycle` (the lifecycle merge) and `github-ci` → `maintenance` (the maintenance reframe). No model change. Maintainer leans on `maintenance` (its executing arm); Troubleshooter/SRE on `maintenance` + `diagnostics` (repair + diagnose).
- **2026-06-21 — operator edit pass + technical additions.** Operator rewrote the Objective/Overview (plain-definition opener) and restructured. Added (additive, no rewrites of operator prose): the **manifest** section (the fields `check-personas.py` validates) + a **composition diagram** + the **composed/invoked mechanics** (activation plumbing) between Adoption and the gate; linked the gate to its source contract (`persona-tier.md` / ADR 0016). **Moved the roster** to a top-level section between Dependencies and Risks. Added two **`[PENDING-IMPL]`** placeholders (the schema direct-link; the dispatch/detection built-vs-designed line) per the new pending-implementation-placeholder rule. **Re-audit trigger:** the documenter resolves the `[PENDING-IMPL]` markers when the manifest schema is finalized + the activation plumbing ships.
- **2026-06-21 — conformed to the abbreviated-design template.** Reshaped into the canonical rungs (Context · Design · Dependencies · Risks & open questions · References · Amendment log) + added the design frontmatter; folded the model/memory/discriminator/adoption/roster/gate under Design; no content lost.
- **2026-06-21 — voice + completeness pass.** Confirmed the pillar is at the right altitude (round-4 had already made it complete); made the designed-vs-built line explicit.
- **2026-06-20 — persona/agent design pass (review round 4).** Refined the pillar into the full model: a persona **declares** stance + composition + Opinions + launch modes; **Memory** = the pseudo-persona beneath all; **retired "role"** (role *is* a persona; crickets = tools + packages); discriminator = cross-capability judgment; drafted the roster. Resolves design-doc §9.6.
- **2026-06-20 — seeded from the agentm HLD.** Migrated the Personas-pillar detail out of the parent. *(Superseded by the round-4 pass.)*
