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

## Evolution & architecture

- [Agent Memory Evolution V1→V4](agent-memory-evolution) — the full HLD: where Agent M started, how it grew, where it's going.
- [Device-Wide Architecture](device-wide-architecture) — the device-wide design Agent M targets.
- [Memory-OS Architecture (V5)](memory-os-architecture) — the V5 memory-OS HLD.
- [The persona tier](persona-tier) — the third classification above the substrate/plugin binary: a standing concern that composes capabilities it does not own (rememberer = degenerate persona; V5-11 chief-of-staff = first real persona). Refines ADR 0011 · pairs with ADR 0016.

## Documentation & tooling

- [Seven-Section Wiki Convergence](seven-section-convergence) — converging agentm's documentation spec + tooling onto the seven-section wiki taxonomy crickets standardized (amend ADR 0004 · retire the duplicate `diataxis-author` copy toward crickets · reshape `templates/wiki/`).

## See also

[Architecture](Architecture) · [Decisions](Decisions) · [Home](Home)
