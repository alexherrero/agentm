# How to refresh an installed harness

> [!NOTE]
> **Goal:** Pull a newer harness version into a project that already has an older one installed, without clobbering user edits.
> **Prereqs:** The harness repo is cloned somewhere on your machine; the target project was installed from some prior version.

`install.sh --update` (POSIX) and `install.ps1 -Update` (Windows) refresh harness-authored files in place without touching user-authored ones.

## Steps

1. Pull the latest harness:

   ```bash
   git -C /path/to/agentic-harness pull
   ```

2. Run the installer against your project with `--update`:

   ```bash
   /path/to/agentic-harness/install.sh --update /path/to/your-project
   ```

   Or on Windows:

   ```powershell
   pwsh -NoProfile -File C:\path\to\agentic-harness\install.ps1 -Update C:\path\to\your-project
   ```

3. Confirm the recorded version matches:

   ```bash
   cat /path/to/your-project/.harness/.version
   ```

## What gets touched

| File | Owner | Touched by `--update`? |
|---|---|---|
| `PLAN.md`, `progress.md`, `features.json`, `init.sh`, `verify.{sh,ps1}`, `known-migrations.md` | User | No |
| `AGENTS.md`, `CLAUDE.md` | User | No |
| `wiki/` scaffold | User | Per-file walk — missing files filled in, existing files preserved |
| `.harness/scripts/`, `.harness/hooks/` | Harness | Yes (overwritten) |
| `.claude/`, `.agent/`, `.agents/`, `.codex/`, `.gemini/` | Harness | Yes (overwritten) |
| `.github/workflows/wiki-sync.yml` | Harness | Yes (overwritten) |
| `.harness/.version` | Harness | Written after a successful update (so future runs can show a delta) |

## Verify

Running a second `--update` back-to-back should be a no-op — the installer is idempotent.

**When in doubt about ownership**, see the `cp_managed` function in [`install.sh`](https://github.com/alexherrero/agentic-harness/blob/main/install.sh#L103-L120). "Managed" files are harness-authored and overwritten on `--update`; anything not wrapped in `cp_managed` is user-authored and preserved.

See [Installer CLI reference](Installer-CLI) for all flags. See [ADR 0002](0002-documentation-convention) for why the boundary exists.
