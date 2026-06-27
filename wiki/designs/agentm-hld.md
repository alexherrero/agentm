---
title: AgentM — High Level Design
status: launched
seeded: 2026-06-19
approved: 2026-06-20
kind: design
scope: arc
area: agentm/architecture
governs:
  - scripts/**
succeeds: memory-os-architecture.md  # the V5 HLD, vault-archived 2026-06-24 (AG Wave 2); see References → Evolution arc
children:
  - agentm-memory-system.md
  - memory-storage-seam.md
  - agentm-experience-and-dreaming.md
  - agentm-opinions-and-gates.md
  - agentm-opinion-registry.md
  - agentm-personas.md
  - persona-tier.md
  - agentm-persona-activation.md
  - agentm-model-effort-routing.md
  - agentm-runner.md
---

> [!NOTE]
> **LAUNCHED (2026-06-20).** The live agentm parent HLD, lifted into tracked `wiki/designs/` in AG Phase 2, **succeeding** the former `memory-os-architecture.md` V5 HLD (vault-archived 2026-06-24, AG Wave 2; crickets' up-links repointed here — see References → Evolution arc). Framed around the four [Foundations](agentm-foundations-hld.md) pillars; the deep mechanics of each live in the child designs (all **content-final 2026-06-24**, lifted AG Phase 3). Built on design-doc Appendix B. Diagrams are hand-authored vector images under `diagrams/`, matching Foundations.

# AgentM — the part of the assistant that's yours

A useful assistant feels like an extension of you — it remembers, it has opinions, it knows how you like to work (the [Foundations](agentm-foundations-hld.md) make that case). agentm is the part that makes it true. It's the **person**: the stateful core that carries the memory, builds experience, forms opinions, and wears different hats for different jobs. Everything that persists lives here. The capabilities it uses to get work done — planning, building, reviewing — are tools ([crickets](https://github.com/alexherrero/crickets/wiki/crickets-hld)); agentm is who picks them up, and who remembers what happened once they're set down.

This doc is about agentm's insides: the four pillars it's built from, the components that make each one real, and how they fit together. The beliefs underneath it live up in the [Foundations](agentm-foundations-hld.md).

## What agentm is for

agentm carries the goals the whole system shares — continuity, trust, control, durability (see the [Foundations](agentm-foundations-hld.md)) — and adds one that's its own:

- **Growth** — agentm doesn't only persist, it accumulates. More memory, sharper opinions, eventually more hats to wear. The tools stay fixed; the person gets better with use.

Everything below serves that: a durable place to keep what's learned, sound judgment about how to use it, and a way to get better with every session.

## The four pillars

agentm-the-person is built from four pillars — the same four the [Foundations](agentm-foundations-hld.md) name: **Experience**, **Memory**, **Opinions**, and **Personas**. Each is an idea, and each is made real by a handful of named components. This doc names those components, shows how they fit, and flags where each touches crickets; the deep mechanics of each pillar live in a child design, linked at the end of its section so this stays readable.

The shape of it — the person and its four pillars, the tools, and the foundation they share:

![How agentm and crickets relate: the stateful person — its four pillars Experience, Memory, Opinions, Personas — running on the shared foundation, with the stateless tools drawing on Memory, following Opinions, and wielded by Personas](diagrams/agentm-relate.svg)

Each pillar, and the components that make it real:

![The four pillars and the components that make each one real: Experience (reflection, scheduled learning, heat policy, idea incubation, import watchlist, the scheduler, dreaming), Memory (memory engine, storage seam, resolution plane, backends, write protocol, recall loop, harness-state I/O, MCP server), Opinions (what done/good/efficient looks like, how we engineer), Personas (the persona tier, brain + the roster, the persona gate)](diagrams/agentm-pillars.svg)

*The four pillars and the components that make each one real. (How each pillar relates to crickets is described per pillar below.)*

### Memory — what it has learned

The durable record: everything agentm knows, kept on disk so it survives a session ending. This is the largest pillar and the ground the other three stand on. Its discipline is **one port** — every caller reaches storage the same way, through a single seam.

**Components:**
- **Memory engine** — the verbs (`save` · `recall` · `forget`) and the cross-cutting logic that lives exactly once (idempotency, content-hash CAS, soft-delete, token-budgeted recall, link integrity).
- **Storage seam** — the one port to disk: a `StorageBackend` contract, a registry, an opaque `Locator`, and a three-tier storage taxonomy (source / shared-abstracts / local-index).
- **Resolution plane** — finds the store without naming it: the config, the `vault_path()` resolver, the selector, and two independent capability mechanisms (a request-guard and an availability-query).
- **Backends** — interchangeable adapters behind the seam: **device-local** (agentm's default) and **obsidian-vault** (a crickets plugin — the live vault). The seam is open: **more backends can be added** by implementing the same contract (a different store, or a different sync layer), with nothing above the seam changing.
- **Write protocol** — concurrency-safe writes: atomic-write + a per-vault mutex + content-hash compare-and-swap, coordinated by primitives with no daemon.
- **Recall loop** — two hooks (session-start always-load + a per-prompt five-step search), token-budgeted, over a device-local vector index.
- **Harness-state I/O** — plan/progress/feature state, backend-aware, reaching disk the same way memory does.
- **MCP server** — an opt-in inbound adapter so external clients can reach the same engine.

**How they fit:** everything points inward to the seam — engine → resolution → seam → backend — and nothing in the substrate depends on a backend or a tool below it.

**Where it touches crickets:** the `obsidian-vault` backend ships as a crickets plugin, depending one-way *up* on the seam; harness-state is shared with crickets' phase tools; and the availability-query is the runtime half of crickets' `enhances:` composition.

*Detail — the seam contract, the write protocol, the recall loop, the storage-serving layers, the memory layers, and the V5-14 as-built/target gap — in the [Memory System design](agentm-memory-system.md).*

### Experience — what's worked before, and what's worth knowing

How the person gets better, in two directions. **Backward:** it learns from its own past — every finished session leaves something behind. **Forward:** on a schedule (when configured), it goes out and learns from the world — approved sources, feeds, the web — and surfaces what's worth knowing back to you.

**Components:**
- **Reflection** *(backward)* — mines a finished session's transcript for durable preferences, workflows, and fixes (a `reflect.py` engine + a Stop-event hook + an idle-recovery hook).
- **Scheduled learning** *(forward — largely designed)* — a periodic, opt-in pass that pulls from approved sources (RSS, the web, named repos) to mine ideas for improving the agent, then surfaces them to you to accept or pass on. The **import watchlist** (adapt-don't-import: a rubric plus a judge sub-agent) is one element of this — the part that screens external *skills* worth borrowing.
- **The scheduler** *(the cron element)* — what lets forward learning, and other upkeep, run on a schedule rather than only as in-session hooks.
- **Heat policy** — curates which memories load every session, promoting frequently-hit ones and demoting cold ones.
- **Idea incubation** — captures a half-formed idea as a skeleton a researcher sub-agent later fills.
- **Dreaming** *(designed, not built)* — a scheduled whole-corpus consolidation pass; the design is locked, the build is still ahead.

**How they fit:** backward learning runs as session hooks (reflection → Memory / incubation / watchlist); forward learning runs on the scheduler (out to approved sources → surfaced to you); heat curation keeps the always-load set lean; dreaming (future) would consolidate the whole corpus.

**Where it touches crickets:** lightly — the sub-agents run inside the harness, and forward learning reaches *out* to sources rather than into crickets. The Experience pillar keeps working on a bare agentm.

*Detail — backward vs. forward learning, the scheduler, the approved-source pipeline, the import watchlist, the heat thresholds, incubation, and the full dream-mode design — in the [Experience & Dreaming design](agentm-experience-and-dreaming.md).*

### Opinions — how things should go

agentm's opinions are **abstract, named buckets of opinionated knowledge** — what good work looks like, captured once and given a name. They stay deliberately abstract: an opinion doesn't reach into any tool. A capability **asks for an opinion by name** and uses it to inform what it does. One opinion can serve many tools, and an opinion can sharpen over time without touching a single tool.

**The surfaces** — the named opinions agentm holds today, or means to:
- **What "done" looks like** — completeness: *is the work actually finished?* The most deterministic surface; it confirms against a checklist. Its implementation is the **check battery** — the gates that must pass — plus the written conventions for shape.
- **What "good" looks like** — quality: *is the work well done?* Confirmed by **adversarial review** — a fresh, skeptical pass that assumes there are flaws and goes looking for them.
- **What's efficient** — cost: *do the work cheaply when you can,* without giving up too much quality. Don't spend tokens (or time) the job doesn't need.
- **How we engineer** — process: the **phase discipline**, how **bugs get fixed** (the bugfix track), and how to **size the approach to the work** — a small change needs only a plan, a large one needs a design, a huge one needs an architecture pass before any design.

Beyond these four, the catalog also holds **recoverable** (can it be undone?), **private** (safe to commit/share?), **ready** (ready to ship to real users?), **simple** (the simplest thing that works?), and **worth-knowing** (worth remembering or surfacing?) — same shape, each a named standard a tool asks for; the full set is in the [Opinions design](agentm-opinions-and-gates.md).

**How they fit:** each surface is a named bucket a capability can ask for; together they answer "should this proceed, and is it good enough?" at the moments that matter. The buckets are independent — a tool might consult *done* and *efficient* but not *good*.

**Where it touches crickets:** by request, not by wiring. A crickets tool **names the opinion it needs** — `done`, `good`, `efficient`, the engineering process — and the substrate hands back the opinionated knowledge; the tool stays free to act on it. (The check battery does run inside crickets' review/release phases, and the phase commands are crickets — but those are one surface's implementation, not the pillar.)

*Detail — each surface, the request-by-name model, the gate inventory behind "done," the adversarial-review contract behind "good," the efficiency budget, and the system-sizing ladder (plan → design → architecture) — in the [Opinions design](agentm-opinions-and-gates.md).*

### Personas — the hats it wears

The top tier: a persona is a **stance the person takes for a job** — a named "who" that composes capabilities, leans on the Opinions it needs, and stands on the memory underneath. Define it once; launch it several ways.

**A persona declares:**
- **its stance** — the cross-capability judgment it makes (this is also what tells a persona apart from a plain tool);
- **what it composes** — the capabilities (crickets tools) it wields, named by `enhances:`;
- **which Opinions it leans on** — the Engineer leans on *what "done" looks like*; the Reviewer on *what "good" looks like*;
- **how it's adopted** — a persona is either **explicitly launched** in a mode it declares (not all support all), or **automatically adopted** when the work calls for it. The launch modes: **sub-agent** (scoped, returns a result), **interactive session** (you talk to it in that stance), **loop** (a cadence, `/loop`), **goal** (autonomous toward a goal, `/goal`). Automatic adoption means a crickets workflow puts the fitting persona on for a step (the work phase adopts the Engineer, the review phase the Reviewer), or agentm detects the need mid-conversation and adopts it on the spot.
- **its tier** — the model + reasoning-effort tier it runs at (T0 Mechanical … T4 Deep), from the [model + effort routing](agentm-model-effort-routing.md) scale (research/audit → T4, roadmap → T3, planning/design → T2, the worker → T1). The `tier:` manifest axis is what makes the right model run each job automatically.

**Memory is the pseudo-persona beneath them all.** `brain` isn't a peer in the roster — it sits *under* every persona, giving each one the memory it stands on. Every other persona is, in effect, "Memory + a stance."

**The roster** (the person's hats — Memory and the Planner seed exist today; the rest are designed):

| Persona | Stance | Leans on | Natural modes |
|---|---|---|---|
| *Memory* (pseudo) | keep the record true, under all personas | — | always-on |
| **Planner** (TPM) | turn intent into a plan and a board | how we engineer | loop · sub-agent |
| **Architect** | shape the broad picture *across* systems — the HLD/architecture pass | how we engineer | interactive · goal |
| **Designer** | design a *single* system in depth before it's built | how we engineer | interactive · goal |
| **Tech-Lead** | hold the technical bar across the work | done · good | interactive · sub-agent |
| **Engineer** (worker) | build the thing | done · efficient | goal · interactive |
| **Reviewer** | assume it's broken, find the flaw | good | sub-agent |
| **Operator** | run things and report — makes no changes | — | loop · sub-agent |
| **Troubleshooter / SRE** | diagnose failures in complex systems | good · how we engineer | interactive · sub-agent |
| **Researcher** | go learn what we don't know | what's worth knowing | goal · loop |
| **Maintainer** | keep the house clean (deps, docs, drift) | done | loop |

*Scope separates **Architect** from **Designer**: the Architect zooms out (the broad picture across systems — an HLD/architecture pass), the Designer zooms in (one system's design). They are the top two rungs of the engineering-sizing ladder — plan → design → architecture.*

**There is no separate "role."** A role *is* a persona. crickets provides the **tools** and the **packages** that bundle them; a package named after the persona that usually wields it (a "worker" bundle) can *look* like a role, but it's only a correlation of tools. The stance lives in the agentm persona; the tools live in crickets. *(This resolves the persona-vs-role question deferred in design-doc §9.6.)*

**The persona gate** (`check-personas.py`) keeps a persona honest: it hard-requires only substrate-native primitives (`requires ⊆ substrate`) and carries no always-load weight — so it composes capabilities without becoming a layer everything else depends on.

**Where it touches crickets:** a persona composes crickets tools by name (`enhances:`) — lighting up richer behavior when a tool is present and degrading gracefully when it's absent — while hard-requiring only the substrate. And a crickets workflow can **adopt** a persona for a step (the Engineer for the work phase, the Reviewer for review), so the right stance and the right tool meet at the moment of use.

*Detail — each persona's composition, the launch-mode mechanics, the role-is-a-persona resolution, and the cross-capability-judgment discriminator — in the [Personas design](agentm-personas.md).*

## How the pillars fit together

The four feed each other. **Experience** writes into **Memory** and, over time, sharpens **Opinions**; **Opinions** inform what Experience keeps and what any tool does, on request; **Personas** sit on top and wield all three plus the tools. **Memory** is the ground the other three stand on — lose it and the person forgets, and the rest has nothing to act on.

One rule holds across all four: the dependency arrow points one way. The pillars and their components rest on the substrate; crickets tools reach *up* into the pillars — drawing on Memory, asking Opinions by name, wielded by Personas — and the substrate reaches for nothing below it. A bare agentm — all four pillars, no tools bolted on — is whole on its own.

## References

The component-level sources now live in each pillar's child design (linked above). This parent keeps the high-level map.

**Child designs**
- [Memory System](agentm-memory-system.md) — the seam, backends, write protocol, recall loop, storage layers, V5-14
- [Memory↔Storage Seam](memory-storage-seam.md) — the storage contract (seam verbs, `Locator` guards, tiers), backends, V5-0 write protocol, routing plane, backend-aware harness state
- [Experience & Dreaming](agentm-experience-and-dreaming.md) — reflection, heat, incubation, adapt-watchlist, dreaming
- [Opinions & Gates](agentm-opinions-and-gates.md) — the check battery, conventions, phase discipline
- [Opinion registry](agentm-opinion-registry.md) — the request-by-name resolver, the nine-opinion catalog, the entry schema
- [Personas](agentm-personas.md) — the persona tier, the gate, the full ~11-persona roster
- [The persona tier](persona-tier.md) — the third classification above the substrate/plugin binary; folds the former ADR 0016
- [Persona activation](agentm-persona-activation.md) — selecting + adopting a persona at runtime; persona-tier's build-part 3
- [Model + effort routing](agentm-model-effort-routing.md) — the model × effort tier scale (T0…T4, Claude + Gemini), the persona→tier map, the `tier:` manifest axis
- [Runner](agentm-runner.md) — the standalone background-job executor: rides the hosts' built-in scheduled tasks, routes writes by ownership tier, reports to the digest

**Anchors**
- design-doc **Appendix B** — the ratified agentm Overview this HLD expands (the input spec, not a sibling HLD)
- [Foundations HLD](agentm-foundations-hld.md) — the four pillars and shared beliefs, inherited by reference
**Evolution arc (V1→V8)** — the version ladder, retained here as the live spine (the standalone `agent-memory-evolution.md`, `memory-os-architecture.md`, and `device-wide-architecture.md` HLDs were vault-archived 2026-06-24, AG Wave 2; full text in `<vault>/_vault-archive/ag-design-history/`):

- **V1 — ContextVault (local, manual):** hand-written markdown pasted into prompts; the agent started blank each session.
- **V2 — Harness workflow state:** per-project `.harness/` (PLAN / progress / features); cross-project knowledge still had no home.
- **V3 — Vault + auto-recall + controlled write:** a synced vault, per-phase recall + offered saves (shipped agentm v3.0.0 / crickets v1.0.0); exposed the local-to-one-machine + reactive-recall limits.
- **V4 — Device-wide harness + vault as knowledge database:** install once to `~/.claude/`, state moves to `<vault>/projects/<slug>/_harness/`, cwd-default project resolution — the device-wide substrate ([Foundations](agentm-foundations-hld.md)). Authored outputs (READMEs, wiki pages, release notes) are **promoted from the vault outward to the repo**, not created directly in it — the configured backing stays canonical and retains the context (the principle behind the documenter-context resolver; under V5 it narrows from a kernel rule to an obsidian-vault-plugin + documenter-context concern).
- **V5 — The unbundling (this HLD's baseline):** agentm becomes a storage-agnostic memory engine + plugin host; every non-memory capability unbundles into crickets; storage pluggable (device-local default, obsidian-vault plugin) via the two seams. The Memory pillar above + [memory-storage-seam](memory-storage-seam.md) hold the mechanics.
- **V6 — Indexed, graph-linked, tiered retrieval (designed):** vector + BM25 + RRF hybrid recall, a typed entity graph, constitutional/indexed tiers — designed-for in [memory-system](agentm-memory-system.md).
- **V7 — Dreaming, multi-surface, self-improving (designed):** offline consolidation cycles on a scheduled sidecar + read-only multi-surface access — [experience-and-dreaming](agentm-experience-and-dreaming.md).
- **V8 — Collective memory, multi-agent concurrency (speculative tail):** a multi-agent dispatcher over one shared vault (queue/lease coordination, briefing/unblock flows, worktree-per-claim).

## Amendment log

**2026-06-20 — authored, reviewed, and finalized.**

Authored 2026-06-19 from the ratified Overview (design-doc Appendix B) and a read-only grounding sweep (components, memory-layers, lifecycle, storage-serving), then restructured through operator review around the four [Foundations](agentm-foundations-hld.md) pillars — **Experience · Memory · Opinions · Personas**. The parent stays high-level: each pillar names its components and where it touches crickets, and the in-the-weeds mechanics were **migrated, not deleted**, into four seeded child designs (memory-system · experience-and-dreaming · opinions-and-gates · personas). Diagrams are hand-authored vector SVGs.

The review rounds settled the model. **Opinions** = four named, abstract surfaces a tool requests by name — what *done* looks like (the check battery is its implementation), what *good* looks like (adversarial review), what's *efficient* (a budget with a quality floor), and *how we engineer* (the phase discipline + the plan→design→architecture sizing ladder). **Experience** = **backward** (reflection from past sessions) + **forward** (scheduled, opt-in learning from approved sources), with a **scheduler**. **Personas** = a full model: a persona declares a stance + composition + the Opinions it leans on + its launch modes (sub-agent / interactive / loop / goal), and may be adopted explicitly or automatically; **Memory** is the pseudo-persona beneath all; the Coordinator is renamed **Planner**; the roster includes the **Architect/Designer split by scope**. **"Role" is retired** — a role *is* a persona, while crickets provides tools + packages — resolving design-doc §9.6.

**Honesty calls:** forward learning, the scheduler, the request-by-name Opinion registry, the persona roster + adoption modes, and the MCP-server-as-seam-client storage convergence (**V5-14**) are **designed, not built**. **Approved 2026-06-20**; children content-final 2026-06-24 + lifted AG Phase 3. **Re-audit triggers:** flip each designed component to as-built as it ships; give every child its own voice/structure pass.

**2026-06-24 — archived the arc-trio predecessors (AG Wave 2, landmarks — vault-archived, not deleted).** The three historical HLDs this parent grew out of — `agent-memory-evolution.md` (the V1→V8 arc), `memory-os-architecture.md` (the V5 HLD this doc `succeeds:`), and `device-wide-architecture.md` (the V4 device-wide substrate) — are moved to `<vault>/_vault-archive/ag-design-history/` (HISTORICAL banners; git retains them). Their still-live value is held here: the **V1→V8 evolution spine** is now a retained subsection in References (it had been outsourced to the now-archived `agent-memory-evolution.md` via a bare anchor); the V5 framing was already fully carried (this HLD was authored to succeed `memory-os-architecture.md`); the V4 device-wide framing lives in [Foundations](agentm-foundations-hld.md) + the Memory pillar. The `succeeds:` frontmatter + banner are updated to name the archive, and crickets' cross-repo up-links (which the basename-preservation note had protected) are repointed to this HLD. No-loss verified per design before archiving. *Why archive, not delete:* the trio are named landmarks — the arc's historical record — worth preserving for context.

**2026-06-24 — folded ADRs 0001 / 0011 / 0014 / 0015 into this design (AG Phase 4, move-and-retire).**

**0001 — Phase-gated workflow (2026-03-01; amended 2026-06-10).** Six phases — Setup / Plan / Work / Review / Release / Bugfix — with fresh context per session and on-disk state as the persistence substrate. Amended 2026-06-10: `/work` now runs the full task list autonomously, halting only when a per-task safety pre-check fails (hard-to-reverse / ambiguous / scope-drifting). Why not continuous context: models rubber-stamp decisions made in the same conversation; fresh context forces re-evaluation against the on-disk plan. Why not per-task approval: routine tasks don't need gating; the safety pre-check is the chokepoint. *Re-audit trigger:* if models demonstrably improve sequential-task judgment without fresh context, re-examine the fresh-context assumption.

**0011 — V5 unbundling: dev-loop deleted from agentm (2026-06-11).** The phase loop and its slash commands were removed from agentm; the crickets **development-lifecycle** plugin is canonical. No backward-compat pointer added. Why not pointer: a pointer re-creates the chain-read problem the living-design model is designed to eliminate; a clean delete forces the host to update their plugin install. *Cross-repo:* crickets side of this decision lives in the [Crickets HLD](https://github.com/alexherrero/crickets/wiki/crickets-hld). *Re-audit triggers:* DC-1 through DC-4 (multi-host, Gemini, command-collision).

**0014 — Tier-2 SDK-fork gate (2026-06-14).** Do not fork through the Agent SDK for context editing; defer behind measurement. Cache-served sessions run at 95–97% hit rate; the 1 h TTL already captures the dominant latency band; only context-editing adds measurable value, and that requires an SDK capability not yet available. Why not fork now: no evidence gains exceed SDK-coupling cost at current scale; premature optimization against a moving API. *Re-audit triggers:* DC-1 through DC-6 (measure context-editing share, SDK ships context-editing, cache hit drops below 90%, multi-host, Gemini, >5 k sessions/day).

**0015 — Capability discovery via `capability_resolver.py` (2026-06-15).** Capability-keyed registry in agentm; single version-range check; Antigravity `capabilities.json` sidecar; graceful degrade (empty capability set, never error). Why not host enumeration: capability IDs are stable across hosts; range checks let the host evolve without agentm changes. Why not error on missing: graceful degrade keeps phases functional when the plugin is absent. *Re-audit triggers:* DC-1 through DC-6 (multi-host, ID collision, range-check ambiguity, sidecar burden, masked mismatch, startup latency).

**2026-06-24 — reconciled to the now-final children + added the model+effort-routing child (AG Phase 3 lift).** All AG child designs are content-final; this parent is brought current. A new cross-cutting child **[model + effort routing](agentm-model-effort-routing.md)** (a model × effort tier scale T0…T4 with Claude + Gemini equivalents, a persona→tier map, and a new **`tier:`** persona-manifest axis) is added to the frontmatter `children`, the Personas "a persona declares" list, and the References child block. The Personas gloss is corrected ("the two personas" → the full ~11-persona roster). Why not a fifth pillar: it is cross-cutting — it adds a `tier:` axis to Personas and realizes the `efficient` opinion's model-routing lever, not a standalone pillar. `diagrams/agentm-pillars.svg` regenerated at the lift to show the tier axis under Personas. *Re-audit trigger:* re-pin the SVG tier axis label on the next persona model change.

**2026-06-20 — lifted + launched (AG Phase 2, A0/A1).** Lifted into tracked `wiki/designs/`, flipped `status: proposed → launched`, and superseded the predecessor `memory-os-architecture.md` with a forward-pointer (its basename was preserved so crickets' up-links resolved; since vault-archived 2026-06-24, AG Wave 2, with those up-links repointed here). Stamped the AG governance frontmatter: `kind: design`, `scope: arc`, `area: agentm/architecture`, `governs: [scripts/**]` — the broad agentm-substrate fallback; the children lift narrower `governs:` globs in Phase 3 (and the seam fold adds `agentm/storage`), at which point most-specific-wins refines resolution automatically. Now resolvable by [`governs_resolver.py`](Design-Governance). *Re-audit trigger satisfied:* status flipped at the lift. (Area + governs reconciled 2026-06-21 to the canonical two-level taxonomy: `agentm` → `agentm/architecture`, `[scripts, harness]` → `[scripts/**]`.)
