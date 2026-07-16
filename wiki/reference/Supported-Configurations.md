# Supported configurations

This page maps the ways you can configure AgentM. You can read more about each choice here. The [recommended defaults](Home) fit most setups. These defaults use a user-scope install with a Google Drive vault.

## Install scope

| Scope | What it does | When to use it |
|---|---|---|
| **user** *(recommended)* | Installs to `~/.claude/`, so AgentM is available in every project on the machine. | Almost always. |
| **project** | Installs into `<project>/.claude/`. | Team dotfiles, or a project that pins its own config — see [Use per-project install](Use-Per-Project-Install). |

You can find the full flags in [Installer CLI](Installer-CLI). You can read about moving a project install to user scope in [Migration tool](Migration-Tool).

## Vault storage & sync

You choose where the MemoryVault lives. You choose how it reaches your other devices.

| Option | Status | Where to read |
|---|---|---|
| **Google Drive** | recommended right now | [Back the vault with Google Drive](Back-The-Vault-With-Drive) |
| **git** | forthcoming (turnkey via `vault-git`) | [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) |
| **Device-local** *(no sync)* | supported | [Run without a vault](Run-Without-A-Vault) |

The search index always stays device-local. It never syncs. You can see the full picture in [Compatibility](Compatibility).

## State mode

This controls where you write the harness state. Harness state includes `PLAN.md`, `progress.md`, and `features.json`.

- **backend** *(default)* — You write to the vault through the active storage backend.
- **local** — You write to `<repo>/.harness/`. You have no vault dependency. You set this with `install.sh --local-state`. You can flip an existing install with `agentm_config.py --state-mode local`.

You can read more in [Single-repo state mode](Single-Repo-State-Mode).

## Verification hooks

`--hooks` *(recommended)* installs the PostToolUse verification hook. This hook runs on Write and Edit. The shipped `verify.sh` template comments out every check. It has no test-running branch at all. It uses typecheck/lint/vet as example checks. You explicitly leave full-suite tests to `/review` or CI. You customize `verify.sh` to activate it. This is optional. You can find details in [Installer CLI](Installer-CLI).

## Hosts

You can use Claude Code and Antigravity. They are both fully supported. You can see [Compatibility](Compatibility).
