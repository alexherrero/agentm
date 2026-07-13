# How to archive a finished project

> [!NOTE]
> **Part of** the [Memory System design](agentm-memory-system) — the project-archival lifecycle convention.
> **Goal:** Retire a finished project's vault directory without losing anything still load-bearing.
> **Prereqs:** Read access to the vault; `scripts/repo_registry.py` if the project has a registered repo.

A project's `_harness/` already archives its own completed plans in place (`PLAN.archive.YYYYMMDD-<slug>.md`). Nothing named what happens when the *whole project* is done — this is that convention.

## Steps

1. **Harvest first.** Read through the project's notes before moving anything. Most content is safe to archive as-is, but a project can accumulate a genuinely load-bearing entry that other active work depends on — a convention, a decision other designs still cite, a reusable pattern. Check for cross-references (`grep -rl "<slug>" <vault>` from outside the project directory) before deciding. If you find one, promote it to `personal/` (or wherever the vault's existing convention homes that kind of entry) first, keeping its filename/slug unchanged so `[[wikilinks]]` elsewhere keep resolving.

2. **Move the project directory.** `mv <vault>/projects/<name> <vault>/projects/_archive/<name>`. This is mechanically free — `recall.py`, `frontmatter_validator.py`, and `vault_lint.py` all already exclude any directory named `_archive` from their walks, so archived content stops showing up in recall and lint without any further wiring.

3. **Unregister the repo, if any.** If the project corresponds to a repo tracked in `repo_registry` (`python3 scripts/repo_registry.py unregister <slug>`), unregister it — check first with the registry's own list output; most vault-only projects were never registered.

4. **Update `Home.md`.** Drop the project from the active Projects list; add (or extend) the "Archived projects" line so the archive is still discoverable from the map, just not in the active eyeline.

## Why this shape

- **Harvest before move, not after.** A note buried in `_archive/` is still fully readable by hand, but it drops out of every automated surface (recall, lint) the moment it moves — anything still load-bearing needs to leave first.
- **`_archive/` is recoverable, not deleted.** Nothing here is destructive; the whole point is an active-projects eyeline that stays small without losing history.
- **No registry entry, no unregister step.** Most projects living only in the vault (research, personal, non-code) were never in `repo_registry` — don't invent a no-op step for them.

## See also

- [Memory System design](agentm-memory-system) — the `_archive/`-per-tier convention this extends to the project level.
- [Audit the vault](Audit-The-Vault) — run before a harvest pass if you're unsure what's safe to move.
- [Vault Lint Checks](Vault-Lint-Checks) — the `_archive` exclusion each lint/recall walker honors.
