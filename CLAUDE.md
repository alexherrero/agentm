# CLAUDE.md

This project uses [agentm](https://github.com/alexherrero/agentm). The universal instructions live in [AGENTS.md](AGENTS.md) — read that first.

## Claude Code specifics

- The phase loop (`/setup`, `/plan`, `/work`, `/review`, `/release`, `/bugfix`) is provided by the companion crickets **development-lifecycle** plugin — `agentm` no longer ships these commands or their specs (V5 unbundling).
- The harness hooks register themselves in `<prefix>/settings.json` as part of a normal `install.sh` / `install.ps1` run — there is no `--hooks` flag any more. Verification still runs per project: the machine-wide `verify-dispatch` hook resolves the edited file's own project and runs its `.harness/verify.sh` if it has one. This repo has none; copy `templates/verify.sh` to add it.
- Sub-agents in [`.claude/agents/`](.claude/agents/) are the memory-engine pair — `adapt-evaluator` and `memory-idea-researcher`. The review agents (`explorer`, `adversarial-reviewer`, `-cross`) come from the crickets code-review / development-lifecycle plugins.
- **Commit messages: no `Co-Authored-By: Claude` trailer.** See [AGENTS.md § Conventions § Commit messages](AGENTS.md#commit-messages) — the rule is host-agnostic; this bullet is the Claude-specific reminder because Claude Code emits the trailer by default.

For anything not Claude-specific, [AGENTS.md](AGENTS.md) is authoritative.

<!-- Harness conventions auto-load below — moved here from the global ~/.claude/CLAUDE.md for the token-efficiency floor-trim (2026-06-13) so non-agentm projects no longer re-read this AGENTS.md on every tool call. -->
@AGENTS.md
