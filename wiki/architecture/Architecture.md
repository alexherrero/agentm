<!-- mode: index -->
# Architecture

AgentM is the substrate an agent runs on: a durable memory engine with four pillars — Memory, Experience, Opinions, and Personas. It installs onto your host (Claude Code or Antigravity), the [crickets](https://github.com/alexherrero/crickets) toolkit composes its capabilities on top, and the memory persists to a storage backend you choose.

![High-level AgentM architecture: an agent runs on a host (Claude Code or Antigravity); the crickets toolkit installs on top and composes onto AgentM one-way through the capability seam; AgentM is the substrate — four pillars (Memory · Experience · Opinions · Personas) on a durable memory engine — which persists through the storage seam to a device-local or Google Drive vault backend](diagrams/agentm-architecture.svg)

## The pieces, and their designs

Everything inherits the shared [Foundations](agentm-foundations-hld), and the [AgentM HLD](agentm-hld) is the parent that frames AgentM as four pillars on a memory engine. The designs under each pillar:

**Memory** — what AgentM remembers, and how it stores and recalls it.

- [Memory System](agentm-memory-system) — how AgentM saves what it learns and pulls the right pieces back when they're useful.
- [Memory↔Storage Seam](memory-storage-seam) — the clean line between the engine and wherever your files actually live, plus the safeguards that keep writes from clobbering each other when devices sync.
  - [Vault Storage & Presentation](agentm-vault-storage-presentation) — where your notes are kept and how they reach your other devices *(proposed)*.
- [Memory index](agentm-memory-index) — the local search index that keeps recall fast and lets it match on meaning, not just keywords.

**Experience** — how AgentM learns and improves between sessions.

- [Experience & Dreaming](agentm-experience-and-dreaming) — how it looks back on its work to learn from it, and reaches out on a schedule to learn new things (its "dreaming").
- [Runner](agentm-runner) — the part that runs AgentM's scheduled background work, using your host's own scheduler.
- [Goal contract](agentm-goal-contract) — the rules for letting AgentM chase a goal on its own: what counts as done, and when to stop.

**Opinions** — the standards AgentM holds about how good work gets done.

- [Opinions & Gates](agentm-opinions-and-gates) — the standards it holds (what *done*, *good*, and *efficient* mean) that any tool can ask for by name.
- [Opinion registry](agentm-opinion-registry) — how a tool asks for one of those standards by name and gets back AgentM's current take.
- [Model + effort routing](agentm-model-effort-routing) — how it picks the right model and effort for a job, to run as cheaply as the work allows without dropping quality.

**Personas** — the roles AgentM steps into for a task.

- [Personas](agentm-personas) — the roles it can take on (Engineer, Reviewer, Planner, and more) and how each one is defined.
  - [Persona tier](persona-tier) — what makes something a role rather than a tool: it borrows capabilities to do a job instead of owning them.
  - [Persona activation](agentm-persona-activation) — how it picks a role and puts it on for the task at hand.

*(The older [MemoryVault](memoryvault) design and its parts are being folded into the designs above; [Designs](Designs) has the full index.)*

## Recent changes

> [!NOTE]
> **Latest: the architecture's designs were reviewed and locked (2026-06-28).** Every high-level design and its sub-designs were finalized — the diagrams standardized to one house style, and each decision recorded in its own design's history. See [Designs](Designs) for the full set.
