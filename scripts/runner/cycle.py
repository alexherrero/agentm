"""The runner's one-cycle loop (agentm-runner.md): read manifests, decide
what's due, run it within budget, write a report, advance state, exit.

Idempotent and crash-safe by construction: a cycle holds no state of its own
beyond the per-job marker in `state.py` — a crashed cycle simply re-runs
whole on the next invocation, and a job whose `mark_start` never reached
`mark_done` (`state.is_orphaned_start`) is retried rather than skipped.

Import note: `vault_lock` is a flat sibling of the `runner` package one
directory up (`scripts/vault_lock.py`, not `scripts/runner/vault_lock.py`).
This resolves when `scripts/` is on `sys.path` — true under both this
repo's test convention (`cd scripts && python -m unittest discover`) and the
`agentm-runner.sh` entry point (`cd scripts && python3 -m runner.cli`).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import manifest as manifest_mod
from . import state as state_mod
from . import watchdog as watchdog_mod

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

try:
    from vault_lock import atomic_write
except ImportError:  # pragma: no cover - exercised only if scripts/ isn't on sys.path
    atomic_write = None


@dataclass
class JobOutcome:
    name: str
    ran: bool
    dry_run: bool = False
    skipped_reason: Optional[str] = None
    exit_code: Optional[int] = None
    cost_usd: float = 0.0


@dataclass
class CycleReport:
    outcomes: list = field(default_factory=list)
    budget_ceiling_hit: bool = False


# Fail-CLOSED default (ROADMAP-TAIL-ADJUDICATIONS.md B3; AA4 2026-07-08
# finding + fix): a stranger's clone ships no `.harness/budget.yaml`, and
# `_read_daily_ceiling` must never let that absence mean "no ceiling at
# all" -- the runner design's own token contract ("A hard budget ceiling",
# wiki/designs/agentm-runner.md) already assumes one always applies. This
# is a conservative single-operator daily-USD cap, deliberately low; an
# operator who wants a different number writes `budget.yaml` and it
# overrides this default exactly as it always has.
_DEFAULT_DAILY_USD_CEILING = 5.0


def _read_daily_ceiling(harness_dir: Optional[Path]) -> float:
    """`.harness/budget.yaml`'s `daily_usd_ceiling`, or
    `_DEFAULT_DAILY_USD_CEILING` if unconfigured/missing/unparseable --
    never `None` (a `None` ceiling used to mean "skip the gate entirely",
    the fail-open bug this default closes)."""
    if harness_dir is None or yaml is None:
        return _DEFAULT_DAILY_USD_CEILING
    p = Path(harness_dir) / "budget.yaml"
    if not p.is_file():
        return _DEFAULT_DAILY_USD_CEILING
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return _DEFAULT_DAILY_USD_CEILING
    daily = data.get("daily_usd_ceiling") if isinstance(data, dict) else None
    return float(daily) if daily is not None else _DEFAULT_DAILY_USD_CEILING


def _spend_so_far(state_root: Optional[Path]) -> float:
    """Sum of every job's last-recorded cost — a coarse fleet-spend proxy
    (each marker holds only its own last run, not a rolling window; good
    enough for a hard stop-loss, not a precise daily ledger)."""
    d = state_mod._state_dir(state_root)
    total = 0.0
    for p in d.glob("*.json"):
        marker = state_mod.read_marker(p.stem, state_root=state_root)
        total += state_mod.last_cost_usd(marker)
    return total


def is_due(job: manifest_mod.JobManifest, *, now: float, state_root: Optional[Path] = None):
    """(due: bool, reason: str). reason in {"never-run", "orphaned-start",
    "due", "not-due", "missed-beyond-lookback"}."""
    marker = state_mod.read_marker(job.name, state_root=state_root)
    if state_mod.is_orphaned_start(marker):
        return True, "orphaned-start"
    last_run = state_mod.last_run_epoch(marker)
    if last_run is None:
        return True, "never-run"
    next_due = last_run + job.interval_seconds
    if now < next_due:
        return False, "not-due"
    overdue_by = now - next_due
    if overdue_by <= job.lookback_seconds:
        return True, "due"
    # Beyond lookback: the design bounds catch-up to the lookback window: a
    # miss older than that is not caught up. Re-anchor the schedule at `now`
    # (implementation call, not a locked design rule) so the job doesn't read
    # as perpetually overdue on every subsequent cycle — it becomes due again
    # after one more full interval from here, exactly as if it had just run.
    # `mark_missed` (not `mark_done`): the job's command never actually ran,
    # so the marker must stay distinguishable from a real completion.
    state_mod.mark_missed(job.name, now=now, state_root=state_root)
    return False, "missed-beyond-lookback"


def _parse_reported_cost(stdout: str) -> float:
    """A job may report its own spend as the last stdout line, a JSON object
    with `total_cost_usd` — the same field name Claude Code's `-p` mode
    reports. Absent or unparseable, cost is 0.0 (nothing to report yet, since
    no consumer job exists today)."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return 0.0
        if isinstance(data, dict) and "total_cost_usd" in data:
            try:
                return float(data["total_cost_usd"])
            except (TypeError, ValueError):
                return 0.0
        return 0.0
    return 0.0


def _emit_report(report_path: Optional[Path], job: manifest_mod.JobManifest,
                  outcome: JobOutcome, *, rendered_command: Optional[str] = None) -> None:
    """Append one JSONL line — the digest (reporting capability) is a
    forward reference, not built yet; this is the interim report surface a
    future digest can consume wholesale rather than re-deriving."""
    if report_path is None:
        return
    record = {
        "job": job.name,
        "tier": job.tier,
        "ran": outcome.ran,
        "dry_run": outcome.dry_run,
        "exit_code": outcome.exit_code,
        "cost_usd": outcome.cost_usd,
        "rendered_command": rendered_command,
        "ts": time.time(),
    }
    line = json.dumps(record) + "\n"
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if job.tier == "T2" and atomic_write is not None:
        # T2 (curated) reports route through the V5-0 write floor, same as
        # any other curated write — the runner is the third writer, not a
        # fourth coordination mechanism.
        existing = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        atomic_write(str(report_path), existing + line)
    else:
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(line)


# A launchd-invoked process gets no LANG/LC_ALL (the plist sets only PATH),
# so CPython's PEP 538 startup coercion writes LC_CTYPE=C.UTF-8 into its own
# os.environ, which subprocess.run() then passes straight to the child
# shell. macOS's system bash (3.2.57, the stock GPLv2-era build Apple still
# ships) mis-tokenizes a `$var` immediately followed by multibyte
# punctuation under ANY UTF-8-aware locale -- confirmed live 2026-07-15/16
# against the real failure shape (`local label="$1"` inside a function,
# under `set -u`, exactly run-fast-tier.sh:31's own pattern): both the
# PEP-538-coerced C.UTF-8 *and* an explicit en_US.UTF-8 reproduce the same
# "unbound variable" mis-tokenization, they just differ in whether Python's
# own decode of the corrupted stderr bytes happens to raise (C.UTF-8: often
# does, producing the opaque exit_code=-1 this module used to swallow) --
# en_US.UTF-8 alone does NOT fix the underlying job failure, it only changes
# which bytes get corrupted. `LC_ALL=C` is the one setting confirmed NOT to
# trigger the mis-tokenization at all (byte-oriented, no multibyte ctype
# classification for bash to get wrong) -- verified against the exact
# run-fast-tier.sh:31 shape live. `encoding="utf-8"` on subprocess.run()
# still decodes whatever bytes the child actually produces correctly; C
# only changes how *bash* classifies bytes for tokenization, not what
# encoding the job's own real output (health_score.py's UTF-8 markdown,
# emoji included) is written in.
def _child_env() -> dict:
    return dict(os.environ, LANG="C", LC_ALL="C")


def _run_one(job: manifest_mod.JobManifest, *, now: float, state_root: Optional[Path],
             report_path: Optional[Path]) -> JobOutcome:
    if job.tier not in manifest_mod.VALID_TIERS:  # structurally unreachable post-load-validation
        raise manifest_mod.ManifestError(f"{job.name}: tier {job.tier!r} is not job-writable")

    if job.dry_run:
        # A new (or not-yet-promoted) job renders what it would do and writes
        # nothing else — the operator promotes it by flipping `dry_run: false`
        # in the manifest.
        outcome = JobOutcome(name=job.name, ran=False, dry_run=True)
        _emit_report(report_path, job, outcome, rendered_command=job.command)
        return outcome

    state_mod.mark_start(job.name, now=now, state_root=state_root)
    try:
        proc = subprocess.run(
            job.command, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=_child_env(),
        )
        exit_code = proc.returncode
        cost = _parse_reported_cost(proc.stdout)
    except Exception:
        exit_code = -1
        cost = 0.0
    state_mod.mark_done(job.name, now=now, cost_usd=cost, state_root=state_root)
    watchdog_mod.record_outcome(job.name, succeeded=(exit_code == 0), now=now, state_root=state_root)
    outcome = JobOutcome(name=job.name, ran=True, exit_code=exit_code, cost_usd=cost)
    _emit_report(report_path, job, outcome)
    return outcome


def run_cycle(
    jobs_dir: Path,
    *,
    now: Optional[float] = None,
    state_root: Optional[Path] = None,
    report_path: Optional[Path] = None,
    harness_dir: Optional[Path] = None,
) -> CycleReport:
    """One idempotent cycle: read manifests, run what's due, advance state,
    return a report. A single job's failure never aborts the cycle — its
    exit code is captured in its own outcome, not propagated."""
    now = now if now is not None else time.time()
    jobs = manifest_mod.load_manifests(jobs_dir)
    # ceiling is never None (fail-CLOSED default) -- spend is always tracked.
    ceiling = _read_daily_ceiling(harness_dir)
    spend = _spend_so_far(state_root)

    report = CycleReport()
    for job in jobs:
        if not job.dry_run and watchdog_mod.is_stopped(job.name, state_root=state_root):
            # The throttle->pause->stop ladder: "throttle" and "pause" are
            # visible-in-the-report warning rungs that still let the job keep
            # attempting to run (a transient blip shouldn't need an operator);
            # only "stop" actually halts it, and only an operator clearing the
            # watchdog state resumes it. Checked BEFORE is_due — a stopped job
            # must never reach the lookback reanchor (below), which would
            # otherwise mistake "held by the watchdog" for "missed" and
            # repeatedly reanchor its schedule instead of staying halted.
            report.outcomes.append(JobOutcome(name=job.name, ran=False, skipped_reason="watchdog-stop"))
            continue
        due, reason = is_due(job, now=now, state_root=state_root)
        if not due:
            report.outcomes.append(JobOutcome(name=job.name, ran=False, skipped_reason=reason))
            continue
        if spend >= ceiling and not job.dry_run:
            # Pre-flight check the fleet ceiling before a real (non-dry-run)
            # run starts — an over-budget run never starts (throttle rung).
            report.budget_ceiling_hit = True
            report.outcomes.append(JobOutcome(name=job.name, ran=False, skipped_reason="budget-ceiling"))
            continue
        outcome = _run_one(job, now=now, state_root=state_root, report_path=report_path)
        spend += outcome.cost_usd
        report.outcomes.append(outcome)
    return report
