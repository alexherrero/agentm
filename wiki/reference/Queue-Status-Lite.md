<!-- mode: reference -->
# Queue status lite

The `/queue-status-lite` command provides a read-only dashboard. It lists every active plan under a project's resolved state directory — its vault directory's `tasks/` on a synced backend, or a repo-local `.harness/` for a project with no vault. Each entry shows the plan status and the head of the progress log. The command is deterministic. It makes no writes. It arbitrates no claims — the human stays the arbiter. The reasoning lives in [Named plans](Named-Plans); this page is the lookup.

## ⚡ Quick Reference

| Property | Value |
|---|---|
| Script (agentm-shipped) | `scripts/queue_status_lite.py` |
| Invocation | `python3 scripts/queue_status_lite.py [--state-dir PATH]` (`--harness-dir` accepted as an alias) |
| `--state-dir PATH` | the project's vault directory, or a repo-local `.harness/`, to enumerate; omit to resolve from cwd via `harness_memory.state_dir` |
| Slash-command surface | `/queue-status-lite` — **crickets-provided** (development-lifecycle plugin), wraps this script |
| Reads | on a synced backend, every task under `tasks/` whose tracker is not done or dropped; with no vault, the repo-local `PLAN.md` plus every named `PLAN-<name>.md` (agentm-vault plan 15 retired the vault's `_harness/` — the two are no longer merged in one directory) |
| Per plan, reports | plan name · its status (a task's tracker, else its `Status:` line) · the head (most-recent entry) of the matching progress log |
| Writes | **none** — read-only; the fixture is byte-identical before/after a run |
| Claim arbitration | **none** — informational dashboard only; the human arbitrates |
| Missing-progress placeholder | `(no progress file)` (also `(empty)` / `(unreadable)`) |
| Exit code | `0` — always, including an unresolved, missing, or empty state directory |

## What it lists

On a synced backend the script reads every active task under the project's `tasks/` directory — a finished (done or dropped) task is left out, since a dashboard of active work doesn't list closed tasks. With no vault it reads the repo-local `.harness/`'s flat pair: the singleton `PLAN.md`, then any named `PLAN-<name>.md` file, alphabetically. The two are never combined — a project resolves to exactly one of them (agentm-vault plan 15):

| Column | Source |
|---|---|
| Plan name | `tasks/<name>/plan.md` for a task, or the filename (`PLAN.md` / `PLAN-<name>.md`) for a repo-local pair |
| Status | a task's tracker `status`, when its `tracker.md` exists and parses; otherwise the plan's `Status:` line (`**Status:**` and `Status:` both accepted; `—` if absent) |
| Progress head | the last non-empty line of the matching progress log — `progress.md` beside the plan itself for a task, `progress-<name>.md` in the repo-local `.harness/` otherwise — truncated to 120 chars with a trailing `…` |

Archived plans (`PLAN.archive.*.md`) and GDrive conflict copies (`PLAN-foo (conflicted copy …).md`) are excluded — the former by the `PLAN-*` glob, the latter via `hm._conflict_family`.

### Output shape

```
Active plans in <dir>:

  PLAN.md      [in-progress]
               last: <most-recent progress line, truncated to 120 chars with …>
  PLAN-foo.md  [planning]
               last: <…>
```

A plan with no matching progress file still lists, with the head shown as `(no progress file)`. An unresolved or missing state directory prints `No plan state directory to read (...)`; a resolved but empty one prints `No plans found in <dir>`. Both still exit `0`.

## Contract

- **Read-only.** The command performs zero filesystem mutation. Its test asserts the fixture state directory is byte-identical before and after a run.
- **Deterministic.** The script enumerates the resolved state directory — no network, no transcript mining, no sub-agent dispatch. Output depends only on the directory contents.
- **No arbitration.** It does not claim, lease, lock, or assign plans to workers. It surfaces state for a human coordinator to read; the design deliberately omits queue/lease machinery.
- **Always exit 0.** Success exits `0`; so does every graceful path — no resolvable state directory, a missing directory, or an empty one. A status read never errors on absence.

## Implementation

| Surface | Location |
|---|---|
| CLI entry · `--state-dir` flag (`--harness-dir` alias) · both paths `return 0` | [`scripts/queue_status_lite.py#L163`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L163) |
| Dir resolution — fall back to `harness_memory.state_dir` when flag omitted | [`scripts/queue_status_lite.py#L156`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L156) |
| `list_plan_files` — every task not yet done or dropped under a vault project's `tasks/`, or a repo-local `.harness/`'s flat pair, singleton first (agentm-vault plans 09/15); local wrapper filters `hm.list_plan_files`'s canonical enumeration by `hm._task_is_finished` | [`scripts/harness_memory.py#L995`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L995) (canonical), [`scripts/queue_status_lite.py#L76`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L76) (local wrapper) |
| `list-plans` CLI verb — enumerates active plan files + emits `active-binding=<slug>` when `.harness/active-plan` is set; used by both session-start hooks for plan discovery | [`scripts/harness_memory.py#L2507`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L2507) (dispatch), locked by `TestListPlansCLI` in [`scripts/test_harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory.py) (7 tests) |
| `_plan_label` — the dashboard name for a plan: `tasks/foo/plan.md` for a task, else `PLAN.md` / `PLAN-foo.md` (agentm-vault plan 09) | [`scripts/queue_status_lite.py#L44`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L44) |
| `_tracker_status` — a task's status from the tracker beside its plan, when one exists and parses; the plan's own `Status:` line is the fallback | [`scripts/queue_status_lite.py#L60`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L60) |
| `_extract_status` — bold + un-bold `Status:` | [`scripts/queue_status_lite.py#L89`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L89) |
| `_progress_head` — `(no progress file)` / `(empty)` / `(unreadable)`, 120-char truncation | [`scripts/queue_status_lite.py#L99`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L99) |
| `collect_plan_statuses` — `PlanStatus` rows; reuses `hm._normalize_plan_name` / `hm._plan_pair` for a repo-local pair, a task's own `progress.md` beside its plan otherwise | [`scripts/queue_status_lite.py#L116`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L116) |
| `harness_memory.state_dir` — the project's vault directory on a synced backend, else a repo-local `.harness/` (renamed from `harness_state_dir` under agentm-vault plan 15, when the vault side stopped being `_harness/`) | [`scripts/harness_memory.py#L946`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L946) |
| Test suite (14 tests); the task-layout reading is covered together with `plan_graph.py` and `machinery_doctor.py` | [`scripts/test_queue_status_lite.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_queue_status_lite.py), [`scripts/test_projects_layout_readers.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_projects_layout_readers.py) |

## Related

- [Named plans](Named-Plans) — the naming contract (`PLAN-<name>.md` / `progress-<name>.md`) this command enumerates, and why the human is the arbiter.
- [Vault write protocol](Vault-Write-Protocol) — the write protocol named plans go through; this read-model takes no lock because it never writes.
- [CI gates](CI-Gates) — `check-multi-plan-naming` (#13) locks the naming contract this command reads against.
- [Repo layout](Repo-Layout) — where `scripts/` lives (test infra + read-model scripts; never propagated to target projects).
