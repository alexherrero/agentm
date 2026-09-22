#!/usr/bin/env python3
"""queue_status_lite — a read-only dashboard of every active plan of a project.

The coordinator's glance: enumerate each active plan — every task under the
project's `tasks/` whose tracker is not done or dropped, or, for a project with no
vault, the repo-local `PLAN.md` plus every named `PLAN-<name>.md` — and, for each,
print its name, its status (the tracker's, else the plan's `Status:` line), and
the most-recent entry of its progress log.

**Read-only by contract** (V5-10 design call): no claim arbitration, no leases,
no writes — the human is the arbiter. This is the agentm read logic; the crickets
`/queue-status-lite` command surface (a later sibling plan) wraps it.

Usage:

    python3 queue_status_lite.py [--state-dir PATH]

With no `--state-dir`, the directory is resolved from the cwd: the project's vault
directory, or `<repo>/.harness/` for a project with no vault. Always exits 0 — a
status read, not a gate.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402

_PROGRESS_HEAD_MAXLEN = 120


class PlanStatus(NamedTuple):
    """One row of the dashboard."""

    plan_name: str  # "PLAN.md" / "PLAN-foo.md"
    status: str  # value of the Status: line, or "—"
    progress_name: str  # "progress.md" / "progress-foo.md"
    progress_head: str  # most-recent progress entry, or a "(…)" placeholder


def _plan_label(plan_path: Path) -> str:
    """How a plan is named in the dashboard: `PLAN.md`, `PLAN-foo.md`, or
    `tasks/foo/plan.md` for a task (agentm-vault plan 09)."""
    if plan_path.name == "plan.md" and plan_path.parent.parent.name == hm._TASKS_DIRNAME:
        return f"{hm._TASKS_DIRNAME}/{plan_path.parent.name}/plan.md"
    return plan_path.name


def _plan_sort_key(name: str) -> tuple[int, str]:
    """Singleton `PLAN.md` first, then named plans alphabetically, then tasks —
    deterministic."""
    if name == "PLAN.md":
        return (0, "")
    return (2, name) if name.startswith(f"{hm._TASKS_DIRNAME}/") else (1, name)


def _tracker_status(plan_path: Path) -> Optional[str]:
    """A task's status from the tracker beside its plan, when one exists and
    parses — the tracker, not the plan, carries status in the task layout."""
    path = plan_path.parent / "tracker.md"
    if plan_path.name != "plan.md" or not path.is_file():
        return None
    try:
        import tracker  # the one tracker schema (agentm-vault plan 09)
        return tracker.read(path)[0].status
    except Exception:  # a tracker that does not parse is the gate's to report
        return None


def list_plan_files(state: Path) -> list[Path]:
    """Every *active* plan file under `state`: each task whose tracker is not done
    or dropped, or a repo-local `.harness/`'s `PLAN.md` plus each `PLAN-<name>.md`.

    The enumeration is the resolver's own (`hm.list_plan_files`), so archived
    plans and GDrive conflict artifacts are left out the same way everywhere; a
    finished task is left out here, as `list-plans` does, because a dashboard of
    active work does not list closed tasks.
    """
    files = [p for p in hm.list_plan_files(state) if not hm._task_is_finished(p)]
    return sorted(files, key=lambda p: _plan_sort_key(_plan_label(p)))


def _extract_status(plan_text: str) -> str:
    """The value of the first `Status:` line (markdown-bold tolerated), or "—"."""
    for line in plan_text.splitlines():
        stripped = line.strip().lstrip("*").strip()
        if stripped.lower().startswith("status:"):
            value = stripped[len("status:"):].strip().strip("*").strip()
            return value or "—"
    return "—"


def _progress_head(path: Path) -> str:
    """The most-recent (last non-empty) line of an append-only progress log."""
    if not path.is_file():
        return "(no progress file)"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "(unreadable)"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "(empty)"
    head = lines[-1]
    if len(head) > _PROGRESS_HEAD_MAXLEN:
        head = head[: _PROGRESS_HEAD_MAXLEN - 1].rstrip() + "…"
    return head


def collect_plan_statuses(state: Path) -> list[PlanStatus]:
    """Read-only: build a `PlanStatus` row for each active plan under `state`.

    No writes, no mutation — the directory is byte-identical after this call. The
    PLAN→progress filename mapping reuses the centralized resolver helpers
    (`hm._normalize_plan_name` / `hm._plan_pair`) so the naming contract lives in
    exactly one place (the contract task 1/2 lock).
    """
    rows: list[PlanStatus] = []
    for plan_path in list_plan_files(state):
        plan_name = _plan_label(plan_path)
        try:
            plan_text = plan_path.read_text(encoding="utf-8")
        except OSError:
            plan_text = ""
        status = _tracker_status(plan_path) or _extract_status(plan_text)
        if plan_name.startswith(f"{hm._TASKS_DIRNAME}/"):
            # A task's progress log sits beside its plan.
            progress_name = plan_name[: -len("plan.md")] + "progress.md"
            progress_head = _progress_head(plan_path.parent / "progress.md")
        else:
            progress_name = hm._plan_pair(hm._normalize_plan_name(plan_name))[1]
            progress_head = _progress_head(state / progress_name)
        rows.append(PlanStatus(plan_name, status, progress_name, progress_head))
    return rows


def render(state: Path, rows: list[PlanStatus]) -> str:
    """A deterministic, human-scannable block. Output depends only on
    `state`'s contents — no wall-clock, no color — so it is test-stable."""
    if not rows:
        return f"No plans found in {state}\n"
    width = max(len(r.plan_name) for r in rows)
    lines = [f"Active plans in {state}:", ""]
    for r in rows:
        lines.append(f"  {r.plan_name:<{width}}  [{r.status}]")
        lines.append(f"  {'':<{width}}  last: {r.progress_head}")
    return "\n".join(lines) + "\n"


def _resolve_state_dir(explicit: Optional[str]) -> Optional[Path]:
    if explicit is not None:
        return Path(explicit)
    resolution = hm.resolve_project({"cwd": Path.cwd()})
    return hm.state_dir(resolution)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="queue_status_lite",
        description="Read-only dashboard of every active plan of a project.",
    )
    parser.add_argument(
        "--state-dir", "--harness-dir",
        dest="state_dir",
        default=None,
        help="the project's vault directory, or a repo-local .harness/, to "
             "enumerate (default: resolve from cwd).",
    )
    args = parser.parse_args(argv)

    state = _resolve_state_dir(args.state_dir)
    if state is None or not state.is_dir():
        # Graceful: no resolvable state directory is not an error for a status read.
        where = state if state is not None else "(unresolved)"
        print(f"No plan state directory to read ({where}).")
        return 0

    rows = collect_plan_statuses(state)
    sys.stdout.write(render(state, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
