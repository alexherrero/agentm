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

import sys
from dataclasses import dataclass, field
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


def run_n1_sequence(config: N1Config, *, dispatcher=dp.dispatch, board_runner=None) -> N1Report:
    """Run the N1 sequence for real: declare the launch grade, dispatch
    every work item, reflect each dispatch on the board, and build a
    handoff pack for the batch. Morning-report / digest / console
    generation happen afterward, against whatever the ledger now holds —
    this function's job ends at dispatch + board + handoff, the same
    boundary `PLAN-observability-console`'s own modules already own for
    reporting.
    """
    grade_event = gr.declare_run_start(
        config.plan, grade=config.grade, root=config.cwd, telemetry_root=config.telemetry_root,
    )

    dispatch_results = [dispatcher(item) for item in config.work_items]

    board_outcomes = []
    if config.project_config_path is not None:
        kwargs = {"config_path": config.project_config_path, "dry_run": config.dry_run_board}
        if board_runner is not None:
            kwargs["runner"] = board_runner
        board_outcomes = bs.post_fleet_run_summary(
            [{"name": r.name, "status": "dispatched"} for r in dispatch_results], **kwargs,
        )

    handoff_manifest = hf.build_fleet_handoff_pack(
        dispatch_results, {}, Path(config.cwd) / "_n1_handoff",
    )

    return N1Report(
        grade_event=grade_event, dispatch_results=dispatch_results,
        board_outcomes=board_outcomes, handoff_manifest=handoff_manifest,
    )
