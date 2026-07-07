---
title: opinions — design
status: launched
kind: design
scope: feature
area: agentm/opinions
parent: agentm-hld.md
seeded: 2026-06-20
approved: 2026-06-21
---

> [!NOTE]
> **LAUNCHED (lifted 2026-06-24, AG Phase 3; originally approved 2026-06-21) · locked 2026-06-28 (final AG design sweep).** child-design — the Opinions pillar, parent [agentm HLD](agentm-hld.md). The compose-and-serve path this pillar left `[PENDING-IMPL]` **shipped 2026-07-06** — see the [opinion registry](agentm-opinion-registry) design, which governs `opinion_resolver.py`; `status: launched` (lifted into tracked `wiki/designs/` 2026-06-24, AG Phase 3).

# AgentM Opinions Design

## Objective

An opinion is **opinionated knowledge agentm holds, named so any tool can ask for it** — a standard for **how to work**. It answers a general question: what does *done*, *good*, or *efficient* mean? What does our engineering process look like? Holding the standard once, by name, buys three things:

- quality gets **checked**, not just asserted;
- one standard serves every caller;
- the standard sharpens over time across agents and tools, without refactoring.

## Overview

An opinion is deliberately **abstract** — knowledge that carries the standard; a capability asks for it by name and acts on what comes back.

![An opinion is a coded base (in agentm code, checked-in) folded with a vault supplement (learned) into a composite served by name; Experience writes the supplement; the named opinions today are done, good, efficient, how-we-engineer](diagrams/agentm-opinion-surfaces.svg)

Two things follow from keeping opinions abstract:

- **One opinion serves many tools.** The same *good* is asked for whether `/review`, a persona, or a future tool asks — defined once, named once.
- **An opinion sharpens without touching a tool.** Improve the standard and every caller that asks for it gets the better standard for free — this is where **Experience** feeds in over time (the [Experience design](agentm-experience-and-dreaming.md)).

Opinions are a **queryable knowledge surface** — a tool names what it needs and the substrate serves it.

## Design

Opinions are what make agentm **opinionated**: a standard for things like *done* / *good* / *efficient* / *how-we-engineer* (the full catalog is below) that the agent **ships with, in code**, and then **grows in agentm's memory as it learns**. The design folds this into existing seams — a coded base, a vault supplement, composed on request and served by name — rather than standing up a new store or recall path. The named opinions are listed at the end; this section is the system that holds and serves them.

### Where opinions live: a coded base, extended in agentm's memory

- **The base is in agentm's code, checked in.** Each opinion ships as a coded default — the standard the agent starts with. This is *why agentm is opinionated out of the box*: it already holds a view of what *done* / *good* / *efficient* / *how-we-engineer* mean. The base changes only by a check-in — it's the durable seed, the same for every install.
- **agentm's memory extends it — and this is the part that learns.** A supplement layer in **agentm's memory — whichever storage backend the agent is connected to (device-local or the vault), through the storage seam** — holds what the agent has *added* to a base opinion over time (an `opinions/` area beside the always-load conventions). **Experience** (reflection + scheduled learning) writes here; the agent never rewrites the coded base. This layer already exists in spirit — the learned conventions in `personal/_always-load/` are exactly this kind of stored, learned supplement to a coded standard.

### How a tool gets one: the composite

On request, agentm **folds the coded base ⊕ the vault supplement into one composite** and serves that. The tool gets the seed plus everything learned since, as a single opinion — it never sees the two layers, and a bare install (base only) or a seasoned one (base + a rich supplement) is served the same way.

Three things agentm already has carry this, so there is nothing new to invent:
- **By-name lookup** rides the seam crickets already uses to reach agentm — the one-way capability bridge (`find_capability.py` → `capability_resolver.py`); a thin `opinion` lookup rides the same path.
- **The base ⊕ overlay fold** is the pattern agentm's style system already uses — a base guide composed with a learned overlay (`style_resolver.py`); opinions compose the same way.
- **The supplement's storage, recall, and learning** is the memory engine ([Memory System](agentm-memory-system.md)).

**Built (2026-07-06).** The `opinion` lookup + the base ⊕ supplement fold ship in `opinion_resolver.py` (see the [opinion registry](agentm-opinion-registry) design) — `opinion_resolve(name)` returns `served` / `base-only` / `no-opinion` / `error`, never raising. The nine coded bases ship as `opinions/<name>.md` stubs; the wirings that let a tool call `opinion_resolve` instead of its own hardwired copy flip one at a time as each consumer's own slice builds (the Opinion slice, design-doc Forward plan, Phase 3 → 4).

### The opinions today

The named opinions, listed like capabilities — what each holds, who asks for it, and just enough to fix its shape. The standard itself lives in the opinion entry, not here, and the set is **open** (these are today's).

| Opinion | What it holds | Serves | Shape |
|---|---|---|---|
| **done** | a completeness checklist | `/work`, `/release`, conventions gates | the check battery + the written conventions — *is it finished?* |
| **good** | a quality standard | `/review`, `code-review` | the adversarial-review contract — *does it survive a hostile read?* |
| **efficient** | a cost budget with a quality floor | `token-audit`, model routing, `/work` | cheap as the job allows, held above the *good* floor |
| **how we engineer** | the process discipline | `/plan`, `/work`, `design`, `/bugfix` | the phase discipline · the bugfix track · the plan → design → architecture sizing ladder |
| **recoverable** | the reversibility doctrine | `/work`, `/release`, `/bugfix`, the push gate | classify each action recoverable / unrecoverable — proceed on the recoverable, stop on the unrecoverable; *can it be undone?* (standard lives in `developer-safety`) |
| **private** | a leak floor | `development-lifecycle` finalize, CI, `diagnostics` | secrets + PII stay out of what's committed — *is it safe to commit / share?* (deterministic floor lives in `privacy`) |
| **ready** | a launch-readiness gate | `/launch` | metrics + alerts + a tested rollback + a flag off-switch + a staged rollout — *is it ready to ship to real users?* |
| **simple** | the simplest-thing-that-works standard | `/simplify`, `maintenance` | Chesterton's Fence + the Rule of 500 — *is any of this accidental complexity?* |
| **worth-knowing** | a relevance bar | `research`, Experience, the Researcher persona | *is this worth remembering, researching, or surfacing?* |

*(The phase discipline is agentm's; the phase commands are crickets' — the discipline-vs-tools split; see Dependencies. The full standard behind each opinion lives in its entry. **`efficient`'s model-routing lever is specified in [model + effort routing](agentm-model-effort-routing.md)** — the model × effort tier scale + persona→tier map that turns "cheap as the job allows" into a concrete model + effort pick.)*

### How the supplement grows: the accumulate loop — spec landed, implementation deferred

**Condensed twin of the fuller section in the [Experience design](agentm-experience-and-dreaming.md#the-experience--opinions-accumulate-loop--spec-landed-implementation-deferred)** (`PLAN-wave-e-experience` task 3, 2026-07-07, operator-approved go/no-go; landed verbatim from `ACCUMULATE-LOOP-SPEC-DRAFT.md`). The one-sentence design: the accumulate loop is the style-learning loop generalized from voice to standards — the same edit-driven, operator-gated capture the wiki system already proved, with a recurrence gate in front and the coded base as an unoverridable floor behind.

- **Route, don't invent.** No new pipeline — a routing rule inside the existing capture paths (reflection, edit-driven capture, the watchlist) targets an opinion supplement when a candidate is standard-shaped. Deterministic-first classification; the LLM assists only at MEDIUM confidence.
- **The signal → opinion map:** `/review` corrections → `good` · gate/battery misses → `done` · token-audit findings → `efficient` · process/retro lessons → `how-we-engineer` · recoverability incidents → `recoverable` · PII findings → `private`. Voice/prose lessons are explicitly excluded — they stay in the style overlay, never double-captured here.
- **Three anti-corruption guards:** a recurrence gate (two distinct sessions before auto-append; one occurrence parks in the opinion's inbox lane); extend-never-override (a supplement that contradicts its base surfaces as a proposed base change, never serves silently — the coded base stays authoritative); provenance-or-it-didn't-happen (every entry carries session/commit/incident refs and a supersedes chain, tracked by a dashboard supplement-health check).
- **Cadence:** capture continuous, promotion recurrence-gated or operator-confirmed (never time-scheduled), maintenance delegated to dreaming's whole-corpus pass — same wave, named owner, no second mechanism.

**`[PENDING-IMPL]`** — the routing rule, the signal map, and the three guards are all unbuilt; this is the spec a follow-on plan implements against. No change to the compose-and-serve model above — the accumulate loop is entirely how the supplement *fills*, not how it's served.

## Dependencies

- **crickets touches by request, not by wiring.** A tool names the opinion it needs and runs its implementation: `/review` asks for *good* (runs the adversarial pass); `/release` and `/work` ask for *done* (run the check battery); any tool can ask for *efficient* or a process opinion. The crickets side of the wiring is the [composition design](https://github.com/alexherrero/crickets/wiki/crickets-composition).
- **Personas lean on opinions** — the "Leans on" column of the [Personas design](agentm-personas.md) names which surface each persona consults.
- **Experience feeds back** — reflection + scheduled learning sharpen the surfaces over time (the [Experience design](agentm-experience-and-dreaming.md)).
- **Points up at** the [agentm HLD](agentm-hld.md) §Opinions. The [V5 unbundling](agentm-hld.md) — phase commands moved to crickets — is why agentm owns the discipline while crickets owns the phase tools.

## Risks & open questions

- **The compose-and-serve path shipped 2026-07-06.** The coded bases are addressable opinions (`opinions/*.md` stubs), the stored supplement layer folds on request through `opinion_resolver.py` (a resolver pattern mirroring `governs_resolver.py` — pure, one-way, never-raise). That code is specified by, and governed by, the [opinion registry](agentm-opinion-registry) design; this pillar stays discipline/area-only. **What's left:** each hardwired consumer (`code-review` embedding *good*, etc.) still flips to calling `opinion_resolve` one at a time as its own slice builds — the registry existing doesn't retrofit every caller at once.
- **Opinion versioning** — when a standard shifts (a new check joins the *done* battery), how do callers that cached the old standard adapt? Open.
- **The Experience → Opinions accumulate loop is now specified; the implementation is deferred.** `PLAN-wave-e-experience` task 3 landed the routing rule, the signal→opinion map, and the three anti-corruption guards (see the section above; full version in the [Experience design](agentm-experience-and-dreaming.md)). No code implements it yet — a follow-on plan builds the routing, the map, and the guards.
- **Re-audit triggers:** flip the request-by-name API to as-built when the registry ships; flip the accumulate loop's `[PENDING-IMPL]` once the follow-on implementation plan lands.

## References

- **Coded bases (in agentm / its tools today):** `AGENTS.md` + `harness/principles.md` (conventions + engineering discipline) · `scripts/check-all.sh` + `wiki/reference/CI-Gates.md` (the *done* battery) · crickets `code-review` + `wiki/explanation/Why-Adversarial-Review.md` (the *good* contract) · `~/.claude/CLAUDE.md` opusplan + `heat_policy.py` (the *efficient* levers) · crickets `developer-workflows` (*how we engineer*)
- **Stored supplement (the learned layer):** agentm's memory — whichever backend it's connected to (device-local or the vault, via the seam); e.g. the learned conventions in `personal/_always-load/` (`docs-prose-style.md`)
- **The base ⊕ overlay precedent:** crickets `wiki-maintenance` `diataxis-author` — `style_resolver.py` composing `style/base-style-guide.md` with a learned overlay; opinions reuse this compose shape
- **The by-name seam:** `find_capability.py` → `capability_resolver.py` — the one-way bridge a thin `opinion` lookup rides
- **The accumulate loop's full spec:** the [Experience design](agentm-experience-and-dreaming.md) § The Experience → Opinions accumulate loop; original source draft at vault `_harness/designs/architecture-governance/ACCUMULATE-LOOP-SPEC-DRAFT.md`

## Amendment log

**2026-07-07 — the Experience → Opinions accumulate loop spec landed, condensed twin, design amendment only (`PLAN-wave-e-experience` task 3, SPEC-FIRST).** Lands the condensed twin of `ACCUMULATE-LOOP-SPEC-DRAFT.md`'s contract here (full version in the [Experience design](agentm-experience-and-dreaming.md), the amendment's primary home) — the operator explicitly approved a go/no-go on the draft before this landing. New subsection "How the supplement grows: the accumulate loop" added after the opinions table: route-don't-invent, the signal→opinion map, the three anti-corruption guards (recurrence gate, extend-never-override, provenance-or-it-didn't-happen), and the cadence (continuous capture, gated promotion, maintenance delegated to dreaming's whole-corpus pass — no second mechanism). Landed verbatim, no redesign. **No code ships in this task** — the routing rule, signal map, and guards are all `[PENDING-IMPL]`, deferred to a follow-on plan. This closes the "designed, not specified" gap the 2026-06-21/2026-06-24 entries below both named as open. *Re-audit trigger:* flip this note's `[PENDING-IMPL]` once the follow-on implementation plan lands.

**2026-07-06 — compose-and-serve `[PENDING-IMPL]` flipped to as-built (AG Wave B leader 2/5).** The request-by-name registry this pillar left open ships in `opinion_resolver.py` + `agentm-opinion.sh`, governed by the [opinion registry](agentm-opinion-registry) design. This pillar's own content (the nine-opinion catalog, the "opinions today" table) needed no change — it already named all nine as of the 2026-06-26 amendment. What's left is per-consumer: each hardwired tool flips to `opinion_resolve` one at a time. *Re-audit trigger:* note when the last hardwired consumer flips.

**2026-06-28 — lock-down sweep (operator review).** Sized the diagram (`width`/`height`); confirmed the nine-opinion catalog + the request-by-name model. Log already newest-first. Locked as a v5–v8 guidepost.

**2026-06-26 — catalog expanded to nine; the resolver mechanism homed in its own design.** The opinions catalog grows from four to nine: added *recoverable* (the reversibility doctrine, provided by `developer-safety`), *private* (the leak floor, provided by `privacy`), *ready* (the launch-readiness gate), *simple* (the simplest-thing-that-works standard), and *worth-knowing* (the relevance bar the Researcher persona leans on). `recoverable` and `private` are promoted from sub-standards folded into other opinions to peer opinions; *voice* stays a prose-style overlay in `style_resolver`, not a catalog opinion. The request-by-name mechanism this pillar left as `[PENDING-IMPL]` is now specified by the new **[opinion registry](agentm-opinion-registry)** child design, which governs `opinion_resolver.py`; this pillar stays discipline/area-only. **Re-audit trigger:** revisit the catalog when a new surface is authored; flip the compose-and-serve `[PENDING-IMPL]` to as-built when the registry ships.

**2026-06-24 — pointed `efficient`'s model-routing lever at the routing design.** The `efficient` opinion names "model routing" as a lever it backs; that lever now has a concrete design — **[model + effort routing](agentm-model-effort-routing.md)** (the model × effort tier scale + persona→tier map + the `tier:` persona-manifest axis). Added a pointer from the opinions-table footnote; one-way (the opinion names the lever, the routing design specifies it). No change to the compose-and-serve model. **Re-audit trigger:** when the request-by-name registry ships, `efficient` returns the routing policy as part of its served composite.

**2026-06-21 — authored, reviewed, and finalized.**

Migrated from the agentm HLD and reframed through operator review into the Opinions pillar: opinions are what make agentm **opinionated** — a coded base (checked-in, the seed) **extended by a learned supplement in agentm's memory** (whichever storage backend it's connected to, device-local or the vault), folded into a **composite** served to a tool **by name**. The four named opinions (done / good / efficient / how-we-engineer) are listed like capabilities — shape only; each standard lives in its own opinion. The system reuses three existing seams — the capability-resolution bridge (by-name lookup), the style system's base⊕overlay compose (`style_resolver.py`), and the memory engine (the supplement) — rather than a new registry or recall.

Content-final. The compose-and-serve path **shipped 2026-07-06** (see the [opinion registry](agentm-opinion-registry) design); `status: launched` (lifted into tracked `wiki/designs/` 2026-06-24, AG Phase 3). **Re-audit triggers:** flip each hardwired consumer to `opinion_resolve` as its own slice builds; specify the Experience → Opinions sharpening loop when forward learning lands; settle opinion versioning.
