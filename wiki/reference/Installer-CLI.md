# Installer CLI reference

This is your command-line reference for `install.sh` (POSIX) and `install.ps1` (Windows / PowerShell 7+).

## ⚡ Quick Reference

| Task | Command |
|---|---|
| Install (or refresh — it is the same command) | `install.sh` |
| Install in single-repo (vault-less) mode | `install.sh --local-state` |
| Install the memory daemon as a launchd agent | `install.sh --daemon` |
| Install without fetching the embedding model | `install.sh --no-embedder` |
| Flip an existing install to vault-less mode | `agentm_config.py --state-mode local` |
| Print help | `install.sh --help` |

## Synopsis

```
install.sh [--local-state] [--daemon|--no-daemon] [--no-embedder]
install.ps1 [-LocalState]
```

There is no target path. AgentM installs to `$AGENTM_INSTALL_PREFIX`
(default `~/.claude/`) and is then available in every project on the machine.

## Flags

| Flag (bash) | Flag (pwsh) | Effect |
|---|---|---|
| `--local-state` | `-LocalState` | Opt this machine into single-repo (vault-less) state: writes `"state_mode": "local"` to the on-host `.agentm-config.json` and skips vault auto-detection, so every phase write lands under `<repo>/.harness/` with no vault required. Flip an existing install with `agentm_config.py --state-mode` (below). See [Single-repo state mode](Single-Repo-State-Mode). |
| `--daemon` | *(bash only)* | Build the Go memory daemon and install it as a launchd agent so it survives a reboot. macOS only. Needed once — afterwards every install run rebuilds and reloads it automatically. |
| `--no-daemon` | *(bash only)* | Skip that automatic refresh for this run. |
| `--no-embedder` | *(bash only)* | Do not fetch the embedding model (~330MB). The daemon then runs lexical-only: hybrid retrieval is unavailable and every status surface says so. |
| `--mcp-server` | *(bash only)* | **Retired.** Generated a launchd plist for the Python FastMCP memory server, which the Go daemon replaced on port 7821. The flag now refuses with exit 2 rather than installing a second agent to fight the real one for the port. Use `--daemon`. |
| `-h`, `--help` | *(no pwsh equivalent)* | `install.sh -h`/`--help` prints the header comment block from the installer and exits. `install.ps1` has no help flag — passing one fails PowerShell parameter binding. |

### Retired flags

`--scope`, `--update` and `--hooks` (and their pwsh `-Scope` / `-Update` /
`-Hooks` twins) were removed with the per-project install. They **fail** rather
than being ignored, each naming its replacement — a stale script passing one is
announcing an expectation the installer no longer meets, and a silent no-op
would let that ride until something downstream broke.

- `--scope` — there is one install scope.
- `--update` — re-running the installer *is* the refresh. Source-mode installs
  are symlinks that never go stale; release-mode installs re-copy every run.
- `--hooks` — the harness hooks install automatically, this one included. The
  per-project verification hook it used to wire up is now the machine-wide
  `verify-dispatch` hook: registered once, it resolves the *edited file's* own
  project at fire time and runs that project's `.harness/verify.sh`. Authoring
  one is still per project and still optional — copy `templates/verify.sh`.

## Config CLI — `agentm_config.py`

You use `scripts/agentm_config.py` to read and set fields on the on-host `.agentm-config.json`. This is your single config file. The vault holds data. The config lives only on the host. You do not need to re-run the installer. The script resolves the install prefix from `AGENTM_INSTALL_PREFIX`. If that is unset, it defaults to `~/.claude/`.

**Re-running the installer keeps what you set here.** Both programs write this file: the installer owns `schema_version`, `mode`, `source_clones`, `installed_at`, `harness_version`, `vault_path`, `state_mode`, `installer_source`, `installed_shas` and `fragments`, and it overwrites only those. Everything else — every key in the table below, and anything a later plugin adds — is carried forward untouched on each re-persist. That was not always true: until the fix recorded in the [CHANGELOG](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md), the installer rebuilt the file from its own key list, so every `plugins.*` and `storage.*` key was deleted on each `install.sh` run. If you ran an installer from before that fix, `agentm_config.py --list` shows what survived; re-set anything missing.

| Operation | Effect |
|---|---|
| `--vault-path <path>` | Set the vault path (validates the dir exists). Writes `plugins.obsidian-vault.vault_path` + `storage.backend=vault` on the on-host `.agentm-config.json` (V5-7 config-plane, tasks 1+2 shipped). `--get vault_path` reads the plugin-namespaced key first, then falls back to the legacy flat `vault_path` key. `--unset vault_path` removes both keys. Backs `harness_memory.py::vault_path()` when `$MEMORY_VAULT_PATH` is unset. |
| `--state-mode <local\|backend>` | Set `state_mode` — the device-level run mode. `local` opts a vault-less machine into repo-local state; `backend` switches back. `vault` is still accepted but is a deprecated alias — the code normalizes it to `backend` on write (LC-5). Idempotent; mutually exclusive with `--vault-path`. See [Single-repo state mode](Single-Repo-State-Mode). |
| `--get <field>` | Read a single field to stdout; `rc=0` if present, `rc=1` (silent) if absent. |
| `--list` | Dump the full config as JSON. |
| `--unset <field>` | Clear a single field. |
| `--storage-backend <name>` | Set `storage.backend` — the named [storage backend](Storage-Seam#backend-selection-part-5) the memory engine selects (`device-local`, `vault`, or a plugin-provided name). Stored under the literal flat key `"storage.backend"` ([`agentm_config.py#L60`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L60)), so it round-trips through `--get storage.backend` / `--unset storage.backend`. Idempotent. Validates **non-empty only** — it does **not** check the backend is registered, so an as-yet-uninstalled backend stays configurable; the resolver's fail-loud guard handles a missing plugin at resolve time (the polished install-the-plugin error lands in part-5 task 3). Unset → the resolver picks from the existing config (fresh → `device-local`; an existing `vault_path` → `vault`). See [Choose a storage backend](Choose-A-Storage-Backend). |
| `--notify-enabled <true\|false>` | Set `plugins.autonomy.notify_enabled` — opt in/out of the daily on-device notification ([`agentm_config.py#L77`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L77)). Absent by default; the notification channel graceful-skips until set `true`. Idempotent. |
| `--email-to <address>` | Set `plugins.autonomy.email_to` — the recipient for the once-daily digest email ([`agentm_config.py#L81`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L81)). Validates non-empty only (no email-format check — same fail-loud-at-use-time philosophy as `--storage-backend`). Idempotent. |
| `--email-smtp-url <url>` | Set `plugins.autonomy.email_smtp_url` — the operator's own SMTP relay or on-device mail agent, never a third-party push service ([`agentm_config.py#L82`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L82)). Accepts `smtp://[user[:password]@]host[:port]` — a password authenticates against the relay (`server.login()`), with TLS negotiated automatically: implicit TLS (`SMTP_SSL`) on port 465, opportunistic `STARTTLS` otherwise (swallowed if the relay doesn't offer it). This is what lets an authenticated transactional-email relay (e.g. Resend) stand in for a bare local relay. Validates non-empty only. Idempotent. |
| `--email-from <address>` | Set `plugins.autonomy.email_from` — the domain-verified sending address some relays require distinct from the SMTP auth username ([`agentm_config.py#L87`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L87)). Optional; absent, the `From` header falls back to `--email-to` (mail-to-self, fine for a local/on-device relay with no verified-sender requirement). Validates non-empty only. Idempotent. |

All four keys are now live. `--notify-enabled`: [`scripts/health/session_notify.py`](https://github.com/alexherrero/agentm/blob/main/scripts/health/session_notify.py) reads `plugins.autonomy.notify_enabled` and fires a once-daily macOS notification when it's `true` — see [Enable on-device notifications](Enable-On-Device-Notifications). `--email-to` + `--email-smtp-url` (+ optional `--email-from`): [`scripts/health/session_email.py`](https://github.com/alexherrero/agentm/blob/main/scripts/health/session_email.py) reads `email_to` and `email_smtp_url` together (either alone is treated as unconfigured) and sends the daily digest over the configured SMTP relay, authenticating and negotiating TLS when the URL carries a password — see [Enable email digest delivery](Enable-Email-Digest-Delivery). Both channels also have runner-job manifests now (`templates/jobs/observability-notify-daily.yaml`, `templates/jobs/observability-email-daily.yaml`) — copy either into the gitignored `.harness/jobs/` to have the local runner invoke it daily; absent that copy, each still needs a manual invocation. See [Autonomy — Delivery](agentm-autonomy#delivery--getting-it-in-front-of-you) for the full channel design.

## Prerequisites

| Tool | Purpose | When needed |
|---|---|---|
| `bash` 4+ or `pwsh` 7+ | Host interpreter | Always |
| `git` | Version discovery (`git describe`), state tracking | Always |
| `python3` | Validation and integrity scripts | Always |
| `gh` | GitHub CLI; used by `ship-release` and any PR/issue flow | Post-install, not by the installer itself |

## Installed tree

Everything lands under the install prefix (`$AGENTM_INSTALL_PREFIX`, default
`~/.claude/`), except the update launcher, which needs to be on your `PATH`.

| Path | Contents |
|---|---|
| `<prefix>/agents/` | The memory-engine sub-agents |
| `<prefix>/skills/` | The shared skills (doctor, memory, console, design) |
| `<prefix>/hooks/<name>/` | Each hook as a directory bundle, with its settings fragment merged into `<prefix>/settings.json` |
| `<prefix>/scripts/` | Helper scripts that root across projects (e.g. `telemetry.sh`) |
| `<prefix>/settings.json` | Hook registrations, merged idempotently — your own entries are preserved |
| `<prefix>/.agentm-config.json` | Install state and on-host config |
| `~/.local/bin/agentm-update` | Launcher that re-runs the recorded installer |
| `~/.gemini/GEMINI.md` | The AgentMemory payload, merged as a managed section when `~/.gemini/` exists |

Re-running is idempotent, and it is how you refresh: in source mode the
customizations are symlinks into your clone, and in release mode they are
re-copied each run.

## Phase commands

The agentm installer does **not** ship the phase loop (`/setup` `/plan` `/work` `/review` `/release` `/bugfix`). You will find this loop in the crickets **development-lifecycle** plugin. You can read about the V5 unbundling in the [AgentM HLD](agentm-hld). agentm installs the memory engine and its customizations machine-wide; the phases run against that, and `/setup` writes a project's own `.harness/` state when you start one.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Argument error (unknown flag, a retired flag, or an unexpected positional argument) |
| `2` | `--mcp-server` was passed — retired, see above |
| non-zero | Boundary violation, file I/O error, or failed merge — inspect stderr for the exact message |

## Files

| Path | Purpose |
|---|---|
| [`install.sh`](https://github.com/alexherrero/agentm/blob/main/install.sh) | POSIX installer |
| [`install.ps1`](https://github.com/alexherrero/agentm/blob/main/install.ps1) | Windows installer |
| [`templates/`](https://github.com/alexherrero/agentm/tree/main/templates) | The update launcher, helper scripts, and hook templates |
| [`adapters/`](https://github.com/alexherrero/agentm/tree/main/adapters) | Per-tool command / agent / skill trees |

## Related

- [Tutorial 1: Your first harness install](01-First-Install) — Read this end-to-end walkthrough.
- [Install AgentM machine-wide](Install-Machine-Wide) — Follow this recipe for production use.
- [Foundations HLD](agentm-foundations-hld) — Learn why the installer boundary exists.
- [Memory-storage seam — On-host state-mode config](memory-storage-seam) — Understand why `--local-state` / `--state-mode` write to `.agentm-config.json` and never to the vault.
