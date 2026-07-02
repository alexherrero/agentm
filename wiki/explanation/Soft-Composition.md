<!-- mode: explanation -->
# Soft composition and hard composition

Why plugins keep `enhances:` and `requires:` in two separate fields, and what the capability resolver actually does when a plugin asks whether another is around.

The two fields stay separate because the resolver was built as a deliberately simple checker, not a dependency solver. It does one thing: given a capability name, look it up in a registry of what's installed and answer yes or no. That single choice decides where everything has to go. A dependency the plugin genuinely can't run without has to be a hard `requires:` — the host installer is the only thing that can promise it's present, so the host enforces it. Everything the resolver handles is therefore optional by definition, which is exactly what `enhances:` means: the enhancement is nice to have, and when it's missing the plugin quietly falls back to standalone behavior. The tempting shortcut is to let the resolver chase a mandatory or transitive dependency too, and collapse the two fields into one. We didn't, because a solver would have to fail the plugin when a required piece is absent — and failing installs is the host's contract with the user, not the substrate's. So the resolver stays a static, fail-safe registry lookup keyed on capability: no install-time enforcement, no error when something's unmet, just a boolean the caller can branch on. The rest of this page fills in each half.

## Hard composition: `requires:` (the host's job)

A plugin that *requires* another to function correctly lists it in `requires:`. On Claude Code this serializes to the `dependencies:` array in the marketplace entry; on Antigravity it is listed in the marketplace entry's `requires:` field. The host installer enforces it: if the dependency is absent, the host refuses to install the plugin or marks it as broken.

Hard deps are the host's job. agentm reads the `requires:` field to document it in the marketplace entry, but does not enforce it — that is the host's contract with the user.

## Soft composition: `enhances:` (the substrate's job)

A plugin that *optionally* uses another plugin's capabilities lists them in `enhances:`. Unlike `requires:`, an unmet `enhances:` is not an error — the dependent plugin falls back to its standalone behavior. An `enhances:` entry may carry a version range (`>= 1.2`) to express "I need at least this version for the enhancement to be safe."

**`enhances ∩ requires = ∅`.** A capability that is truly required must be in `requires:` (hard dep, host-enforced). A capability in `enhances:` is by definition optional — if it is not optional, it is in the wrong field. This invariant is enforced by design: agentm's resolver is a single-range check (LC-3), not a solver, so it is structurally incapable of resolving transitive or mandatory deps.

## The runtime: `capability_available()`

When a plugin's phase script checks whether an enhancement is available, it calls `agentm capability <name>` (or imports `capability_available` directly). The resolver:

1. Builds a registry from installed plugins' declared `capabilities:` — one provider per capability, first installed wins.
2. Returns exit 0 (available) or exit 1 (unavailable), with optional version-range matching.
3. Falls through to unavailable on any error — the safe default (LC-4).

The caller never names the providing plugin — it names the *capability*. "git-review" is a capability; a plugin that declares `capabilities: [git-review]` is its provider. The caller is insulated from which plugin provides it.

## Why "vocabulary, not wire"

`enhances:` borrows vocabulary from the MCP / capability-negotiation world (V5-9) but is not a wire protocol. It is a manifest-level declaration, resolved statically at runtime from on-disk JSON files. There is no negotiation, no handshake, no socket — just a registry lookup and a boolean answer.

V5-9 (MCP negotiation) will sit on top of this vocabulary, not replace it. The resolver is the read-only, substrate-level half; MCP will add the wire half. The two are designed to be composable.

## Personas and `enhances:`

> [!NOTE]
> **Status: implemented (V5-12)** — The `kind: persona` primitive reuses `enhances:` for its optional composition. A persona lists capabilities it can exploit in `enhances:`, subject to the same soft-dep semantics described above: unmet entries are not errors, the persona falls back to standalone behavior. The `check-personas` gate does **not** validate `enhances:` entries — soft deps may name any capability (confirmed in [`scripts/check-personas.py`](../../scripts/check-personas.py), which only asserts `requires ⊆ substrate-native` and no-always-load). See [persona-tier-schema reference](persona-tier-schema).

## Related

- [Capability resolver reference](../reference/Capability-Resolver) — the API.
- [Persona tier schema](persona-tier-schema) — the `kind: persona` manifest fields and `check-personas` gate (V5-12).
- [AgentM HLD — Capability discovery](agentm-hld) — the design decisions.
- [Persona tier design](persona-tier) — design decisions for the persona primitive; DC-3 covers `enhances:` reuse.
- [Foundations HLD — crickets split](agentm-foundations-hld) — the C3 principle (substrate beneath, not plugin host).
