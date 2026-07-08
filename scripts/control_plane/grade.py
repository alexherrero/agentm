#!/usr/bin/env python3
"""grade.py — the launch-time authorization-grade statement + a fixture
proof of the recoverability doctrine's existing park-the-action-not-the-run
behavior (PLAN-autonomy-control-plane task 5).

**The grade statement.** An unattended run states its authorization grade
as one line at launch, stamped into the ledger's `run-start` event `grade`
tag — the schema already carries this field (`PLAN-observability-ledger`
task 2's `event_log.resolve_attribution_tags(grade=...)`). `G-ship`
(autonomous through merge-on-green) is the standing default; enforcement
machinery for further grades waits until a second grade is in live use
(per `agentm-autonomy.md`'s own simplification — this plan does not build
a grade ladder, only the one-line declaration).

**No new enforcement code.** The recoverability doctrine (`developer-
safety:recoverability` — a recoverable action proceeds announced, only a
genuinely unrecoverable one stops for confirmation, and the stop is a
*park of that one action*, never a run-kill) already exists as harness
convention. This task's job is to confirm that claim with an executable
fixture, not to implement a new gate — `run_actions_under_doctrine()`
below models the shape the doctrine already describes so a fleet loop has
something concrete to call, but it introduces no new stop condition beyond
what `is_recoverable` already decides.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
_CRICKETS_EVENT_LOG_REL = Path("src") / "tokens" / "scripts"

DEFAULT_GRADE = "G-ship"

_event_log_module = None
_event_log_loaded = False


def _candidate_event_log_dirs() -> list[Path]:
    candidates = []
    env_dir = os.environ.get("CRICKETS_SCRIPTS_DIR", "").strip()
    if env_dir:
        candidates.append(Path(os.path.expanduser(env_dir)))
    candidates.append(Path.home() / "Antigravity" / "crickets" / _CRICKETS_EVENT_LOG_REL)
    return candidates


def _find_event_log_dir() -> "Path | None":
    for candidate in _candidate_event_log_dirs():
        if (candidate / "event_log.py").is_file():
            return candidate
    return None


def load_event_log_module():
    """Return crickets' event_log module, loaded once and cached. None if
    crickets is unresolvable (graceful-return)."""
    global _event_log_module, _event_log_loaded
    if _event_log_loaded:
        return _event_log_module
    _event_log_loaded = True
    d = _find_event_log_dir()
    if d is None:
        _event_log_module = None
        return None
    spec = importlib.util.spec_from_file_location("crickets_event_log_bridge_grade", d / "event_log.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["crickets_event_log_bridge_grade"] = module
    spec.loader.exec_module(module)
    _event_log_module = module
    return module


def _reset_cache_for_tests() -> None:
    global _event_log_module, _event_log_loaded
    _event_log_module = None
    _event_log_loaded = False


def declare_run_start(
    plan: str, *, grade: str = DEFAULT_GRADE, session_id: str = "",
    root: "str | Path | None" = None, telemetry_root: "str | Path | None" = None,
) -> "dict | None":
    """Append one `run-start` event to the telemetry event log, its `grade`
    tag carrying the declared authorization grade. Returns the event
    record on success, `None` on any graceful-skip condition (crickets
    unresolvable, event-log append failed) — never raises, matching every
    other writer this arc has built.
    """
    event_log = load_event_log_module()
    if event_log is None:
        return None

    tags = event_log.resolve_attribution_tags(
        root=Path(root) if root is not None else None, grade=grade,
    )
    record = event_log.build_event("run-start", session_id=session_id, tags=tags)
    telemetry_root_path = Path(telemetry_root) if telemetry_root is not None else None
    if not event_log.append_event(record, telemetry_root=telemetry_root_path):
        return None
    return record


@dataclass(frozen=True)
class Action:
    name: str
    recoverable: bool


@dataclass
class ActionOutcome:
    name: str
    executed: bool
    parked: bool


@dataclass
class RunReport:
    grade: str
    outcomes: list = field(default_factory=list)

    @property
    def parked_actions(self) -> list:
        return [o for o in self.outcomes if o.parked]

    @property
    def executed_actions(self) -> list:
        return [o for o in self.outcomes if o.executed]


def run_actions_under_doctrine(actions: list, *, grade: str = DEFAULT_GRADE, executor=None) -> RunReport:
    """Run a sequence of `Action`s under the recoverability doctrine: a
    recoverable action executes (via `executor`, default a no-op); an
    unrecoverable one is parked (recorded, never executed) — and, the
    doctrine's own claim this task exists to confirm, **the run continues
    to the next action** rather than halting entirely. No grade currently
    changes this behavior (per the design's own simplification — the grade
    is a launch-time statement, not yet a live enforcement switch); `grade`
    is accepted and carried on the report for that reason, not consulted.
    """
    executor = executor if executor is not None else (lambda action: None)
    report = RunReport(grade=grade)
    for action in actions:
        if action.recoverable:
            executor(action)
            report.outcomes.append(ActionOutcome(name=action.name, executed=True, parked=False))
        else:
            report.outcomes.append(ActionOutcome(name=action.name, executed=False, parked=True))
    return report
