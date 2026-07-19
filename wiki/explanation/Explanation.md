<!-- mode: index -->
# Explanation

These pages explain *why* AgentM works the way it does — the choices behind it and the trade-offs each one accepts. If you are looking for technical specifications, see [Reference](Reference). For high-level design, see [Architecture](Architecture).

## What AgentM is

AgentM gives your coding agent a permanent memory. It writes what it learns — about you, and about each project — as plain Markdown notes in a folder you own, brings the right notes back at the right moment, and looks after the collection over time so it gets better instead of messier. Every note is a file: readable, editable, yours, with no hidden database behind it. The agent curates that collection, but you hold the vetoes — nothing durable gets written, linked, or forgotten without you somewhere in the loop.

## The life of a memory

A note moves through six stages between the moment it's noticed and the moment it's quietly retired:

1. **Capture** — reflection notices something durable in a session. A confident, clear-cut signal saves straight to a curated note; a hunch lands in an inbox for you to confirm or drop. See [Experience & Dreaming](agentm-experience-and-dreaming).
2. **Index** — the note is embedded into the vector index and linked to its neighbors by typed edges (`uses`, `fixed`, `supersedes`, and six more), so recall can follow a relationship, not just match text. See [Memory index](agentm-memory-index).
3. **Recall** — every prompt searches the vault by meaning and by keyword, on top of a small always-load floor of standing rules that never has to be searched for.
4. **Heat** — notes that keep getting used float up toward that always-load floor; notes nobody's touched cool back down. Forgetting is a managed policy here, not an accident.
5. **Sleep** — between sessions, dreaming looks over the inbox and the whole corpus. Every proposal is undo-backed. Reversible moves — compression, tidying, and new links — apply automatically and land in the revert log; an inbox duplicate collapses the same way once it's an exact match or a confident model verdict, but a corpus-wide merge, an unresolved fuzzy duplicate, or a flagged contradiction still waits on you. See [Auto-organization](agentm-auto-organization).
6. **Watch** — `/console`, the session-start digest (see [Auto-orchestration](Auto-Orchestration)), and the nightly [health score](Health-Scorecard) keep the collection's shape visible, so drift shows up before it becomes a problem.

## What's here

- **[Product intent](Product-Intent)** — why we created AgentM, and who it is for.
- **[How the pieces fit](How-The-Pieces-Fit)** — how the phases, adapters, templates, and scripts work together.
- **[Memory↔storage seam](Storage-Seam-Concepts)** — why the engine uses an interface for generalized storage access, and why the default store is plain markdown.
- **[Memory↔process seam](Memory-Process-Seam)** — why a process reaches the engine through a client, and why that dependency is one way.
- **[Soft composition and hard composition](Soft-Composition)** — why in plugins `enhances:` and `requires:` stay separate, and what the capability resolver does.
- **[Auto-detect + Auto-configure](Auto-Detect-Configure)** — why the first session proposes a config and waits for your approval, and why it lives in `project.json`.
- **[Auto-orchestration](Auto-Orchestration)** — why the memory skills became something automatic instead of explicitly requested, and how the briefing, idle work, and phase dispatch stay quiet until they are needed.
- **[Single-repo state mode](Single-Repo-State-Mode)** — why AgentM falls back to repo-local state when there is no vault accessible.
- **[GitHub Projects integration](GitHub-Projects-Integration)** — why and how the harness mirrors your work onto a GitHub board.
- **[Named plans](Named-Plans)** — why the harness can hold more than one active plan in a single shared vault.
- **[Health scorecard](Health-Scorecard)** — the nightly health-tier scorecard's own reasoning: why it's advisory-only, and why a dark check counts for neither the family score nor against it.

## See also

[Designs](Designs) · [Architecture](Architecture) · [Reference](Reference) · [Home](Home)
