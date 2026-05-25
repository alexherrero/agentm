# CLAUDE.md

This project uses [agentm](https://github.com/alexherrero/agentm). The universal instructions live in [AGENTS.md](AGENTS.md) — read that first.

## Claude Code specifics

- Slash commands (`/setup`, `/plan`, `/work`, `/review`, `/release`, `/bugfix`) are in [`.claude/commands/`](.claude/commands/). They point back to the canonical phase specs in [`harness/phases/`](harness/phases/).
- Verification hooks (typecheck / lint / test on Write|Edit) are configured in [`.claude/settings.json`](.claude/settings.json) when `install.sh --hooks` (POSIX) or `install.ps1 -Hooks` (Windows/PowerShell 7+) is run.
- Sub-agents live in [`.claude/agents/`](.claude/agents/) — `explorer` (read-only fan-out) and `adversarial-reviewer` (critic).
- **Commit messages: no `Co-Authored-By: Claude` trailer.** See [AGENTS.md § Conventions § Commit messages](AGENTS.md#commit-messages) — the rule is host-agnostic; this bullet is the Claude-specific reminder because Claude Code emits the trailer by default.

For anything not Claude-specific, [AGENTS.md](AGENTS.md) is authoritative.
