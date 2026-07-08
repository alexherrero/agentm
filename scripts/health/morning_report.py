#!/usr/bin/env python3
"""morning_report.py — names why an overnight run ended, with spend attached
(PLAN-observability-console task 4, `wiki/designs/agentm-autonomy.md`'s
"When the window runs out" section: "The morning report names why the run
ended — plan finished, gates green, an escalation parked, or the window ran
out — with the spend attached.").

Spend attached is the rollup's per-plan total (`by_plan.cost_usd`) -- there
is no narrower per-run-instance spend slice at this layer today (a plan can
span more than one run/session, and nothing yet stamps a shared run
identifier across a single overnight stretch's events). If
`PLAN-autonomy-control-plane` introduces such an identifier, this module's
`spend_usd` field is the one to repoint at it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

ENDING_CAUSES = ("plan-finished", "gates-green", "escalation-parked", "window-exhausted")

_ENDING_CAUSE_LABELS = {
    "plan-finished": "the plan finished",
    "gates-green": "gates went green",
    "escalation-parked": "an escalation was parked",
    "window-exhausted": "the window ran out",
}


def _spend_for_plan(db_path: "str | Path", plan_slug: str) -> float:
    """The rollup's total spend for `plan_slug`, or 0.0 if the plan has no
    rows yet (never raises on a missing rollup -- a morning report must be
    renderable even before any spend has accumulated)."""
    sys.path.insert(0, str(HERE))
    import observability_console as oc  # noqa: E402

    try:
        rollup = oc.read_rollup(db_path)
    except ValueError:
        return 0.0
    for row in rollup["by_plan"]:
        if row["plan"] == plan_slug:
            return float(row["cost_usd"])
    return 0.0


def compute_morning_report(
    ending_cause: str, *, plan_slug: str, db_path: "str | Path",
    park_state: "dict | None" = None,
) -> dict:
    """Assemble the morning report's data. `ending_cause` must be one of
    `ENDING_CAUSES` -- a value outside that set is a caller bug, not a
    graceful-degrade case, so this raises `ValueError` rather than silently
    rendering an unrecognized cause."""
    if ending_cause not in ENDING_CAUSES:
        raise ValueError(f"unrecognized ending_cause {ending_cause!r} -- must be one of {ENDING_CAUSES}")

    spend = _spend_for_plan(db_path, plan_slug)
    data = {
        "plan": plan_slug,
        "ending_cause": ending_cause,
        "ending_cause_label": _ENDING_CAUSE_LABELS[ending_cause],
        "spend_usd": round(spend, 6),
        "park_state": None,
    }
    if ending_cause == "window-exhausted" and park_state is not None:
        data["park_state"] = {
            "parked_at": park_state.get("parked_at"),
            "task_progress": park_state.get("task_progress"),
            "resume_command": park_state.get("resume_command"),
        }
    return data


def render_morning_report(data: dict) -> str:
    """Render the morning report as markdown. Deterministic given `data`."""
    lines = [
        f"# Morning report — {data['plan']}",
        "",
        f"Ended because {data['ending_cause_label']} ({data['ending_cause']}).",
        f"Spend: ${data['spend_usd']:.4f}",
        "",
    ]
    park = data.get("park_state")
    if park is not None:
        lines.append("## Park details")
        lines.append("")
        lines.append(f"- Parked at: {park['parked_at']}")
        lines.append(f"- Progress: {park['task_progress']}")
        lines.append("")
        lines.append("### Resume")
        lines.append("")
        lines.append("```")
        lines.append(str(park["resume_command"]))
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Render a morning report for a plan's run.")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--ending-cause", required=True, choices=ENDING_CAUSES)
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--park-dir", default=None, help="if given and ending-cause is window-exhausted, read the park state from here")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    park_state = None
    if args.ending_cause == "window-exhausted":
        sys.path.insert(0, str(HERE))
        import window_park  # noqa: E402
        park_state = window_park.read_park_state(args.plan, park_dir=args.park_dir)

    try:
        data = compute_morning_report(
            args.ending_cause, plan_slug=args.plan, db_path=args.db_path, park_state=park_state,
        )
    except ValueError as e:
        print(f"morning_report: {e}", file=sys.stderr)
        return 2

    print(render_morning_report(data), end="")
    print(json.dumps({"total_cost_usd": 0.0}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
