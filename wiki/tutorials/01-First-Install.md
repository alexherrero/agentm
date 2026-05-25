# Tutorial 1 — Your first harness install

> [!NOTE]
> **Goal:** Install the harness into a fresh scratch project, watch it scaffold the state files, and confirm the install is healthy.
> **Time:** ~5 minutes.
> **Prereqs:** `bash` 4+, `git`, `python3` available on your `PATH`. No agent required for this tutorial — we're only verifying the install works.

By the end of this tutorial you'll have a real harness-installed project on disk, understand what files the installer drops, and know how to tell a clean install from a broken one. You'll use this muscle every time you onboard a new project.

## Step 1 — Clone the harness

Pick a directory you'd be happy throwing away. For this tutorial we'll use `~/harness-playground`.

```bash
mkdir -p ~/harness-playground && cd ~/harness-playground
git clone https://github.com/alexherrero/agentm.git
```

You should see a new `agentm/` subdirectory. `ls agentm` should list `install.sh`, `install.ps1`, `harness/`, `adapters/`, `templates/`, and a few more.

## Step 2 — Create a scratch project to install into

A harness install needs a target project. It can be completely empty.

```bash
mkdir my-first-project
cd my-first-project
git init
```

`ls -A` should show exactly one thing: `.git/`.

## Step 3 — Run the installer

From inside the scratch project, run the installer with `../agentm` as the harness path.

```bash
../agentm/install.sh .
```

You should see output ending in something like:

```
Installed agentm <version> into <path>
Run the /setup command in your agent to scaffold project-specific files.
```

No errors, no "boundary violation" messages — if you see either, stop and check your paths.

## Step 4 — Look at what got installed

```bash
ls -A
```

You should see these new entries:

- `.harness/` — state files (`PLAN.md`, `progress.md`, `features.json`, `init.sh`, `verify.sh`, `known-migrations.md`).
- `.claude/` — Claude Code commands, agents, and skills.
- `.agent/`, `.agents/`, `.gemini/` — adapter trees for the other supported tools.
- `AGENTS.md` — universal agent entry point.
- `CLAUDE.md` — Claude-Code-specific entry (points back at `AGENTS.md`).
- `wiki/` — empty documentation scaffold.
- `.github/workflows/wiki-sync.yml` — workflow that mirrors `wiki/**` to the GitHub Wiki.

The `.harness/` and `wiki/` trees are yours to edit. Everything else is managed by the installer and gets refreshed on `--update`.

## Step 5 — Confirm the install is healthy

Run a quick structural sanity check that the installed tree is usable:

```bash
cat .harness/PLAN.md | head -5
```

You should see a real PLAN.md starter template, not an empty file. If the file is empty or missing, the install is broken.

```bash
ls .claude/commands/
```

You should see six files: `bugfix.md`, `plan.md`, `release.md`, `review.md`, `setup.md`, `work.md`. These are the six phase commands the harness ships with.

## Step 6 — Practice the refresh flow

The installer is idempotent. Re-running it should be a no-op.

```bash
../agentm/install.sh .
```

No errors. The second run should only report files that were already present.

Now practice the refresh:

```bash
../agentm/install.sh --update .
```

This overwrites harness-managed files (commands, agents, skills) with the current version, leaves your state files alone, and records the new version in `.harness/.version`.

```bash
cat .harness/.version
```

You should see the current harness version string (e.g. `v0.8.7`).

## What you learned

- **The installer is a one-shot copy** — no daemon, no background process, no config parsing. It reads from the harness repo's `templates/` and `adapters/` trees and writes into your project.
- **State vs. managed files are separated.** `.harness/` and `wiki/` are yours; `.claude/`, `.agent/`, `.agents/`, `.gemini/` are managed and refreshed on `--update`.
- **Idempotent re-runs are safe.** Run `install.sh` against the same project twice — it won't clobber your work.
- **A clean install produces a specific tree.** If any of the expected files are missing after Step 4, the install is broken — don't try to work around it.

## Next

- **Use the harness on a real project:** [How to install into an existing project](Install-Into-Project).
- **Look up a specific flag:** [Installer CLI reference](Installer-CLI).
- **Understand *why* the harness is shaped this way:** [ADR 0001: Phase-gated workflow](0001-phase-gated-workflow), [ADR 0002: Documentation convention](0002-documentation-convention).
