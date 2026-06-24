# How to refresh an installed harness

> [!NOTE]
> **Goal:** Pull a newer harness version into a project that already has an older one installed, without clobbering user edits.
> **Prereqs:** The harness repo is cloned somewhere on your machine; the target project was installed from some prior version.

`install.sh --update` (POSIX) and `install.ps1 -Update` (Windows) refresh harness-authored files in place without touching user-authored ones.

## Steps

1. Pull the latest harness:

   ```bash
   git -C /path/to/agentm pull
   ```

2. Run the installer against your project with `--update`:

   ```bash
   /path/to/agentm/install.sh --update /path/to/your-project
   ```

   Or on Windows:

   ```powershell
   pwsh -NoProfile -File C:\path\to\agentm\install.ps1 -Update C:\path\to\your-project
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
| `.harness/scripts/`, `.harness/hooks/` | Harness | Yes (wiped + recreated from source) |
| `.claude/`, `.agents/` (the supported adapters) | Harness | Yes (wiped + recreated from source) |
| `.gemini/` (vestigial dropped-host adapter — still emitted pending reconciliation, see [Compatibility](Compatibility)) | Harness | Yes (wiped + recreated from source) |
| `.github/workflows/wiki-sync.yml` | Harness | Yes (overwritten) |
| `.harness/.version` | Harness | Written after a successful update (so future runs can show a delta) |

## Sync semantics on `--update` (v1.0.0+)

Starting with v1.0.0, `--update` is a **true sync** against the GitHub source-of-truth, not a refresh-current-set. Twelve fully-harness-authored subdirs are wiped before being recreated from source:

```
.claude/commands  .agents/rules       .gemini/commands
.claude/agents    .agents/workflows   .gemini/agents
.claude/skills    .agents/skills      .harness/scripts
.harness/hooks
```

The Antigravity tree moved from `.agent/` (singular) to `.agents/` (plural) in V4 #22, matching Antigravity 2.0's default. Any orphaned harness-installed paths — the legacy `.agent/` tree, or `.codex/` from pre-v1.0.0 installs — are automatically removed — the installer reports them as `removed legacy <path>/` in the output. User state files at `.harness/` root (`PLAN.md`, `progress.md`, `features.json`, `init.sh`, `verify.{sh,ps1}`, `known-migrations.md`), merged `settings.json` files, `wiki/**`, and root-level `AGENTS.md` / `CLAUDE.md` are deliberately excluded from the wipe and survive untouched.

This is what makes future host removals or skill rearrangements clean: local trees stay in lockstep with GitHub on every `--update`, no per-removal patches needed.

## Verify

Running a second `--update` back-to-back should be a no-op — the installer is idempotent.

**When in doubt about ownership**, see the `cp_managed` function and the `MANAGED_PARENTS` array in [`install.sh`](https://github.com/alexherrero/agentm/blob/main/install.sh). Dirs listed in `MANAGED_PARENTS` are fully harness-authored and wiped-then-recreated on `--update`; anything outside that list is user-authored and preserved.

See [Installer CLI reference](Installer-CLI) for all flags. See [ADR 0002](agentm-foundations-hld) for why the boundary exists.
