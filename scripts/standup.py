#!/usr/bin/env python3
"""standup — derive worker states from the plan graph (V5-11 task 3).

Thin bridge over ``plan_graph.build_plan_graph()``.  For each *active* plan,
derives a ``worker_state`` from its task counts and last-touched timestamp and
returns an annotated table.  The team-coordinator persona calls this script
and narrates the returned table as a standup paragraph.

**Advisory only / read-only.**  Zero writes to disk.

Usage::

    python3 scripts/standup.py [--harness-dir PATH]

Output is a deterministic, human-scannable table plus a JSON block that the
persona's narration step consumes.

Worker states:
    building    Tasks remain and progress is recent (< IDLE_THRESHOLD_HOURS).
    mergeable   All tasks done, plan not yet merged (Status ≠ "done" only in
                the plan-level sense — we use tasks_done == tasks_total here,
                because plan Status can lag the last commit).
    idle        No progress-log touch in > IDLE_THRESHOLD_HOURS, *and* tasks
                remain (otherwise it would be mergeable).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402
import plan_graph as pg  # noqa: E402

# Named constant — easy to adjust without a schema migration.
IDLE_THRESHOLD_HOURS: int = 2


@dataclass
class WorkerRow:
    """One row of the standup table."""

    slug: str
    filename: str
    tasks_done: int
    tasks_total: int
    last_touched: Optional[str]  # ISO-formatted string, or None
    worker_state: str            # "building" | "mergeable" | "idle"


def _derive_state(
    plan: pg.PlanInfo,
    now: datetime,
    idle_threshold: timedelta,
) -> str:
    """Return the worker state string for *plan* at *now*."""
    if plan.tasks_total > 0 and plan.tasks_done == plan.tasks_total:
        return "mergeable"
    if plan.last_touched is None:
        return "idle"
    if (now - plan.last_touched) > idle_threshold:
        return "idle"
    return "building"


def build_standup(
    harness_dir: Path,
    now: Optional[datetime] = None,
    idle_threshold_hours: int = IDLE_THRESHOLD_HOURS,
) -> List[WorkerRow]:
    """Return an annotated standup table for every *active* plan.

    *now* is injected for deterministic testing — defaults to the current UTC
    clock when None.  Queued plans are excluded from the standup (they haven't
    started).
    """
    if now is None:
        now = datetime.utcnow()
    threshold = timedelta(hours=idle_threshold_hours)

    plans = pg.build_plan_graph(harness_dir)
    rows: List[WorkerRow] = []
    for plan in plans:
        if not plan.active:
            continue
        state = _derive_state(plan, now, threshold)
        touched_str = (
            plan.last_touched.strftime("%Y-%m-%d %H:%M")
            if plan.last_touched
            else None
        )
        rows.append(
            WorkerRow(
                slug=plan.slug or "(singleton)",
                filename=plan.filename,
                tasks_done=plan.tasks_done,
                tasks_total=plan.tasks_total,
                last_touched=touched_str,
                worker_state=state,
            )
        )
    return rows


def render_table(rows: List[WorkerRow]) -> str:
    """Plain-text table suitable for human reading and persona narration."""
    if not rows:
        return "(no active plans)"
    lines = [
        f"{'PLAN':<30} {'TASKS':>8}  {'LAST TOUCHED':>16}  STATE",
        "-" * 68,
    ]
    for r in rows:
        tasks = f"{r.tasks_done}/{r.tasks_total}"
        touched = r.last_touched or "—"
        lines.append(f"{r.slug:<30} {tasks:>8}  {touched:>16}  {r.worker_state}")
    return "\n".join(lines)


def _main() -> None:
    ap = argparse.ArgumentParser(description="Standup table for active workers.")
    ap.add_argument("--harness-dir", help="Path to the _harness/ directory.")
    ap.add_argument(
        "--json", action="store_true",
        help="Emit JSON array instead of the human-readable table.",
    )
    args = ap.parse_args()

    if args.harness_dir:
        harness_dir = Path(args.harness_dir)
    else:
        harness_dir = hm.harness_state_dir()

    rows = build_standup(harness_dir)

    if args.json:
        print(json.dumps([asdict(r) for r in rows], indent=2))
    else:
        print(render_table(rows))


if __name__ == "__main__":
    _main()
