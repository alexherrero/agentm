<!-- mode: reference -->
# Queue status lite

The `/queue-status-lite` command provides a read-only dashboard. It lists every active plan in a project's `_harness/` directory. Each entry shows the plan status and the head of the progress log. The command is deterministic. It makes no writes. It arbitrates no claims — the human stays the arbiter. The reasoning lives in [Named plans](Named-Plans); this page is the lookup.

## ⚡ Quick Reference

| Property | Value |
|---|---|
| Script (agentm-shipped) | `scripts/queue_status_lite.py` |
| Invocation | `python3 scripts/queue_status_lite.py [--harness-dir PATH]` |
| `--harness-dir PATH` | the `_harness/` directory to enumerate; omit to resolve from cwd via `harness_state_dir` |
| Slash-command surface | `/queue-status-lite` — **crickets-provided** (development-lifecycle plugin), wraps this script |
| Reads | every `PLAN*.md` in the resolved `_harness/` (named + unnamed), plus every `tasks/<slug>/plan.md` beside it (agentm-vault plan 09) |
| Per plan, reports | plan name · its status (a task's tracker, else its `Status:` line) · the head (most-recent entry) of the matching `progress*.md` |
| Writes | **none** — read-only; the fixture is byte-identical before/after a run |
| Claim arbitration | **none** — informational dashboard only; the human arbitrates |
| Missing-progress placeholder | `(no progress file)` (also `(empty)` / `(unreadable)`) |
| Exit code | `0` — always, including unresolved / missing / empty `_harness/` |

## What it lists

The script reads every `PLAN*.md` file in the resolved `_harness/` directory — the singleton `PLAN.md`, any named `PLAN-<name>.md` file, and, beside a vault `_harness/`, any task's `tasks/<name>/plan.md` (agentm-vault plan 09). It prints the singleton first, then named plans alphabetically, then tasks:

| Column | Source |
|---|---|
| Plan name | the filename (`PLAN.md` or `PLAN-<name>.md`), or `tasks/<name>/plan.md` for a task |
| Status | a task's tracker `status`, when its `tracker.md` exists and parses; otherwise the plan's `Status:` line (`**Status:**` and `Status:` both accepted; `—` if absent) |
| Progress head | the last non-empty line of the matching `progress*.md` file — beside the plan itself for a task, `progress-<name>.md` in `_harness/` otherwise — truncated to 120 chars with a trailing `…` |

Archived plans (`PLAN.archive.*.md`) and GDrive conflict copies (`PLAN-foo (conflicted copy …).md`) are excluded — the former by the `PLAN-*` glob, the latter via `hm._conflict_family`.

### Output shape

```
Active plans in <dir>:

  PLAN.md      [in-progress]
               last: <most-recent progress line, truncated to 120 chars with …>
  PLAN-foo.md  [planning]
               last: <…>
```

A plan with no matching progress file still lists, with the head shown as `(no progress file)`. An unresolved / missing / empty `_harness/` prints `No plans found …` or `No _harness/ directory to read …` and still exits `0`.

## Contract

- **Read-only.** The command performs zero filesystem mutation. Its test asserts the fixture `_harness/` directory is byte-identical before and after a run.
- **Deterministic.** The script enumerates the resolved `_harness/` directory — no network, no transcript mining, no sub-agent dispatch. Output depends only on the directory contents.
- **No arbitration.** It does not claim, lease, lock, or assign plans to workers. It surfaces state for a human coordinator to read; the design deliberately omits queue/lease machinery.
- **Always exit 0.** Success exits `0`; so does every graceful path — no resolvable `_harness/`, a missing directory, or an empty one. A status read never errors on absence.

## Implementation

| Surface | Location |
|---|---|
| CLI entry · single `--harness-dir` flag · both paths `return 0` | [`scripts/queue_status_lite.py#L171`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L171) |
| Dir resolution — fall back to `harness_state_dir` when flag omitted | [`scripts/queue_status_lite.py#L164`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L164) |
| `list_plan_files` — singleton-first sort, excludes archives + conflict copies, then every `tasks/<slug>/plan.md` beside a vault `_harness/` (agentm-vault plan 09); **canonical public copy** now in `harness_memory.py` (V5-5 task 3); `queue_status_lite.py` carries a local copy until the two converge | [`scripts/harness_memory.py#L962`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L962) (canonical), [`scripts/queue_status_lite.py#L73`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L73) (local copy) |
| `list-plans` CLI verb — enumerates active plan files + emits `active-binding=<slug>` when `.harness/active-plan` is set; used by both session-start hooks for plan discovery | [`scripts/harness_memory.py#L2321`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L2321) (dispatch), locked by `TestListPlansCLI` in [`scripts/test_harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory.py) (7 tests) |
| `_plan_label` — the dashboard name for a plan: `PLAN.md`, `PLAN-foo.md`, or `tasks/foo/plan.md` for a task (agentm-vault plan 09) | [`scripts/queue_status_lite.py#L44`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L44) |
| `_tracker_status` — a task's status from the tracker beside its plan, when one exists and parses; the plan's own `Status:` line is the fallback | [`scripts/queue_status_lite.py#L60`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L60) |
| `_extract_status` — bold + un-bold `Status:` | [`scripts/queue_status_lite.py#L97`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L97) |
| `_progress_head` — `(no progress file)` / `(empty)` / `(unreadable)`, 120-char truncation | [`scripts/queue_status_lite.py#L107`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L107) |
| `collect_plan_statuses` — `PlanStatus` rows; reuses `hm._normalize_plan_name` / `hm._plan_pair` for a flat pair, a task's own `progress.md` beside its plan otherwise | [`scripts/queue_status_lite.py#L124`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L124) |
| `harness_state_dir` — directory companion to `vault_state_path` (local → `.harness`, vault → `_harness`, else None) | [`scripts/harness_memory.py#L941`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L941) |
| Test suite (14 tests); the task-layout reading is covered together with `plan_graph.py` and `machinery_doctor.py` | [`scripts/test_queue_status_lite.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_queue_status_lite.py), [`scripts/test_projects_layout_readers.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_projects_layout_readers.py) |

## Related

- [Named plans](Named-Plans) — the naming contract (`PLAN-<name>.md` / `progress-<name>.md`) this command enumerates, and why the human is the arbiter.
- [Vault write protocol](Vault-Write-Protocol) — the write protocol named plans go through; this read-model takes no lock because it never writes.
- [CI gates](CI-Gates) — `check-multi-plan-naming` (#13) locks the naming contract this command reads against.
- [Repo layout](Repo-Layout) — where `scripts/` lives (test infra + read-model scripts; never propagated to target projects).
