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

**Agent M** is a phase-gated agent harness with a persistent memory layer. The harness gives the dev loop hard boundaries — Setup · Plan · Work · Review · Release · Bugfix — so an agent executes one phase at a time against on-disk state instead of freestyling the whole lifecycle. The MemoryVault gives it a durable, file-based memory that carries your preferences, project state, and learned lessons across sessions and across projects. Imagine the workflows you saw in the movies: you talk to your agent, it remembers your projects and your notes, and it improves automatically as you work — no knowledge graph to hand-maintain. Agent M has grown across the paired releases of `agentm` (this harness) and [`crickets`](https://github.com/alexherrero/crickets) (the toolkit of skills, hooks, and sub-agents that ride on top).

> [!NOTE]
> This wiki documents the `agentm` repo for contributors. Projects that *install* the harness get [`templates/wiki/`](https://github.com/alexherrero/agentm/tree/main/templates/wiki) scaffolded into them instead — see [ADR 0002](0002-documentation-convention) for why the two are kept separate.

## 📚 Get started

Agent M is two sibling repos plus a vault folder. Clone both, point the vault at your sync setup, and the harness is operational.

- [Tutorial — your first harness install](01-First-Install) — fresh clone to a healthy installed scratch project in ~5 minutes.
- [Install the harness into a project](Install-Into-Project) — add the scaffold to an existing repo.
- [Run without a vault](Run-Without-A-Vault) — operate the harness with no MemoryVault configured (repo-local state).

## 🔧 What do you want to do?

| What | How-to |
|---|---|
| 🧱 **Stand up a project** — detect → propose → approve → persist the per-project config | [Configure a new project](Configure-A-New-Project) |
| ⬆️ **Pull a newer harness** into a project that already has one | [Update an installed harness](Update-Installed-Harness) |
| 🚀 **Cut a release** — tag, changelog, GitHub release | [Cut a release](Cut-A-Release) |
| 🧠 **Tune the memory** — recall budgets, save modes, confidence thresholds per phase | [Use auto-context in phases](Use-Auto-Context-In-Harness-Phases) · [Tune auto-orchestration](Tune-Auto-Orchestration) |
| 🩺 **Keep the vault healthy** — lint it, find missing note links | [Audit the vault](Audit-The-Vault) · [Find missing note links](Find-Missing-Note-Links) |
| 🌐 **Read the vault from any agent** — Claude.ai · Gemini · ChatGPT · Antigravity | [Use AgentMemory in any agent](Use-AgentMemory-In-Any-Agent) |
| 🖥️ **Keep state per-repo** — when to stay `--scope project` | [Use per-project install](Use-Per-Project-Install) |
| 🔌 **Expose the vault as an MCP server** — Claude Code · Cursor · Goose · Claude Desktop | [Stand up the memory MCP server](Stand-Up-Memory-MCP-Server) |

## 📖 Look up a detail

For contributors running the harness:

- [Compatibility](Compatibility) — supported hosts (Claude Code · Antigravity) + OS matrix + the adapter contract.
- [Installer CLI](Installer-CLI) — flags, prerequisites, and the ownership table for `install.sh` / `install.ps1`.
- [CI gates](CI-Gates) — what each CI workflow proves and the script behind it.
- [Completed features](Completed-Features) — the reverse-chronological log of shipped work.
- [Memory MCP tools](Memory-MCP-Tools) — four-tool MCP surface: parameters, pagination, soft-delete contract, error codes.

> [!NOTE]
> **Latest release — [v5.8.0](https://github.com/alexherrero/agentm/releases/tag/v5.8.0) (2026-06-19):** V5-7 config-plane migration — `vault_path` now reads/writes the plugin-namespaced key (`plugins.obsidian-vault.vault_path`); `--vault-path` writes the plugin key + `storage.backend=vault`; `choose_protocol` loses its `vault_root` parameter; vault selection is always explicit via `storage.backend`. First-read migration is atomic; legacy flat key falls back transparently. ([ADR 0013 amendment](0013-storage-seam-fail-loud-selection))

→ Full field-level detail lives under **Reference** in the sidebar.

## 🏛️ How it's built

The structural component map — six components, each a folder under **Architecture** in the sidebar. → **[Browse the architecture](Architecture)**

- [AgentMemory](AgentMemory) — the MemoryVault substrate: durable, file-based memory across sessions and projects.
- [Device-Wide Substrate](Device-Wide-Substrate) — how the harness and its memory span every project on the machine.
- [Phases](Phases) — the phase-gated workflow with hard boundaries and on-disk state.
- [Orchestration and Auto-Detection](Orchestration-And-Auto-Detection) — auto-wiring the right phase + context by detecting a project's shape.
- [Host adapters](Host-Adapters) — adapting to each host (Claude Code · Antigravity 2.0 · Antigravity CLI).
- [Toolkit interface ↔ crickets](Toolkit-Interface) — the seam with the sibling toolkit: what each owns, how they compose.

## 🧩 Major designs

The high-level designs behind Agent M's memory layer — the full HLDs, where the design started and where it's going. → **[Browse all designs](Designs)**

- [MemoryVault](memoryvault) — permanent agent memory: [write-primitives](write-primitives) · [recall-loop](recall-loop) · [reflection-and-recovery](reflection-and-recovery) · [idea-ledger](idea-ledger) · [seed-pass](seed-pass) · [discovery-mining](discovery-mining).
- [Agent Memory Evolution V1→V4](agent-memory-evolution) — the full HLD: where Agent M started, how it grew, where it's going.
- [Device-Wide Architecture](device-wide-architecture) — the device-wide design Agent M targets.
- [Memory-OS Architecture (V5)](memory-os-architecture) — the V5 memory-OS HLD.

## 💡 Why it works the way it does

- [Product intent](Product-Intent) — what problem the harness solves and for whom.
- [Auto-detect + auto-configure](Auto-Detect-Configure) — why first-session config proposes-then-approves and lives in `project.json`.
- [How the pieces fit](How-The-Pieces-Fit) — the narrative of how phases, adapters, templates, and scripts interact.
- [GitHub Projects integration](GitHub-Projects-Integration) — why and how the harness writes to ProjectsV2.
- [Auto-orchestration](Auto-Orchestration) — why the memory skills became a push surface that never nags.
- [Single-repo state mode](Single-Repo-State-Mode) — how the harness degrades to repo-local state when no vault is reachable.
- [Memory↔process seam](Memory-Process-Seam) — why a process reads memory through a one-way, read-only client instead of reaching into the engine.

## 📐 Architecture decisions

Every load-bearing call is recorded as an ADR — the "why X, and why not Y" trail, with re-audit triggers. → **[Browse all decisions](Decisions)**
