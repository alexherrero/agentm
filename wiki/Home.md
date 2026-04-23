# agentic-harness Wiki

Dogfood documentation for the harness repo itself. Every page is written for a single reader intent — learning, doing, looking up, or understanding — per the Diátaxis convention ([ADR 0004](0004-diataxis-documentation-spec)).

> [!NOTE]
> This wiki documents agentic-harness for contributors to the harness repo. It is **never** installed into target projects — target projects get [`templates/wiki/`](https://github.com/alexherrero/agentic-harness/tree/main/templates/wiki) instead. See [ADR 0002](0002-documentation-convention) for why.

## 📚 New here? Learn by doing.

- [Tutorial 1 — Your first harness install](01-First-Install) — fresh clone to a healthy installed scratch project in ~5 minutes.

## 🔧 Trying to do something specific?

- [How to install the harness into a project](Install-Into-Project) — add the scaffold to an existing repo.
- [How to refresh an installed harness](Update-Installed-Harness) — pull a newer harness version into a project that already has one.
- [How to cut a release](Cut-A-Release) — tag, changelog, GitHub release via the `ship-release` skill.

## 📖 Looking up a detail?

- [Installer CLI reference](Installer-CLI) — flags, prerequisites, ownership table for `install.sh` / `install.ps1`.
- [CI gates reference](CI-Gates) — what each CI workflow proves and the script behind it.
- [Repo layout reference](Repo-Layout) — top-level directory map and four-adapter parity table.
- [Completed features](Completed-Features) — reverse-chronological log of shipped work.

## 💡 Want to know why?

- [Product intent](Product-Intent) — what problem the harness solves and for whom.
- [How the pieces fit](How-The-Pieces-Fit) — narrative of how phases, adapters, templates, and scripts interact.
- [GitHub Projects integration](GitHub-Projects-Integration) — why and how the harness writes to ProjectsV2.

### Architecture decisions

- [ADR 0001 — Phase-gated workflow](0001-phase-gated-workflow)
- [ADR 0002 — Documentation convention](0002-documentation-convention)
- [ADR 0003 — ProjectsV2 ownership and linking](0003-ProjectsV2-Ownership-And-Linking)
- [ADR 0004 — Diátaxis documentation spec](0004-diataxis-documentation-spec)

## Conventions

Page templates, filename rules, and the Diátaxis four-mode split are described in [`templates/wiki/README.md`](https://github.com/alexherrero/agentic-harness/blob/main/templates/wiki/README.md) — the same conventions this wiki follows.
