# How to run the harness without a vault (single-repo local state)

> [!NOTE]
> **Status:** pending
> **Plan:** `.harness/PLAN.md` tasks 2 (vault-less write path + repo-local marker) + 3 (`--local-state` entry point).
> **Goal:** Opt a single repo into vault-less state so every phase write lands in `<repo>/.harness/` — no Obsidian / GDrive / mounted vault required.
> **Prereqs:** _Filled by human._ (agentm version that ships `--local-state`, `python3` on `PATH`, a `.git` dir.)

Single-repo mode lets you drive the full phase workflow on a machine with no memory vault. You opt in explicitly — the installer writes a repo-local `.project-mode=local` marker at `<repo>/.harness/.project-mode`, and from then on `harness_memory.py` reads and writes state under `<repo>/.harness/` instead of routing through a vault. For the model behind it, see [Single-repo state mode](Single-Repo-State-Mode).

## Steps

_Steps are pending — filled from the implementation diff at `/work` (plan tasks 2–3). The shape will be:_

1. Opt the repo into local mode at install time — `install.sh --local-state <target>` (or the equivalent `/setup` prompt). _Filled by human._
2. Confirm the repo-local marker landed at `<repo>/.harness/.project-mode` reading `local`. _Filled by human._
3. Run a phase write (e.g. `/plan`) and confirm state lands repo-local under `<repo>/.harness/` with no `ValueError`. _Filled by human._

## Related

- [Single-repo state mode](Single-Repo-State-Mode) — why this mode exists and the vault-vs-local resolution model.
- [Installer CLI reference](Installer-CLI) — the `--local-state` flag.
- [Project config reference](Project-Config) — how `register()` degrades when no vault is present.
- [Repo layout reference](Repo-Layout) — where the `.project-mode` marker sits on disk.
