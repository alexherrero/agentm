# Installer CLI reference

Command-line reference for `install.sh` (POSIX) and `install.ps1` (Windows / PowerShell 7+).

## ⚡ Quick Reference

| Task | Command |
|---|---|
| First install | `install.sh <target>` |
| Install with verification hooks | `install.sh --hooks <target>` |
| Refresh managed files | `install.sh --update <target>` |
| Refresh + hook update | `install.sh --update --hooks <target>` |
| Install in single-repo (vault-less) mode | `install.sh --local-state <target>` |
| Flip an existing install to vault-less mode | `agentm_config.py --state-mode local` |
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
| `--local-state` | `-LocalState` | Opt this machine into single-repo (vault-less) state: writes `"state_mode": "local"` to the on-host `.agentm-config.json` and skips vault auto-detection, so every phase write lands under `<repo>/.harness/` with no vault required. Flip an existing install with `agentm_config.py --state-mode` (below). See [Single-repo state mode](Single-Repo-State-Mode). |
| `-h`, `--help` | `-Help` | Print the header comment block from the installer and exit. |

## Config CLI — `agentm_config.py`

`scripts/agentm_config.py` is the operator-facing way to read and set fields on the on-host `.agentm-config.json` (the single config file — the vault holds data, config is on-host only) without re-running the installer. Resolves the install prefix from `AGENTM_INSTALL_PREFIX`, else `~/.claude/`.

| Operation | Effect |
|---|---|
| `--vault-path <path>` | Set the vault path (validates the dir exists). Writes `plugins.obsidian-vault.vault_path` + `storage.backend=vault` on the on-host `.agentm-config.json` (V5-7 config-plane, tasks 1+2 shipped). `--get vault_path` reads the plugin-namespaced key first, then falls back to the legacy flat `vault_path` key. `--unset vault_path` removes both keys. Backs `harness_memory.py::vault_path()` when `$MEMORY_VAULT_PATH` is unset. |
| `--state-mode <local\|vault>` | Set `state_mode` — the device-level run mode. `local` opts a vault-less machine into repo-local state; `vault` switches back. Idempotent; mutually exclusive with `--vault-path`. See [Single-repo state mode](Single-Repo-State-Mode). |
| `--get <field>` | Read a single field to stdout; `rc=0` if present, `rc=1` (silent) if absent. |
| `--list` | Dump the full config as JSON. |
| `--unset <field>` | Clear a single field. |
| `--storage-backend <name>` | Set `storage.backend` — the named [storage backend](Storage-Seam#backend-selection-part-5) the memory engine selects (`device-local`, `vault`, or a plugin-provided name). Stored under the literal flat key `"storage.backend"` ([`agentm_config.py#L151`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L151)), so it round-trips through `--get storage.backend` / `--unset storage.backend`. Idempotent. Validates **non-empty only** — it does **not** check the backend is registered, so an as-yet-uninstalled backend stays configurable; the resolver's fail-loud guard handles a missing plugin at resolve time (the polished install-the-plugin error lands in part-5 task 3). Unset → the resolver picks from the existing config (fresh → `device-local`; an existing `vault_path` → `vault`). See [Choose a storage backend](Choose-A-Storage-Backend). |

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
| `.agents/` (Antigravity adapter tree) | Harness | Overwritten on `--update` (wipe-and-recreate from source — see Update-Installed-Harness) |
| `.gemini/` (vestigial dropped-host adapter — Gemini CLI, dropped v2.4.0; still emitted pending reconciliation, see [Compatibility](Compatibility)) | Harness | Overwritten on `--update` |
| `AGENTS.md`, `CLAUDE.md` | User (skip-if-exists) | Left alone |
| `wiki/` scaffold | User | Per-file walk; missing files filled in, existing left alone |
| `.github/workflows/wiki-sync.yml` | Harness | Overwritten |

## Phase commands

The phase loop (`/setup` `/plan` `/work` `/review` `/release` `/bugfix`) is **not** shipped by the agentm installer — it lives in the crickets **developer-workflows** plugin since the V5 unbundling ([ADR 0011](agentm-hld)). agentm no longer vendors the phase specs; the installer drops the state substrate (`.harness/`, `.claude/`, `.agents/`) those phases run against.

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
- [ADR 0002: Documentation convention](seven-section-convergence) — why the installer boundary exists.
- [ADR 0009: On-host state-mode config](memory-storage-seam) — why `--local-state` / `--state-mode` write to `.agentm-config.json` and never to the vault.
