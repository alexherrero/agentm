"""The runner's watchdog / circuit-breaker (agentm-runner.md § Safety).

A finished run is not necessarily a successful one, so liveness (did the job
run at all, recently) is tracked separately from completion (did it exit 0).
A job that trips a threshold escalates through a throttle -> pause -> stop
ladder rather than being retried forever at full cadence.

State lives beside the per-job marker in the same local, non-synced
`state_root` (mirrors state.py's rule: never inside the synced vault).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_RUNGS = ("healthy", "throttle", "pause", "stop")
# Consecutive-failure thresholds that advance one rung. A success at any rung
# resets straight back to "healthy" — the ladder punishes a *streak*, not a
# lifetime failure count.
_RUNG_THRESHOLDS = {"throttle": 3, "pause": 5, "stop": 8}


def _watchdog_path(job_name: str, state_root: Optional[Path]) -> Path:
    from . import state as state_mod
    return state_mod._state_dir(state_root) / f"{job_name}.watchdog.json"


def read_health(job_name: str, *, state_root: Optional[Path] = None) -> dict:
    """`{"rung": "healthy", "consecutive_failures": 0, "last_success": None}`
    if the job has no recorded health history yet."""
    p = _watchdog_path(job_name, state_root)
    if not p.is_file():
        return {"rung": "healthy", "consecutive_failures": 0, "last_success": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"rung": "healthy", "consecutive_failures": 0, "last_success": None}


def _rung_for(consecutive_failures: int) -> str:
    rung = "healthy"
    for candidate, threshold in _RUNG_THRESHOLDS.items():
        if consecutive_failures >= threshold:
            rung = candidate
    return rung


def record_outcome(job_name: str, *, succeeded: bool, now: float,
                    state_root: Optional[Path] = None) -> dict:
    """Update and persist the job's health record after a real (non-dry-run)
    run; return the new record. A success resets the streak to "healthy"."""
    health = read_health(job_name, state_root=state_root)
    if succeeded:
        health = {"rung": "healthy", "consecutive_failures": 0, "last_success": now}
    else:
        failures = health.get("consecutive_failures", 0) + 1
        health = {
            "rung": _rung_for(failures),
            "consecutive_failures": failures,
            "last_success": health.get("last_success"),
        }
    p = _watchdog_path(job_name, state_root)
    p.write_text(json.dumps(health), encoding="utf-8")
    return health


def is_stopped(job_name: str, *, state_root: Optional[Path] = None) -> bool:
    """True once a job has tripped the "stop" rung — the runner's actual gate:
    a stopped job does not run again until an operator clears its watchdog
    state (a manual re-enable, not an automatic timeout: a repeatedly-broken
    job should not silently resume)."""
    return read_health(job_name, state_root=state_root).get("rung") == "stop"


def is_paused(job_name: str, *, state_root: Optional[Path] = None) -> bool:
    """True at the "pause" or "stop" rung — informational only (surfaced in
    the digest so the operator notices a degrading streak early). "throttle"
    and "pause" do not themselves stop the job from attempting to run again;
    a transient blip shouldn't need an operator to intervene. Only
    `is_stopped` gates execution."""
    rung = read_health(job_name, state_root=state_root).get("rung")
    return rung in ("pause", "stop")
