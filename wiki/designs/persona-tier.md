# The persona tier: a standing concern that composes capabilities it does not own

> [!NOTE]
> **Status:** final (locked) — 2026-06-16. Design-only pass; build pickup deferred to a build session.
> **Position in arc:** refinement of [`memory-os-architecture.md`](memory-os-architecture) (the V5 "unbundling" HLD) and [ADR 0011](../decisions/0011-v5-unbundling-dev-loop). Pairs with [ADR 0016](../decisions/0016-persona-tier), which records the load-bearing calls.
> **Method:** the locked 10-section design template (the V5-11 design method).

## Context

### Objective

ADR 0011 split agentm's world in two — a neutral memory-OS **substrate** and crickets **capabilities** (plugins) — but that binary has no slot for a standing concern that *composes* capabilities without *owning* them, like the upcoming V5-11 PM chief-of-staff. This design names that missing third tier — the **persona** — defines it by its inverted dependency direction, and maps it entirely onto shipped infrastructure so it costs **no new machinery**. The payoff is a classification that keeps composition from silently becoming cross-repo coupling.

### Background

The memory engine has always been more than plumbing: it recalls, reflects, and curates — a tenant with behavior anchored on the neutral kernel, depending on nothing but kernel-native scripts. ADR 0011's binary files it under "substrate," which under-describes it. At the same time the roadmap wants the **V5-11 chief-of-staff** (the PM depth-maintainer / drift-corrector, see [[multi-agent-orchestration]], [[github-project-sync]]): a standing concern that composes dev-loop and board capabilities it does not own, must run on a bare substrate, and must not drag a hard dependency on crickets into agentm.

Two shipped pieces make naming this tier cheap. [ADR 0015](../decisions/0015-capability-discovery) (V5-8) shipped the `enhances:` soft-composition runtime — a capability-keyed, graceful-degrade resolver with `enhances ∩ requires = ∅`. And the host installers already dispatch primitives by **positive match** on `kind:`, so an unknown kind is skipped, not rejected. The third tier therefore needs no new resolver, no new loader, and no new host adapter — only a name, a directory, and one gate.

The constraints that drive every call below: **bare agentm must stay coherent** (the memory engine needs zero plugins) and **agentm must take no hard dependency on crickets** (discovery, never bundling). Both are invariants from ADR 0011 / ADR 0006; this design holds them *mechanically* rather than by discipline.

## Design

### Overview

A **persona** is a standing concern that **composes capabilities it does not own**, is **anchored on the neutral substrate**, and whose **hard dependencies (`requires:`) are restricted to substrate-native primitives only**. Its soft composition (`enhances:`) may name any capability, present or absent. It lives in agentm for universality and neutrality — not because it "remembers."

The defining signature is the **inverted dependency direction**. A plugin is a capability that *others compose* — it is depended **upon**. A persona *composes capabilities* and is depended upon by nothing — it sits **above** the capability layer, rooted at the substrate. The **rememberer** (the memory engine) is the **degenerate persona**: zero composed plugins, `requires:` ⊆ substrate — the persona agentm already shipped, now named. The **V5-11 chief-of-staff** is the **first real persona**: it composes dev-loop/board capabilities via `enhances:`, hard-requires only agentm-native scripts.

### Infrastructure

**N/A: no new infrastructure.** The tier is a classification mapped onto shipped pieces — the table below is reuse, not new components.

| Concern | Shipped mechanism reused | New surface |
|---|---|---|
| Primitive kind | Positive-match `kind:` dispatch in `install.{sh,ps1}` | `kind: persona` (a value, not a code path) |
| Home on disk | `harness/`-style primitive directory | `personas/` directory |
| Composition (soft) | V5-8 resolver, `enhances:` ([ADR 0015](../decisions/0015-capability-discovery)) | none — `enhances:` reused verbatim |
| Hard-dep restriction | `requires:` + one-way seam gates | `check-personas` (one new static gate) |
| Load | Existing on-demand memory-load path | none — dormant-until-activated |

Trigger shape is unchanged: `check-personas` runs where the other `check-*` gates run (local `check-all.sh` + CI on push); persona bodies load at activation time, not at session start.

### Detailed Design

**The primitive.** Each persona is a markdown file under `personas/` with frontmatter — `kind: persona`, `requires: [...]` (substrate-native scripts/primitives), `enhances: [...]` (composed capabilities) — and a body carrying the standing-concern instructions. The rememberer is the degenerate case: `enhances: []`, `requires:` ⊆ substrate.

**Composition is `enhances:`, not a new keyword.** "Composes" is the conceptual verb; the on-disk field is `enhances:`, resolved by the V5-8 resolver. A persona names the capabilities it wants and the resolver answers "present on this host?" at load time, degrading cleanly when absent. There is deliberately **no `composes:` alias** — that would fork a resolver and a vocabulary that already exist, for identical semantics.

**The gate is the enforcement.** `check-personas` asserts, for every file under `personas/`, that each `requires:` entry resolves to an agentm-native primitive (never a crickets capability). This is what mechanically holds *agentm-no-hard-dep-on-crickets*; soft `enhances:` is unrestricted. It mirrors `check-capability-resolver-one-way.py` and `check-process-seam-import-direction.sh` — a one-way dependency assertion, statically checkable, added to the gate battery.

**Load is on demand.** A persona body loads only when the persona is activated, reusing the existing on-demand memory-load path — not the always-load floor. The rememberer is the implicit always-on degenerate persona; everything else is dormant until invoked. A persona may be *surfaced* through a sub-agent at activation, but it is not a sub-agent (it is a durable classification, not ephemeral fan-out).

**The naming question (deferred).** The mechanism ships as `kind: persona` / `personas/` / `check-personas`. Whether the operator-facing *term* later becomes "role" (aligning with crickets' tech-lead / worker "role" vocabulary) is a pure doc-only call deferred to **V5-6** (the identity rewrite) — see Re-verification below for why it is zero-code.

#### Re-verification against shipped infrastructure (§10 re-checks cleared)

This design treats the pre-audit as a pre-audit; the load-bearing assumptions were re-checked against the live codebase, not assumed:

1. **`queue_status_lite` is agentm-native.** ✓ `scripts/queue_status_lite.py` carries the read logic; its docstring states "this is the agentm read logic; the crickets `/queue-status-lite` command surface … wraps it." A chief-of-staff persona may therefore `requires:` it without breaching `requires ⊆ substrate`.
2. **Both hosts tolerate an unknown `kind: persona`.** ✓ Installer dispatch is positive-match (`[[ "$_am_kind" == "skill" ]] || continue` in `install.sh`); `validate-adapters.py` and `check-references.py` validate named surfaces by directory + required keys, with **no closed `kind:` enum**. An unknown kind is skipped, never rejected. (`project_config.py`'s `kind not in ("skill","hook")` guard is the project-config toggle path, not the primitive loader, and personas are not project-config-toggled.)
3. **`enhances:` reuse, not a `composes:` alias.** ✓ ADR 0015 already owns the soft-composition field + runtime; the semantics a persona needs (optional, present-or-absent, degrade) are identical. Reuse; do not alias.
4. **persona→role rename is doc-only.** ✓ No shipped "persona"/"role" code vocabulary exists in agentm (the "role" language lives only in crickets agent descriptions). Renaming the operator-facing term touches no `kind:` value, no gate name, no directory — it is prose, deferred to V5-6 to land once.
5. **The null hypothesis, re-litigated** — see Alternatives Considered.

## Alternatives Considered

**Null hypothesis — "a persona is just a plugin (or a sub-agent); no third tier is needed." Rejected.**

- **Inverted dependency direction.** A plugin is composed *by* others; a persona *composes* and is composed by nothing. Filing a persona as a crickets plugin creates plugin→plugin hard-dep chains — exactly what ADR 0015's `enhances ∩ requires = ∅` and one-provider/no-solver rules forbid. Restricting persona `requires:` to substrate-native keeps the hard-dep graph acyclic and rooted at the neutral substrate.
- **Neutrality + universality.** A persona must be available regardless of installed plugin set, composing what is present and degrading for what is absent. Homing it in crickets couples it to crickets' presence and a specific plugin set, and inverts the dependency arrow (the capability layer would own the thing that composes it). Anchoring on agentm makes it universal: bare agentm has the rememberer; add crickets and the chief-of-staff composes the dev loop.
- **Zero new mechanism.** The tier reuses the V5-8 resolver (composition), `requires:` + one static gate (hard-dep restriction), the on-demand memory-load path (load), and positive-match dispatch (a new `kind:`). It is an **architectural** axis — where a thing lives and which way its deps point — not a **mechanical** one. That is precisely why it is worth adding (a free classification) and precisely why it is *not* just a plugin (a plugin points the other way). **The tier buys architecture, not mechanism.**

**Model a persona as a skill. Rejected** — a skill is a capability (the thing a persona composes); filing a persona as a skill collapses the tier distinction and inverts the arrow.

**Model a persona as a sub-agent. Rejected** — sub-agents are read-only ephemeral fan-out (AGENTS.md rule 6); a persona is a durable classification with hard substrate deps and declared soft composition. It may be surfaced through a sub-agent, but it is not one.

**Add a `composes:` keyword. Rejected** — see DC-3 in [ADR 0016](../decisions/0016-persona-tier); identical semantics to `enhances:`, so a second keyword is pure machinery.

## Dependencies

- **[ADR 0015](../decisions/0015-capability-discovery) / the V5-8 resolver** (`scripts/capability_resolver.py`) — the composition runtime, reused verbatim. Hard prerequisite (already shipped).
- **The positive-match `kind:` dispatch** in `install.{sh,ps1}` — relied on for unknown-kind tolerance (already shipped).
- **V5-6 (identity rewrite)** — *coordinates with*, does not block: the persona→role naming call is parked there. No build dependency.
- **No dependency on crickets.** The chief-of-staff persona composes crickets capabilities via soft `enhances:` only; `check-personas` forbids any crickets entry in `requires:`.

## Migrations

**N/A: no existing state to migrate.** The tier is additive — a new `kind:`, a new directory, a new gate. The memory engine is *re-described* as the degenerate rememberer persona but is not moved or rewritten; no on-disk artifact changes. A later, separate step may author the memory engine's persona manifest, but this design does not require touching shipped memory-engine code.

## Technical Debt & Risks

- **`check-personas` is a permanent new gate surface** to keep wired into `check-all.sh` + CI. Small but real.
- **The persona/role term is unsettled until V5-6.** Docs carry "persona" in the interim; a later doc-only sweep may rename. Risk: a reader meets two terms across the V5-6 boundary. Mitigation: ADR 0016 DC-6 records the deferral explicitly.
- **Tier confusion.** Operators may mis-file a capability as a persona or vice-versa. Mitigation: the one-sentence inverted-dependency test (DC-1) and the rememberer as a worked example; `check-personas` catches the most damaging error (a crickets hard-dep) automatically.
- **Surfacing path is unspecified here.** *How* an activated persona's body reaches the agent (injected context vs. on-demand sub-agent) is left to the build — flagged so it is not mistaken for "already decided."

## Quality Attributes

### Reliability

Graceful degradation is inherited from the resolver: a persona composing an absent capability gets a clean "unavailable," never a crash (ADR 0015 DC-5). A bare agentm runs the rememberer with zero plugins — the degenerate persona is the floor, so there is no "no persona" failure state.

### Data Integrity

The one-way `requires ⊆ substrate` invariant is the integrity property; `check-personas` is its deterministic check. A persona cannot, by construction passing the gate, introduce a hard dependency that breaks bare-agentm coherence.

### Testability

Every load-bearing call is deterministically checkable: `check-personas` (requires ⊆ substrate), the existing resolver tests (composition), and the existing adapter/parity gates (unknown-kind tolerance). No LLM-judge verification is required for the mechanism. The §10 re-checks above are themselves reproducible greps/reads against the tree.

### Security

`requires ⊆ substrate` also narrows the trust surface: a persona's hard dependencies are confined to agentm-native, in-repo scripts — it cannot hard-bind to an arbitrary external plugin. Soft `enhances:` only ever *reads* capability availability through the resolver; it grants no execution authority.

## Project management

### Parts (for `/design translate`)

Default part split follows the Detailed Design subsections — buildable independently, in order:

1. **`kind: persona` primitive + `personas/` directory** (S) — the schema + one example (the rememberer manifest).
2. **`check-personas` gate** (M) — `requires ⊆ substrate` assertion, wired into `check-all.sh` + CI, with tests.
3. **On-demand load + surfacing path** (M) — activation → body load, reusing the memory-load path; resolve the surfacing-path open item.
4. **First real persona** (M, V5-11) — the chief-of-staff manifest composing dev-loop/board capabilities. Gated behind 1–3.

### Documentation Plan

- [ADR 0016](../decisions/0016-persona-tier) — shipped with this design (the *why*).
- This design doc (`wiki/designs/persona-tier.md`) — the canonical "why we built the persona tier."
- At build time: a `wiki/reference/` page for the `kind: persona` schema + `check-personas` contract; an update to [`memory-os-architecture.md`](memory-os-architecture) noting the rememberer-as-degenerate-persona reframe; an `enhances:` cross-reference from [Soft-Composition](../explanation/Soft-Composition).

### Launch Plans

Additive, no flag day — each part lands behind its own gate. The rememberer persona is a re-description of shipped behavior (no runtime change); the chief-of-staff is the first net-new persona and ships under V5-11.

## Operations

### Rollback Strategy

Fully reversible. The tier is additive: removing `personas/`, the `kind: persona` handling (a skipped kind today), and `check-personas` returns the tree to its pre-tier state with no data migration. The memory engine is untouched, so a rollback cannot regress it.

## Document History

| Date | Change | Status |
|---|---|---|
| 2026-06-16 | Initial authoring from the personas-vs-capabilities pre-audit; §10 re-checks cleared against shipped infra; paired with ADR 0016. | final |
