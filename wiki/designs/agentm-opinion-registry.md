---
title: opinion registry — design
status: launched
kind: design
scope: feature
area: agentm/opinion-registry
governs:
  - scripts/opinion_resolver.py
  - scripts/agentm-opinion.sh
  - scripts/check-opinion-resolver-one-way.py
  - scripts/check-opinion-honesty.py
  - opinions/**
parent: agentm-hld.md
seeded: 2026-06-26
approved: 2026-06-26
---

> **The opinion registry is the thin resolver that makes agentm's opinions addressable by name: a tool or persona asks for `good` or `done`, and the registry folds the coded base with the learned supplement and serves one composite over the seams agentm already has.** Implements the compose-and-serve `[PENDING-IMPL]` in [opinions](agentm-opinions-and-gates); parent [agentm HLD](agentm-hld).

# AgentM Opinion Registry Design

## Objective

An opinion is a standard agentm holds for *how to work* — what *done*, *good*, *recoverable*, or *ready* mean. The [opinions design](agentm-opinions-and-gates) says a tool should be able to ask for one **by name** and get back the coded base folded with everything the agent has learned since. That request-by-name path is the one piece the opinions design leaves as `[PENDING-IMPL]`. This design specifies it.

Today each opinion is hardwired into its tool — `code-review` carries its own copy of *good*, the check battery carries *done* — so no tool can ask agentm for the standard; it carries a private copy. The registry closes that gap. It is the resolver that scans the opinion entries, resolves a name to its composed standard, and serves it, so the standard is defined once and every caller asks for it. It is a dependency leader: the per-capability opinion wirings and the persona roster's "Leans on" column both resolve through it.

## Overview

The registry adds **one resolver**. Opinions live in two layers agentm already has — a coded base checked into the repo and a learned supplement in the memory backend — and the resolver folds them on request, the same base⊕overlay fold the style system already uses. The build roadmap names this the *registry*; what it adds is the resolve-by-name path over those existing seams (the [opinions](agentm-opinions-and-gates) design's reuse-the-seams intent, made concrete).

![A persona (via its opinions: manifest axis) or a capability/skill (via composition requires:/enhances:) calls opinion_resolve(name); the resolver scans the coded-base entries in the repo and the learned supplement in the memory backend, folds them, and serves one composite (reasons served / base-only / no-opinion); crickets reaches the resolver one-way through the capability bridge](diagrams/agentm-opinion-registry.svg)

*A consumer asks by name; the resolver folds the coded base (in-repo) with the learned supplement (memory backend) and serves one composite. It rides the one-way capability bridge: crickets depends on agentm in a single direction.*

## Design

### The resolver — request-by-name over the seams that exist

The registry is a resolver in agentm, built to mirror the two that already do this job for other markdown data: `capability_resolver.py` (the one-way capability bridge) and `governs_resolver.py` (resolve a target to its governing design by scanning `wiki/designs/` frontmatter as data). The opinion resolver copies that template closely — it reads markdown as data, never imports it, and never raises.

The resolver exposes two functions, with a thin CLI shim over them:

```python
opinion_resolve(name, *, root=None) -> dict   # rich form → {served, name, base_present, supplement_present, reason}
opinion_available(name) -> bool               # boolean surface (mirrors capability_available)
```

`scripts/agentm-opinion.sh` wraps `opinion_resolver.py` (exit `0` served · `1` unavailable · `2` usage), mirroring `agentm-capability.sh` and `agentm-governs.sh`.

Crickets reaches the resolver through a thin bridge that mirrors the crickets-side `find_capability.py` (in `developer-workflows`) — path-fallback discovery, graceful-skip when agentm is absent. The direction is one-way: crickets depends on agentm in a single direction.

### What an opinion is — the entry schema

No opinion schema exists today, so this design defines the first one. Each opinion is a markdown file with YAML frontmatter, read as data exactly as `governs_resolver` reads a design's frontmatter:

```yaml
---
name: good                 # the canonical request-by-name key
kind: opinion              # the primitive kind (mirrors kind: design / kind: persona)
question: "does it survive a hostile read?"   # the one-line standard the surface answers
serves: [code-review, "/review"]              # consumers, advisory — for discovery, not resolution
implements: crickets/code-review              # pointer to the built artifact carrying the standard today
composes: []               # optional — sub-providers folded into the served composite
---
The standard prose — the coded base of the opinion.
```

The served composite is **the base body ⊕ the learned supplement**, folded by the same mechanism crickets' `style_resolver.py` (in `wiki-maintenance`'s diataxis-author) already uses for the prose-style base⊕overlay. Two storage layers:

- **The coded base** ships as `opinions/<name>.md` in the agentm repo — the durable seed, the same for every install, changed only by a check-in.
- **The learned supplement** lives in the memory backend through the storage seam — an `opinions/` area beside the always-load conventions, written by Experience over time. The resolver reads it; it never writes it.

Opinion content does **not** live in the kernel config. The resolver reads markdown-as-data, the way `governs_resolver` does; the config plane stays out of the content path.

### The opinion catalog

The names the resolver serves — the catalog the [opinions pillar](agentm-opinions-and-gates) owns. The set is open; `voice` stays in the `style_resolver` sibling (a prose-style overlay).

| Opinion | is it…? | where the standard lives today |
|---|---|---|
| **done** | finished? | the check battery + the written conventions |
| **good** | survives a hostile read? | the adversarial-review contract (`code-review`) |
| **efficient** | as cheap as the job allows, above the *good* floor? | opusplan + `heat_policy.py` + the model×effort tier scale |
| **how-we-engineer** | the way we work? | the phase discipline + the bugfix track + the sizing ladder |
| **recoverable** | able to be undone? | the recoverability doctrine (`developer-safety`) |
| **private** | safe to commit / share? | the deterministic leak-floor (`privacy`) |
| **ready** | ready to ship to real users? | the launch-readiness gate (`/launch`) |
| **simple** | the simplest thing that works? | Chesterton's Fence + the Rule of 500 (`/simplify`) |
| **worth-knowing** | worth remembering / surfacing? | the relevance bar (research + Experience) |

`recoverable`, `private`, `ready`, and `simple` have a standard a capability already holds; `worth-knowing` is the relevance bar the Researcher persona leans on. Each is addressable by name once it has an entry.

### Resolution — the algorithm

`build_index(root)` scans `opinions/*.md` frontmatter into a `name → OpinionEntry` map (at most one base entry per name), reading and never importing. `opinion_resolve(name)` returns the composite plus a **reason**, fail-safe and never raising, in the resolver style (the `governs_resolver` pattern):

| Reason | Meaning |
|---|---|
| `served` | base present; supplement folded in if any |
| `base-only` | base present, no supplement yet (a bare install) |
| `no-opinion` | unknown name |
| `error` | the scan collapsed; degrade, don't crash |

Three invariants hold at the resolver boundary (the operator's ruling on the opinion-system invariants):

1. **The coded base is always recoverable** and the supplement is fully discardable — the supplement extends the base, it never overwrites it, and the agent never rewrites the coded base.
2. **Opinion changes are revertable**, unified with the revert-log the [memory system](agentm-memory-system) already needs for its curated tier.
3. The resolver surfaces **served-with-supplement vs base-only**, so a caller can tell a seasoned standard from a bare one.

**Versioning is always-latest, no pin.** A caller gets the current standard; reproducibility comes from recording the supplement revision in `/review` output. **A missing name returns `no-opinion` and never raises** — a soft consumer degrades to quiet absence; a hard consumer's gate is the layer that decides whether absence is fatal (below).

### How a consumer asks — two grammars, one resolver

Opinions bind through the axes that already exist:

- **A persona** declares the surfaces it leans on in its manifest `opinions:` field — the rendered form is the roster's "Leans on" column. The registry is what that field resolves *through*; the binding resolves at persona-adoption time, riding the persona activation plumbing — which [personas](agentm-personas) calls its unbuilt core, so this binding is co-blocked with that plumbing rather than riding an existing seam. This mirrors the `tier:` axis (itself designed-not-built): the persona declares the binding, the opinions pillar owns the surface, one-way. The registry is addressable by name through its CLI and the composition edges before persona-adoption resolution exists.
- **A capability or a single skill** declares through a dedicated `opinions:` frontmatter list — the unambiguous anchor the honesty lint needs (a bare `requires:`/`enhances:` value can't tell an opinion reference from a same-named capability reference; disambiguation needs a field of its own). The existing composition edge still carries the strength: `requires: opinion` is **hard** — a gate needs the standard to run at all (e.g. `/review`'s gate needs what *good* looks like); the resolver returning `base-only`/`no-opinion` is what that gate raises on. `enhances: opinion` is **soft** — the tool works without it and does more when it's present; absence is a quiet graceful-degrade.

The resolver supports **sub-capability granularity**: the caller can be a whole capability or a single primitive. `privacy` is the worked case — the capability *provides* the `private` standard (its deterministic leak-floor), while its `privacy-review` skill is the named sub-caller that consumes *good*.

### How the resolved text reaches the caller — three seams

Declaring `opinions:` says a caller depends on a standard; it doesn't say how the standard's text arrives there, and that differs by what the caller is.

**Code with a render step** — the caller constructs an artifact (an incident body, a dispatch announcement) — calls `opinion_resolve(name)` directly at runtime and folds the composite in. This is live: it sees a supplement the moment Experience writes one, and it degrades to `base-only`/no-section when agentm is unreachable, the same graceful-skip every crickets↔agentm bridge already uses. `diagnostics` (→ *how-we-engineer*) and `development-lifecycle`'s routed-dispatch layer (→ *efficient*) are the two shipped instances.

**A static markdown skill or command file** has no runtime call site — the host loads it verbatim as a prompt, so there is nothing to hook `opinion_resolve()` into. Test which of two shapes it is using the opinion entry's own `implements:` pointer (the field already named as "the artifact carrying the standard today"):

- **Self-provider** — the caller *is* the `implements:` artifact (`good`'s `implements: crickets/code-review` and `code-review` itself; `done`/`how-we-engineer`'s `implements: crickets/development-lifecycle` and `development-lifecycle` itself). The opinion's coded base was extracted *from* this caller's own prose — asking for it back resolves a reflection, not reuse. It stays hardwired, unchanged. What locks today is a **drift check**: the caller's prose is canonical, the opinion stub is its mirror, and the check flags the *stub* falling out of sync with the caller — never the reverse. No runtime request, no interpolation.
- **Genuine cross-plugin reuse** — the caller is *not* the `implements:` artifact (`design` asking for *good*, which `code-review` owns). This has real dedup value, and the text must physically arrive in the caller's file. Because that file is generated once and read by installs that may have no agentm sibling at all — crickets ships its plugins standalone, no agentm required — the text is **baked in at build time from a committed snapshot, never read live from agentm or the vault**. A live cross-repo read can't survive `generate.py check`'s determinism gate (a standalone-crickets CI has no agentm to read, so a fresh build and the committed one would diverge); a snapshot sidesteps that by being base-only *by construction*, matching a bare install's `reason: base-only`. A parity gate (mirroring `check-lib-parity`/`check-vault-lock-parity`'s shape) keeps the snapshot honest against `opinions/*.md`, graceful-skipping when the agentm sibling isn't present to compare against. The snapshot's location and the `generate.py` wiring are crickets' own build pipeline to specify, in the [composition design](https://github.com/alexherrero/crickets/wiki/crickets-composition) — this design locks the *shape* (committed snapshot, parity-gated, base-only), not the crickets-side mechanics. When Experience ships the supplement, a cross-plugin consumer gains a runtime overlay on top of the baked base (the existing bridge pattern) — additive, not a rewrite of the baked floor.

**Classification is mechanical — grounding isn't.** Whether a caller is self-provider or cross-plugin follows directly from the entry's `implements:` pointer, no judgment call needed. But landing in the cross-plugin class doesn't itself confirm a caller is build-ready: a future plan still needs to locate the actual prose site to interpolate into, or confirm none exists and drop the reference — the same discipline already applied to `token-audit` (see the corrected consumer table below).

A representative slice of the consumer set (the full map is the [composition design](https://github.com/alexherrero/crickets/wiki/crickets-composition) + the [persona roster](agentm-personas)), with the seam each one resolves through:

| Caller | Asks for | Strength | Seam |
|---|---|---|---|
| `diagnostics` | how-we-engineer | enhances | code, cross-plugin — **shipped**, resolves live |
| `development-lifecycle` routed-dispatch | efficient | enhances | code, cross-plugin — **shipped**, resolves live |
| `code-review` / `/review` | good | requires (the gate needs it) | self-provider (`good` implements `code-review` itself) |
| `development-lifecycle` (`/work`, `/release`) | done | requires | self-provider (`done` implements `development-lifecycle` itself) |
| `development-lifecycle` (`/work`, `/release`) | recoverable | requires | markdown-prose, cross-plugin (`recoverable` implements `developer-safety`) |
| `development-lifecycle` (`/plan`, `/bugfix`) | how-we-engineer | enhances | self-provider (same artifact as the `done` row above) |
| `development-lifecycle` finalize · CI | private | requires | markdown-prose, cross-plugin (`private` implements `privacy`) |
| `/launch` | ready | requires | self-provider (`/launch` is a `development-lifecycle` skill; `ready` implements the same capability) |
| `/simplify` | simple | requires | self-provider (`/simplify` is a `code-review` skill; `simple` implements the same capability) |
| `design` | how-we-engineer · good | enhances | markdown-prose, cross-plugin (both) |
| Researcher persona | worth-knowing | enhances | persona axis, not this grammar (`worth-knowing` implements `agentm/experience`; binds via the persona `opinions:` manifest, co-blocked with persona activation) |
| Persona — Tech-Lead | done · good | adoption-time | persona axis |
| Persona — Engineer | done · efficient | adoption-time | persona axis |
| Persona — Reviewer | good | adoption-time | persona axis |

Two corrections from the pre-2026-07-06 table: **`token-audit` is removed** — it is `efficient`'s measurement sub-provider via `composes:` (below), not a caller requesting `efficient` back; listing it as a consumer was a modeling error. **`maintenance` and `wiki`** were named as deferred bindings in a downstream build plan but were never in this design's own consumer map and have no located prose site — they stay absent pending a grounding pass, not added speculatively.

### Opinions provide as well as consume

**`efficient` is compound** — the resolver assembles it from sub-providers via the entry's `composes:` field. It returns a cost budget with a quality floor: its model-routing lever is the [model + effort routing](agentm-model-effort-routing) tier scale and the measurement half comes from `token-audit`, so `efficient` `composes:` those two sub-payloads into the served composite.

**Some opinions are provided by a capability that holds the standard.** `recoverable`'s standard lives in `developer-safety`'s recoverability doctrine; `private`'s deterministic leak-floor lives in `privacy`; `ready`'s readiness gate lives in `/launch`; `simple`'s lives in `/simplify`. The capability is the opinion's `implements:` artifact; the opinion makes that standard addressable by name to every other caller.

Surfaces that consume **no opinion**, each for its own reason (the honesty lint expects no binding from them): `reporting` (reports, doesn't judge — it follows the *voice* convention), `github-projects` (mirrors state), `obsidian-vault` (infrastructure below the substrate), `conventions` (the inverse — opinions cite *it*). `developer-safety` and `privacy` are **providers** of `recoverable`/`private`, not consumers.

### Enforcement

The registry is kept honest by three gates that copy the capability-resolver pattern, plus a dedicated resolver test:

1. **`check-opinion-resolver-one-way.py`** (wired into `check-all.sh`) — an AST gate that fails if `opinion_resolver.py` uses `import_module` / `__import__` / `exec` / `eval` or imports non-stdlib outside a reviewed sibling allowlist. The resolver discovers opinions; it never runs them.
2. **Update `check-capability-naming.py`** — its reserved `OPINION_NAMES` set is drifted (it reserves `{efficiency, quality, good, done}` while the canonical surfaces are the nine-opinion catalog). Reconcile it to the catalog names so no capability can shadow an opinion and the one-way capability→opinion rule stays whole.
3. **An honesty lint** — every consumer-declared opinion name must resolve to a real `opinions/` entry: a persona `opinions:` entry, or a capability/skill's own `opinions:` frontmatter list (the dedicated field the consumer grammar below locks). `check-opinion-honesty.py` ships scanning the persona field only; extending it to the capability/skill `opinions:` field is the fast-follow this amendment unblocks. No orphan opinion references either way.

Plus **`test_opinion_resolver.py`**, copying `test_governs_resolver.py` (served / base-only / no-opinion / error, never-raise).

### The boundary

- **vs the capability resolver** — that resolves a capability to its provider (one provider, a hard `CapabilityMismatchError` on the consumer side). The opinion resolver resolves a name to a composed standard (base⊕supplement fold, soft never-raise). It rides the capability bridge but is its own resolver, the way `governs_resolver` is.
- **vs the persona tier** — the `tier:` axis is the structural precedent (manifest-declared, policy-owned, adoption-applied, one-way); opinions reuse the same manifest + validator + adoption pattern, bound to standards instead of model-effort.
- **vs model + effort routing** — that is a sub-provider folded *into* `efficient`'s composite.
- **vs the gate model** — the registry **serves** the standard; enforcement stays with *done*'s deterministic check battery and *good*'s adversarial pass. The registry makes those addressable by name. Opinions carry no advisory/enforced state machine — that knob belongs to the [experience design](agentm-experience-and-dreaming).

## Dependencies

- **rides the one-way capability bridge** — crickets' `find_capability.py` → agentm's `capability_resolver.py`; the opinion lookup rides the same path crickets already uses to reach agentm.
- **rides the style base⊕overlay fold** — crickets' `style_resolver.py` (wiki-maintenance) is the compose precedent for base ⊕ supplement.
- **rides the memory engine** — the learned supplement is a memory entry in the `opinions/` area, through the storage seam ([memory system](agentm-memory-system)).
- **binds through** the persona `opinions:` manifest axis ([personas](agentm-personas)) and the composition `requires:`/`enhances:` edges ([composition](https://github.com/alexherrero/crickets/wiki/crickets-composition)).
- **`efficient` composes** [model + effort routing](agentm-model-effort-routing) (the tier scale — itself `[PENDING-IMPL]`) and `token-audit` (the measurement).
- **implements the `[PENDING-IMPL]`** in the [opinions pillar](agentm-opinions-and-gates); points up at the [agentm HLD](agentm-hld) §Opinions.

## Migrations

- **Addressable before fully-extracted.** Ship `opinions/<name>.md` as **thin stubs first** — frontmatter + an `implements:` pointer to the artifact that carries the standard today (`done`→`check-all.sh` + `CI-Gates.md`, `good`→`code-review`) + a short body — then deepen the bodies. This makes opinions addressable-by-name immediately, unblocking the per-capability wirings, without a risky one-shot extraction of every hardwired standard.
- **The wirings flip as they consume.** Each consumer moves from a private hardwired copy to `opinion_resolve(name)` when its slice builds (the launched consumer designs call this `opinion_request` — the same operation); the present-tense "compose with opinions" prose in the consumer designs is reconciled to as-built at the same time.
- **At lift (docs):** expand the [opinions pillar](agentm-opinions-and-gates)'s "opinions today" table to the nine-opinion catalog (its canonical home); add the `agentm/opinion-registry` area to the area taxonomy; repoint the pillar's "that code is what this design will govern once built" line to name this design as the governor of `opinion_resolver.py` (the pillar stays discipline/area-only, the registry governs the code).
- **At build:** fix the drifted `OPINION_NAMES` guard (a crickets code change), reserving all nine names; then build `opinion_resolver.py` + `agentm-opinion.sh` + the gates + the stub entries.
- **The markdown-prose consumer grammar is now locked** (see the new subsection above) — this closes the reconciliation `PLAN-wave-d-opinion-wiring` (crickets) flagged as open for whoever owns agentm's wiki, after that plan shipped the 2 real code bindings and narrowed away from the 7 markdown-prose ones pending this design. Extending `check-opinion-honesty.py` to the capability/skill `opinions:` field, and the crickets-side snapshot + parity gate + `generate.py` wiring, are the fast-follow build work this unblocks.

## Risks & open questions

- **Buildability illusion.** The registry sits under the github-projects ≥4-deep dependency stack (board-depth → Planner → persona activation → this registry); dependents read "designed" while leaning on it. As it builds, downgrade dependents' status language from "designed" to "blocked on the registry" until it exists.
- **Compound `efficient` is partly blocked.** Its full composite needs the model-effort-routing tier scale, which is itself `[PENDING-IMPL]`. Until that ships, `efficient` serves base + the `token-audit` measurement only; the routing lever folds in when its peer leader lands.
- **Schema-first risk.** This is the first opinion schema; getting a field wrong forces a migration across every wiring later. The stub-first migration limits the blast radius.
- **The consumer syntax co-evolves.** The persona-side `opinions:` manifest field is itself `[PENDING-IMPL]`, so the declaration grammar the resolver reads is not fully locked — co-design the manifest field and the resolver together.
- **Re-audit triggers:** flip the parent opinions design's request-by-name `[PENDING-IMPL]` to as-built when this ships; fold the model-routing lever into `efficient` when the tier scale lands; revisit the name-space when a new surface is authored (the catalog stands at nine).
- **The shared snapshot is a second copy, not a single source of truth.** Cross-plugin markdown consumers bake a committed snapshot of the opinion's base text into crickets' own `dist/` at build time — a copy of what `opinions/*.md` already holds, which is exactly what this registry's own pitch ("defined once, every caller asks") was meant to avoid. The parity gate is what makes the copy safe: it fails the moment the snapshot drifts from the coded base, and graceful-skips (rather than false-passes) when the agentm sibling isn't present to compare against. Re-audit this the moment Experience ships the supplement — a cross-plugin consumer's runtime overlay should compose with the baked base, not remove the need for the gate.

## Locked design calls

- **Soft resolver, hard at the gate.** `opinion_resolve` always returns a reason and never raises (the `governs_resolver` style). Fail-loud is pushed up to the hard `requires:` consumer's gate, which raises if its required opinion came back base-absent. A bare install and a seasoned one are served the same way.
- **Always-latest, no pin.** Reproducibility comes from recording the supplement revision in `/review` output (operator ruling).
- **Invariants committed at the boundary now**, even though both the supplement *writer* (the Experience loop) and the *revert-log* primitive it unifies with are unbuilt: base always recoverable, supplement fully discardable, opinion changes revertable (operator ruling). The registry only reads the supplement, so it is unaffected; the revertability invariant becomes enforceable when the memory-system revert-log lands.
- **The registry serves; gates enforce.** No advisory/enforced state machine for opinions.
- **The sharpening loop is out of scope** — it is the Experience → Opinions loop (operator ruling); this design only specifies that the supplement is a memory entry the resolver folds, not how it gets written.
- **The coded base ships as thin stubs first** (operator ruling) — each `opinions/<name>.md` starts as frontmatter + an `implements:` pointer + a short body, deepened later, so opinions are addressable by name without a one-shot extraction.
- **The name-space is the nine-opinion catalog** (operator ruling, 2026-06-26) — the original four plus *recoverable*, *private*, *ready*, *simple*, and *worth-knowing* (see the catalog table). *voice* stays in the built `style_resolver` sibling (a prose-style overlay, not a behavioral opinion). The set stays open — a new surface needs an authored entry.
- **`worth-knowing` is a first-class opinion** (operator ruling, 2026-06-26) — the relevance bar the Researcher persona leans on, lifted from a roster-only surface into the catalog (this reverses the earlier "normalize as drift" call). `recoverable` and `private` are likewise promoted from folded sub-standards into peer opinions; their standards stay provided by `developer-safety` / `privacy`.
- **The markdown-prose consumer grammar is locked** (operator ruling, 2026-07-06). A capability/skill declares via a dedicated `opinions:` frontmatter list — the disambiguation a bare `requires:`/`enhances:` value can't provide. Delivery then splits on an objective test: is the caller the opinion's own `implements:` artifact? **Self-provider** stays hardwired in the caller's own prose, kept honest by a stub-tracks-plugin drift check, no runtime request. **Genuine cross-plugin reuse** bakes the coded base into the caller's `dist/` at `generate.py` time from a committed, parity-gated snapshot — never a live cross-repo or vault read. The learned supplement stays out of both today; it layers on as a runtime overlay for cross-plugin consumers once Experience ships it.
- **A runtime bridge (every markdown consumer calling `opinion_resolve()` live) was considered and rejected** for the reasons above, adversarially reviewed 2026-07-06. It would match the 2 shipped code bindings' live behavior and deliver the supplement the moment it exists, but it fails the audience the grammar most needs to serve: crickets ships its plugins standalone, and from a real host-plugin install the existing bridge's path-fallback resolves to nothing without a coincidental sibling clone at a hardcoded conventional path — so a runtime-only consumer would silently degrade to no-standard-at-all for exactly the users who never had agentm to begin with. The supplement it would carry is moot regardless today: `opinion_resolver.py`'s CLI (`main()`) never passes a `supplement_dir`, so it never resolves one, and Experience — the supplement's writer — is itself `[PENDING-IMPL]`.
- **Classification is mechanical; confirming a build site is not.** A caller's self-provider/cross-plugin class follows directly from its opinion's `implements:` pointer — no judgment call. Landing in the cross-plugin class is not itself confirmation that a caller is ready to wire: a future plan still needs to locate the real prose site to interpolate into, or drop the reference, the same discipline `token-audit` and the persona-axis rows already got in the corrected consumer table.

## References

- **The resolver precedents to copy:** agentm `scripts/governs_resolver.py` + `test_governs_resolver.py` (markdown-data resolve-by-name, never-raise) · agentm `scripts/capability_resolver.py` with crickets' `find_capability.py` (the one-way bridge) · crickets `style_resolver.py` (wiki-maintenance / diataxis-author — the base⊕overlay fold) · agentm `scripts/capability_version_match.py` (graceful-degrade matching)
- **The guard to reconcile:** crickets `scripts/check-capability-naming.py` (the reserved `OPINION_NAMES` set)
- **The coded bases today:** `AGENTS.md` + `harness/principles.md` · `scripts/check-all.sh` + `wiki/reference/CI-Gates.md` (*done*) · crickets `code-review` (*good*) · `harness/skills/memory/scripts/heat_policy.py` + opusplan (*efficient* levers) · `development-lifecycle` (*how we engineer*)
- **Up:** [opinions pillar](agentm-opinions-and-gates) (the `[PENDING-IMPL]` this implements) · [agentm HLD](agentm-hld) §Opinions
- **Consumers:** [composition](https://github.com/alexherrero/crickets/wiki/crickets-composition) (the capability wiring) · [personas](agentm-personas) (the "Leans on" column)
- design-doc **Forward plan** — the Opinion slice (Phase 3 → 4), the migration this realizes

## Amendment log

*Newest first. Collapses to one ≤2-paragraph entry at finalization; git holds the granular history.*

- **2026-07-06 — markdown-prose consumer grammar locked (AG Wave D follow-on).** Wave D's `PLAN-wave-d-opinion-wiring` build found only 2 of the 9 registered opinions had a real code binding with a runtime call site (`diagnostics`→*how-we-engineer*, `development-lifecycle` routed-dispatch→*efficient*, both shipped); the other 7 are static markdown skill/command files with no runtime hook, exactly the gap `check-opinion-honesty.py`'s own docstring flagged as deferred "until that grammar is locked." This closes it. A capability/skill now declares via a dedicated `opinions:` frontmatter list; delivery splits on the opinion's own `implements:` pointer into **self-provider** (the caller already is the standard's home artifact — stays hardwired, a stub-tracks-plugin drift check replaces any runtime request) and **genuine cross-plugin reuse** (baked into the caller's `dist/` at `generate.py`-time from a committed, parity-gated snapshot — never a live cross-repo or vault read, so it survives crickets' standalone-install model and a vault-less, agentm-less CI).
  A runtime-bridge alternative (every markdown consumer resolving live, matching the 2 shipped code bindings) was adversarially reviewed and rejected: crickets ships plugins standalone, and the existing bridge's path-fallback doesn't resolve from a real host-plugin install without a coincidental sibling clone, so it would have silently degraded to nothing for the audience the grammar most needs — and the supplement it would carry is moot today regardless (the CLI doesn't fold one; Experience is unbuilt). The consumer table is corrected to match: `token-audit` removed (it's `efficient`'s measurement sub-provider, not a consumer — the same modeling-error correction Wave D's plan already made crickets-side); `development-lifecycle`'s `done`+`recoverable` row split across two different seams (their `implements:` targets differ); the two shipped code bindings added. `maintenance`/`wiki` — named in Wave D's deferred-7 but never present in this design's own consumer map — are left out pending a grounding pass. The crickets-side build mechanics (snapshot location, the parity gate, the `generate.py` wiring) are the [composition design](https://github.com/alexherrero/crickets/wiki/crickets-composition)'s to specify, cross-repo.

- **2026-07-06 — built (AG Wave B leader 2/5).** `scripts/opinion_resolver.py` + `scripts/agentm-opinion.sh`, mirroring `governs_resolver.py`'s shape (pure frontmatter scan, never-raise, four reasons: served/base-only/no-opinion/error). The nine coded bases ship as `opinions/*.md` thin stubs. Two enforcement gates: `check-opinion-resolver-one-way.py` (AST, no plugin imports) and `check-opinion-honesty.py` (no orphan persona `opinions:` references — scoped to the unambiguous persona-manifest field only; a capability/skill `requires:`/`enhances:` opinion reference stays undetectable until that grammar is co-designed with persona activation, per Risks). The crickets-side `OPINION_NAMES` drift the readiness ledger flagged (agentmDesigns#9 / agentmExperience#6) is fixed in that repo. `governs:` now points at the resolver, the CLI, both gates, and `opinions/**`.

- **2026-06-28 — lock-down sweep (operator review).** Reviewed in the final AG design sweep — SVG already sized, no mermaid / ADR / grounding issues, the log already newest-first; nothing to change. Locked as a v5–v8 guidepost.
- **2026-06-26 — authored, reviewed, and finalized.** The Bucket-A substrate sub-design specifying the request-by-name registry the [opinions pillar](agentm-opinions-and-gates) leaves as `[PENDING-IMPL]` — a dependency-leader that gates the per-capability opinion wirings and the persona roster's "Leans on" column. The registry is a resolver in agentm (`opinion_resolver.py` + `agentm-opinion.sh`) built to mirror the existing `governs_resolver.py`: it scans `opinions/<name>.md` entries as data, resolves a name to the base ⊕ learned-supplement composite (via the `style_resolver` fold), reasons `served`/`base-only`/`no-opinion`/`error`, and never raises; crickets reaches it one-way through the capability bridge. It defines the first opinion-entry schema and binds through the existing persona `opinions:` axis + composition `requires:`/`enhances:` edges, kept honest by an AST one-way gate + the reconciled naming guard + an orphan-reference lint.

  **Operator calls:** soft resolver / hard at the gate; always-latest no-pin; invariants at the boundary; registry serves, gates enforce; sharpening loop out of scope; coded base ships thin-stubs-first. The catalog expands to **nine** — *done · good · efficient · how-we-engineer · recoverable · private · ready · simple · worth-knowing* — with `recoverable`/`private` promoted from folded sub-standards to peer opinions provided by `developer-safety`/`privacy`, and `voice` kept in the `style_resolver` sibling. *Re-audit:* flip the parent `[PENDING-IMPL]` at ship; fold the routing lever into `efficient` when the tier scale lands; revisit the name-space when a new surface is authored.
