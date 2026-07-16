# Compatibility

This page lists the hosts and surfaces AgentM is verified to run with.

## Supported hosts

| Host | Adapter dir | Phase commands | Status |
|---|---|---|---|
| **Claude Code** (Anthropic CLI / IDE extension) | [`adapters/claude-code/`](https://github.com/alexherrero/agentm/tree/main/adapters/claude-code) | `/setup` `/plan` `/work` `/review` `/release` `/bugfix` | ✅ first-class — primary development surface, CI-verified on every PR |
| **Antigravity** (Google IDE + Antigravity CLI) | [`adapters/antigravity/`](https://github.com/alexherrero/agentm/tree/main/adapters/antigravity) | Equivalent entrypoints invoked via `AGENTS.md`-aware prompts | ✅ first-class — CI-verified on every PR |

Both adapters act as thin shims for agentm's own surfaces. These surfaces include always-on rules and utility skills. The phase loop these commands invoke ships in the crickets **developer-workflows** plugin. This began with the V5 unbundling per the [AgentM HLD](agentm-hld). AgentM no longer vendors the phase specs. To add a host, you add an adapter directory. You verify the canonical specs still apply. You do not rewrite the harness.

## Supported operating systems

| OS | Tested via | Frequency |
|---|---|---|
| Linux (`ubuntu-latest`) | [`.github/workflows/tests-linux.yml`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-linux.yml) | Every PR (a plain push runs only the lightweight `syntax` job) |
| macOS (`macos-latest`) | [`.github/workflows/tests-mac.yml`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-mac.yml) | Every PR (worktree-native flow, ratified 2026-07-06 — a plain push runs nothing here) |
| Windows (`windows-latest`, PowerShell 7+) | [`.github/workflows/tests-windows.yml`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-windows.yml) | Every PR (worktree-native flow, ratified 2026-07-06 — a plain push runs nothing here) |

The single aggregate `CI` badge in the README and wiki Home rolls up all three OS workflows into one status. To drill into a failure, you click the badge. You open the Actions tab. You pick the failing OS.

## Vault storage & sync

The MemoryVault is a folder of markdown. You can place it anywhere on your machine. To back it up and sync it across devices, you pick a transport:

| Option | Status | Best for |
|---|---|---|
| **Google Drive** | ✅ recommended right now | the simplest setup — background sync across devices with nothing to run. See [Back the vault with Google Drive](Back-The-Vault-With-Drive). |
| **git** | forthcoming (turnkey via [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git)) | version history, off-device backup, and a safe chat-write path. Doable manually today; turnkey once `vault-git` ships. |
| **Device-local (no sync)** | ✅ supported | one machine, zero vault/Drive dependency. See [Run without a vault](Run-Without-A-Vault). |

The search index stays device-local (`~/.agentm/memory/_meta/`). It never syncs.

## Sibling repo

AgentM pairs with **[Crickets (`crickets`)](https://github.com/alexherrero/crickets)**. Crickets provides the customization surface. This includes skills, hooks, sub-agents, bundles, MCP servers, and slash commands. CI tests Crickets on the same OS matrix. Both repos ship paired releases per the [Foundations HLD](agentm-foundations-hld).

## Out-of-scope hosts

Hosts that previously had adapters but were dropped:

- **Codex** — dropped in v1.0.0 (2026-05-11) per the [Foundations HLD](agentm-foundations-hld). The surface diverged from `AGENTS.md`-aware tooling. Maintaining parity took too much effort.
- **Gemini CLI** — dropped in v2.4.0 (2026-05-17). Google replaced Gemini CLI with the new Antigravity CLI. AgentM follows the upstream consolidation. Antigravity CLI adapter work is roadmap item #17.

## When a host stops working

If a host's CI starts failing or an adapter goes stale:

1. You check the host's release notes for surface changes (`.claude/` shape, `.agents/` shape — formerly `.agent/`, command syntax, etc.).
2. You verify the adapter's per-host paths still match. You reference each customization's `supported_hosts` in its manifest.
3. You run `bash scripts/smoke-install-bash.sh` locally. A failure on the affected host reproduces the bug.
4. You patch the adapter shim or the canonical spec. You choose the layer that resolves the surface change.

For new hosts, you read the [Foundations HLD](agentm-foundations-hld) for the adapter contract.
