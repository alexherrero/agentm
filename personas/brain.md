---
kind: persona
name: brain
requires: []
enhances: []
description: >
  The standing concern that recalls, reflects, and curates the operator's memory
  across sessions. Anchors on the neutral substrate (harness_memory, storage_seam);
  composes no external capabilities. The degenerate persona — zero composed plugins,
  requires ⊆ substrate — the persona agentm already shipped, now named.
tier: T0
opinions: []
modes: [sub-agent, interactive, loop, goal]
triggers: []
---

# The brain

The brain is agentm's first persona and its degenerate case: a standing concern that runs entirely on substrate-native primitives, composes no external capabilities, and is the always-present floor for every bare agentm installation.

## Standing concern

The brain carries agentm's recall, reflection, and curation behavior — the tenant with behavior that ADR 0011 filed as "substrate" but that is more precisely a *persona*: a classification with inverted dependency direction, anchored on the neutral kernel, owning no new engine. It runs on `harness_memory`, `storage_seam`, and the other agentm-native scripts; nothing in crickets is a hard dependency.

## Degenerate form

`requires: []` — no substrate primitives are declared as hard deps (the memory engine's own scripts satisfy them implicitly as the always-present floor).  
`enhances: []` — no external capabilities composed.

This is the minimal valid persona: one that is already fully operational on a bare substrate, and that the `check-personas` gate validates as the conformance example for `requires ⊆ substrate` + no-always-load.

## Activation axes

`tier: T0` — the roster (`agentm-personas.md`) lists the brain's tier as the range T0–T1 (mechanical read/reflect through light curation); the manifest's `tier:` field takes a single value, so it pins the cheaper floor of that range, matching the brain's degenerate, always-cheap nature. `opinions: []` and `triggers: []` are empty — the brain leans on no named Opinion surface and answers no workflow-step or detection trigger, because it is never explicitly adopted; it composes automatically beneath every other persona as a pipeline step (`agentm-persona-activation.md` § "Memory beneath is automatic"), not through the normal select → gate → load path. `modes: [sub-agent, interactive, loop, goal]` lists the full set rather than a genuine "always-on" fifth mode (which `check-personas.py`'s `_VALID_MODES` does not define) — it is realized in whichever mode the persona it sits beneath is running in, so it supports all four rather than declaring one of its own.

## Related

- [ADR 0016 — The persona tier](../wiki/decisions/0016-persona-tier.md) — the decision that named this tier and defined the brain as its degenerate first instance.
- [Persona tier design](../wiki/designs/persona-tier.md) — the full mechanism and the §10 re-verification against shipped infrastructure.
