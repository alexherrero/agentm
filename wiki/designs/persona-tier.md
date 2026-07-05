---
status: launched
kind: design
scope: feature
area: agentm/personas
parent: agentm-hld.md
---

# The persona tier

> [!NOTE]
> **Status:** launched — build parts 1–2 + 4 shipped (V5-11, commit [7966ac3](https://github.com/alexherrero/agentm/commit/7966ac3)); **part 3 (on-demand load + surfacing path) is `[PENDING-IMPL]`**; ADR 0016 folded into the Amendment log below (2026-06-24).
> **Position in arc:** refinement of [AgentM HLD](agentm-hld) (the V5 "unbundling" HLD).
> **Method:** the locked 10-section design template (the V5-11 design method).
> **Roadmap:** **V5-12** (agentm kernel, ROADMAP-MASTER bucket ⑤) — slotted 2026-06-16; sequenced after V5-10, ahead of V5-11 as its substrate (V5-11's Planner (TPM) is this tier's first *real* persona = build-part 4).

## Context

### Objective

The V5 unbundling split agentm's world in two — a neutral memory-OS **substrate** and crickets **capabilities** (plugins) — but that binary has no slot for a standing concern that *composes* capabilities without *owning* them, like the Planner (TPM). This design names that missing third tier — the **persona** — defines it by its inverted dependency direction, and maps it entirely onto shipped infrastructure so it costs **no new machinery**. The payoff is a classification that keeps composition from silently becoming cross-repo coupling.

### Background

The memory engine has always been more than plumbing: it recalls, reflects, and curates — a tenant with behavior anchored on the neutral kernel, depending on nothing but kernel-native scripts. The V5 binary classifies it under "substrate," which under-describes it. At the same time the roadmap wants the **Planner (TPM)** (the PM depth-maintainer / drift-corrector, see [[multi-agent-orchestration]], [[github-project-sync]]): a standing concern that composes dev-loop and board capabilities it does not own, must run on a bare substrate, and must not drag a hard dependency on crickets into agentm.

Two shipped pieces make naming this tier cheap. The [AgentM HLD](agentm-hld) (V5-8) shipped the `enhances:` soft-composition runtime — a capability-keyed, graceful-degrade resolver with `enhances ∩ requires = ∅`. And the host installers already dispatch primitives by **positive match** on `kind:`, so an unknown kind is skipped, not rejected. The third tier therefore needs no new resolver, no new loader, and no new host adapter — only a name, a directory, and one gate.

The constraints that drive every call below: **bare agentm must stay coherent** (the memory engine needs zero plugins) and **agentm must take no hard dependency on crickets** (discovery, never bundling). Both are invariants from the [AgentM HLD](agentm-hld); this design holds them *mechanically* rather than by discipline.

## Design

### Overview

A **persona** is a standing concern that **composes capabilities it does not own**. Three properties define it:

- it makes a **cross-capability judgment** — a call spanning more than one capability that none could make alone (the persona/tool discriminator, owned by [personas](agentm-personas));
- it is **anchored on the neutral substrate**, with hard dependencies (`requires:`) restricted to substrate-native primitives;
- its soft composition (`enhances:`) may name any capability, present or absent.

A persona **owns no engine**. It is a stance plus a composition manifest over engines that stay in crickets. It lives in agentm for universality and neutrality, not because it "remembers."

The defining signature is the **inverted dependency direction**. A plugin is a capability that *others compose* — it is depended **upon**. A persona *composes capabilities* and is depended upon by nothing — it sits **above** the capability layer, rooted at the substrate. The **brain** (the memory engine) is the **degenerate persona**: zero composed plugins, `requires:` ⊆ substrate — the persona agentm already shipped, now named. The **Planner (TPM)** is the **first real persona**: it composes dev-loop/board capabilities via `enhances:`, hard-requires only agentm-native scripts.

Two near-miss axes are explicitly *not* the test. **"Remembers"** breaks both ways: the Planner (TPM) is stateless, while the planned crickets `security-review` capability `requires: agentm` *for memory*. **"Crosses multiple plugins"** is insufficient — `github-projects` composes `developer-workflows` yet is a plain capability. The discriminator is **cross-capability judgment**, with the inverted, substrate-only hard-dep direction as its mechanical floor (the canonical test lives in [personas](agentm-personas)).

Mechanically, a persona is **just an agent-def**: an opinionated prompt, a `tools:` allowlist, a `model:`, and declared deps. What makes it a persona is its tier, home, and composition-scope — the file format is ordinary. The shape is conventional: a thing that composes capabilities, ships no feature of its own, degrades when a composed capability is absent, and offers one-click install for it.

### Infrastructure

**N/A: no new infrastructure.** The tier is a classification mapped onto shipped pieces — the table below is pure reuse.

| Concern | Shipped mechanism reused | New surface |
|---|---|---|
| Primitive kind | Positive-match `kind:` dispatch in `install.{sh,ps1}` | `kind: persona` (a value, not a code path) |
| Home on disk | `harness/`-style primitive directory | `personas/` directory |
| Composition (soft) | V5-8 resolver, `enhances:` ([AgentM HLD](agentm-hld)) | none — `enhances:` reused verbatim |
| Hard-dep restriction | `requires:` + one-way seam gates | `check-personas` (one new static gate) |
| Load | Existing on-demand memory-load path | none — dormant-until-activated |

Trigger shape is unchanged: `check-personas` runs where the other `check-*` gates run (local `check-all.sh` + CI on push); persona bodies load at activation time, not at session start.

### Detailed Design

**The primitive.** Each persona is a markdown file under `personas/` with frontmatter — `kind: persona`, `requires: [...]` (substrate-native scripts/primitives), `enhances: [...]` (composed capabilities), plus the agent-def fields it shares with any agent (`model:`, a read-only `tools:` allowlist) — and a body carrying the standing-concern instructions. The brain is the degenerate case: `enhances: []`, `requires:` ⊆ substrate.

**Composition reuses `enhances:`.** "Composes" is the conceptual verb; the on-disk field is `enhances:`, resolved by the V5-8 resolver. A persona names the capabilities it wants and the resolver answers "present on this host?" at load time, degrading cleanly when absent. There is deliberately **no `composes:` alias** — that would fork a resolver and a vocabulary that already exist, for identical semantics.

**The gate is the enforcement.** `check-personas` asserts two things for every file under `personas/`: that each `requires:` entry resolves to an agentm-native primitive (never a crickets capability), and that no persona manifest lands in the always-load set (so a dormant persona never inflates the per-call token floor — [#46](https://github.com/alexherrero/agentm/issues/46)). The first holds *agentm-no-hard-dep-on-crickets*; the second holds the token floor; soft `enhances:` is unrestricted. It mirrors `check-capability-resolver-one-way.py` and `check-process-seam-import-direction.sh` — a one-way dependency assertion, statically checkable, added to the gate battery.

**Load is on demand `[PENDING-IMPL]`.** Design: a persona body loads only when the persona is activated, reusing the existing on-demand memory-load path — not the always-load floor. The brain is the implicit always-on degenerate persona; everything else is dormant until invoked. A persona may be *surfaced* through a sub-agent at activation; even so, it stays a durable classification, not ephemeral fan-out. The surfacing mechanism is specified by the [persona activation](agentm-persona-activation) design (build-part 3), not yet built.

**The naming question (deferred).** agentm **keeps "persona"** for this tier. The collision is on the crickets side — it calls its four coordinator roles "personas" loosely, though it mostly already says "role" — so the rename moves *there*: crickets' looser "persona" usage becomes "**role**", freeing the term for the tier that earns it (a standing-concern identity that composes, distinct from a thin role that wraps one phase command). The pass is doc-only and rides **V5-6** (the identity rewrite) — see Re-verification below for why it is zero-code.

#### Re-verification against shipped infrastructure (§10 re-checks cleared)

This design treats the pre-audit as a pre-audit; the key assumptions were re-checked against the live codebase, not assumed:

1. **`queue_status_lite` is agentm-native.** ✓ `scripts/queue_status_lite.py` carries the read logic; its docstring states "this is the agentm read logic; the crickets `/queue-status-lite` command surface … wraps it." The Planner (TPM) persona may therefore `requires:` it without breaching `requires ⊆ substrate`.
2. **Both hosts tolerate an unknown `kind: persona`.** ✓ Installer dispatch is positive-match (`[[ "$_am_kind" == "skill" ]] || continue` in `install.sh`); `validate-adapters.py` and `check-references.py` validate named surfaces by directory + required keys, with **no closed `kind:` enum**. An unknown kind is skipped, never rejected. (`project_config.py`'s `kind not in ("skill","hook")` guard is the project-config toggle path, not the primitive loader, and personas are not project-config-toggled.)
3. **`enhances:` reuse.** ✓ The AgentM HLD already owns the soft-composition field + runtime; the semantics a persona needs (optional, present-or-absent, degrade) are identical. Reuse; do not alias.
4. **The crickets "persona"→"role" rename is doc-only, and runs the *right* direction.** ✓ agentm **keeps "persona"**; the rename is crickets-side — its loose "persona" usage → "role" (verified loose-synonym usage in `project-manager.md`, `worker.md`, `tech-lead.md`, `researcher.md`, `Coordinator-Roles.md`), scope = `Coordinator-Roles.md` + the developer-workflows how-to + the four agent descriptions. No shipped "persona"/"role" *code* vocabulary exists in either repo — renaming touches no `kind:` value, no gate name, no directory. It is prose, ridden on V5-6 to land once, consistently.
5. **The null hypothesis, re-litigated** — see Alternatives Considered.

## Alternatives Considered

**The null hypothesis, strongest form — ship the Planner (TPM) as a *new crickets plugin* `coordination` with `requires: [developer-workflows, github-projects, agentm]`, and call its agent-def a "persona" informally. This works mechanically.** Rejected on three *architectural* (not mechanical) grounds:

1. **Swallowing / up-reach.** The Planner (TPM) composes `developer-workflows` (queue) + `github-projects` (board) + substrate. Home it in `developer-workflows` and that plugin reaches *up* into `github-projects`, which `requires:` it, not the reverse. Home it in `github-projects` and "render a board" now owns coordination *thinking*, blowing its one-job contract. A *new* `coordination` plugin dodges both — but then the persona tier has been built *inside* crickets, and it `requires: agentm` anyway. You re-derived the layer and mis-homed it.
2. **Home / taste.** The Planner (TPM) carries agentm's *opinion about how a program should be run* (computed-not-guessed, advisory-not-acting, smaller-merge-first). The V5-6 thesis is that agentm's identity is the thing that *persists*. Putting that standing point-of-view in an optional, swappable plugin declares "agentm's taste is optional" — the opposite of "do most of the opinionation in agentm."
3. **Default-presence.** A persona ships with agentm and is *always there, degrading*; a plugin is opt-in. If minding-the-program is a plugin, then "is anyone minding the program?" depends on your install set. As a persona the stance is always present and merely says "install github-projects for the board."

**Honest residual:** the tier buys **architecture** — neutral home, default-presence, identity-anchoring — **not new runtime mechanism** (it reuses the V5-8 resolver, `requires:` + one gate, the on-demand load path, and positive-match `kind:` dispatch). If the operator weights shipping-velocity over the identity-home argument, the null is a *legitimate* cheaper path whose cost is precisely "agentm's program-minding taste lives in an optional plugin." The layer earns its keep on the three arguments above.

**Where opinionation lives (the migration rule).** Capability-local opinion — how `/work` gates, how a release is recoverable — stays in crickets; *cross-capability* opinion — what order to merge, which plans run together — is what a persona arbitrates, homed in agentm. The line: capability-local → the capability; cross-capability → the persona. This is additive (no migration sweep); a stranded cross-capability stance migrates only case-by-case when a persona claims it, as the PM-thin→Planner (TPM) handoff already does.

**Filing the persona as a crickets plugin (general case). Rejected** — the inverted arrow again: a plugin is composed *by* others; a persona *composes* and is composed by nothing. Persona-as-plugin creates plugin→plugin hard-dep chains, exactly what the `enhances ∩ requires = ∅` / one-provider / no-solver rules forbid. Restricting persona `requires:` to substrate-native keeps the hard-dep graph acyclic and rooted at the neutral substrate, and keeps the tier universal (bare agentm has the brain; add crickets and the Planner (TPM) composes the dev loop).

**Model a persona as a skill. Rejected** — a skill is a capability (the thing a persona composes); filing a persona as a skill collapses the tier distinction and inverts the arrow.

**Model a persona as a sub-agent. Rejected** — sub-agents are read-only ephemeral fan-out (AGENTS.md rule 6); a persona is a durable classification with hard substrate deps and declared soft composition. It may be surfaced through a sub-agent, but it is not one.

**Add a `composes:` keyword. Rejected** — see DC-3 in the Amendment log (the former ADR 0016); identical semantics to `enhances:`, so a second keyword is pure machinery.

## Dependencies

- **[AgentM HLD](agentm-hld) / the V5-8 resolver** (`scripts/capability_resolver.py`) — the composition runtime, reused verbatim. Hard prerequisite (already shipped).
- **The positive-match `kind:` dispatch** in `install.{sh,ps1}` — relied on for unknown-kind tolerance (already shipped).
- **V5-6 (identity rewrite)** — *coordinates with*, does not block: the persona→role naming call is parked there. No build dependency.
- **No dependency on crickets.** The Planner (TPM) composes crickets capabilities via soft `enhances:` only; `check-personas` forbids any crickets entry in `requires:`.

## Migrations

**N/A: no existing state to migrate.** The tier is additive — a new `kind:`, a new directory, a new gate. The memory engine is *re-described* as the degenerate brain persona but is not moved or rewritten; no on-disk artifact changes. A later, separate step may author the memory engine's persona manifest, but this design does not require touching shipped memory-engine code.

## Technical Debt & Risks

- **`check-personas` is a permanent new gate surface** to keep wired into `check-all.sh` + CI. Small but real.
- **The persona/role term is unsettled until V5-6.** Docs carry "persona" in the interim; a later doc-only sweep may rename. Risk: a reader meets two terms across the V5-6 boundary. Mitigation: DC-6 in the Amendment log (the former ADR 0016) records the deferral explicitly.
- **Tier confusion.** Operators may mis-file a capability as a persona or vice-versa. Mitigation: the one-sentence inverted-dependency test (DC-1) and the brain as a worked example; `check-personas` catches the most damaging error (a crickets hard-dep) automatically.
- **Surfacing path `[PENDING-IMPL]`.** How an activated persona's body reaches the agent (injected context vs. on-demand sub-agent) is specified by the [persona activation](agentm-persona-activation) design (`build-part 3`), not yet built. The Planner (TPM) manifest (`personas/team-coordinator.md`) therefore exists as a seed that cannot yet be activated.

## Quality Attributes

### Reliability

Graceful degradation is inherited from the resolver: a persona composing an absent capability gets a clean "unavailable," never a crash. A bare agentm runs the brain with zero plugins — the degenerate persona is the floor, so there is no "no persona" failure state.

### Data Integrity

The one-way `requires ⊆ substrate` invariant is the integrity property; `check-personas` is its deterministic check. A persona cannot, by construction passing the gate, introduce a hard dependency that breaks bare-agentm coherence.

### Testability

Every design call here is deterministically checkable: `check-personas` (requires ⊆ substrate), the existing resolver tests (composition), and the existing adapter/parity gates (unknown-kind tolerance). No LLM-judge verification is required for the mechanism. The §10 re-checks above are themselves reproducible greps/reads against the tree.

### Security

`requires ⊆ substrate` also narrows the trust surface: a persona's hard dependencies are confined to agentm-native, in-repo scripts — it cannot hard-bind to an arbitrary external plugin. Soft `enhances:` only ever *reads* capability availability through the resolver; it grants no execution authority.

## Project management

### Parts (for `/design translate`)

Default part split follows the Detailed Design subsections — buildable independently, in order:

1. **`kind: persona` primitive + `personas/` directory** (S) — the schema + one example (the brain manifest).
2. **`check-personas` gate** (M) — `requires ⊆ substrate` assertion, wired into `check-all.sh` + CI, with tests.
3. **On-demand load + surfacing path** (M) `[PENDING-IMPL]` — activation → body load, reusing the memory-load path; resolve the surfacing-path open item.
4. **First real persona** (M, V5-11) — the Planner (TPM) manifest composing dev-loop/board capabilities. Gated behind 1–3.

### Documentation Plan

- The *why* + key calls (the former ADR 0016) — folded into the Amendment log below (2026-06-24).
- This design doc (`wiki/designs/persona-tier.md`) — the canonical "why we built the persona tier."
- At build time: a `wiki/reference/` page for the `kind: persona` schema + `check-personas` contract; an update to [AgentM HLD](agentm-hld) noting the brain-as-degenerate-persona reframe; an `enhances:` cross-reference from [Soft-Composition](../explanation/Soft-Composition).

### Launch Plans

Additive, no flag day — each part lands behind its own gate. The brain persona is a re-description of shipped behavior (no runtime change); the Planner (TPM) is the first net-new persona and ships under V5-11.

## Operations

### Rollback Strategy

Fully reversible. The tier is additive: removing `personas/`, the `kind: persona` handling (a skipped kind today), and `check-personas` returns the tree to its pre-tier state with no data migration. The memory engine is untouched, so a rollback cannot regress it.

## Amendment log

**2026-06-28 — lock-down sweep (operator review).** Removed the redundant `## Document History` table (the AG convention is amendment-log-only; its build status lives in the launched NOTE, its rationale in the folded ADR 0016 below, granular history in git). No content change to the tier contract. Locked as a v5–v8 guidepost.

**2026-06-26 — reconciled the discriminator wording to cross-capability judgment.** The Overview + DC-1 led with "arbitrates," which [personas](agentm-personas) had already recorded as superseded by "cross-capability judgment" — but the tier body was never repointed (a one-directional supersession). The body now leads with the live test and keeps the inverted-dependency direction as its mechanical floor, pointing at [personas](agentm-personas) as the canonical home. No change to the rejected near-miss axes or the `github-projects` counterexample. *Re-audit trigger:* if the canonical discriminator section in [personas](agentm-personas) is renamed or moved, repoint this body's link.

**2026-06-24 — folded ADR 0016 (the persona tier) into this design (AG ADR-migration tail, move-and-retire).** ADR 0016 was the decision record paired with this design (the *why* + the key calls); the held ADR resolved (design-doc amendment 2026-06-24) and folds here, so this design now records both the mechanism (above) and the decision rationale (below). This design **stays** — a live, cited sub-contract, now nudged toward living-design shape. *(0016 refined [AgentM HLD — V5 unbundling](agentm-hld), the substrate/plugin binary it adds the missing third tier to.)*

**The persona tier (2026-06-16; was ADR 0016).** Name a **third tier** — the **persona** — defined by its **dependency direction**, not by what it stores: a standing concern that **composes capabilities it does not own** (arbitrating between them when it composes more than one), is **anchored on the neutral substrate**, and whose hard deps (`requires:`) are **substrate-native only** (soft `enhances:` may name any capability). It owns no engine — a *stance + composition manifest* over engines that stay in crickets. The **brain** (the memory engine) is the degenerate persona (zero composed plugins); the **Planner (TPM)** is the first real persona. The key calls:

- **DC-1 — the signature is the inverted dependency direction.** A plugin is depended *upon*; a persona *composes* and is depended on by nothing — it sits above the capability layer, rooted at the substrate. *Why not classify by "it remembers" / "stateful":* breaks both ways (the Planner (TPM) is stateless; a `security-review` plugin remembers) — neither necessary nor sufficient. *Why not "crosses multiple plugins":* `github-projects` crosses `developer-workflows` and is a plain capability; **cross-capability judgment** is the discriminator, with the inverted substrate-only hard-dep direction as its mechanical floor — see [personas](agentm-personas).
- **DC-2 — a persona is a first-class primitive (`kind: persona` in `personas/`),** not a skill, not a sub-agent. Both hosts' dispatch is positive-match (unknown `kind:` skipped, never rejected); mechanically an agent-def shape, made a *persona* by tier + home + composition-scope, gate-checkable via the new `kind:`. *Why not a skill:* a skill is a capability (the thing a persona composes) — collapses the tier. *Why not a sub-agent:* sub-agents are read-only ephemeral fan-out; a persona is a durable classification.
- **DC-3 — composition reuses `enhances:` + the V5-8 resolver, no `composes:` alias.** *Why not a new keyword:* the soft-composition vocabulary + runtime already exist; a second keyword forks the resolver for identical semantics.
- **DC-4 — `requires ⊆ substrate` + no-always-load, enforced by `check-personas`.** Mechanically holds the *agentm-takes-no-hard-dep-on-crickets* + *bare-agentm-coherent* invariants (the brain is the lone always-on exception). *Why not allow crickets capabilities in `requires:`:* inverts the host/plugin relationship, breaks bare-agentm coherence, reintroduces cross-repo coupling.
- **DC-5 — personas live in agentm, loaded on demand.** *Why not ship from crickets:* a persona must be available regardless of installed plugins (compose-present, degrade-absent) — homing it in crickets couples it to a plugin set and inverts the dependency arrow.
- **DC-6 — agentm keeps "persona"; crickets' looser "persona" → "role" rename is doc-only** (coordinated with V5-6). *Why not rename agentm's tier:* "persona" is the established identity name; the collision is resolved by moving the *looser* use (crickets' four coordinator roles), not the precise one.

*Re-audit triggers:* a persona needs multi-provider composition / conflict resolution (then `enhances:`-reuse is revisited — ADR 0015's "becomes a solver" trigger); either host adds a closed-enum primitive validator that rejects unknown kinds; a persona needs a hard dep only crickets provides (migrate the dep into the substrate, or re-tier the artifact); V5-6 settles agentm's persona/role vocabulary; the underlying model ships a new major (re-audit the tier with the rest of the harness). **Resist the persona-zoo** — add the next persona only when a real cross-capability arbitration concern with no single-plugin home appears.
