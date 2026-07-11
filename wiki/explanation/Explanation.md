<!-- mode: index -->
# Explanation

These pages explain *why* AgentM works the way it does — the choices behind it and the trade-offs each one accepts. If you are looking for technical specifications, see [Reference](Reference). For high-level design, see [Architecture](Architecture).

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
