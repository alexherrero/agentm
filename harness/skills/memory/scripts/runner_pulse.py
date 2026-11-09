#!/usr/bin/env python3
"""Is the runner still cycling? (task 198, `#917`)

On 2026-10-23 `python3` moved to a version with no PyYAML and every runner
cycle exited at manifest parse for twelve hours. Nothing went red: the doctor's
`runner-cycle` row read `last-cycle.json` and reported what that cycle did,
never when it was. This module grades the when.

The rule. A cycle starts every `RUNNER_INTERVAL` seconds. The last one is fresh
within one interval, late (WARN) past one, and stopped (FAIL) past two. The
start that counts is the latest of three:

- `last-cycle.json`'s `at`, which is when the last finished cycle began;
- a job marker still in `start` that is newer than that: a cycle is running
  now, and a long nightly job is not a silent runner;
- the machine's last wake. launchd counts a `StartInterval` in awake time, so
  after a night asleep the next cycle is due up to one interval after waking,
  not hours ago. Measuring from the wake keeps a laptop's first minutes from
  reading red. Where the wake cannot be read, wall time is used.

When the grade is not OK, the runner's own log says why if it ends in a
startup error: the last line of a traceback, or a missing module.

`agentmd status` grades the same file on the same rule in Go
(`daemon/internal/health`); the two read the same constants and are pinned to
the same cases.

Read-only. It runs nothing and writes nothing.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# The runner's launchd `StartInterval` (com.agentm.runner.plist).
RUNNER_INTERVAL = 1800

# How much of the runner's log to read for a startup error. The log grows by
# a cycle summary every half hour; the error, when there is one, is the tail.
LOG_TAIL_BYTES = 8192

# A traceback's last line (`runner.manifest.ManifestError: …`), or a missing
# module said any other way.
_STARTUP_ERROR = re.compile(
    r"^(?:[A-Za-z_][\w.]*(?:Error|Exception)\b.*|.*No module named .*|.*not available.*)$")


@dataclass
class Pulse:
    level: str           # OK | WARN | FAIL | UNVERIFIED
    detail: str
    at: Optional[float] = None      # when the last cycle began


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600}h"


def last_wake() -> Optional[float]:
    """When the machine last woke from sleep, from `kern.waketime`; None where
    there is no such reading (not macOS, or never slept since boot)."""
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["sysctl", "-n", "kern.waketime"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"sec\s*=\s*(\d+)", out)
    sec = int(m.group(1)) if m else 0
    return float(sec) if sec > 0 else None


def running_since(state_dir: Path, after: float) -> Optional[tuple]:
    """(job, started_at) for the newest job marker still in `start` that began
    after `after`: a cycle in progress. None when there is none."""
    best = None
    for p in Path(state_dir).glob("*.json"):
        if p.name == "last-cycle.json" or p.name.endswith(".watchdog.json"):
            continue
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(m, dict) or m.get("status") != "start":
            continue
        started = m.get("started_at")
        if isinstance(started, (int, float)) and started > after and (best is None or started > best[1]):
            best = (p.stem, float(started))
    return best


def startup_error(log: Path) -> Optional[str]:
    """The runner log's last line, when it is a startup error; else None."""
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - LOG_TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1]
    return last[:300] if _STARTUP_ERROR.match(last) else None


def default_log(state_dir: Path) -> Path:
    """`runner-launchd.log`, beside the runner's state in the cache root's
    `telemetry/` (the plist's StandardOutPath)."""
    return Path(state_dir).parent / "telemetry" / "runner-launchd.log"


def grade(state_dir: Path, *, now: Optional[float] = None, wake: Optional[float] = None,
          log: Optional[Path] = None, read_wake: bool = True) -> Pulse:
    """The runner's pulse, by the rule in the module docstring."""
    now = time.time() if now is None else now
    if wake is None and read_wake:
        wake = last_wake()
    p = Path(state_dir) / "last-cycle.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Pulse("UNVERIFIED", f"no cycle recorded yet at {p}")
    except (OSError, json.JSONDecodeError) as e:
        return Pulse("FAIL", f"{p}: unreadable ({e})")
    at = data.get("at") if isinstance(data, dict) else None
    if not isinstance(at, (int, float)):
        return Pulse("FAIL", f"{p}: no `at` recorded")
    began, how = float(at), "the last cycle began"
    running = running_since(state_dir, began)
    if running:
        began, how = running[1], f"a cycle is running `{running[0]}`, started"
    age = now - began
    since_wake = now - wake if wake is not None and began < wake <= now else None
    graded = min(age, since_wake) if since_wake is not None else age
    detail = f"{how} {_age(age)} ago"
    if since_wake is not None:
        detail += f" (the machine woke {_age(since_wake)} ago)"
    if graded <= RUNNER_INTERVAL:
        return Pulse("OK", detail, at=began)
    level = "FAIL" if graded > 2 * RUNNER_INTERVAL else "WARN"
    detail += f" — the runner starts one every {_age(RUNNER_INTERVAL)}"
    err = startup_error(log if log is not None else default_log(state_dir))
    if err:
        detail += f"; its log ends in a startup error: {err}"
    return Pulse(level, detail, at=began)
