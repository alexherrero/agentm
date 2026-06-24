<!-- mode: decision -->
# ADR 0016 — The persona tier: a third classification above the substrate/plugin binary

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-16
> **Roadmap:** **V5-12** (agentm kernel, ROADMAP-MASTER bucket ⑤) — slotted 2026-06-16, sequenced ahead of V5-11 as its substrate (V5-11's chief-of-staff is this tier's first real persona).

## Context

[ADR 0011](agentm-hld) framed agentm's V5 identity as a **binary**: agentm is the neutral memory-OS **substrate** (the memory engine + the plugin host), crickets supplies **capabilities** as plugins, and the dev loop is a **consumer** of the substrate that crickets provides. That binary is correct as far as it goes, but it under-describes one thing agentm already ships and one thing the roadmap wants to ship.

The thing it already ships: the **memory engine itself** is not "just the substrate." The substrate is the storage-agnostic kernel + plugin host — neutral plumbing. The memory engine is a *standing concern* that runs **on** that plumbing: it recalls, reflects, reflects-on-idle, curates always-load entries. ADR 0011's binary files it under "substrate," but it is not plumbing — it is a tenant with behavior, anchored on the neutral kernel, depending on nothing but kernel-native scripts.

The thing the roadmap wants to ship: the **V5-11 chief-of-staff** (the PM depth-maintainer / drift-corrector — see [[multi-agent-orchestration]], [[github-project-sync]]). It is a standing concern that **composes** dev-loop and board capabilities (queue status, board sync) it does **not** own, must work on a bare substrate (degrading to what's installed), and must **not** drag a hard dependency on crickets into agentm. ADR 0011's binary has no slot for it: it is not the kernel, and it cannot be a crickets plugin without inverting the dependency arrow agentm's whole repositioning depends on.

**Open questions this decision resolves:**

- Is there a classification between "the substrate" and "a crickets plugin" — and if so, what is its defining signature?
- Where does such a thing live, and why is that not arbitrary?
- Does it need new machinery, or does it map onto shipped infra (the `requires:` / `enhances:` vocabulary + the V5-8 resolver, [ADR 0015](agentm-hld))?
- How is the bare-agentm-stays-coherent + agentm-takes-no-hard-dep-on-crickets invariant *mechanically* held, not just asserted?

## Decision

Name a **third tier** — the **persona** — and define it by its **dependency direction**, not by what it stores.

A **persona** is a standing concern that **composes capabilities it does not own** — **arbitrating among them** when it composes more than one, making the judgment calls *between* capabilities that none of them could make alone — is **anchored on the neutral substrate**, and whose **hard dependencies (`requires:`) are restricted to substrate-native primitives only**. Its soft composition (`enhances:`) may name any capability, present or absent. It **owns no engine of its own** (V5-11's words: "owns no new engine") — it is a *stance plus a composition manifest* over engines that stay in crickets. It lives in **agentm** for universality and neutrality — *not* because it "remembers."

- The **rememberer** (the memory engine) is the **degenerate persona**: zero composed plugins, `requires:` ⊆ substrate. It is the persona agentm already shipped, now named.
- The **V5-11 chief-of-staff** is the **first real persona**: it composes the dev-loop/board capabilities via `enhances:`, hard-requires only agentm-native scripts.

The mechanism is fully detailed in the [persona-tier design doc](persona-tier); this ADR records the *why* and the load-bearing calls.

### [DC-1] The signature is the inverted dependency direction (LC-1).

A **plugin** is a capability that *others compose* — it is depended **upon**. A **persona** *composes capabilities* and is depended upon by nothing — it sits **above** the capability layer, rooted at the substrate. This inversion is the whole definition; everything else follows from it.

**Why not classify by "it remembers" / "it's stateful":** the axis breaks in *both* directions. The first real persona, the chief-of-staff, is designed **stateless** ("keeps no state of its own between runs") — a persona that does not remember. The planned crickets **security-review** plugin `requires: agentm` *for memory* — it remembers the threat model and dismissed findings, yet is a bounded scan with a done-state — a capability that *does* remember. "Remembers" is therefore neither necessary nor sufficient; it only *feels* right because both personas and memory-coupled capabilities touch the substrate, and reading that touch as "memory" is the error.

**Why not classify by "it crosses multiple plugins":** insufficient — `github-projects` composes `developer-workflows` (it hard-`requires:` it) and is a plain capability. Crossing is not the discriminator; **arbitration** is — making a call *between* the capabilities it composes that none could make alone — together with the inverted, substrate-only hard-dep direction.

Dependency direction is the structural test that classifies all of these correctly: a persona depends *down* onto the substrate and *sideways* (soft) onto capabilities, and is depended upon by nothing.

### [DC-2] A persona is a first-class primitive (`kind: persona` in `personas/`), not a skill and not a sub-agent (LC-2).

Personas are agentm-native definitions under `personas/`, carrying `kind: persona`. Both host installers tolerate the new kind because their primitive dispatch is **positive-match** (`[[ "$_am_kind" == "skill" ]] || continue`), not a closed enum — an unknown kind is skipped, never rejected (verified, §10).

A persona is **mechanically the same shape as an agent-def** — an opinionated prompt + a `tools:` allowlist + a `model:` + declared deps. What makes it a *persona* rather than just an agent-def is **tier, home, and composition-scope** (substrate-anchored, composes-across, `requires ⊆ substrate`), **not a new file format**. The new `kind:` exists to make that classification *gate-checkable*, not because the file is structurally novel.

**Why not model a persona as a skill:** a skill is a *capability* — plugin-tier, the thing a persona composes. Filing a persona as a skill collapses the very tier distinction this ADR draws and points the dependency arrow the wrong way.

**Why not model a persona as a sub-agent:** sub-agents are read-only, ephemeral fan-out ([AGENTS.md](https://github.com/alexherrero/agentm/blob/main/AGENTS.md) rule 6) — dispatched and discarded. A persona is a *durable classification* with hard substrate deps and declared soft composition. A persona may be *surfaced* through a sub-agent at activation time, but it is not one.

### [DC-3] Composition reuses `enhances:` and the V5-8 resolver — no `composes:` alias (LC-3).

"Composes" is the conceptual verb; the on-disk field is **`enhances:`**, resolved by the shipped capability resolver ([ADR 0015](agentm-hld)): capability-keyed, graceful-degrade, one-provider. A persona composes by declaring `enhances:` and letting the resolver answer "is this capability present?" at load time.

**Why not a new `composes:` keyword:** ADR 0015 already owns the soft-composition vocabulary and its runtime. A second keyword forks the resolver, doubles the gate surface, and buys nothing — the semantics are identical (optional, present-or-absent, degrade cleanly). New vocabulary for an existing mechanism is exactly the machinery this tier is designed *not* to add.

### [DC-4] `requires ⊆ substrate` + no-always-load, enforced by a `check-personas` gate (LC-4).

A new static gate, `check-personas`, asserts two things for every file under `personas/`:

1. **`requires: ⊆ substrate`** — every hard-dep entry resolves to an agentm-native primitive/script, never a crickets capability — mechanically holding the **agentm-takes-no-hard-dep-on-crickets** invariant. Soft `enhances:` may name crickets capabilities freely (discovery, never bundling).
2. **No persona manifest lands in the always-load set** — personas are adopted on demand (DC-5), so the gate pins that no persona is injected into every session's system prompt, holding the per-call token floor ([#46](https://github.com/alexherrero/agentm/issues/46) heat / always-load curation). The lone exception is the rememberer, which *is* the existing recall/reflect floor and adds nothing new.

It mirrors the existing one-way seam gates (`check-capability-resolver-one-way.py`, `check-process-seam-import-direction.sh`).

**Why not allow crickets capabilities in `requires:`:** a hard dependency from a neutral-substrate artifact onto a specific plugin inverts the host/plugin relationship V5 establishes, breaks bare-agentm coherence (the rememberer would need a plugin), and reintroduces the cross-repo flag-day coupling ADR 0011 spent its budget removing. The soft/hard split (`enhances ∩ requires = ∅`, ADR 0015 DC-3) is exactly the seam that keeps composition from becoming coupling.

### [DC-5] Personas live in agentm, loaded on demand (LC-5).

The persona's *definition* is host-neutral and lives in agentm; its body is loaded **only when the persona is activated**, reusing the existing on-demand memory-load path (not the always-load floor). The rememberer is the implicit always-on degenerate persona; everything else is dormant until invoked.

**Why not ship personas from crickets:** a persona's defining traits are neutrality and universality — it must be available regardless of which plugins are installed, composing what is present and degrading for what is absent. Homing it in crickets would couple it to crickets' presence and to a specific plugin set, and — fatally — invert the dependency arrow (the capability layer would own the thing that composes it).

### [DC-6] agentm keeps "persona"; the *crickets* "persona"→"role" rename is doc-only and coordinated with V5-6 (LC-6).

The word collides: crickets *already* calls its four coordinator roles "personas" loosely, though its own reference page mostly says **roles** ("Coordinator **Roles**", "each role names a persona"). The resolution is **not** to rename the agentm tier — agentm **keeps "persona"** for this substrate-anchored composition tier. Instead, crickets' looser "persona" usage renames to **"role"** (the dominant word there already), freeing the term for the tier that earns it. Scope is doc-only, zero behavior change: `Coordinator-Roles.md` + the developer-workflows how-to + the four agent descriptions (`worker` / `tech-lead` / `researcher` / `project-manager`). There is no shipped "persona"/"role" *code* vocabulary in either repo to migrate — `kind: persona`, `personas/`, and `check-personas` are agentm-side and unaffected (verified, §10).

The rename rides the **V5-6 narrative shed** (the agentm identity rewrite, ADR 0011 DC-4's "own piece"): both are identity-prose passes, so coordinating them keeps "persona"/"role" from drifting across the boundary.

**Why not rename the agentm tier instead:** "persona" is the load-bearing name — a standing-concern *identity* that composes, distinct from a bounded *role* that wraps one phase command. Renaming agentm's tier to "role" would erase exactly the distinction the four crickets roles fail (they are thin single-capability skins; the persona is the tier above). The collision is resolved by moving the *looser* use, not the *precise* one.

## Consequences

**Positive:**

- **Names what the memory engine always was.** The rememberer makes the tier concrete from day one — operators learn "persona" as "the thing you already have, generalized," not an abstraction.
- **Opens the chief-of-staff slot with zero new runtime.** Composition = the V5-8 resolver; hard-dep restriction = `requires:` + one static gate; load = the existing on-demand path; the primitive = a new `kind:` the loaders already tolerate.
- **Mechanically enforces both hard invariants.** `check-personas` (requires ⊆ substrate) holds *agentm-no-hard-dep-on-crickets*; the degenerate-rememberer rule holds *bare-agentm-coherent*. Neither is left to discipline.
- **Sharpens ADR 0011 without overturning it.** The substrate/plugin split stands; this ADR adds the missing third box and corrects the line that filed the memory engine as "just substrate."
- **No regression to "agentm does everything."** agentm still ships **zero capabilities-as-plugins**; a persona owns **no engine** — it is a stance plus a composition manifest over engines that remain in crickets. The only thing agentm gains is the *home for cross-capability taste*; the dependency arrows never harden agentm→crickets.

**Negative:**

- **A third tier is one more thing to learn**, and a new `kind:` + gate + `personas/` dir is net surface against principle #7 (simplicity) — justified only because ≥2 genuine personas already exist (rememberer + chief-of-staff). Mitigation: the rememberer anchors it; the inverted-dependency test (DC-1) is a one-sentence rule. **Resist the persona-zoo** — add the next persona only when a real cross-capability arbitration concern with no single-plugin home appears.
- **`check-personas` is a new gate to maintain** in `check-all.sh` + CI — a small, permanent surface.
- **A pending crickets doc-rename** (its looser "persona" → "role"), coordinated with V5-6; until it lands, a reader can meet "persona" meaning two things across the repo boundary.
- **The tier's value is architectural, not mechanical.** It buys a neutral home, default-presence, and identity-anchoring — not new runtime. If the operator weights shipping-velocity over the identity-home argument, the **null** (ship the chief-of-staff as a new crickets `coordination` plugin) is a legitimate cheaper path whose cost is precisely "agentm's program-minding taste lives in an optional, swappable plugin." The tier earns its keep on three architectural arguments — the swallow/up-reach dilemma, the home/taste argument, and default-presence (litigated in the [design doc](persona-tier)'s Alternatives) — not on mechanism.

**Load-bearing assumptions (with re-audit triggers):**

- **The V5-8 resolver remains the single soft-composition vocabulary.** Re-audit trigger: a persona needs multi-provider composition or conflict resolution — that is ADR 0015 LC-3's "becomes a solver, needs its own ADR" trigger, and `enhances:`-reuse (DC-3) is revisited.
- **Both hosts' primitive dispatch stays positive-match (unknown `kind:` skipped, not rejected).** Re-audit trigger: either Claude Code or Antigravity adds a closed-enum primitive validator that rejects unknown kinds — then personas need an explicit host-adapter carve-out.
- **A persona's hard deps stay satisfiable by substrate-native primitives** (e.g. `queue_status_lite.py`, verified agentm-native). Re-audit trigger: a persona needs a hard dep only crickets provides — then either that dep migrates into the substrate or the artifact is re-tiered (it was a plugin, not a persona).
- **persona→role naming is deferred, not decided.** Re-audit trigger: V5-6 (the identity rewrite) lands and settles agentm's vocabulary.
- **Scaffolding decays with the model.** Re-audit trigger: the underlying model ships a new major version — re-audit the tier with the rest of the harness (the operator's standing principle).

## Related

- [ADR 0011 — V5 unbundling](agentm-hld) — the binary this ADR refines; the persona is the missing third tier its substrate/plugin split did not name.
- [ADR 0015 — Capability discovery: the `enhances:` runtime](agentm-hld) — the shipped resolver this tier reuses for composition; `enhances ∩ requires = ∅` is the seam `check-personas` extends.
- [ADR 0006 — Split customizations into crickets](agentm-foundations-hld) — C3 (substrate beneath, not plugin host) is the anchoring principle the persona's "lives in agentm" call rests on.
- [Persona tier design doc](persona-tier) — the full mechanism, the §10 re-verification, and the null-hypothesis litigation.
- [Memory-OS Architecture (V5)](memory-os-architecture) — the V5 HLD this tier extends; the kernel/plugin boundary it draws is the one the persona sits above.
