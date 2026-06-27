<!-- mode: index -->
# Designs

The high-level design docs behind Agent M — its memory layer and the harness tooling around it — the full HLDs for what shipped and what's in flight, where the design started and where it's going. The [Architecture](Architecture) pillar overviews link *down* into these for the deep detail.

## MemoryVault

The permanent agent-memory store and its six implementation parts.

- [MemoryVault](memoryvault) — the design for durable, file-based agent memory.
  - [write-primitives](write-primitives) — how entries are captured and shaped.
  - [recall-loop](recall-loop) — how the right entries surface within a token budget.
  - [reflection-and-recovery](reflection-and-recovery) — self-maintenance + crash recovery.
  - [idea-ledger](idea-ledger) — the incubator for not-yet-acted-on ideas.
  - [seed-pass](seed-pass) — the co-created first pass that bootstraps a vault.
  - [discovery-mining](discovery-mining) — surfacing latent structure from existing notes.

## Architecture-Governance (AG track) — the live parents

The current top-level design parents (AG-track Phase 1, `status: launched`). Children point *up* at these; all children are content-final and launched as of AG Phase 3 (2026-06-24).

- [Foundations](agentm-foundations-hld) — the shared cross-repo "why": the nine principles agentm and crickets both stand on, and how the person (agentm) and its tools (crickets) relate. The root both HLDs inherit by reference.
- [AgentM HLD](agentm-hld) — the agentm parent: the four pillars (Experience · Memory · Opinions · Personas), their components, and where each touches crickets. **Succeeds** the V5 Memory-OS HLD (vault-archived 2026-06-24, AG Wave 2; its V1→V8 arc lives in the AgentM HLD's Evolution-arc subsection).
  - [Memory System](agentm-memory-system) — the seam, backends, write protocol, recall loop, storage layers, V5-14 target gap.
  - [Memory↔Storage Seam](memory-storage-seam) — the storage contract (seam verbs, `Locator` guards, tiers), backends, V5-0 write protocol, routing plane, backend-aware harness state.
  - [Experience & Dreaming](agentm-experience-and-dreaming) — reflection (backward), scheduled learning (forward), heat policy, dreaming, the scheduler.
  - [Opinions & Gates](agentm-opinions-and-gates) — the nine-opinion catalog and the request-by-name model.
  - [Opinion registry](agentm-opinion-registry) — the request-by-name resolver, the entry schema, the catalog the resolver serves.
  - [Personas](agentm-personas) — the persona tier, the ~11-persona roster, the gate, launch modes, role-retirement.
  - [Model + effort routing](agentm-model-effort-routing) — the T0…T4 tier scale (Claude + Gemini), the persona→tier map, the `tier:` manifest axis.
  - [Runner](agentm-runner) — agentm's background-job executor on the hosts' built-in schedulers; writes routed by ownership tier, reported in the digest.

## Evolution & architecture

- The three historical arc HLDs — **Agent Memory Evolution (V1→V8)**, **Device-Wide Architecture (V4)**, and **Memory-OS Architecture (V5)** — were **vault-archived 2026-06-24 (AG Wave 2)**; their still-live framing now lives in the [AgentM HLD](agentm-hld) (the V1→V8 Evolution-arc subsection + the V5 baseline) and [Foundations](agentm-foundations-hld) (the V4 device-wide substrate). Full text: `<vault>/_vault-archive/ag-design-history/`.
- [The persona tier](persona-tier) — the third classification above the substrate/plugin binary: a standing concern that composes capabilities it does not own (brain = degenerate persona; Planner (TPM) = first real persona). Refines the V5 unbundling ([agentm-hld](agentm-hld)); folds the former ADR 0016 (its load-bearing calls).

## See also

[Architecture](Architecture) · [Home](Home)
