# Compatibility

Hosts and surfaces Agent M is verified to run with.

## Supported hosts

| Host | Adapter dir | Phase commands | Status |
|---|---|---|---|
| **Claude Code** (Anthropic CLI / IDE extension) | [`adapters/claude-code/`](https://github.com/alexherrero/agentm/tree/main/adapters/claude-code) | `/setup` `/plan` `/work` `/review` `/release` `/bugfix` | ✅ first-class — primary development surface, CI-verified on every push |
| **Antigravity** (Google IDE + Antigravity CLI) | [`adapters/antigravity/`](https://github.com/alexherrero/agentm/tree/main/adapters/antigravity) | Equivalent entrypoints invoked via `AGENTS.md`-aware prompts | ✅ first-class — CI-verified on every push |

Both adapters are thin shims for agentm's own surfaces — its always-on rules and utility skills. The phase loop those commands invoke ships in the crickets **developer-workflows** plugin since the V5 unbundling ([ADR 0011](0011-v5-unbundling-dev-loop)); agentm no longer vendors the phase specs. Adding a host = adding an adapter dir + verifying the canonical specs still apply; no harness rewrite needed.

## Supported operating systems

| OS | Tested via | Frequency |
|---|---|---|
| Linux (`ubuntu-latest`) | [`.github/workflows/tests-linux.yml`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-linux.yml) | Every push + every PR |
| macOS (`macos-latest`) | [`.github/workflows/tests-mac.yml`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-mac.yml) | Every push + every PR |
| Windows (`windows-latest`, PowerShell 7+) | [`.github/workflows/tests-windows.yml`](https://github.com/alexherrero/agentm/blob/main/.github/workflows/tests-windows.yml) | Every push + every PR |

The single aggregate `CI` badge in the README + wiki Home rolls up all three OS workflows into one status. Diagnostic drill-down: click the badge → Actions tab → pick the OS that's failing.

## Sibling repo

Agent M pairs with **[Crickets (`crickets`)](https://github.com/alexherrero/crickets)** — the customization surface (skills, hooks, sub-agents, bundles, MCP servers, slash commands). Crickets is tested on the same OS matrix; both repos ship paired releases per [ADR 0006](0006-crickets-split).

## Out-of-scope hosts

Hosts that previously had adapters but were dropped:

- **Codex** — dropped in v1.0.0 (2026-05-11) per [ADR 0005](0005-drop-codex-support). Surface diverged enough from `AGENTS.md`-aware tooling that maintaining parity wasn't earning its keep.
- **Gemini CLI** — dropped in v2.4.0 (2026-05-17). Google replaced Gemini CLI with the new Antigravity CLI; we follow the upstream consolidation. Antigravity CLI adapter work is roadmap item #17.

## When a host stops working

If a host's CI starts failing or a host's adapter goes stale:

1. Check the host's release notes for surface changes (`.claude/` shape, `.agents/` shape — formerly `.agent/`, command syntax, etc.)
2. Verify the adapter's per-host paths still match — reference: each customization's `supported_hosts` in its manifest
3. Run `bash scripts/smoke-install-bash.sh` locally; if it fails on the affected host, you've reproduced
4. Patch the adapter shim OR the canonical spec it points at, whichever resolves the surface change at the right layer

For new hosts, see [ADR 0006](0006-crickets-split) for the adapter contract.
