#!/usr/bin/env python3
"""n1_run.py — the N1 night-shift sequence, re-executed under the Autonomy
arc's control plane (PLAN-autonomy-control-plane task 6, the arc's
acceptance demo).

Ties together every module this plan and `PLAN-observability-console`
built into one orchestration: dispatch fleet work through Agent View
(`dispatch.py`), reflect state changes on the board (`board_sync.py`),
carry the machine-readable tier/model label (`handoff.py`), declare the
launch-time grade (`grade.py`), consult the goal contract's Decide step
(`goal_contract.py`) for the run-level done determination, and — when the
run ends, however it ends — produce the morning report
(`health.morning_report`) and let the digest ladder (`health.inbox_digest`)
and console (`health.observability_console`) read the same rollup.

**This module is real orchestration, not a demo harness.** `run_n1_sequence()`
below is what an operator (or an unattended overnight session) actually
calls to run the sequence for real — it is not a simulation layer.

**Goal-contract wiring (proving-ledger item 19):** `goal_contract.decide()`
had never had a caller outside its own test. This module is now that
caller — the first real one. When `config.done_check_path` is set, the
done-check is fingerprinted *before* dispatch (goal start) and re-checked
at decide time (after dispatch), and `report.decision` comes from
`decide()`, never from the dispatcher self-certifying success. Dispatch
returncodes stand in for `gates_green` here — this run's own deterministic
signal, same as `/work`'s gate battery elsewhere. `cold_review_confirmed`
defaults to `False`: without an explicit external confirmation the
contract can never reach "done" on its own, by design.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
HEALTH_DIR = SCRIPTS_DIR / "health"

for _p in (HERE, SCRIPTS_DIR, HEALTH_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import dispatch as dp  # noqa: E402
import board_sync as bs  # noqa: E402
import handoff as hf  # noqa: E402
import grade as gr  # noqa: E402
import goal_contract as gc  # noqa: E402


@dataclass
class N1Config:
    plan: str
    work_items: list  # list[dispatch.WorkItem]
    cwd: "str | Path"
    telemetry_root: "str | Path | None" = None
    project_config_path: "str | Path | None" = None
    grade: str = gr.DEFAULT_GRADE
    dry_run_board: bool = True
    # Goal-contract wiring (proving-ledger item 19). done_check_path is the
    # run's success criterion (the `done` opinion script, or an operator
    # --accept test) -- when set, the run consults goal_contract.decide()
    # for its own done determination instead of self-certifying on green
    # dispatch results. cold_review_confirmed must come from an actual cold
    # /review dispatch upstream of this call; it is never derived here.
    done_check_path: "str | Path | None" = None
    cold_review_confirmed: bool = False


@dataclass
class N1Report:
    grade_event: "dict | None"
    dispatch_results: list = field(default_factory=list)
    board_outcomes: list = field(default_factory=list)
    handoff_manifest: "dict | None" = None
    decision: "gc.Decision | None" = None


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

    When `config.done_check_path` is set, the done-check is fingerprinted
    here, before dispatch runs -- goal start, per the design -- and
    re-checked at decide time below, after dispatch has run. That ordering
    is what makes the tamper check meaningful: a snapshot taken after
    dispatch would just fingerprint whatever the run already produced.
    """
    done_check_snapshot = None
    if config.done_check_path is not None:
        done_check_snapshot = gc.snapshot_done_check(config.done_check_path)

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

    # Decide step (goal_contract.decide()): the run-level done determination
    # comes from the contract's own check, never from this dispatcher
    # self-certifying on green returncodes. No done-check configured means
    # no contract to consult -- decision stays None rather than guessing.
    decision = None
    if config.done_check_path is not None:
        gates_green = bool(dispatch_results) and all(r.returncode == 0 for r in dispatch_results)
        decision = gc.decide(
            gates_green=gates_green,
            done_check_path=config.done_check_path,
            done_check_snapshot=done_check_snapshot,
            cold_review_confirmed=config.cold_review_confirmed,
        )

    return N1Report(
        grade_event=grade_event, dispatch_results=dispatch_results,
        board_outcomes=board_outcomes, handoff_manifest=handoff_manifest,
        decision=decision,
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
        "decision": asdict(report.decision) if report.decision is not None else None,
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
    p.add_argument(
        "--done-check", default=None,
        help=(
            "path to this run's success criterion (the `done` opinion script, "
            "or an operator --accept test); when set, the run-level done "
            "determination is decided by goal_contract.decide() instead of "
            "being left unstated"
        ),
    )
    p.add_argument(
        "--cold-review-confirmed", action="store_true",
        help=(
            "pass only after an actual cold /review sub-agent has confirmed "
            "this run -- the contract never self-certifies, so omitting this "
            "flag caps the decision at 'continue' even with green dispatch results"
        ),
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
        done_check_path=args.done_check,
        cold_review_confirmed=args.cold_review_confirmed,
    )
    report = run_n1_sequence(config)
    print(json.dumps(_report_to_dict(report), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
