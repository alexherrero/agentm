<!-- mode: index -->
# AgentMemory

_Durable memory in a world of ephemeral context — a file-based vault that working agents read and write, chat surfaces only read, and every session starts from._

AgentMemory is the harness's long-term memory: a plain-Markdown vault, synced through Google Drive, that outlives any single session or repo. It holds three kinds of thing — your standing preferences and conventions, per-project state, and lessons learned along the way — as one fact per file with YAML frontmatter, so a fresh session recalls what the last one knew without you re-explaining it.

## How it works

The vault is a directory tree, not a database. Entries live under a few well-known regions, and access splits by *who is asking*:

| Region | Holds | Loaded |
|---|---|---|
| `personal/_always-load/` | standing preferences, conventions, durable fixes | every session start |
| `projects/<name>/` | per-project goals, plans, progress | on demand, by project |
| `_meta/` | audit reports, embeddings cache, orchestration state | by the tooling, not the agent |

Access is **asymmetric by surface**: the filesystem working agents you run directly (Claude Code, Antigravity) read *and* write the vault following your entry conventions; chat surfaces (Claude.ai, Claude Desktop) read and *suggest* entries but never write. A read-only vault lint and a personal-notes link-discovery audit keep the corpus on-spec — both surface fixes for you to apply, never editing an entry themselves.

## How it fits

- **[Device-Wide Substrate](Device-Wide-Substrate)** — the vault is *the* device-wide store. Memory spans every project because it lives outside any one repo, on the machine, not in the tree.
- **[Orchestration and Auto-Detection](Orchestration-And-Auto-Detection)** — the push/pull surfaces that read and write vault state at session start and phase boundaries.
- **[Phases](Phases)** — phase state (PLAN, progress) is written to the vault, not held in the conversation; that is what lets one phase per session stay coherent.

## See also

Detail:

- [AgentMemory context payload](AgentMemory-Context-Payload) — the block you paste into any agent surface to point it at the vault.
- [Vault lint checks](Vault-Lint-Checks) · [Note relatedness signals](Note-Relatedness-Signals) — the audit catalogs.
- [Use AgentMemory in any agent](Use-AgentMemory-In-Any-Agent) · [Audit the vault](Audit-The-Vault) — the how-to recipes.

Designs:

- [MemoryVault](memoryvault) — the write-primitives → recall → reflection build, part by part.
- [Agent Memory Evolution V1→V4](agentm-hld) · [Memory-OS Architecture (V5)](agentm-hld) — where it came from and where it's going.

[Architecture](Architecture) · [Designs](Designs) · [Home](Home)
