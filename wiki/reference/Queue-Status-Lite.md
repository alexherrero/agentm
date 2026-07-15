<!-- mode: reference -->
# Queue status lite

`/queue-status-lite` is the read-only coordinator dashboard: it lists every active plan in a project's `_harness/`, along with its status and the head of its progress log. It is deterministic, makes no writes, and arbitrates no claims — the human stays the arbiter. The *why* is [Named plans](Named-Plans); this page is the lookup.

## ⚡ Quick Reference

| Property | Value |
|---|---|
| Script (agentm-shipped) | `scripts/queue_status_lite.py` |
| Invocation | `python3 scripts/queue_status_lite.py [--harness-dir PATH]` |
| `--harness-dir PATH` | the `_harness/` directory to enumerate; omit to resolve from cwd via `harness_state_dir` |
| Slash-command surface | `/queue-status-lite` — **crickets-provided** (developer-workflows plugin), wraps this script |
| Reads | every `PLAN*.md` in the resolved `_harness/` (named + unnamed) |
| Per plan, reports | plan name · its `Status:` line · the head (most-recent entry) of the matching `progress*.md` |
| Writes | **none** — read-only; the fixture is byte-identical before/after a run |
| Claim arbitration | **none** — informational dashboard only; the human arbitrates |
| Missing-progress placeholder | `(no progress file)` (also `(empty)` / `(unreadable)`) |
| Exit code | `0` — always, including unresolved / missing / empty `_harness/` |

## What it lists

For every `PLAN*.md` file in the resolved `_harness/` directory — both the unnamed singleton `PLAN.md` and any `PLAN-<name>.md` — it prints the singleton first, then named plans alphabetically:

| Column | Source |
|---|---|
| Plan name | the filename (`PLAN.md`, or `PLAN-<name>.md`) |
| Status | the plan's `Status:` line, read from the plan file (markdown-bold `**Status:**` and plain `Status:` both accepted; `—` if absent) |
| Progress head | the most-recent (last non-empty) line of the matching `progress*.md` (`progress-<name>.md`, or `progress.md` for the singleton), truncated to 120 chars with a trailing `…` |

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
- **Deterministic.** No network, no transcript mining, no sub-agent dispatch — pure enumeration of the resolved `_harness/`. Output depends only on the directory contents.
- **No arbitration.** It does not claim, lease, lock, or assign plans to workers. It surfaces state for a human coordinator to read; the design deliberately omits queue/lease machinery.
- **Always exit 0.** Success exits `0`; so does every graceful path — no resolvable `_harness/`, a missing directory, or an empty one. A status read never errors on absence.

## Implementation

| Surface | Location |
|---|---|
| CLI entry · single `--harness-dir` flag · both paths `return 0` | [`scripts/queue_status_lite.py#L137`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L137) |
| Dir resolution — fall back to `harness_state_dir` when flag omitted | [`scripts/queue_status_lite.py#L130`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L130) |
| `list_plan_files` — singleton-first sort, excludes archives + conflict copies; **canonical public copy** now in `harness_memory.py` (V5-5 task 3); `queue_status_lite.py` carries a local copy until the two converge | [`scripts/harness_memory.py#L588`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L588) (canonical), [`scripts/queue_status_lite.py#L49`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L49) (local copy) |
| `list-plans` CLI verb — enumerates active plan files + emits `active-binding=<slug>` when `.harness/active-plan` is set; used by both session-start hooks for plan discovery | [`scripts/harness_memory.py#L1837`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L1837) (dispatch), locked by `TestListPlansCLI` in [`scripts/test_harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory.py) (7 tests) |
| `_extract_status` — bold + un-bold `Status:` | [`scripts/queue_status_lite.py#L68`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L68) |
| `_progress_head` — `(no progress file)` / `(empty)` / `(unreadable)`, 120-char truncation | [`scripts/queue_status_lite.py#L78`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L78) |
| `collect_plan_statuses` — `PlanStatus` rows; reuses `hm._normalize_plan_name` / `hm._plan_pair` | [`scripts/queue_status_lite.py#L95`](https://github.com/alexherrero/agentm/blob/main/scripts/queue_status_lite.py#L95) |
| `harness_state_dir` — directory companion to `vault_state_path` (local → `.harness`, vault → `_harness`, else None) | [`scripts/harness_memory.py#L567`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L567) |
| Test suite (14 tests) | [`scripts/test_queue_status_lite.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_queue_status_lite.py) |

## Related

- [Named plans](Named-Plans) — the naming contract (`PLAN-<name>.md` / `progress-<name>.md`) this command enumerates, and why the human is the arbiter.
- [Vault write protocol](Vault-Write-Protocol) — the write protocol named plans go through; this read-model takes no lock because it never writes.
- [CI gates](CI-Gates) — `check-multi-plan-naming` (#13) locks the naming contract this command reads against.
- [Repo layout](Repo-Layout) — where `scripts/` lives (test infra + read-model scripts; never propagated to target projects).
