---
description: Show wiki pages modified recently across registered repos. Walks repo_registry (vault-backed at <vault>/_meta/repos.json from V4 #30 plan 1) + emits a one-row-per-page table sorted by mtime. Default 7-day window; --repo + --days + --limit flags.
---

Show wiki pages modified recently across all registered repos.

Invokes `scripts/recent-wiki-changes.sh` (or `.ps1` on Windows) and passes through any operator-supplied flags. The script walks `repo_registry.list_repos()` from V4 #30 plan 1 (vault-backed registry at `<vault>/_meta/repos.json`); for each registered repo's `root_path`, it walks the `wiki/` subtree for files modified within the configured window.

## Usage

```bash
# Default — all registered repos, last 7 days, up to 50 rows:
/recent-wiki-changes

# Filter to one repo:
/recent-wiki-changes --repo agentm

# Custom window (14 days; overrides $AGENTM_WIKI_RECENT_DAYS env):
/recent-wiki-changes --days 14

# Cap output:
/recent-wiki-changes --limit 10

# Combine:
/recent-wiki-changes --repo crickets --days 30 --limit 20
```

## Output

Tab-aligned table:

```
SLUG     MODE         PAGE                                     MODIFIED
-------  -----------  ---------------------------------------  ----------------
agentm   how-to       how-to/Use-Recent-Wiki-Changes.md        2026-05-28 09:14
agentm   explanation  explanation/decisions/0004-diataxis.md   2026-05-27 21:26
sherwood reference    reference/CI-Conventions.md              2026-05-27 14:00
...

(N row(s); last 7 day(s); --limit 50)
```

`MODE` shows the Diátaxis mode dir (`tutorials` / `how-to` / `reference` / `explanation`) or `—` for top-level pages like `Home.md` / `_Sidebar.md`.

## Graceful-skip

- **`MEMORY_VAULT_PATH` unset OR vault missing** → exits 1 with `{"skipped": true, "reason": "..."}` JSON marker.
- **No repos registered** → prints an actionable hint with the `repo_registry register` command.
- **No recent changes** → prints "No wiki changes in the last N day(s)" and exits 0.

## Companion surfaces

- `wiki-author` skill (V4 #30 plan 2 task 3) — operator-facing dispatcher for wiki WRITES (this command is the READ surface).
- `list-plans.{sh,ps1}` — same cross-repo pattern but for `PLAN.md` files (from V4 #26).
- crickets' `wiki-maintenance:documenter` sub-agent — the actual write-executor; this command does not invoke it.

## Per ROADMAP-V4 #30 plan 2 of 3

This command ships the **cross-repo views** bullet (e) of V4 #30. Companion to:
- bullet (b) — wiki I/O conventions codified (ADR 0004 Amendment 2026-05-27)
- bullet (d) — `wiki-author` skill dispatcher

Future V4 #38 wiki bundle (first sub-item of the opinionated capability bundles meta-item) may extend this surface with opinionated views; for now, it's a pure data layer.
