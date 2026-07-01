# How to install AgentM machine-wide (recommended)

> [!NOTE]
> **Goal:** Install AgentM the recommended way — once for every project on your machine — with the verification hooks and a Google Drive–backed vault, so your memory and customizations follow you across projects and devices.
> **Prereqs:** a coding agent (Claude Code or Antigravity) and a local clone of the [agentm repo](https://github.com/alexherrero/agentm). Optional but recommended: a Google Drive folder to hold the vault.

This is the recommended setup, and the one the [home page](Home) points to. Under **user scope**, AgentM's customizations install into `~/.claude/` and apply to every project you open, rather than to a single repo — so you set it up once. Your memory vault lives in a Google Drive folder, so it syncs across your devices. For the exceptions where you'd deliberately scope the install to one project instead, see [Use per-project install](Use-Per-Project-Install).

## Prerequisites

- A coding agent installed — Claude Code or Antigravity ([see requirements](Compatibility)).
- A local clone of the agentm repo (this guide assumes it's at `~/agentm`).
- _Recommended:_ a Google Drive folder for the vault, so memory syncs across devices ([back the vault with Drive](Back-The-Vault-With-Drive)).

## Steps

1. **Point the vault at a Google Drive folder.** Create a folder named `Agent` in your Google Drive (the recommended default) and set it as the vault path:

   ```bash
   export MEMORY_VAULT_PATH="<your-google-drive>/Agent"
   ```

   The user-scope install below persists this path into your config, so you only set it once. `$MEMORY_VAULT_PATH` also stays available afterward as a per-invocation override.

2. **Install for every project, with hooks.** Run the installer in user scope:

   ```bash
   bash ~/agentm/install.sh --hooks --scope user
   ```

   `--scope user` installs the customizations into `~/.claude/`, so they apply to every project — no target path is needed. `--hooks` wires in the verification hooks (typecheck / lint / test on write).

3. **Add the crickets plugins.** AgentM pairs with the crickets toolkit — install its plugins for both hosts:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/alexherrero/crickets/main/bootstrap.sh | bash
   ```

4. **Confirm it's wired up.** Open a project with your agent and check that the vault resolves and the backend is selected — the [audit the vault](Audit-The-Vault) how-to walks that health check.

## Troubleshooting

- **The vault path isn't picked up.** Make sure `MEMORY_VAULT_PATH` points at the real Drive folder and that the folder exists. The install persists it during a `--scope user` run; if you set it afterward, re-run the install or set it with `agentm_config --vault-path <path>`.
- **You want just one project, not the whole machine.** That's the per-project exception — use `--scope project <target>` instead. See [Use per-project install](Use-Per-Project-Install).

## Related

- [Use per-project install](Use-Per-Project-Install) — the deliberate `--scope project` exception, and when to choose it.
- [Back the vault with Google Drive](Back-The-Vault-With-Drive) — set up the Drive-synced vault the recommended install uses.
- [Supported configurations](Supported-Configurations) — the full matrix of scope, storage, and state-mode choices.
- [Update an installed harness](Update-Installed-Harness) — pull a newer version into an installed project.
- [Compatibility](Compatibility) — supported hosts and the OS matrix.
