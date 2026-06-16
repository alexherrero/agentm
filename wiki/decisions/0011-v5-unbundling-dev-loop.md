# ADR 0011 — V5 unbundling: slim the dev loop + migrate-to-diataxis into crickets plugins

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-11

## Context

Through v4.x, `agentm` shipped the phase-gated dev loop as vendored source: the six phase entrypoints (`/setup /plan /work /review /release /bugfix`), the three review sub-agents (`adversarial-reviewer` / `-cross` / `explorer`), the `evidence-tracker` hook, and the `migrate-to-diataxis` skill — each duplicated across all three host adapters (Claude Code / Antigravity / Gemini) plus the shared `harness/` source. [ADR 0006](0006-crickets-split.md) split *customizations* into the sibling `crickets` repo but deliberately kept the dev loop in the harness — the phase workflow was still agentm's protagonist.

Two things changed that make the dev loop's home in agentm the wrong call:

- **crickets now ships the dev loop as launched, dogfood-proven native plugins.** `developer-workflows` / `code-review` (v3.1.0) provide the phase commands and review agents; `wiki-maintenance` (v3.2.0) provides the documentation toolchain including the one-shot Diátaxis migration. Each agentm primitive is now **byte-duplicated** by a crickets plugin that is the more actively maintained copy (per-plugin versioning, soft composition — crickets [ADR 0015](https://github.com/alexherrero/crickets/wiki/0015-partial-revision-36), [ADR 0017](https://github.com/alexherrero/crickets/wiki/0017-enhances-soft-composition)). Maintaining two copies is pure drift risk plus an ongoing parity tax.
- **agentm's V5 identity is "storage-agnostic memory OS + plugin host", not "vendored dev loop."** The memory engine (the recall/reflect hooks, the `memory` skill, `adapt-evaluator` + `memory-idea-researcher`, `harness_memory.py`) is what only agentm provides. The dev loop is a *consumer* of that substrate that any host with the crickets plugins can supply.

This ADR records the **opening slim** of ROADMAP bucket ⑤ ("the unbundling") — scoped narrowly to the dev-loop + docs slices that crickets has already launched. It is **not** full V5: the memory↔storage / memory↔process seams, the device-local default, the vault-as-plugin cutover, and the PM slim (gated on crickets `github-projects`, #41, not yet built) are separate V5 parts. It continues the trajectory of the earlier `documenter` and `diataxis-author` retirements, which moved doc-authoring agents to crickets first.

**Open questions this decision resolves:**

- After the slim, does agentm hard-require crickets, soft-require it with a graceful fallback, or vendor a pointer to it?
- How much of the dev/docs surface is safe to delete *now*, versus entangled with later V5 parts?
- Does `migrate-to-diataxis` go, and does `wiki-author` go with it?
- How far do the docs get rewritten — stop advertising deleted primitives, or rewrite agentm's whole identity?

## Decision

### 1. Clean delete, agentm-unaware — no hard-require, no soft-require, no pointer (DC-2)

Delete the dev-loop primitives outright across all three host adapters and the `harness/` source. A bare agentm install simply **has no** `/plan /work /review` and says nothing about them — the loop is technically optional, provided by crickets when installed, just far less useful without it.

**Why not hard-require crickets:** it would couple the memory engine's install to a dev-loop install it doesn't need. The memory substrate stands alone; forcing the toolkit on every memory-OS user inverts the host/plugin relationship V5 establishes.

**Why not soft-require with a "install crickets" pointer** (the `documenter` / `diataxis-author` graceful-skip pattern): those retirements kept a *surviving agentm dispatch site* that could suggest-then-skip the missing provider. The dev-loop slim deletes the invocation surfaces entirely — once `harness/phases/` is gone there is no `/plan` site to attach fallback text to. A pointer would advertise a capability agentm no longer has. Clean delete is the more honest, and simpler, shape.

**Why not keep the dev loop vendored alongside crickets':** that is the pre-V5 status quo — it re-creates exactly the duplication ADR 0006 began removing and blocks the plugin-host repositioning.

### 2. Slim reach = pure duplicates only (DC-1)

Delete only primitives **byte-duplicated in already-launched, dogfood-proven** crickets plugins: phases 01–05, the bugfix pipeline, the three review agents, the `evidence-tracker` hook, and `migrate-to-diataxis`.

**Why not also slim `harness-context-session-start` + the auto-orchestration push surface now:** they are entangled with V5-5 (the orchestration three-way split the roadmap sequences *before* that delete). Slimming them here would couple two unrelated refactors and risk the memory-engine byte-untouched invariant. They follow the orchestration out at V5-4 / V5-5.

**Why not a bigger-bang full V5:** the seams, device-local default, and vault-as-plugin cutover each carry their own blast radius. Bundling them inflates one atomic diff into an unreviewable flag day. The slim is the *contract* step of an expand→parallel-run→contract cutover that has been in parallel-run since the crickets plugins launched — never a flag day.

### 3. `migrate-to-diataxis` slims; `wiki-author` stays (DC-3)

crickets' `diataxis-author` (crickets [ADR 0008](https://github.com/alexherrero/crickets/wiki/0008-diataxis-author)) absorbed the four-mode one-shot migration via `/diataxis migrate`; it is the canonical converter now, so the agentm skill is deleted. `wiki-author` remains agentm-native (locked in the prior documenter-retire plan), degrading to crickets-graceful for the write step only.

**Why not slim `wiki-author` too:** it was not byte-superseded the way the migration skill was — it is still agentm's native doc-authoring path, not a duplicate of a crickets plugin.

**Why keep the bare name `migrate-to-diataxis` in `doctor`'s prose:** an operator who had the skill pre-V5 needs to know where the capability went. `doctor` names `diataxis-author` as the absorber — truthful provenance, not a pointer for a capability agentm pretends to still offer. This is the one place the deleted name legitimately survives outside `wiki/` + `CHANGELOG.md`.

### 4. Doc reframe is minimal + truthful, not the V5 manifesto (DC-4)

The doc edits only stop the surviving docs from advertising deleted primitives and add this "why" ADR. `AGENTS.md`, `CLAUDE.md`, the Antigravity rules, and the host READMEs reframe the phase loop as *provided by the companion crickets developer-workflows plugin (optional)* — stated as architecture fact, **not** an install instruction (DC-2).

**Why not the full "agentm = memory OS" identity rewrite now:** that is V5-6, its own piece. Conflating "remove false claims" with "rewrite the thesis" balloons a gate-safe doc pass into a manifesto.

## Consequences

**Positive**

- **The parity tax drops again.** `CANON_SKILLS` falls from two entries to one (`doctor`); the dev-loop by-name expectations leave `check-parity` / `validate-adapters` / `check-references` / `check-integrity-*` / `smoke-install-*` / `install.{sh,ps1}`. New dev/docs capability now grows in crickets, at crickets' cadence, with no agentm retrofit.
- **agentm's identity sharpens.** A bare install is the memory engine plus a few utility skills (`doctor`, `wiki-author`, `memory`, `design`, `ship-release`) — smaller, and truthful about what it is.
- **The dev loop evolves independently.** Bug fixes and new phases land in crickets without an agentm release; `verify-phases.sh` (kept) passing post-slim proves agentm's state/dispatch plumbing is decoupled from the moved specs.

**Negative**

- **A bare agentm loses `/plan /work /review`.** This is intended, but it is a real capability loss for anyone who installs agentm without crickets. Mitigation: the repositioning is explicit in the reframed docs and this ADR; the loop is one `crickets` install away.
- **Two-repo coordination** for anyone wanting the full loop — the same cost ADR 0006 accepted, now extended to the dev loop itself.
- **ADR 0006's "two retained skills" assumption is revised.** It assumed the harness keeps `doctor` + `migrate-to-diataxis` and doesn't grow the catalog; the migration skill now moves out, leaving `doctor` (and `wiki-author`, which landed later). This ADR is the documented evolution of that assumption — 0006 is not wholly superseded, only that one load-bearing line.

**Load-bearing assumptions (with re-audit triggers)**

- The crickets `developer-workflows` / `code-review` / `wiki-maintenance` plugins remain the canonical providers and stay launched. **Re-audit trigger:** crickets retires, renames, or regresses any of them below the deleted agentm copies' behavior — then agentm either re-vendors a harness-shaped primitive (not a copy of crickets') or pins a crickets version floor.
- The deleted primitives were true byte-duplicates, not agentm-specific forks. **Re-audit trigger:** a future need for an agentm-shaped phase the crickets generic can't express — it returns to `harness/`, owned by agentm, rather than as a vendored copy.
- DC-1's deferred slice (`harness-context-session-start` + the auto-orchestration push surface) follows the orchestration out later. **Re-audit trigger:** V5-5 lands the three-way orchestration split.
- Scaffolding decays with the model. **Re-audit trigger:** the underlying model ships a new major version — re-audit the whole slim (the operator's standing harness-maintenance principle).

## Related

- [ADR 0001 — Phase-gated workflow](0001-phase-gated-workflow.md) — the workflow whose *primitives* this slim relocates to crickets; the workflow shape itself is unchanged, only its home.
- [ADR 0004 — Diátaxis documentation spec](0004-diataxis-documentation-spec.md) — the convention `migrate-to-diataxis` encoded, now enforced by crickets' `diataxis-author`.
- [ADR 0006 — Split customizations into `crickets`](0006-crickets-split.md) — the precedent; this ADR revises its "two retained skills" load-bearing assumption.
- [ADR 0016 — The persona tier](0016-persona-tier.md) — refines this ADR: the substrate/plugin binary did not name the third tier (the persona) that lives in agentm yet composes capabilities it does not own.
- [Memory-OS Architecture (V5)](memory-os-architecture) — the V5 design this slim opens (bucket ⑤); V5-6 is the deferred identity rewrite.
- [Seven-Section Wiki Convergence](seven-section-convergence) — the prior docs-retirement wave (documenter / diataxis-author) this continues.
- crickets [ADR 0013 — Bundles are native plugins](https://github.com/alexherrero/crickets/wiki/0013-bundles-native-plugins), [ADR 0015 — #36 partial-revision](https://github.com/alexherrero/crickets/wiki/0015-partial-revision-36), [ADR 0017 — Soft composition + three-way developer split](https://github.com/alexherrero/crickets/wiki/0017-enhances-soft-composition) — the toolkit-side decisions that made the dev loop a launched plugin suite.
- crickets [developer-plugin-suite design](https://github.com/alexherrero/crickets/wiki/developer-plugin-suite) — the migration sections that map each agentm primitive to its crickets provider.
- The AgentMemory V5 roadmap (V5-6) — the vault planning doc that sequences this slim ahead of the seams and the PM slim.
