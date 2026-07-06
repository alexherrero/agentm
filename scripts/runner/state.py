"""Local, non-synced runner state: per-job start/done markers.

Lives at `~/.cache/agentm/runner/` by default — never inside the synced
vault (mirrors `vault_lock.py`'s lockdir rule: the runner's own bookkeeping
is not vault content). `state_root` is injectable so tests never touch the
real cache dir.

The marker is also the crash-recovery signal: a `mark_start` with no
matching `mark_done` on the next cycle is an orphaned run (the idle-hook
orphan-marker pattern) — `is_orphaned_start` surfaces it so the cycle can
retry rather than treat the job as merely "recently run."
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

_DEFAULT_STATE_ROOT = Path.home() / ".cache" / "agentm" / "runner"


def _state_dir(state_root: Optional[Path]) -> Path:
    d = Path(state_root) if state_root is not None else _DEFAULT_STATE_ROOT
    d.mkdir(parents=True, exist_ok=True)
    return d


def _marker_path(job_name: str, state_root: Optional[Path]) -> Path:
    return _state_dir(state_root) / f"{job_name}.json"


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
    p = _marker_path(job_name, state_root)
    p.write_text(
        json.dumps({"status": "start", "started_at": now if now is not None else time.time()}),
        encoding="utf-8",
    )


def mark_done(
    job_name: str,
    *,
    now: Optional[float] = None,
    cost_usd: float = 0.0,
    state_root: Optional[Path] = None,
) -> None:
    p = _marker_path(job_name, state_root)
    p.write_text(
        json.dumps({
            "status": "done",
            "last_run": now if now is not None else time.time(),
            "last_cost_usd": cost_usd,
        }),
        encoding="utf-8",
    )


def is_orphaned_start(marker: dict) -> bool:
    """True if the marker's last write was a "start" that never reached "done"."""
    return marker.get("status") == "start"


def last_run_epoch(marker: dict) -> Optional[float]:
    return marker.get("last_run") if marker.get("status") == "done" else None


def last_cost_usd(marker: dict) -> float:
    return float(marker.get("last_cost_usd", 0.0) or 0.0) if marker.get("status") == "done" else 0.0
