# How to install AgentM

> [!NOTE]
> **Goal:** Install AgentM once for every project on your machine, with a Google Drive–backed vault, so your memory and customizations follow you across projects and devices.
> **Prereqs:** a coding agent (Claude Code or Antigravity) and a local clone of the [agentm repo](https://github.com/alexherrero/agentm). Optional but recommended: a Google Drive folder to hold the vault.

AgentM installs once, for your whole machine. Its customizations go into `~/.claude/` and apply to every project you open, so there is nothing to install per repo and nothing to keep in sync between them. Your memory vault lives in a Google Drive folder, so it follows you across devices.

## Prerequisites

- A coding agent installed — Claude Code or Antigravity ([see requirements](Compatibility)).
- A local clone of the agentm repo (this guide assumes it's at `~/agentm`).
- _Recommended:_ a Google Drive folder for the vault, so memory syncs across devices ([back the vault with Drive](Back-The-Vault-With-Drive)).

## Steps

1. **Point the vault at a Google Drive folder.** Create a folder named `Agent` in your Google Drive (the recommended default) and set it as the vault path:

   ```bash
   export MEMORY_VAULT_PATH="<your-google-drive>/Agent"
   ```

   The install below persists this path into your config, so you only set it once. `$MEMORY_VAULT_PATH` also stays available afterward as a per-invocation override.

2. **Install.** Run the installer — it takes no target path:

   ```bash
   bash ~/agentm/install.sh
   ```

   The customizations land in `~/.claude/`, so they apply to every project. The harness hooks install and register themselves as part of this. Re-run the same command any time to refresh.

3. **Add the crickets plugins.** AgentM pairs with the crickets toolkit — install its plugins for both hosts:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/alexherrero/crickets/main/bootstrap.sh | bash
   ```

4. **Open a fresh session and run the doctor.** Start a new session with your agent so the new config loads, then run the **doctor** to get a report of how everything's wired up — the vault path, the active backend, the hooks, and anything that needs attention. (For a deeper vault-only pass, see [audit the vault](Audit-The-Vault).)

## Troubleshooting

| Symptom | Fix |
|---|---|
| The vault path isn't picked up | Make sure `MEMORY_VAULT_PATH` points at the real Drive folder and that it exists. The install persists it; if you set it afterward, re-run the install or set it with `agentm_config --vault-path <path>`. |
| You want AgentM in only one project | There is no per-project install any more. AgentM's customizations are machine-wide; what varies per project is its *state* (`PLAN.md`, `progress.md`), which lives in the vault under that project's slug — or in `<repo>/.harness/` if you set [single-repo state mode](Single-Repo-State-Mode). |

## Related

- [Back the vault with Google Drive](Back-The-Vault-With-Drive) — set up the Drive-synced vault the recommended install uses.
- [Supported configurations](Supported-Configurations) — the full matrix of scope, storage, and state-mode choices.
- [Update an installed harness](Update-Installed-Harness) — pull a newer version onto your machine.
- [Compatibility](Compatibility) — supported hosts and the OS matrix.
