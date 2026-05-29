<p align="center">
  <img src="https://raw.githubusercontent.com/alexherrero/agentm/main/assets/agent-m/banner-1600.png" alt="Agent M — The structural backend harness you wished you had">
</p>

<p align="center"><em>The agent harness that gives you the assistant you want — part Star Trek Computer, part J.A.R.V.I.S.</em></p>

<p align="center">
  <a href="https://github.com/alexherrero/agentm/actions/workflows/ci-all.yml"><img src="https://img.shields.io/github/actions/workflow/status/alexherrero/agentm/ci-all.yml?branch=main&style=for-the-badge&label=CI&labelColor=0a0a0a&logo=github&logoColor=f4efe6" alt="CI"></a>
  <a href="https://github.com/alexherrero/agentm/releases/latest"><img src="https://img.shields.io/github/v/release/alexherrero/agentm?label=LATEST&labelColor=0a0a0a&logo=github&logoColor=f4efe6&style=for-the-badge" alt="Latest release"></a>
  <a href="https://github.com/alexherrero/agentm/blob/main/LICENSE"><img src="https://img.shields.io/badge/LICENSE-MIT-f4efe6?labelColor=0a0a0a&style=for-the-badge" alt="License: MIT"></a>
</p>

<p align="center"><sub>Works with Claude Code + Antigravity — <a href="https://github.com/alexherrero/agentm/wiki/Compatibility">see compatibility</a></sub></p>

Agent M is an agentic memory implementation that combines a persistent knowledge layer with personally curated content (i.e. your own notes in markdown format) through a combination of skills, sidecars, and vectorized indexing. Imagine those workflows you saw in the movies. You're talking to your agent, *"Let's open a new file for project M"* and off you go. It remembers your projects and files together, can talk to you about them, and it learns and grows with you as you work. The context it builds is self-maintaining and it improves automatically as you go. No need to spend time maintaining your own knowledge graphs, and it can help you with your personal notes too, when **you** want it to.

Agent M has grown over time across the paired releases of `agentm` and `crickets`. The full V1→V4 evolution — what shipped, what's deferred, where the design is going — lives in [Agent Memory Evolution](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md) on the toolkit side. This page is the entry point for the harness itself.

> [!NOTE]
> This wiki documents the agentm repo for contributors. Target projects get [`templates/wiki/`](https://github.com/alexherrero/agentm/tree/main/templates/wiki) installed instead. See [ADR 0002](0002-documentation-convention) for why.

## Get started

Once both repos are cloned and the vault folder exists, Agent M is operational.

**1. Install both repos as siblings**

```bash
git clone https://github.com/alexherrero/agentm.git ~/Antigravity/agentm
git clone https://github.com/alexherrero/crickets.git    ~/Antigravity/crickets
```

**2. Point the vault at your existing Obsidian + sync setup**

```bash
mkdir -p "<sync-root>/AgentMemory/personal-private/_always-load"
mkdir -p "<sync-root>/AgentMemory/projects"
mkdir -p "<sync-root>/AgentMemory/_meta"
export MEMORY_VAULT_PATH="<sync-root>/AgentMemory"
```

Pre-v4.1.0 vaults used `personal-projects/` (renamed to `projects/` in V4 #26). Existing operators run `bash agentm/scripts/rename-vault-personal-projects.sh` after upgrading. The resolver transparently handles both layouts.

Any sync layer works (Google Drive, Dropbox, syncthing).

**3. Install the quality-gates bundle and the memory skill into your target project**

```bash
bash ~/Antigravity/crickets/install.sh <target-project> --bundle quality-gates
bash ~/Antigravity/crickets/install.sh <target-project> --skill memory
```

The bundle lands the `evaluator` sub-agent + four base hooks (kill-switch, steer, commit-on-stop, evidence-tracker) in one operation.

**4. Seed your always-load entries**

Capture your locked conventions, coding-style rules, project invariants under `<vault>/personal-private/_always-load/`. One entry per concern. The first pass is co-created — you and the agent walk through it together; you approve each entry.

**5. Verify**

```bash
python3 ~/Antigravity/agentm/scripts/harness_memory.py recall --phase setup
```

Should print your always-load entries within the 4000-token budget. Empty = vault is reachable but un-seeded. Errored = `MEMORY_VAULT_PATH` is unset or unreadable.

## 📚 New here? Learn by doing.

- [Tutorial 1 — Your first harness install](01-First-Install) — fresh clone to a healthy installed scratch project in ~5 minutes.

## 🔧 Trying to do something specific?

- [How to install the harness into a project](Install-Into-Project) — add the scaffold to an existing repo.
- [How to configure a new project on first session](Configure-A-New-Project) — detect → propose → approve → persist the per-project enablement config.
- [How to refresh an installed harness](Update-Installed-Harness) — pull a newer harness version into a project that already has one.
- [How to cut a release](Cut-A-Release) — tag, changelog, GitHub release via the `ship-release` skill.
- [How to use auto-context in harness phases](Use-Auto-Context-In-Harness-Phases) — tune MemoryVault recall budgets, save modes, and confidence thresholds for each phase.
- [How to use per-project install](Use-Per-Project-Install) — when to deliberately keep `--scope project` instead of migrating to user scope (CI runners; shared dev hosts; multi-developer dotfiles).

## 📖 Looking up a detail?

- [Installer CLI reference](Installer-CLI) — flags, prerequisites, ownership table for `install.sh` / `install.ps1`.
- [Detection rules reference](Detection-Rules) — the 10 built-in rules and what each attaches a rationale to.
- [Project config reference](Project-Config) — the `project.json` enablement-block schema.
- [Migration tool reference](Migration-Tool) — flag-by-flag for `migrate-to-user-scope.{sh,ps1}` + the 4-state matrix + `.agentm-migrate-record.json` schema.
- [CI gates reference](CI-Gates) — what each CI workflow proves and the script behind it.
- [Repo layout reference](Repo-Layout) — top-level directory map and four-adapter parity table.
- [Compatibility](Compatibility) — supported hosts (Claude Code, Antigravity) + OS matrix + adapter contract.
- [Completed features](Completed-Features) — reverse-chronological log of shipped work.

## 💡 Want to know why?

- [Agent Memory Evolution V1→V4](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md) — the full HLD: where Agent M started, how it grew, where it's going. The architecture, the schema, the workflows, the V4 design space.
- [V3 Retrospective](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/v3-retrospective.md) — what shipped across the V3 arc, what we learned, what's deferred.
- [Product intent](Product-Intent) — what problem the harness solves and for whom.
- [Auto-detect + auto-configure](Auto-Detect-Configure) — why first-session config proposes-then-approves and why it lives in `project.json`.
- [How the pieces fit](How-The-Pieces-Fit) — narrative of how phases, adapters, templates, and scripts interact.
- [GitHub Projects integration](GitHub-Projects-Integration) — why and how the harness writes to ProjectsV2.

### Architecture decisions

- [ADR 0001 — Phase-gated workflow](0001-phase-gated-workflow)
- [ADR 0002 — Documentation convention](0002-documentation-convention)
- [ADR 0003 — ProjectsV2 ownership and linking](0003-ProjectsV2-Ownership-And-Linking)
- [ADR 0004 — Diátaxis documentation spec](0004-diataxis-documentation-spec)
- [ADR 0005 — Drop Codex support; three-adapter scope](0005-drop-codex-support)
- [ADR 0006 — Split customizations into `crickets`](0006-crickets-split)
- [ADR 0007 — Auto-context into harness phases](0007-auto-context-into-harness-phases)

## Conventions

Page templates, filename rules, and the Diátaxis four-mode split are described in [`templates/wiki/README.md`](https://github.com/alexherrero/agentm/blob/main/templates/wiki/README.md) — the same conventions this wiki follows.
