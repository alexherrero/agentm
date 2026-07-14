#!/usr/bin/env python3
"""n1_run.py — the N1 night-shift sequence, re-executed under the Autonomy
arc's control plane (PLAN-autonomy-control-plane task 6, the arc's
acceptance demo).

Ties together every module this plan and `PLAN-observability-console`
built into one orchestration: dispatch fleet work through Agent View
(`dispatch.py`), reflect state changes on the board (`board_sync.py`),
carry the machine-readable tier/model label (`handoff.py`), declare the
launch-time grade (`grade.py`), and — when the run ends, however it ends —
produce the morning report (`health.morning_report`) and let the digest
ladder (`health.inbox_digest`) and console (`health.observability_console`)
read the same rollup.

**This module is real orchestration, not a demo harness.** `run_n1_sequence()`
below is what an operator (or an unattended overnight session) actually
calls to run the sequence for real — it is not a simulation layer.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEALTH_DIR = HERE.parent / "health"

for _p in (HERE, HEALTH_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import dispatch as dp  # noqa: E402
import board_sync as bs  # noqa: E402
import handoff as hf  # noqa: E402
import grade as gr  # noqa: E402


@dataclass
class N1Config:
    plan: str
    work_items: list  # list[dispatch.WorkItem]
    cwd: "str | Path"
    telemetry_root: "str | Path | None" = None
    project_config_path: "str | Path | None" = None
    grade: str = gr.DEFAULT_GRADE
    dry_run_board: bool = True


@dataclass
class N1Report:
    grade_event: "dict | None"
    dispatch_results: list = field(default_factory=list)
    board_outcomes: list = field(default_factory=list)
    handoff_manifest: "dict | None" = None


def run_n1_sequence(
    config: N1Config, *, dispatcher=dp.dispatch, board_runner=None,
    grade_declarer=gr.declare_run_start, handoff_builder=hf.build_fleet_handoff_pack,
) -> N1Report:
    """Run the N1 sequence for real: declare the launch grade, dispatch
    every work item, reflect each dispatch on the board, and build a
    handoff pack for the batch. Morning-report / digest / console
    generation happen afterward, against whatever the ledger now holds —
    this function's job ends at dispatch + board + handoff, the same
    boundary `PLAN-observability-console`'s own modules already own for
    reporting.

    `grade_declarer` / `handoff_builder` default to the real crickets-
    backed functions (`grade.declare_run_start` / `handoff.
    build_fleet_handoff_pack`) -- override only for hermetic unit tests
    that must not depend on a crickets sibling checkout being reachable.

    A work item with no `cwd` of its own dispatches under `config.cwd` --
    never `dispatch()`'s own bare `Path.cwd()` fallback, which silently
    resolves to wherever *this process* happens to be running from (e.g.
    `scripts/`, the runner's own invocation convention) rather than the
    project root a fleet-dispatched session actually needs to operate in.
    Confirmed live (V8 proving Phase 3, 2026-07-13): an n1-overnight run
    invoked from `scripts/` per that convention dispatched both real work
    items rooted at `scripts/` instead of the repo root before this fix.
    """
    grade_event = grade_declarer(
        config.plan, grade=config.grade, root=config.cwd, telemetry_root=config.telemetry_root,
    )

    items = [item if item.cwd is not None else replace(item, cwd=str(config.cwd)) for item in config.work_items]
    dispatch_results = [dispatcher(item) for item in items]

    board_outcomes = []
    if config.project_config_path is not None:
        kwargs = {"config_path": config.project_config_path, "dry_run": config.dry_run_board}
        if board_runner is not None:
            kwargs["runner"] = board_runner
        board_outcomes = bs.post_fleet_run_summary(
            [{"name": r.name, "status": "dispatched"} for r in dispatch_results], **kwargs,
        )

    handoff_manifest = handoff_builder(
        dispatch_results, {}, Path(config.cwd) / "_n1_handoff",
    )

    return N1Report(
        grade_event=grade_event, dispatch_results=dispatch_results,
        board_outcomes=board_outcomes, handoff_manifest=handoff_manifest,
    )


# ── CLI (CONS-7 task 6: a durable trigger for the overnight path -- finishing
# already-shipped orchestration, not new behavior; `run_n1_sequence` above is
# unchanged) ──────────────────────────────────────────────────────────────────
def _load_work_items(path: "str | Path") -> list:
    """Load `dp.WorkItem`s from a JSON file shaped
    `{"work_items": [{"plan": ..., "task": ..., "prompt": ..., ...}, ...]}`.
    Raises `ValueError` (not a bare JSON/KeyError) on a malformed manifest --
    a scheduled job should fail loud and namable, not with a raw traceback."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"n1_run: unreadable work-items file {p}: {e}") from e
    items_raw = raw.get("work_items") if isinstance(raw, dict) else None
    if not isinstance(items_raw, list):
        raise ValueError(f"n1_run: {p} must contain a top-level {{'work_items': [...]}} object")
    items = []
    for i, entry in enumerate(items_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"n1_run: work_items[{i}] in {p} is not an object")
        try:
            items.append(dp.WorkItem(**entry))
        except TypeError as e:
            raise ValueError(f"n1_run: work_items[{i}] in {p} has an invalid field: {e}") from e
    return items


def _report_to_dict(report: N1Report) -> dict:
    return {
        "grade_event": report.grade_event,
        "dispatch_results": [asdict(r) for r in report.dispatch_results],
        "board_outcomes": report.board_outcomes,
        "handoff_manifest": report.handoff_manifest,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="n1_run",
        description=(
            "Run the N1 night-shift sequence for real: declare the launch "
            "grade, dispatch every work item, reflect each on the board, "
            "and build a handoff pack for the batch."
        ),
    )
    p.add_argument("--plan", required=True, help="the plan slug this run belongs to")
    p.add_argument(
        "--work-items", required=True,
        help="path to a JSON file: {\"work_items\": [{\"plan\":..,\"task\":..,\"prompt\":..}, ...]}",
    )
    p.add_argument("--cwd", default=".", help="dispatch cwd (default: the current directory)")
    p.add_argument("--telemetry-root", default=None)
    p.add_argument("--project-config", default=None, help="path to .harness/project.json for board reflection")
    p.add_argument("--grade", default=gr.DEFAULT_GRADE)
    p.add_argument(
        "--live-board", action="store_true",
        help="post real board updates instead of the default dry-run preview",
    )
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = build_arg_parser().parse_args(argv if argv is not None else sys.argv[1:])
    try:
        work_items = _load_work_items(args.work_items)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    config = N1Config(
        plan=args.plan,
        work_items=work_items,
        cwd=args.cwd,
        telemetry_root=args.telemetry_root,
        project_config_path=args.project_config,
        grade=args.grade,
        dry_run_board=not args.live_board,
    )
    report = run_n1_sequence(config)
    print(json.dumps(_report_to_dict(report), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
