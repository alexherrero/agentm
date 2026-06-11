<!-- mode: index -->
# Explanation

Background and rationale — *why* the harness works the way it does. These pages give the reasoning behind the design; the load-bearing calls themselves are recorded as [Decisions](Decisions), and the structural map is under [Architecture](Architecture).

## What's here

- **[Product intent](Product-Intent)** — what problem the harness solves and for whom.
- **[Auto-detect + auto-configure](Auto-Detect-Configure)** — why first-session config proposes-then-approves, and why it lives in `project.json`.
- **[How the pieces fit](How-The-Pieces-Fit)** — the narrative of how phases, adapters, templates, and scripts interact.
- **[GitHub Projects integration](GitHub-Projects-Integration)** — why and how the harness writes to ProjectsV2.
- **[Auto-orchestration](Auto-Orchestration)** — why the memory skills became a push surface, and how the briefing + idle chain + phase dispatch never nag.
- **[Single-repo state mode](Single-Repo-State-Mode)** — how the harness degrades gracefully to repo-local state when no vault is reachable.

## See also

[Decisions](Decisions) · [Architecture](Architecture) · [Designs](Designs) · [Home](Home)
