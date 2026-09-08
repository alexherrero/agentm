"""CLI entry for the AgentM runner. Invoke via `scripts/agentm-runner.sh run`
(the three host triggers — Desktop/Antigravity Scheduled Tasks, OS cron, and
an on-demand pass — all call this same entry point; only the trigger differs).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import cycle as cycle_mod
from . import manifest as manifest_mod
from . import watchdog as watchdog_mod

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

    # The watchdog's `stop` rung is the runner's only hard gate and had no
    # operator surface: the ladder's own docstring said a stopped job waits for
    # "an operator to clear its watchdog state", which in practice meant
    # deleting a JSON file whose path nothing printed. `health-pass` sat parked
    # for six weeks that way. These two are the way in and the way out.
    health = sub.add_parser("health", help="what the watchdog has parked, and why")
    health.add_argument("--state-root", default=None)
    health.add_argument("--json", action="store_true")

    resume = sub.add_parser("resume", help="clear a job's watchdog stop and let it run again")
    resume.add_argument("job", help="the job name, as the manifest names it")
    resume.add_argument("--state-root", default=None)
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

    state_root = Path(ns.state_root) if ns.state_root else None

    if ns.cmd == "health":
        stopped = watchdog_mod.stopped_jobs(state_root=state_root)
        if ns.json:
            print(json.dumps([{"job": n, **r} for n, r in stopped], indent=2))
        elif not stopped:
            print("agentm-runner: nothing parked — every job is free to run.")
        else:
            print(f"agentm-runner: {len(stopped)} job(s) parked at the watchdog's "
                  f"stop rung and will not run until resumed:\n")
            for name, record in stopped:
                last = record.get("last_success")
                when = (datetime.fromtimestamp(last, timezone.utc)
                        .strftime("%Y-%m-%d") if last else "never")
                print(f"  {name} — {record.get('consecutive_failures', 0)} "
                      f"consecutive failures, last success {when}")
            print("\nResume one with: agentm-runner.sh resume <job>")
        # Parked jobs are a state to report, not an error to exit on: this is
        # the surface that says so, and a non-zero exit would make every caller
        # treat "something is parked" as "the runner is broken".
        return 0

    if ns.cmd == "resume":
        record = watchdog_mod.read_health(ns.job, state_root=state_root)
        rung = record.get("rung", "healthy")
        existed = watchdog_mod.clear(ns.job, state_root=state_root)
        if not existed:
            print(f"agentm-runner: {ns.job} had no watchdog record; it was already "
                  "free to run.")
        else:
            print(f"agentm-runner: {ns.job} cleared from `{rung}` "
                  f"({record.get('consecutive_failures', 0)} consecutive failures) "
                  "— it runs at its next due cycle.")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
