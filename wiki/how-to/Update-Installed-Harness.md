# How to refresh an installed harness

> [!NOTE]
> **Goal:** Pull a newer AgentM version onto a machine that already has one installed, without losing anything you configured.
> **Prereqs:** AgentM installed (see [Install AgentM](Install-Machine-Wide)), and — for source installs — the agentm repo cloned on your machine.

Re-running the installer **is** the refresh. There is no separate update flag: `--update` was retired along with the per-project install, and passing it now fails rather than being silently ignored.

## Steps

1. Pull the latest AgentM:

   ```bash
   git -C /path/to/agentm pull
   ```

2. Re-run the installer. It takes no target path:

   ```bash
   bash /path/to/agentm/install.sh
   ```

   Or on Windows:

   ```powershell
   pwsh -NoProfile -File C:\path\to\agentm\install.ps1
   ```

   If you'd rather not remember where your clone is, the installer records that at install time and leaves you a launcher:

   ```bash
   agentm-update
   ```

   It reads `installer_source` from `<prefix>/.agentm-config.json` and re-runs it, passing through any flags you give it.

3. Confirm the recorded version:

   ```bash
   python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.claude/.agentm-config.json')))['harness_version'])"
   ```

## Why there is no update flag

How a refresh reaches you depends on which install mode you're in, and neither mode needs a flag:

- **Source mode** — you have a clone at `~/Antigravity/agentm`, and the customizations in your prefix are *symlinks into it*. `git pull` alone updates most of them; you re-run the installer to pick up newly added skills, agents or hooks, which need new symlinks.
- **Release mode** — no clone, so the customizations were copied. Re-running re-copies them.

Either way the answer is the same command, which is why the flag went.

## What a refresh touches

| Path | Behavior on re-run |
|---|---|
| `<prefix>/agents/`, `skills/`, `hooks/` | Refreshed from source (re-symlinked or re-copied) |
| `<prefix>/scripts/` | Refreshed |
| `<prefix>/settings.json` | **Merged, not overwritten.** Hook registrations are deduped by command, so a refresh adds nothing twice — and entries you wrote yourself are preserved |
| `<prefix>/.agentm-config.json` | The installer updates only the keys it owns (`harness_version`, `mode`, `installed_at`, `installer_source`, `installed_shas`, `fragments`, `vault_path`, `state_mode`) and carries everything else forward — including every `plugins.*` key set via `agentm_config.py` |
| `~/.local/bin/agentm-update` | Refreshed |
| `~/.gemini/GEMINI.md` | Its managed AgentMemory section is refreshed; the rest of your file is untouched |
| The memory daemon | Rebuilt and reloaded if already installed, so a harness refresh is also a daemon refresh. Skip it for one run with `--no-daemon` |

Your project state — `PLAN.md`, `progress.md`, `features.json` — lives in the vault (or `<repo>/.harness/` under [single-repo state mode](Single-Repo-State-Mode)), not in the install prefix. A refresh never touches it.

## Verify

Running the installer twice back-to-back should be a no-op the second time: same exit code, and the same number of hook registrations in `settings.json`. If a second run keeps adding entries, the merge is broken — that's what `smoke-install-bash.sh` asserts on every CI run.

See [Installer CLI reference](Installer-CLI) for every flag. See the [Foundations HLD](agentm-foundations-hld) for why the installer boundary exists.
