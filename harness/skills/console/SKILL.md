---
name: console
description: Terminal-first (with an --html mode) report that composes agentm's existing observability surfaces — the health index + family table, queue-status-lite, the board-drift one-liner, spend from the observability rollup, and a memory-activity section (inbox, watchlist, incubator, newest curated entries, heat-policy decay report) — behind one entry point. Builds nothing new underneath; every section reads or shells out to a surface that already exists and is independently invocable on its own.
kind: skill
supported_hosts: [claude-code, antigravity]
version: 0.1.0
install_scope: project
---

# console — the unified observability report

`/console` answers "what is agentm doing right now?" in one read — instead of running `scripts/status.py`, `scripts/queue_status_lite.py`, a board-drift check, an observability-console render, and a memory-vault glance separately. It is a **static, on-demand report, not a live dashboard service** — the same "static derived data needs no daemon" call the Autonomy arc's design already made for the observability console this skill extends (`wiki/designs/agentm-autonomy.md`, "Out of scope").

**Not for:** a live/continuously-refreshing view (there is no daemon behind this — re-run the skill to refresh), or a substitute for any of the deeper commands it composes (`/doctor` for install-health, `/memory` for the memory engine's own sub-commands, `/report-board-drift` for the operator-confirmed drift-correction cycle). `/console` is the glance; the composed commands are where you go deeper.

## Modes

| Mode | What it does |
|---|---|
| `python3 harness/skills/console/scripts/console.py` (or the installed equivalent, e.g. `.claude/skills/console/scripts/console.py`) | Terminal report: five sections (Health, Plans, Board drift, Spend, Memory activity), each on its own heading. |
| `python3 .../console.py --html [--output PATH]` | Renders the same five sections as a single self-contained static HTML page, extending `scripts/health/observability_console.py`'s already-built spend page (its exact rendered tables are reused, not re-implemented) with the other four sections wrapped around it. Default output path matches the existing console's own convention: `~/.cache/agentm/telemetry/console.html`. |

Both modes are read-only: nothing here mutates the repo, the vault, or the GitHub project board. The board-drift section calls the read-only drift *detector* only (one `gh issue list` read) — never the comment-posting `/report-board-drift` cycle. The heat-policy section always runs in dry-run mode (never passes `--apply`).

## What each section composes (and where to look if a section says "n/a")

- **Health** — `scripts/status.py`, which reads `scripts/health/health_score.py`'s health-history ledger (the vault, when one resolves, else a device-local fallback — `resolve_history_path()`). Says "n/a" outside an agentm dev checkout, or if no scorecard has ever been recorded (run `bash scripts/health/run-fast-tier.sh | python3 scripts/health/health_score.py --history` once).
- **Plans** — `scripts/queue_status_lite.py`, the coordinator's read-only glance over every active `_harness/` plan.
- **Board drift** — crickets' `src/github-projects/scripts/check_project_sync.py` (a sibling crickets checkout is required; the plugin's own `.harness/project.json` must resolve a `github.repo`). Says "n/a" if no crickets sibling is found, or reports the detector's own graceful-skip line if this repo isn't board-synced.
- **Spend** — `scripts/runner/aggregator.py` refreshes the SQLite rollup (best-effort; a missing crickets sibling means no refresh, not a failure) and `scripts/health/observability_console.py` reads it.
- **Memory activity** — inbox count (`<vault>/personal/_inbox/*.md`), a watchlist summary (`harness/skills/memory/scripts/watchlist_review.py`'s own entry-listing function), incubator count (`<vault>/_idea-incubator/*/`), the five most-recently-modified curated entries under `<vault>/personal/` (excluding the inbox/watchlist/archive staging areas), and the heat-based always-load decay report (`recall.py heat-policy`, dry-run). Says "n/a" if no vault resolves (`$MEMORY_VAULT_PATH` or `plugins.obsidian-vault.vault_path`).

## Known scope limits (v1)

- No recall-trace substrate — "why did entry Y get recalled into this session" is out of scope for v1 (it needs machinery this skill does not build; see `CONSOLIDATION-VERDICT.md` Ruling 7).
- The Health/Plans/Spend/Board-drift sections are agentm-dev-checkout-aware: they read this repo's own `scripts/health/`, `scripts/runner/`, and a crickets sibling. On a bare downstream install (agentm consumed as a harness, not developed directly), those four sections report "n/a" and only Memory activity has real data — expected, not a bug, since those four surfaces are this repo's own dev-tooling and are not shipped to installs (see `install.sh` — nothing under `scripts/health/`, `scripts/runner/`, or `scripts/control_plane/` is copied to a consumer project).
