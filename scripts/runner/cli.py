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
from . import manifest as manifest_mod

_DEFAULT_JOBS_DIR = Path(".harness") / "jobs"
_DEFAULT_REPORT_PATH = Path.home() / ".cache" / "agentm" / "runner" / "digest.jsonl"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentm-runner", description="One idempotent runner cycle.")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run one cycle now")
    run.add_argument("--jobs-dir", default=str(_DEFAULT_JOBS_DIR))
    run.add_argument("--harness-dir", default=".harness")
    run.add_argument("--report-path", default=str(_DEFAULT_REPORT_PATH))
    run.add_argument("--state-root", default=None,
                     help="per-job markers and the cycle summary (default ~/.cache/agentm/runner)")
    run.add_argument("--strict", action="store_true",
                     help="the old all-or-nothing load: exit 3 on the first refused manifest, run nothing")
    return p


def main(argv=None) -> int:
    ns = _build_parser().parse_args(argv)
    if ns.cmd == "run":
        try:
            report = cycle_mod.run_cycle(
                Path(ns.jobs_dir),
                harness_dir=Path(ns.harness_dir),
                report_path=Path(ns.report_path),
                state_root=Path(ns.state_root) if ns.state_root else None,
                strict=ns.strict,
            )
        except manifest_mod.ManifestError as e:  # --strict only
            print(f"agentm-runner: {e}", file=sys.stderr)
            return 3
        print(json.dumps(cycle_mod.report_summary(report), indent=2))
        if report.refused and report.loaded == 0:
            names = ", ".join(r["file"] for r in report.refused)
            print(f"agentm-runner: no manifest loaded — refused {len(report.refused)}: {names}", file=sys.stderr)
            return 3
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
