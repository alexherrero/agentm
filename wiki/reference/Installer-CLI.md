# Installer CLI reference

This is your command-line reference for `install.sh` (POSIX) and `install.ps1` (Windows / PowerShell 7+).

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
install.sh [--hooks] [--update] [--scope user|project] [--local-state] [--mcp-server] <target-project-path>
install.ps1 [-Hooks] [-Update] [-Scope user|project] [-LocalState] <target-project-path>
```

## Flags

| Flag (bash) | Flag (pwsh) | Effect |
|---|---|---|
| `--hooks` | `-Hooks` | Copy hook scripts into `.harness/hooks/` and merge PostToolUse / PreCompact / SessionStart entries into `.claude/settings.json`. Requires `jq` on POSIX; pwsh uses native JSON cmdlets. |
| `--update` | `-Update` | **True sync** (v1.0.0+): wipe the harness-authored dirs (`.claude/{commands,agents,skills,hooks}`, `.agents/{rules,workflows,skills}`, `.gemini/{commands,agents}`, `.harness/{scripts,hooks}`) and recreate from source. Orphan paths from older versions (e.g. the legacy `.agent/` tree, or `.codex/`) are auto-removed. Leaves user-owned files (`.harness/PLAN.md`, `progress.md`, `verify.sh`, `init.sh`, `known-migrations.md`, `AGENTS.md`, `CLAUDE.md`, `wiki/**`) alone. Writes `.harness/.version`. |
| `--scope user\|project` | `-Scope user\|project` | Install scope (default `project`). `--scope user` installs customizations to `~/.claude/` (target path not required) and also merges the AgentMemory payload into `~/.gemini/GEMINI.md` when `~/.gemini/` exists — the Antigravity global channel, so the vault rule applies across every workspace. `--scope project` installs into `<target>/.claude/` as usual. |
| `--local-state` | `-LocalState` | Opt this machine into single-repo (vault-less) state: writes `"state_mode": "local"` to the on-host `.agentm-config.json` and skips vault auto-detection, so every phase write lands under `<repo>/.harness/` with no vault required. Flip an existing install with `agentm_config.py --state-mode` (below). See [Single-repo state mode](Single-Repo-State-Mode). |
| `--mcp-server` | *(bash only)* | Generate a launchd plist for the memory MCP daemon. macOS only. |
| `-h`, `--help` | *(no pwsh equivalent)* | `install.sh -h`/`--help` prints the header comment block from the installer and exits. `install.ps1` has no help flag — passing one fails PowerShell parameter binding. |

## Config CLI — `agentm_config.py`

You use `scripts/agentm_config.py` to read and set fields on the on-host `.agentm-config.json`. This is your single config file. The vault holds data. The config lives only on the host. You do not need to re-run the installer. The script resolves the install prefix from `AGENTM_INSTALL_PREFIX`. If that is unset, it defaults to `~/.claude/`.

| Operation | Effect |
|---|---|
| `--vault-path <path>` | Set the vault path (validates the dir exists). Writes `plugins.obsidian-vault.vault_path` + `storage.backend=vault` on the on-host `.agentm-config.json` (V5-7 config-plane, tasks 1+2 shipped). `--get vault_path` reads the plugin-namespaced key first, then falls back to the legacy flat `vault_path` key. `--unset vault_path` removes both keys. Backs `harness_memory.py::vault_path()` when `$MEMORY_VAULT_PATH` is unset. |
| `--state-mode <local\|backend>` | Set `state_mode` — the device-level run mode. `local` opts a vault-less machine into repo-local state; `backend` switches back. `vault` is still accepted but is a deprecated alias — the code normalizes it to `backend` on write (LC-5). Idempotent; mutually exclusive with `--vault-path`. See [Single-repo state mode](Single-Repo-State-Mode). |
| `--get <field>` | Read a single field to stdout; `rc=0` if present, `rc=1` (silent) if absent. |
| `--list` | Dump the full config as JSON. |
| `--unset <field>` | Clear a single field. |
| `--storage-backend <name>` | Set `storage.backend` — the named [storage backend](Storage-Seam#backend-selection-part-5) the memory engine selects (`device-local`, `vault`, or a plugin-provided name). Stored under the literal flat key `"storage.backend"` ([`agentm_config.py#L60`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L60)), so it round-trips through `--get storage.backend` / `--unset storage.backend`. Idempotent. Validates **non-empty only** — it does **not** check the backend is registered, so an as-yet-uninstalled backend stays configurable; the resolver's fail-loud guard handles a missing plugin at resolve time (the polished install-the-plugin error lands in part-5 task 3). Unset → the resolver picks from the existing config (fresh → `device-local`; an existing `vault_path` → `vault`). See [Choose a storage backend](Choose-A-Storage-Backend). |
| `--notify-enabled <true\|false>` | Set `plugins.autonomy.notify_enabled` — opt in/out of the daily on-device notification ([`agentm_config.py#L77`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L77)). Absent by default; the notification channel graceful-skips until set `true`. Idempotent. |
| `--email-to <address>` | Set `plugins.autonomy.email_to` — the recipient for the once-daily digest email ([`agentm_config.py#L78`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L78)). Validates non-empty only (no email-format check — same fail-loud-at-use-time philosophy as `--storage-backend`). Idempotent. |
| `--email-smtp-url <url>` | Set `plugins.autonomy.email_smtp_url` — the operator's own SMTP relay or on-device mail agent, never a third-party push service ([`agentm_config.py#L79`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L79)). Validates non-empty only. Idempotent. |

All three keys are now live. `--notify-enabled`: [`scripts/health/session_notify.py`](https://github.com/alexherrero/agentm/blob/main/scripts/health/session_notify.py) reads `plugins.autonomy.notify_enabled` and fires a once-daily macOS notification when it's `true` — see [Enable on-device notifications](Enable-On-Device-Notifications). `--email-to` + `--email-smtp-url`: [`scripts/health/session_email.py`](https://github.com/alexherrero/agentm/blob/main/scripts/health/session_email.py) reads both together (either alone is treated as unconfigured) and sends the daily digest over the configured SMTP relay — see [Enable email digest delivery](Enable-Email-Digest-Delivery). Both channels also have runner-job manifests now (`templates/jobs/observability-notify-daily.yaml`, `templates/jobs/observability-email-daily.yaml`) — copy either into the gitignored `.harness/jobs/` to have the local runner invoke it daily; absent that copy, each still needs a manual invocation. See [Autonomy — Delivery](agentm-autonomy#delivery--getting-it-in-front-of-you) for the full channel design.

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
| `.claude/commands/`, `.claude/agents/`, `.claude/skills/`, `.claude/hooks/` | Harness | Overwritten |
| `.agents/` (Antigravity adapter tree) | Harness | Overwritten on `--update` (wipe-and-recreate from source — see Update-Installed-Harness) |
| `.gemini/` (vestigial dropped-host adapter — Gemini CLI, dropped v2.4.0; still emitted pending reconciliation, see [Compatibility](Compatibility)) | Harness | Overwritten on `--update` |
| `AGENTS.md`, `CLAUDE.md` | User (skip-if-exists) | Left alone |
| `wiki/` scaffold | User | Per-file walk; missing files filled in, existing left alone |
| `.github/workflows/wiki-sync.yml` | Harness | Overwritten |

## Phase commands

The agentm installer does **not** ship the phase loop (`/setup` `/plan` `/work` `/review` `/release` `/bugfix`). You will find this loop in the crickets **developer-workflows** plugin. You can read about the V5 unbundling in the [AgentM HLD](agentm-hld). The agentm repository no longer vendors the phase specs. Instead, the installer drops the state substrate (`.harness/`, `.claude/`, `.agents/`). Your phases run against this substrate.

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

- [Tutorial 1: Your first harness install](01-First-Install) — Read this end-to-end walkthrough.
- [How to install into an existing project](Install-Into-Project) — Follow this recipe for production use.
- [Foundations HLD](agentm-foundations-hld) — Learn why the installer boundary exists.
- [Memory-storage seam — On-host state-mode config](memory-storage-seam) — Understand why `--local-state` / `--state-mode` write to `.agentm-config.json` and never to the vault.
