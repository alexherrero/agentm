# Compatibility

Hosts and surfaces AgentM is verified to run with.

## Supported hosts

| Host | Adapter dir | Phase commands | Status |
|---|---|---|---|
| **Claude Code** (Anthropic CLI / IDE extension) | [`adapters/claude-code/`](https://github.com/alexherrero/agentm/tree/main/adapters/claude-code) | `/setup` `/plan` `/work` `/review` `/release` `/bugfix` | ✅ first-class — primary development surface, CI-verified on every PR |
| **Antigravity** (Google IDE + Antigravity CLI) | [`adapters/antigravity/`](https://github.com/alexherrero/agentm/tree/main/adapters/antigravity) | Equivalent entrypoints invoked via `AGENTS.md`-aware prompts | ✅ first-class — CI-verified on every PR |

Both adapters are thin shims for agentm's own surfaces — its always-on rules and utility skills. The phase loop those commands invoke ships in the crickets **developer-workflows** plugin since the V5 unbundling (the [AgentM HLD](agentm-hld)); agentm no longer vendors the phase specs. Adding a host means adding an adapter directory and verifying the canonical specs still apply; it needs no harness rewrite.

## Supported operating systems

| OS | Tested via | Frequency |
|---|---|---|
| Linux (`ubuntu-latest`) | [`.github/workflows/tests-linux.yml`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-linux.yml) | Every PR (a plain push runs only the lightweight `syntax` job) |
| macOS (`macos-latest`) | [`.github/workflows/tests-mac.yml`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-mac.yml) | Every PR (worktree-native flow, ratified 2026-07-06 — a plain push runs nothing here) |
| Windows (`windows-latest`, PowerShell 7+) | [`.github/workflows/tests-windows.yml`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-windows.yml) | Every PR (worktree-native flow, ratified 2026-07-06 — a plain push runs nothing here) |

The single aggregate `CI` badge in the README and wiki Home rolls up all three OS workflows into one status. To drill into a failure, click the badge, open the Actions tab, and pick the OS that's failing.

## Vault storage & sync

The MemoryVault is a folder of markdown. On one machine it can live anywhere; to back it up and sync it across devices, pick a transport:

| Option | Status | Best for |
|---|---|---|
| **Google Drive** | ✅ recommended right now | the simplest setup — background sync across devices with nothing to run. See [Back the vault with Google Drive](Back-The-Vault-With-Drive). |
| **git** | forthcoming (turnkey via [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git)) | version history, off-device backup, and a safe chat-write path. Doable manually today; turnkey once `vault-git` ships. |
| **Device-local (no sync)** | ✅ supported | one machine, zero vault/Drive dependency. See [Run without a vault](Run-Without-A-Vault). |

The search index stays device-local either way (`~/.agentm/memory/_meta/`), so it never syncs.

## Sibling repo

AgentM pairs with **[Crickets (`crickets`)](https://github.com/alexherrero/crickets)** — the customization surface (skills, hooks, sub-agents, bundles, MCP servers, slash commands). Crickets is tested on the same OS matrix; both repos ship paired releases per the [Foundations HLD](agentm-foundations-hld).

## Out-of-scope hosts

Hosts that previously had adapters but were dropped:

- **Codex** — dropped in v1.0.0 (2026-05-11) per the [Foundations HLD](agentm-foundations-hld). Surface diverged enough from `AGENTS.md`-aware tooling that maintaining parity wasn't earning its keep.
- **Gemini CLI** — dropped in v2.4.0 (2026-05-17). Google replaced Gemini CLI with the new Antigravity CLI; we follow the upstream consolidation. Antigravity CLI adapter work is roadmap item #17.

## When a host stops working

If a host's CI starts failing or a host's adapter goes stale:

1. Check the host's release notes for surface changes (`.claude/` shape, `.agents/` shape — formerly `.agent/`, command syntax, etc.)
2. Verify the adapter's per-host paths still match — reference: each customization's `supported_hosts` in its manifest
3. Run `bash scripts/smoke-install-bash.sh` locally; if it fails on the affected host, you've reproduced
4. Patch the adapter shim OR the canonical spec it points at, whichever resolves the surface change at the right layer

For new hosts, see the [Foundations HLD](agentm-foundations-hld) for the adapter contract.
