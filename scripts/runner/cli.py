"""CLI entry for the AgentM runner. Invoke via `scripts/agentm-runner.sh run`
(the three host triggers — Desktop/Antigravity Scheduled Tasks, OS cron, and
an on-demand pass — all call this same entry point; only the trigger differs).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cycle as cycle_mod

_DEFAULT_JOBS_DIR = Path(".harness") / "jobs"
_DEFAULT_REPORT_PATH = Path.home() / ".cache" / "agentm" / "runner" / "digest.jsonl"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentm-runner", description="One idempotent runner cycle.")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run one cycle now")
    run.add_argument("--jobs-dir", default=str(_DEFAULT_JOBS_DIR))
    run.add_argument("--harness-dir", default=".harness")
    run.add_argument("--report-path", default=str(_DEFAULT_REPORT_PATH))
    return p


def main(argv=None) -> int:
    ns = _build_parser().parse_args(argv)
    if ns.cmd == "run":
        report = cycle_mod.run_cycle(
            Path(ns.jobs_dir),
            harness_dir=Path(ns.harness_dir),
            report_path=Path(ns.report_path),
        )
        summary = {
            "budget_ceiling_hit": report.budget_ceiling_hit,
            "outcomes": [
                {
                    "job": o.name, "ran": o.ran, "dry_run": o.dry_run,
                    "skipped_reason": o.skipped_reason, "exit_code": o.exit_code,
                    "cost_usd": o.cost_usd,
                }
                for o in report.outcomes
            ],
        }
        print(json.dumps(summary, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
