# How to run the harness without a vault (single-repo local state)

> [!NOTE]
> **Status:** implemented (Hardening I, v4.15.0)
> **Goal:** Opt a machine into vault-less state so every phase write lands in `<repo>/.harness/` — no Obsidian / GDrive / mounted vault required.
> **Prereqs:** agentm v4.15.0+ (ships `--local-state`), `python3` on `PATH`, a `.git` dir in the target repo.

Single-repo mode lets you drive the full phase workflow on a machine with no memory vault. You opt in explicitly — `install.sh --local-state` writes `"state_mode": "local"` to the on-host `.agentm-config.json` and skips vault wiring. From then on `harness_memory.py` reads and writes state under `<repo>/.harness/` instead of routing through a vault. For the model behind it (and the higher-precedence per-repo `.project-mode` marker), see [Single-repo state mode](Single-Repo-State-Mode).

## Steps

1. Opt the machine into local mode at install time:

   ```bash
   bash /path/to/agentm/install.sh --local-state /path/to/your-project
   # Windows / PowerShell 7+:
   #   install.ps1 -LocalState C:\path\to\your-project
   ```

   The installer writes `"state_mode": "local"` to `.agentm-config.json` and prints `state_mode: local (repo-local, vault-less); skipping vault detection`.

2. Confirm the device config carries the mode:

   ```bash
   python3 /path/to/agentm/scripts/agentm_config.py --get state_mode   # → local
   ```

   Already installed without the flag? Flip it without re-running the installer: `python3 /path/to/agentm/scripts/agentm_config.py --state-mode local` (use `--state-mode vault` to switch back). To override the device default for a *single* repo, write `local` to `<repo>/.harness/.project-mode` — that per-repo marker wins over the device setting.

3. Run a phase write (e.g. `/plan`) and confirm state lands repo-local under `<repo>/.harness/` (e.g. `PLAN.md`) with no `ValueError`. Reads and writes round-trip against that directory with no vault configured.

## Related

- [Single-repo state mode](Single-Repo-State-Mode) — why this mode exists and the vault-vs-local resolution model.
- [Installer CLI reference](Installer-CLI) — the `--local-state` flag and the `agentm_config.py --state-mode` setter.
- [Project config reference](Project-Config) — how `register()` degrades when no vault is present.
- [Repo layout reference](Repo-Layout) — where the `.project-mode` marker sits on disk.
