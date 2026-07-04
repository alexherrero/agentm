# How to deliberately use the per-project install

> [!NOTE]
> **Goal:** Install agentm into a project's `.claude/` directory (per-project scope) instead of the default user-scope `~/.claude/` install, and document why you chose that.
> **Prereqs:** `bash` 4+ (or `pwsh` 7+ on Windows), `git`, `python3`. Agentm v4.5.0+ (so the install-state.json mode field exists).

Agentm's default-recommended scope is `--scope user` (installs into `~/.claude/` so customizations stay synced across every project on the machine). The [migration tool](Migration-Tool) exists specifically to move pre-V4.3 per-project installs into user scope. Three cases justify deliberately keeping `--scope project` instead.

## When per-project scope is the right choice

| Case | Why per-project wins | Migration policy |
|---|---|---|
| **CI runners** | CI environments are ephemeral; `~/` is reset between jobs. Per-project `.claude/` is committed to the repo + propagates with the checkout. | Migration tool refuses to run when `$CI=true` env detected (unless `--ci-override`). |
| **Shared dev environments** | Multi-user host (e.g. shared dev box; cloud workspace) where each user has their own `~/.claude/` but the project's customizations should be the same for everyone. | Run migration on the personal dev host, NOT the shared box. Document the choice in the project's README. |
| **Multi-developer dotfiles patterns** | Team checks `.claude/` into git so every team member gets the same agentm config on clone. | Don't migrate. Use `bash install.sh --scope project <target>` explicitly per the steps below. |

Reference: locked DC-10 from plan #22 (V4 #30 plan 1 of 3) — `--scope project` mode is preserved as a legitimate, fully supported install path.

## Steps

1. Run the installer with explicit `--scope project`:

   ```bash
   ~/agentm/install.sh --scope project /path/to/your-project
   ```

   On Windows / PowerShell 7+:

   ```powershell
   ~/agentm/install.ps1 -Scope project /path/to/your-project
   ```

2. Verify the install-state JSON shows `mode=project`:

   ```bash
   cat /path/to/your-project/.claude/.agentm-install-state.json
   ```

   You should see `"mode": "project"` in the output. This is the signal the migration tool reads to detect "explicit per-project" (state 3 of the 4-state matrix in [Migration-Tool](Migration-Tool)) and require explicit confirmation before migrating.

3. Commit the installed scaffold:

   ```bash
   cd /path/to/your-project
   git checkout -b add-agentm-project-scope
   git add .claude .agents AGENTS.md CLAUDE.md .harness wiki .github
   git commit -m "Install agentm (per-project scope; CI / shared-dev / dotfiles use case)"
   ```

   Make sure `.claude/` is **not** in your `.gitignore`. Per-project mode only works when the customizations are checked in.

4. Document the choice in your project's `AGENTS.md` (or equivalent):

   ```markdown
   ## Agentm scope

   This project deliberately uses `--scope project` (per-project install)
   because <CI runner / shared dev box / team dotfiles / etc>. Do NOT run
   `scripts/migrate-to-user-scope.sh` against this checkout.
   ```

   This protects against future operators (or future-you) running the migration tool by reflex.

## Per-project + user-scope on the same machine

You can have both: `~/.claude/` populated for general agentm work, and `<project>/.claude/` populated for a specific repo that needs scope-project semantics. The two don't conflict — Claude Code resolves customizations from the per-project `.claude/` first, falling back to `~/.claude/`. If you want both:

1. Run `~/agentm/install.sh --scope user` once (typically on first-machine setup) to get `~/.claude/`.
2. Run `~/agentm/install.sh --scope project /path/to/specific-repo` for each repo that needs per-project mode.

Both invocations write their own `.agentm-install-state.json` to the appropriate prefix. The migration tool's 4-state detection looks at the **project** install-state.json, not the user one.

## How to undo a per-project install

If you change your mind + want to migrate to user scope, see [Migration-Tool](Migration-Tool). The migration tool's preview mode (default) is read-only — safe to run for a "what would happen if I migrated this?" check without committing.

If you want to **fully uninstall** the per-project install (no migration), remove the four subdirs by hand:

```bash
cd /path/to/your-project
rm -rf .claude/{skills,hooks,agents,commands}
```

(Leave any operator-edited files outside those subdirs intact — `.claude/settings.json` for example is a user-owned merge target.)

## Related

- [Migration-Tool](Migration-Tool) — full reference for the `migrate-to-user-scope.{sh,ps1}` CLI + the 4-state matrix
- [Install-Into-Project](Install-Into-Project) — the default install workflow (defaults to `--scope user` from v4.5.0+)
- [Installer-CLI](Installer-CLI) — install.sh / install.ps1 flag reference
- [Memory-storage seam](memory-storage-seam) § 6 — dev-setup invisibility policy (load-bearing assumption preserved)
