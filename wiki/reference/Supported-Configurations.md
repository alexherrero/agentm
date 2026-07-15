# Supported configurations

How AgentM can be set up, and where to read more about each choice. The [recommended defaults](Home) — a user-scope install with a Google Drive vault — suit most setups; this page is the map of the alternatives.

## Install scope

| Scope | What it does | When to use it |
|---|---|---|
| **user** *(recommended)* | Installs to `~/.claude/`, so AgentM is available in every project on the machine. | Almost always. |
| **project** | Installs into `<project>/.claude/`. | Team dotfiles, or a project that pins its own config — see [Use per-project install](Use-Per-Project-Install). |

Full flags: [Installer CLI](Installer-CLI). Moving a project install to user scope: [Migration tool](Migration-Tool).

## Vault storage & sync

Where the MemoryVault lives and how it reaches your other devices:

| Option | Status | Where to read |
|---|---|---|
| **Google Drive** | recommended right now | [Back the vault with Google Drive](Back-The-Vault-With-Drive) |
| **git** | forthcoming (turnkey via `vault-git`) | [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) |
| **Device-local** *(no sync)* | supported | [Run without a vault](Run-Without-A-Vault) |

The search index always stays device-local, so it never syncs. Full picture: [Compatibility](Compatibility).

## State mode

Where harness state (`PLAN.md`, `progress.md`, `features.json`) is written:

- **backend** *(default)* — in the vault, through the active storage backend.
- **local** — in `<repo>/.harness/`, with no vault dependency. Set it with `install.sh --local-state`, or flip an existing install with `agentm_config.py --state-mode local`.

See [Single-repo state mode](Single-Repo-State-Mode).

## Verification hooks

`--hooks` *(recommended)* installs the PostToolUse verification hook wired to run on Write and Edit — but the shipped `verify.sh` template ships with every check commented out, and has no test-running branch at all (typecheck/lint/vet are the example checks; full-suite tests are explicitly left to `/review` or CI). Customize `verify.sh` to activate it. Optional. Details in [Installer CLI](Installer-CLI).

## Hosts

Claude Code and Antigravity are both fully supported. See [Compatibility](Compatibility).
