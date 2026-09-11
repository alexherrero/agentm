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
from datetime import datetime, timedelta
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
    # The manifests the loader refused this cycle, as {"file", "reason"} —
    # every job that did load still ran (filing-v2 remainders task 1).
    refused: list = field(default_factory=list)
    loaded: int = 0


def report_summary(report: "CycleReport", *, now: Optional[float] = None) -> dict:
    """The cycle as one JSON object: printed by the CLI and left at
    `state.cycle_summary_path()` for the session brief and the doctor."""
    return {
        "at": now if now is not None else time.time(),
        "loaded": report.loaded,
        "refused": list(report.refused),
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


def _write_cycle_summary(state_root: Optional[Path], report: "CycleReport", now: float) -> None:
    p = state_mod.cycle_summary_path(state_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report_summary(report, now=now), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


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


_SPEND_WINDOW_SECONDS = 86400


def _spend_so_far(state_root: Optional[Path], now: Optional[float] = None) -> float:
    """Sum of the last-recorded cost of every job that ran in the past day —
    a coarse fleet-spend proxy for a daily ceiling (each marker holds only its
    own last run; good enough for a hard stop-loss, not a precise ledger).

    Only the past day, because the ceiling is a daily one and a cost that
    never ages out is a deadlock rather than a ceiling. Until a job reported a
    real cost this never mattered; the nightly enrichment batch reports one
    (agentm-vault plan 04), and summed forever, one heavy night would have
    held every job past the ceiling for good — the batch included, which
    could then never run again to replace its own number.
    """
    now = now if now is not None else time.time()
    d = state_mod._state_dir(state_root)
    total = 0.0
    for p in d.glob("*.json"):
        marker = state_mod.read_marker(p.stem, state_root=state_root)
        last = state_mod.last_run_epoch(marker)
        if last is None or now - last > _SPEND_WINDOW_SECONDS:
            continue
        total += state_mod.last_cost_usd(marker)
    return total


def _spends(job: manifest_mod.JobManifest) -> bool:
    """Whether a job spends model tokens: it says so with a `budget:`.

    The fleet ceiling gates only these. A job with no budget makes no model
    call — every such manifest says as much in its own comments — and holding
    the hourly sweep or a shepherd back because the enrichment batch spent
    would save nothing and stop the machine's upkeep for a day.
    """
    return job.budget_tokens is not None


def in_window(window: tuple[int, int], now: float) -> bool:
    """Whether `now` falls inside a (start, end) window of local minutes.

    Half-open: a job may start at 02:00 and may not start at 06:00. A window
    whose end is before its start wraps midnight.
    """
    t = datetime.fromtimestamp(now)
    minute = t.hour * 60 + t.minute
    start, end = window
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def window_opening_at_or_before(window: tuple[int, int], at: float) -> float:
    """The epoch of the most recent opening of `window` at or before `at`.

    Calendar arithmetic rather than `- 86400`, so a daylight-saving night lands
    on the right wall-clock minute.
    """
    t = datetime.fromtimestamp(at)
    opening = t.replace(hour=window[0] // 60, minute=window[0] % 60, second=0, microsecond=0)
    if opening > t:
        opening -= timedelta(days=1)
    return opening.timestamp()


def _next_due(job: manifest_mod.JobManifest, last_run: float) -> float:
    """When a job is next due, with its window taken into account.

    Without a window it is `last_run + interval`, as it always was. With one,
    that moment is moved to where the job may actually start:

    - If it falls outside the window, the job is due at the window's next
      opening. A daily job last run at 13:07 is due at 02:00, and the hours it
      waited are not lateness — the lookback counts from 02:00, so a job does
      not skip its first night for having waited for it.
    - If it falls inside, a job with an interval of a day or more is due at that
      window's opening rather than at the minute it last happened to start.
      Otherwise a night whose first step ran long would push every later night's
      start later, until two nightly jobs no longer met in one cycle — and their
      order is the point. A sub-day interval keeps its own clock: snapped to the
      opening, an hourly job would be due again on every tick of the window.
    """
    raw = last_run + job.interval_seconds
    window = job.window_minutes
    if window is None:
        return raw
    if not in_window(window, raw):
        return _next_opening(window, raw)
    if job.interval_seconds < 86400:
        return raw
    return window_opening_at_or_before(window, raw)


def _next_opening(window: tuple[int, int], at: float) -> float:
    """The epoch of the first opening of `window` strictly after `at`."""
    t = datetime.fromtimestamp(at)
    opening = t.replace(hour=window[0] // 60, minute=window[0] % 60, second=0, microsecond=0)
    if opening <= t:
        opening += timedelta(days=1)
    return opening.timestamp()


def is_due(job: manifest_mod.JobManifest, *, now: float, state_root: Optional[Path] = None):
    """(due: bool, reason: str). reason in {"never-run", "orphaned-start",
    "due", "not-due", "missed-beyond-lookback", "outside-window <window>"}.

    The window is checked first. A job waiting for its window is not late, so
    nothing here touches its marker while it waits — the lookback re-anchor
    below must never mistake "not yet 02:00" for "missed".
    """
    window = job.window_minutes
    if window is not None and not in_window(window, now):
        return False, f"outside-window {job.window}"
    marker = state_mod.read_marker(job.name, state_root=state_root)
    if state_mod.is_orphaned_start(marker):
        return True, "orphaned-start"
    last_run = state_mod.last_run_epoch(marker)
    if last_run is None:
        return True, "never-run"
    next_due = _next_due(job, last_run)
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
    strict: bool = False,
) -> CycleReport:
    """One idempotent cycle: read manifests, run what's due, advance state,
    return a report. A single job's failure never aborts the cycle — its
    exit code is captured in its own outcome, not propagated. Neither does a
    manifest the loader refuses: it lands on the report's `refused` list and
    the jobs that loaded still run (`strict=True` restores the old all-or-
    nothing load, raising on the first bad file)."""
    now = now if now is not None else time.time()
    if strict:
        jobs = manifest_mod.load_manifests(jobs_dir)
        refused: list = []
    else:
        jobs, refusals = manifest_mod.load_manifests_lenient(jobs_dir)
        refused = [{"file": r.path.name, "reason": r.reason} for r in refusals]
    # ceiling is never None (fail-CLOSED default) -- spend is always tracked.
    ceiling = _read_daily_ceiling(harness_dir)
    spend = _spend_so_far(state_root, now)

    report = CycleReport(refused=refused, loaded=len(jobs))
    # `order` first, name second: the loader's filename order is what a job
    # without an `order` keeps, and the night's steps run in the order they
    # declare.
    for job in sorted(jobs, key=lambda j: (j.order, j.name)):
        if not job.enabled:
            # Registered and off. Reported by name rather than dropped at load
            # time, because a job nobody can see in the cycle report is a job
            # nobody remembers to turn on.
            report.outcomes.append(JobOutcome(name=job.name, ran=False, skipped_reason="disabled"))
            continue
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
        if spend >= ceiling and not job.dry_run and _spends(job):
            # Pre-flight check the fleet ceiling before a real (non-dry-run)
            # run of a spending job starts — an over-budget run never starts
            # (throttle rung). A job that spends nothing is never over budget.
            report.budget_ceiling_hit = True
            report.outcomes.append(JobOutcome(name=job.name, ran=False, skipped_reason="budget-ceiling"))
            continue
        outcome = _run_one(job, now=now, state_root=state_root, report_path=report_path)
        spend += outcome.cost_usd
        report.outcomes.append(outcome)
    _write_cycle_summary(state_root, report, now)
    return report
