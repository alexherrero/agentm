<!-- mode: index -->
# Designs

Design documents for AgentM, organized by parent/child with high-level or parent designs (HLD) followed by their children. This is a complete index of all current and proposed designs. 

The [Architecture](Architecture) page lists those designs that have been implemented but organized by architectural pillar for clarity.

## Foundation

[Foundations](agentm-foundations-hld) — (*Final*) lays out the nine principles that guide the development of agentm and crickets, and how the person (agentm) and its tools (crickets) relate. Both HLDs inherit it by reference.

## AgentM HLD

[AgentM HLD](agentm-hld) — is the high-level parent design for AgentM. It is an overview of it's four pillars (Memory · Experience · Opinions · Personas) and how they are built on a durable memory engine, and where each touches crickets.

| Design                                                                          | What it covers                                                                                       |  Status  |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | :------: |
| [Memory System](agentm-memory-system)                                           | The engine end to end — how memory is stored, written, and recalled                                  |  Final   |
| &nbsp;&nbsp;↳ [Capture](agentm-capture)                                         | Getting a thought or article into memory from your phone, browser, or a chat — the staging front door |  Final   |
| &nbsp;&nbsp;↳ [Auto-Organization](agentm-auto-organization)                     | Keeping the vault tidy on its own — write-time linking, tidying by age, dedup, and lint                |  Final   |
| [Memory↔Storage Seam](memory-storage-seam)                                      | The contract memory uses to reach storage, so the backend can change without touching the engine     |  Final   |
| &nbsp;&nbsp;↳ [Vault Storage & Presentation](agentm-vault-storage-presentation) | Where the vault lives and how it syncs to your devices                                               | Proposed |
| [Memory index](agentm-memory-index)                                             | The metadata table behind fast, hybrid recall                                                        |  Final   |
| [Experience & Dreaming](agentm-experience-and-dreaming)                         | How AgentM learns between sessions — reflection, scheduled learning, dreaming                        |  Final   |
| [Runner](agentm-runner)                                                         | The background-job executor that runs scheduled work on the host's own scheduler                     |  Final   |
| [Goal contract](agentm-goal-contract)                                           | The contract for a persona pursuing an objective on an autonomous run                                |  Final   |
| [Opinions & Gates](agentm-opinions-and-gates)                                   | The standards AgentM holds, and asking for one by name                                               |  Final   |
| [Opinion registry](agentm-opinion-registry)                                     | The resolver, the entry schema, and the catalog it serves                                            |  Final   |
| [Model + effort routing](agentm-model-effort-routing)                           | Matching each role to the right model and reasoning effort                                           |  Final   |
| [Personas](agentm-personas)                                                     | The roster of roles, the gate that admits them, and how they launch                                  |  Final   |
| [Persona activation](agentm-persona-activation)                                 | Selecting and adopting a persona at runtime                                                          |  Final   |
| [The persona tier](persona-tier)                                                | The classification the roster sits in — a standing concern that composes capabilities it doesn't own |  Final   |

## MemoryVault *(legacy — folding into the designs above)*

The original durable-memory design and its six implementation parts. Its still-live substance is being split into the memory designs above and then retired; kept here until that pass completes.

| Design | What it covers |
|---|---|
| [MemoryVault](memoryvault) | The original design for durable, file-based agent memory |
| &nbsp;&nbsp;↳ [write-primitives](write-primitives) | How entries are captured and shaped |
| &nbsp;&nbsp;↳ [recall-loop](recall-loop) | How the right entries surface within a token budget |
| &nbsp;&nbsp;↳ [reflection-and-recovery](reflection-and-recovery) | Self-maintenance and crash recovery |
| &nbsp;&nbsp;↳ [idea-ledger](idea-ledger) | The incubator for not-yet-acted-on ideas |
| &nbsp;&nbsp;↳ [seed-pass](seed-pass) | The co-created first pass that bootstraps a vault |
| &nbsp;&nbsp;↳ [discovery-mining](discovery-mining) | Surfacing latent structure from existing notes |

## The evolution arc

The three historical arc HLDs — Agent Memory Evolution, Device-Wide Architecture, and Memory-OS Architecture — were archived to the vault. Their still-live framing now lives in the [AgentM HLD](agentm-hld) (the evolution-arc subsection) and [Foundations](agentm-foundations-hld) (the device-wide substrate).

## See also

[Architecture](Architecture) · [Home](Home)
