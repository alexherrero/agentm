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
> **LAUNCHED (lifted 2026-06-24, AG Phase 3; originally approved 2026-06-21).** Abbreviated-design child — the Personas pillar, parent [agentm HLD](agentm-hld.md); follows the abbreviated-design template. Operator-approved after the edit pass + technical additions, and **re-approved 2026-06-27** after the discriminator-fold review pass. Two `[PENDING-IMPL]` markers await implementation (documenter); `status: launched` (lifted into tracked `wiki/designs/` 2026-06-24, AG Phase 3).

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

`brain` is a **pseudo-persona that sits under every other persona**, giving each the memory it uses to act (recall at the start, reflection at the end). Every other persona is, in effect, **Memory + a stance** — which is why even a bare agentm "has a persona."

### Persona, tool, and the retired "role"

The persona-vs-tool discriminator is **cross-capability judgment**: does the thing make a call that spans *more than one* capability, leaning on a named opinion to make it? If yes, it is a candidate persona; if it does one thing and makes no such judgment, it is a tool/skill/capability that belongs in crickets. The crickets read-only helpers (`explorer`, `evaluator`) are tools; `github-projects` is a tool even though it composes `developer-workflows` (composition alone is not the test); the stance-bearing coordinators are personas.

Underneath the judgment is a **mechanical floor**: a persona hard-requires only the substrate (`requires:` ⊆ agentm `scripts/`) and composes everything else softly (`enhances:`) — the **inverted dependency direction**, where a persona composes capabilities yet is depended on by nothing. A thing that must hard-require a crickets capability is a capability that depends up, not a persona. §The persona gate enforces this floor.

**Classifying a new standing concern X, in order:**
1. **Cross-capability judgment?** No → a tool/skill/capability; home it in crickets. Yes → a candidate persona; continue.
2. **Substrate-only hard deps?** If X must hard-require a crickets capability, re-home it in crickets; otherwise continue.
3. **Place it** as `kind: persona` in agentm `personas/`; the gate validates it.

![Classifying a new standing concern: ask cross-capability judgment (spans more than one capability, leans on an opinion) — the false branch routes to a tool/capability homed in crickets; the true branch drops to the mechanical floor (hard-requires only substrate), where a hard-require on crickets routes back to crickets as a capability that depends up, and substrate-only deps make it a persona filed in agentm personas/; two axes — 'remembers' and 'crosses more than one plugin' — are shown as rejected discriminators](diagrams/agentm-persona-discriminator.svg)

The test is a **human call** made when a file is placed in `personas/` — nothing queries it at runtime, unlike the opinion and capability resolvers a running tool calls. The only enforcement is the build-time gate (`check-personas.py`, run by `check-all.sh` + CI — see §The persona gate), which catches the mechanical floor; the judgment stays the author's, with the one-sentence test and the worked examples as the safety net. Two axes are explicitly *not* the test: "it remembers" (the Planner is stateless) and "it crosses multiple plugins" (`github-projects` does, yet is a capability).

**The vocabulary it sits in.** A **skill** is a capability — the thing a persona composes. A **sub-agent** is read-only ephemeral fan-out; a persona may be *surfaced through* one but is a durable classification, not a sub-agent. **Role** is retired — a role *is* a persona; what looks like a role is a crickets package of correlated tools named after the persona that wields them (the crickets-side rename is still pending). The **homing rule**: capability-local opinion (how `/work` gates) stays in the crickets capability; cross-capability opinion (what order to merge) is what the persona arbitrates, homed in agentm.

**Resist the persona-zoo:** add the next persona only when a real cross-capability concern with no single-plugin home appears.

### Adoption — how a persona is put on

The persona is the *who*; how it's put on is a separate axis, with two paths.

**Explicitly launched** in a mode the persona declares (the same persona, defined once, runs in any mode it supports):

**Automatically adopted** — put on when the work calls for it: by a **crickets workflow** (the work phase wears the Engineer, the review phase the Reviewer), or by **detection** (a design question surfaces → the Designer; a failure to diagnose → the Troubleshooter). This is what makes personas feel native — you don't summon the Reviewer; the review phase wears it. A manifest can declare which automatic triggers it answers to.

**Adoption pathways** — personas run in one of the following modes.
- **Sub-agent** — dispatched, scoped, returns a result. Best for read-only fan-out or a bounded task. *(A sub-agent launch can run **cold** — with fresh context even though the persona is stateful; the Reviewer must, for adversarial independence.)*
- **Interactive session** — a session *wearing the hat*; you converse with it in that stance. Best for design/architecture and diagnosis.
- **Loop** — runs its job on a cadence (`/loop`). Best for coordination and upkeep.
- **Goal** — given a goal, works autonomously toward it on the host's run (Claude's `/goal`, Antigravity's Agent Manager), under the [goal contract](agentm-goal-contract). Best for open-ended pursuit.

Not every persona supports every mode — For example, the Reviewer is sub-agent-only (cold independence), the Operator is loop/sub-agent, the Architect is interactive/goal. The declared set is part of the definition.

### The manifest — what a persona declares

![How a persona is composed: a manifest (stance + enhances + opinions + modes/triggers) composes crickets tools via capability_resolver and leans on opinions, standing on Memory; invoked manually (sub-agent/interactive/loop/goal) or automatically (workflow step / detection)](diagrams/agentm-persona-composition.svg)

A persona is a file in `personas/` (`kind: persona`) — a light manifest plus a prose body. The manifest is what `check-personas.py` validates and what the runtime composes from. It declares:

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

**Invoked.** The two paths from §Adoption are realized by the **activation plumbing** — how a manifest's stance + composition reach a *running* agent: injected context for an interactive session, a dispatched sub-agent (cold or warm), a scheduled `/loop`, or a host-run goal (the [goal contract](agentm-goal-contract)). **Automatic** adoption fires when a crickets workflow step, or a mid-conversation detection cue, matches a manifest trigger. *(This plumbing is the unbuilt core: today only `brain` + the `team-coordinator` Planner seed run; the resolver, the gate, and the `kind: persona` primitive are built — the dispatch/detection wiring is design.)* **`[PENDING-IMPL]`** — refresh the built-vs-designed line and cite the dispatch/detection implementation once the activation plumbing ships (documenter).

### The persona gate

`check-personas.py` keeps the tier honest at build time: **`requires ⊆ substrate`** (a persona hard-requires only substrate, never a crickets capability — composition is the soft `enhances:` path) and **no always-load** (a persona carries no always-load weight). These keep a persona from quietly becoming a layer everything depends on, and keep it cheap. The gate is build-time *enforcement* of the manifest contract — the contract itself is defined in the [persona-tier design](persona-tier.md), not here.

## Dependencies

- **Composes crickets tools by name** through the soft `enhances:` path — the runtime resolver is agentm's `capability_resolver.py` (the [composition design](https://github.com/alexherrero/crickets/wiki/crickets-composition)); when a named tool is absent the persona degrades gracefully and still works on a bare agentm.
- **Leans on** the [Opinions](agentm-opinions-and-gates.md) surfaces (the "Leans on" column) — *how* a persona retrieves an opinion is the request-by-name registry there (designed, not built).
- **Points up at** the [agentm HLD](agentm-hld.md) §Personas; **builds on** `wiki/designs/persona-tier.md` (the locked tier design — the inverted-dependency tier).

## The roster

*(Confirmed roster. The brain (memory) and the Planner seed run today; the others are designed as personas but not yet authored as manifests — each persona's design is the capabilities it composes, linked in the Composes column.)*

| Persona | Stance (the judgment it makes) | Composes | Leans on | Modes | Tier |
|---|---|---|---|---|---|
| *Memory* (pseudo) | keep the record true, beneath all | [the memory engine](agentm-memory-system.md) | — | always-on | T0–T1 |
| **Planner** (TPM) | turn intent into a plan and a board | [development-lifecycle](https://github.com/alexherrero/crickets/wiki/crickets-development-lifecycle) (plan) · [github-projects](https://github.com/alexherrero/crickets/wiki/crickets-github-projects) · [persona-tier](persona-tier.md) (first real persona) | how we engineer | loop · sub-agent | T2–T3 |
| **Architect** | shape the broad picture *across* systems | [design/architecture](https://github.com/alexherrero/crickets/wiki/crickets-design) · [research](https://github.com/alexherrero/crickets/wiki/crickets-research) | how we engineer | interactive · goal | T3 |
| **Designer** | design a *single* system in depth | [design](https://github.com/alexherrero/crickets/wiki/crickets-design) · [research](https://github.com/alexherrero/crickets/wiki/crickets-research) | how we engineer | interactive · goal | T2 |
| **Tech-Lead** | hold the technical bar across the work | [development-lifecycle](https://github.com/alexherrero/crickets/wiki/crickets-development-lifecycle) · [code-review](https://github.com/alexherrero/crickets/wiki/crickets-code-review) | done · good | interactive · sub-agent | T2 |
| **Engineer** (worker) | build the thing | [development-lifecycle](https://github.com/alexherrero/crickets/wiki/crickets-development-lifecycle) (work) · [code-review](https://github.com/alexherrero/crickets/wiki/crickets-code-review) | done · efficient | goal · interactive | T1 |
| **Reviewer** | assume it's broken, find the flaw | [code-review](https://github.com/alexherrero/crickets/wiki/crickets-code-review) (adversarial) | good | sub-agent | T4 |
| **Operator** | run things and report — no changes | [queue-status](https://github.com/alexherrero/crickets/wiki/crickets-development-lifecycle) · [maintenance](https://github.com/alexherrero/crickets/wiki/crickets-maintenance) (read) | — | loop · sub-agent | T0–T1 |
| **Troubleshooter / SRE** | diagnose failures in complex systems | [code-review](https://github.com/alexherrero/crickets/wiki/crickets-code-review) · [maintenance](https://github.com/alexherrero/crickets/wiki/crickets-maintenance) · [diagnostics](https://github.com/alexherrero/crickets/wiki/crickets-diagnostics) | good · how we engineer | interactive · sub-agent | T3 |
| **Researcher** | go learn what we don't know | [research](https://github.com/alexherrero/crickets/wiki/crickets-research) · [forward-experience](agentm-experience-and-dreaming.md) | what's worth knowing | goal · loop | T4 |
| **Maintainer** | keep the house clean (deps, docs, drift) | [maintenance](https://github.com/alexherrero/crickets/wiki/crickets-maintenance) · [wiki](https://github.com/alexherrero/crickets/wiki/crickets-wiki) | done | loop | T1 |

- **Architect vs. Designer — scope is the differentiator.** Architect zooms *out* (the broad picture across systems); Designer zooms *in* (one system). They are the top two rungs of the [sizing ladder](agentm-opinions-and-gates.md) — plan → design (Designer) → architecture (Architect).
- **Operator** is change-free (observe + report), distinct from the **Maintainer** (makes upkeep changes).
- **Tech-Lead** holds the bar and is the one most likely to *dispatch* others as sub-agents; the **Engineer** builds.
- **Tier** — the rightmost column is each persona's model+effort tier (T0…T4), bound from [model + effort routing](agentm-model-effort-routing.md). A mode-spanning persona escalates one tier for a harder sub-task (e.g. the Planner: planning at T2 → a roadmap call at T3).

## Risks & open questions

- **Mostly unbuilt — not unspecified.** Today only `brain` (the memory engine, with the full [memory system](agentm-memory-system.md) design beneath it) and `team-coordinator` (the Planner seed — the chief-of-staff / TPM, designed in [github-projects](https://github.com/alexherrero/crickets/wiki/crickets-github-projects) + [persona-tier](persona-tier.md)) run as manifests. The other roster personas are specified by the capability + contract designs they compose (linked in the roster), but not yet authored as `kind: persona` manifests. What is genuinely *designed-not-built* is the wiring: the **launch-mode activation plumbing** (how a stance reaches a running agent), the **automatic-detection triggers**, and the **opinion-retrieval wiring**.
- **Re-audit triggers:** confirm the roster as personas ship; flip the adoption plumbing to as-built as it lands; reconcile `Coordinator-Roles.md` + the crickets agent-defs at the role-retirement landing.

## References

- `personas/` — `rememberer.md` (the pseudo-persona, renaming to `brain.md`), `team-coordinator.md` (today's Planner seed)
- `scripts/check-personas.py` — the `requires ⊆ substrate` + no-always-load gate
- `scripts/capability_resolver.py` — the `enhances:` runtime resolver a persona composes through
- [persona-tier](persona-tier.md) (the locked tier design — reconciled to point here for the discriminator)
- design-doc §4 (classification spine) + §9.6 (persona-vs-role — **resolved here**)

## Amendment log

- **2026-06-28 — lock-down sweep (operator review).** Dropped the three `former ADR 0016` citations (body · Dependencies · References) — the persona-tier design holds that decision in its own amendment log (item-3 hygiene). Everything else was already compliant from the 06-27 re-promote (both SVGs sized, log newest-first). Locked as a v5–v8 guidepost.
- **2026-06-27 — re-review compression (operator).** The 2026-06-26 discriminator fold over-weighted this abbreviated design (~40% of the body) and triplicated the gate. Compressed the discriminator to its core — the live test, the in-order classification, the decision-tree SVG, and a tightened note that the test is a human call enforced only by the build-time gate (§The persona gate stays the single home for the gate mechanics; `persona-tier` holds the rest of the detail). Fixed three nits: `(shape: persona)` → `(kind: persona)` in §manifest; the References seed file named `rememberer.md` (renaming to `brain.md`, pending); the Goal launch-mode qualified as riding the host's run (the [goal contract](agentm-goal-contract)), since agentm ships no `/goal` command. No content lost.
- **2026-06-26 — made this the canonical home of the persona/tool discriminator.** Expanded the discriminator section into the full test: the live test (cross-capability judgment), the mechanical floor (the inverted dependency direction / `requires:` ⊆ substrate, gate-enforced), the in-order classification rule + a decision-tree diagram, **how the check runs** (`check-personas.py` is a build-time lint run by `check-all.sh` + CI — not a runtime resolver; the judgment test is the author's call at placement), the two rejected near-miss axes, the surrounding vocabulary (skill · sub-agent · role · opinion · substrate), the homing rule, and resist-the-persona-zoo. [persona-tier](persona-tier) is reconciled to point here (closing its one-directional "arbitrates" supersession); the HLD, the design-doc spine, and persona-activation point here for the rule. *Re-audit:* land the role-retirement rename in crickets; do the manifest `rememberer`→`brain` rename.
- **2026-06-24 — added the `tier:` model+effort axis (pointer to the routing design).** A persona now declares a **`tier:`** (model × reasoning-effort, T0…T4) — a manifest field + a Tier column on the roster — bound from the new cross-cutting [model + effort routing](agentm-model-effort-routing.md) design (which owns the scale, the Claude/Gemini equivalents, and the enforcement). One-way: personas declare the tier, the routing design defines the scale; no scale/rationale is duplicated here. **Re-audit trigger:** wire the `tier:` field into `check-personas.py` + the activation plumbing (apply the tier at adoption) when the routing design is built.
- **2026-06-23 — roster reconciled to two capability renames (propagation).** The "Leans on" column updated for renames that landed elsewhere: `developer-workflows` → `development-lifecycle` (the lifecycle merge) and `github-ci` → `maintenance` (the maintenance reframe). No model change. Maintainer leans on `maintenance` (its executing arm); Troubleshooter/SRE on `maintenance` + `diagnostics` (repair + diagnose).
- **2026-06-21 — operator edit pass + technical additions.** Operator rewrote the Objective/Overview (plain-definition opener) and restructured. Added (additive, no rewrites of operator prose): the **manifest** section (the fields `check-personas.py` validates) + a **composition diagram** + the **composed/invoked mechanics** (activation plumbing) between Adoption and the gate; linked the gate to its source contract (`persona-tier.md` / ADR 0016). **Moved the roster** to a top-level section between Dependencies and Risks. Added two **`[PENDING-IMPL]`** placeholders (the schema direct-link; the dispatch/detection built-vs-designed line) per the new pending-implementation-placeholder rule. **Re-audit trigger:** the documenter resolves the `[PENDING-IMPL]` markers when the manifest schema is finalized + the activation plumbing ships.
- **2026-06-21 — conformed to the abbreviated-design template.** Reshaped into the canonical rungs (Context · Design · Dependencies · Risks & open questions · References · Amendment log) + added the design frontmatter; folded the model/memory/discriminator/adoption/roster/gate under Design; no content lost.
- **2026-06-21 — voice + completeness pass.** Confirmed the pillar is at the right altitude (round-4 had already made it complete); made the designed-vs-built line explicit.
- **2026-06-20 — persona/agent design pass (review round 4).** Refined the pillar into the full model: a persona **declares** stance + composition + Opinions + launch modes; **Memory** = the pseudo-persona beneath all; **retired "role"** (role *is* a persona; crickets = tools + packages); discriminator = cross-capability judgment; drafted the roster. Resolves design-doc §9.6.
- **2026-06-20 — seeded from the agentm HLD.** Migrated the Personas-pillar detail out of the parent. *(Superseded by the round-4 pass.)*
