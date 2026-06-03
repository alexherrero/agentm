# Installer CLI reference

Command-line reference for `install.sh` (POSIX) and `install.ps1` (Windows / PowerShell 7+).

## ⚡ Quick Reference

| Task | Command |
|---|---|
| First install | `install.sh <target>` |
| Install with verification hooks | `install.sh --hooks <target>` |
| Refresh managed files | `install.sh --update <target>` |
| Refresh + hook update | `install.sh --update --hooks <target>` |
| Install in single-repo (vault-less) mode | `install.sh --local-state <target>` _(pending — lands in Hardening I plan task 3)_ |
| Print help | `install.sh --help` |

## Synopsis

```
install.sh [--hooks] [--update] [--scope user|project] [--local-state] <target-project-path>
install.ps1 [-Hooks] [-Update] [-Scope user|project] [-LocalState] <target-project-path>
```

## Flags

| Flag (bash) | Flag (pwsh) | Effect |
|---|---|---|
| `--hooks` | `-Hooks` | Copy hook scripts into `.harness/hooks/` and merge PostToolUse / PreCompact / SessionStart entries into `.claude/settings.json`. Requires `jq` on POSIX; pwsh uses native JSON cmdlets. |
| `--update` | `-Update` | **True sync** (v1.0.0+): wipe the harness-authored dirs (`.claude/{commands,agents,skills}`, `.agents/{rules,workflows,skills}`, `.gemini/{commands,agents}`, `.harness/{scripts,hooks}`) and recreate from source. Orphan paths from older versions (e.g. the legacy `.agent/` tree, or `.codex/`) are auto-removed. Leaves user-owned files (`.harness/PLAN.md`, `progress.md`, `verify.sh`, `init.sh`, `known-migrations.md`, `AGENTS.md`, `CLAUDE.md`, `wiki/**`) alone. Writes `.harness/.version`. |
| `--scope user\|project` | `-Scope user\|project` | Install scope (default `project`). `--scope user` installs customizations to `~/.claude/` (target path not required) and also merges the AgentMemory payload into `~/.gemini/GEMINI.md` when `~/.gemini/` exists — the Antigravity global channel, so the vault rule applies across every workspace. `--scope project` installs into `<target>/.claude/` as usual. |
| `--local-state` | `-LocalState` | _Pending — lands in Hardening I plan task 3._ Opt the target repo into single-repo (vault-less) state: writes a repo-local `.project-mode=local` marker at `<target>/.harness/.project-mode` and skips vault wiring, so every phase write lands under `<target>/.harness/` with no vault required. See [Single-repo state mode](../explanation/Single-Repo-State-Mode.md). |
| `-h`, `--help` | `-Help` | Print the header comment block from the installer and exit. |

## Prerequisites

| Tool | Purpose | When needed |
|---|---|---|
| `bash` 4+ or `pwsh` 7+ | Host interpreter | Always |
| `git` | Version discovery (`git describe`), state tracking | Always |
| `python3` | Validation and integrity scripts | Always |
| `jq` | JSON merge for hook settings | `--hooks` on POSIX only |
| `gh` | GitHub CLI; used by `ship-release` and any PR/issue flow | Post-install, not by the installer itself |

## Installed tree

| Tree | Owner | `--update` behavior |
|---|---|---|
| `.harness/PLAN.md`, `progress.md`, `features.json`, `init.sh`, `verify.{sh,ps1}`, `known-migrations.md` | User | Skip-if-exists; never overwritten |
| `.harness/scripts/` (telemetry, cross-review) | Harness | Overwritten |
| `.harness/hooks/` | Harness | Overwritten (only with `--hooks`) |
| `.claude/commands/`, `.claude/agents/`, `.claude/skills/` | Harness | Overwritten |
| `.agents/`, `.gemini/` (adapter trees) | Harness | Overwritten on `--update` (wipe-and-recreate from source — see Update-Installed-Harness) |
| `AGENTS.md`, `CLAUDE.md` | User (skip-if-exists) | Left alone |
| `wiki/` scaffold | User | Per-file walk; missing files filled in, existing left alone |
| `.github/workflows/wiki-sync.yml` | Harness | Overwritten |

## Phase commands the installer ships

| Command | Canonical spec |
|---|---|
| `/setup` | [`harness/phases/01-setup.md`](https://github.com/alexherrero/agentm/blob/main/harness/phases/01-setup.md) |
| `/plan` | [`harness/phases/02-plan.md`](https://github.com/alexherrero/agentm/blob/main/harness/phases/02-plan.md) |
| `/work` | [`harness/phases/03-work.md`](https://github.com/alexherrero/agentm/blob/main/harness/phases/03-work.md) |
| `/review` | [`harness/phases/04-review.md`](https://github.com/alexherrero/agentm/blob/main/harness/phases/04-review.md) |
| `/release` | [`harness/phases/05-release.md`](https://github.com/alexherrero/agentm/blob/main/harness/phases/05-release.md) |
| `/bugfix` | [`harness/pipelines/bugfix.md`](https://github.com/alexherrero/agentm/blob/main/harness/pipelines/bugfix.md) |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Argument error (unknown flag, missing or duplicate target path) |
| non-zero | Boundary violation, file I/O error, or failed merge — inspect stderr for the exact message |

## Files

| Path | Purpose |
|---|---|
| [`install.sh`](https://github.com/alexherrero/agentm/blob/main/install.sh) | POSIX installer |
| [`install.ps1`](https://github.com/alexherrero/agentm/blob/main/install.ps1) | Windows installer |
| [`templates/`](https://github.com/alexherrero/agentm/tree/main/templates) | Scaffold copied into every target |
| [`adapters/`](https://github.com/alexherrero/agentm/tree/main/adapters) | Per-tool command / agent / skill trees |

## Related

- [Tutorial 1: Your first harness install](01-First-Install) — end-to-end walkthrough.
- [How to install into an existing project](Install-Into-Project) — recipe for production use.
- [ADR 0002: Documentation convention](0002-documentation-convention) — why the installer boundary exists.
