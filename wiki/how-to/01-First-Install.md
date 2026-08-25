# Tutorial 1 — Your first AgentM install

> [!NOTE]
> **Goal:** Install AgentM into a throwaway location, look at exactly what lands, and learn to tell a clean install from a broken one — without touching your real setup.
> **Time:** ~5 minutes.
> **Prereqs:** `bash` 4+, `git`, `python3` on your `PATH`. No agent required for this tutorial — we're only verifying the install works.

AgentM installs **once, for your whole machine**. There is no per-project install and no target path: the customizations land in one prefix and apply to every project you open. By the end of this tutorial you'll have run a real install, know which files it produces, and know how to check one.

We'll install into a scratch prefix rather than the real `~/.claude/`, so you can run every command here safely and delete the result afterwards.

## Step 1 — Clone AgentM

Pick a directory you'd be happy throwing away. For this tutorial we'll use `~/agentm-playground`.

```bash
mkdir -p ~/agentm-playground && cd ~/agentm-playground
git clone https://github.com/alexherrero/agentm.git
```

You should see a new `agentm/` subdirectory. `ls agentm` lists `install.sh`, `install.ps1`, `harness/`, `adapters/`, `templates/`, and a few more.

## Step 2 — Install into a scratch prefix

`AGENTM_INSTALL_PREFIX` decides where the customizations go. Unset, it means `~/.claude/` — your real setup. Point it somewhere disposable instead:

```bash
export AGENTM_INSTALL_PREFIX=~/agentm-playground/prefix
bash agentm/install.sh --no-daemon
```

Notice there is no target path. `--no-daemon` keeps this tutorial from building and installing the memory daemon, which is a real background service and not something you want from a practice run.

You should see output ending in something like:

```
==> done (agentm <version> installed to ~/agentm-playground/prefix).
```

No errors and no boundary-violation messages. If you see either, stop and check your paths.

## Step 3 — Look at what got installed

```bash
ls -A ~/agentm-playground/prefix
```

You should see:

- `agents/` — the memory-engine sub-agents.
- `skills/` — the shared skills (`doctor`, `memory`, `console`, `design`).
- `hooks/<name>/` — each hook as its own directory bundle.
- `scripts/` — helper scripts that root across projects, like `telemetry.sh`.
- `settings.json` — where each hook registered itself.
- `.agentm-config.json` — install state and on-host config.

Two things land outside the prefix by design: the `agentm-update` launcher goes to `~/.local/bin/` so it's on your `PATH`, and the AgentMemory rule is merged into `~/.gemini/GEMINI.md` if that file exists.

Nothing was written into any project. That is the whole point — a project gets AgentM by existing on a machine that has it.

## Step 4 — Confirm the install is healthy

An installed hook is only useful if it actually fires, which takes two things: the script on disk, and a registration pointing at it. Check both.

```bash
ls ~/agentm-playground/prefix/hooks/
python3 -c "import json; s=json.load(open('$HOME/agentm-playground/prefix/settings.json')); print(len(s.get('hooks', {})), 'hook events registered')"
```

You should see five hook directories and a non-zero count of registered events. A tree with hook directories but an empty `settings.json` is the classic silent-broken install: the files are all there, and nothing ever runs. That specific failure is what `check-integrity-bash.sh` exists to catch.

## Step 5 — Practice the refresh

Re-running the installer *is* the refresh. There is no separate update flag.

```bash
bash agentm/install.sh --no-daemon
```

It should succeed again, and `settings.json` should still register the same number of events — the merge is idempotent, so a refresh never duplicates its own entries, and never drops entries you added yourself.

## Step 6 — Clean up

```bash
unset AGENTM_INSTALL_PREFIX
rm -rf ~/agentm-playground
```

That removes everything this tutorial created inside the prefix. If you want to keep the launcher it wrote to `~/.local/bin/agentm-update`, leave it; otherwise delete that too.

## What you learned

- **One install, one location.** AgentM installs to `$AGENTM_INSTALL_PREFIX` (default `~/.claude/`) and applies everywhere. Projects don't get their own copy.
- **`AGENTM_INSTALL_PREFIX` makes installs testable.** Pointing it at a scratch directory is how you try things without risking your setup.
- **Re-running is the refresh, and it's idempotent.** No update flag, no clobbering.
- **Installed ≠ wired up.** The hook files and their registrations in `settings.json` are separate things, and only the pair does anything useful.

## Next

- **Set up your real install:** [Install AgentM](Install-Machine-Wide).
- **Look up a specific flag:** [Installer CLI reference](Installer-CLI).
- **Understand *why* the harness is shaped this way:** [Phase-gated workflow design](agentm-hld), [Documentation convention design](agentm-foundations-hld).
