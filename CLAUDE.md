# CLAUDE.md

This project uses [agentm](https://github.com/alexherrero/agentm). The universal instructions live in [AGENTS.md](AGENTS.md) — read that first.

## Claude Code specifics

- The phase loop (`/setup`, `/plan`, `/work`, `/review`, `/release`, `/bugfix`) is provided by the companion crickets **developer-workflows** plugin — `agentm` no longer ships these commands or their specs (V5 unbundling).
- Verification hooks (typecheck / lint / test on Write|Edit) are configured in [`.claude/settings.json`](.claude/settings.json) when `install.sh --hooks` (POSIX) or `install.ps1 -Hooks` (Windows/PowerShell 7+) is run.
- Sub-agents in [`.claude/agents/`](.claude/agents/) are the memory-engine pair — `adapt-evaluator` and `memory-idea-researcher`. The review agents (`explorer`, `adversarial-reviewer`, `-cross`) come from the crickets code-review / developer-workflows plugins.
- **Commit messages: no `Co-Authored-By: Claude` trailer.** See [AGENTS.md § Conventions § Commit messages](AGENTS.md#commit-messages) — the rule is host-agnostic; this bullet is the Claude-specific reminder because Claude Code emits the trailer by default.

For anything not Claude-specific, [AGENTS.md](AGENTS.md) is authoritative.

<!-- Harness conventions auto-load below — moved here from the global ~/.claude/CLAUDE.md for the token-efficiency floor-trim (2026-06-13) so non-agentm projects no longer re-read this AGENTS.md on every tool call. -->
@AGENTS.md
