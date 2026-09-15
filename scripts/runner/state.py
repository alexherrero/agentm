"""Local, non-synced runner state: per-job start/done markers.

Lives at `~/.cache/agentm/runner/` by default — never inside the synced
vault (mirrors `vault_lock.py`'s lockdir rule: the runner's own bookkeeping
is not vault content). The default honours `XDG_CACHE_HOME`, read on every
call the way `vault_lock._default_lock_root` reads it, so the battery's
per-test cache root moves these markers with the locks; `state_root` is
injectable so a test can name a directory of its own. Until 2026-09-14 the
default was fixed at import from the home directory alone and every reader
made the directory, so a suite that asked after a marker left
`~/.cache/agentm/runner` under whatever home it ran in — the directory the
live runner keeps its records in. Only a write makes it now.

The marker is also the crash-recovery signal: a `mark_start` with no
matching `mark_done` on the next cycle is an orphaned run (the idle-hook
orphan-marker pattern) — `is_orphaned_start` surfaces it so the cycle can
retry rather than treat the job as merely "recently run."
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional


def default_state_root() -> Path:
    """`~/.cache/agentm/runner`, honouring `XDG_CACHE_HOME` when set.

    Resolved on every call, never cached at import: the battery points
    `XDG_CACHE_HOME` at a fresh directory before each test, and a root fixed
    when the module loaded would sit under the real home for the whole run.
    With the variable unset the path is the one the live runner has always
    written."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "agentm" / "runner"


def _state_dir(state_root: Optional[Path]) -> Path:
    """The directory the markers live in — resolved, not made. A reader asking
    after a job that never ran leaves nothing behind; `_write` makes the
    directory with the first marker."""
    return Path(state_root) if state_root is not None else default_state_root()


def _marker_path(job_name: str, state_root: Optional[Path]) -> Path:
    return _state_dir(state_root) / f"{job_name}.json"


def _write(p: Path, payload: dict) -> None:
    """Every marker is written here, and the directory is made here and
    nowhere else."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")


def cycle_summary_path(state_root: Optional[Path] = None) -> Path:
    """Where the runner leaves its last cycle's own account — what loaded,
    what it refused, what ran. The session brief and the doctor read it."""
    return _state_dir(state_root) / "last-cycle.json"


def read_marker(job_name: str, *, state_root: Optional[Path] = None) -> dict:
    """`{}` if the job has never run; else the last-written marker dict."""
    p = _marker_path(job_name, state_root)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def mark_start(job_name: str, *, now: Optional[float] = None, state_root: Optional[Path] = None) -> None:
    _write(_marker_path(job_name, state_root),
           {"status": "start", "started_at": now if now is not None else time.time()})


def mark_done(
    job_name: str,
    *,
    now: Optional[float] = None,
    cost_usd: float = 0.0,
    state_root: Optional[Path] = None,
) -> None:
    ts = now if now is not None else time.time()
    _write(_marker_path(job_name, state_root), {
        "status": "done",
        "last_run": ts,
        "last_cost_usd": cost_usd,
        "last_real_run": ts,
    })


def mark_missed(job_name: str, *, now: Optional[float] = None, state_root: Optional[Path] = None) -> None:
    """Re-anchors a job's due-clock exactly like `mark_done` -- `cycle.is_due`'s
    missed-beyond-lookback branch calls this instead, after the job's command
    was never actually invoked (2026-07-17 finding: a launchd-triggered runner
    that goes dark for days produces a marker byte-identical to a real
    success, because both write "status": "done" with no other distinguishing
    field). Preserves the prior marker's `last_real_run` -- a re-anchor never
    advances it, so a reader can always tell how long it's actually been
    since the job's command last ran, no matter how many silent re-anchors
    have happened since.

    A marker written before this field existed has no `last_real_run` key
    at all -- `prior.get("last_real_run", prior.get("last_run"))` falls
    back to its `last_run` in that case (the best-available reading, and no
    less precise than that marker already was pre-fix), rather than
    `.get("last_real_run")` alone, which would read as `None` (a key that's
    *present* with value `None`, meaning "genuinely never run", and a key
    that's *absent*, meaning "written before this existed", must not be
    conflated -- only the latter falls back)."""
    prior = read_marker(job_name, state_root=state_root)
    _write(_marker_path(job_name, state_root), {
        "status": "done",
        "last_run": now if now is not None else time.time(),
        "last_cost_usd": 0.0,
        "last_real_run": prior.get("last_real_run", prior.get("last_run")),
        "missed": True,
    })


def is_orphaned_start(marker: dict) -> bool:
    """True if the marker's last write was a "start" that never reached "done"."""
    return marker.get("status") == "start"


def last_run_epoch(marker: dict) -> Optional[float]:
    return marker.get("last_run") if marker.get("status") == "done" else None


def last_cost_usd(marker: dict) -> float:
    return float(marker.get("last_cost_usd", 0.0) or 0.0) if marker.get("status") == "done" else 0.0


def last_real_run_epoch(marker: dict) -> Optional[float]:
    """The timestamp of the job's last genuine command execution -- distinct
    from `last_run_epoch`, which also advances on a `mark_missed` re-anchor
    that never actually ran the job. `None` if the job has never really run
    (including a job that has only ever been re-anchored)."""
    if marker.get("status") != "done":
        return None
    val = marker.get("last_real_run")
    return float(val) if val is not None else None


def was_last_advance_a_miss(marker: dict) -> bool:
    """True if the marker's most recent due-clock advance was a `mark_missed`
    re-anchor rather than a real completion -- the honesty flag `/console`
    and the SessionStart briefing read to surface a silently-stalled job."""
    return marker.get("status") == "done" and bool(marker.get("missed"))
