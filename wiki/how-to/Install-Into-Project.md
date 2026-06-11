# How to install the harness into a project

> [!NOTE]
> **Goal:** Install or refresh the agentm scaffold in an existing project.
> **Prereqs:** `bash` 4+ (or `pwsh` 7+ on Windows), `git`, `python3`. For `--hooks` on POSIX, also `jq`.

## Steps

1. Clone or update the harness repo somewhere on your machine:

   ```bash
   git clone https://github.com/alexherrero/agentm.git ~/agentm
   # or, if already cloned:
   git -C ~/agentm pull
   ```

2. Run the installer against your project root:

   ```bash
   ~/agentm/install.sh /path/to/your-project
   ```

3. Commit the installed scaffold on a branch:

   ```bash
   cd /path/to/your-project
   git checkout -b add-agentm
   git add .harness .claude .agents AGENTS.md CLAUDE.md wiki .github
   git commit -m "Install agentm"
   ```

## Variants

### Enable verification hooks

Add `--hooks` to install PostToolUse, PreCompact, and SessionStart hooks into `.claude/settings.json`:

```bash
~/agentm/install.sh --hooks /path/to/your-project
```

### Refresh an existing install

Overwrites harness-managed files with the current version, leaves state files untouched:

```bash
~/agentm/install.sh --update /path/to/your-project
```

### Windows / PowerShell 7+

```powershell
pwsh -NoProfile -File C:\path\to\agentm\install.ps1 [-Hooks] [-Update] C:\path\to\your-project
```

## Verify

```bash
cd /path/to/your-project
ls .claude/commands/    # expect: bugfix.md plan.md release.md review.md setup.md work.md
cat .harness/.version   # expect: a version string (only after --update)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `installer-boundary violation` | The installer refused to copy from outside `templates/` or `adapters/`. You've likely modified `install.sh` — revert and retry. |
| Files missing from `.claude/commands/` | Re-run with `--update` to refresh the managed tree. |
| `jq: command not found` | Install `jq` (needed only with `--hooks` on POSIX), or drop `--hooks`. |

See [Installer CLI reference](Installer-CLI) for all flags. See [ADR 0002: Documentation convention](0002-documentation-convention) for the installer boundary rationale.
