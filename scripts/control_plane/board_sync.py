#!/usr/bin/env python3
"""board_sync.py — reflects control-plane fleet-dispatch state changes onto
the GitHub Project board (PLAN-autonomy-control-plane task 3, "Planner
(TPM) as board brain").

**Adopts** the existing `github-projects` board-sync path — the same
`project_sync.py post --type task-progress` call `/work` step 10 and
`/release` step 7/8 already make — it does not redesign the Planner
persona or build a general persona-activation dispatcher. That dispatcher
(reading a persona's `modes:`/`triggers:` frontmatter and branching actual
behavior) is a separate, larger, still-unbuilt gap crickets'
`planner_maintain.py` docstring names explicitly as out of scope for its
own task; it stays out of scope here too. What this module adds is new: a
board-progress call site for fleet-dispatched work specifically, which
(unlike a `/work` task) has no `.harness/PLAN.md` checkbox of its own to
hang a board update on.

Graceful-skip everywhere github-projects' own entrypoints already are:
missing `project.json`, missing `gh`, or an unresolvable `project_sync.py`
sibling all degrade to a silent no-op, never an error.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_CRICKETS_PROJECT_SYNC_REL = Path("src") / "github-projects" / "scripts"


def _candidate_project_sync_dirs() -> list[Path]:
    candidates = []
    env_dir = os.environ.get("CRICKETS_SCRIPTS_DIR", "").strip()
    if env_dir:
        candidates.append(Path(os.path.expanduser(env_dir)).parent / "github-projects" / "scripts")
    candidates.append(Path.home() / "Antigravity" / "crickets" / _CRICKETS_PROJECT_SYNC_REL)
    return candidates


def find_project_sync_script() -> "Path | None":
    for candidate in _candidate_project_sync_dirs():
        p = candidate / "project_sync.py"
        if p.is_file():
            return p
    return None


def board_sync_available(*, config_path: "str | Path", gh_bin: str = "gh") -> bool:
    """True iff `project.json` exists, `gh` is on PATH, and crickets'
    `project_sync.py` is resolvable — the three preconditions every
    github-projects entrypoint already gates on."""
    if not Path(config_path).is_file():
        return False
    if shutil.which(gh_bin) is None:
        return False
    return find_project_sync_script() is not None


def post_dispatch_progress(
    name: str, *, summary: str, config_path: "str | Path",
    commit: "str | None" = None, dry_run: bool = False,
    gh_bin: str = "gh", runner=subprocess.run,
) -> dict:
    """Post a `task-progress` board update for a dispatched fleet work item
    (`name`, the same `<plan>-<task>` identifier `dispatch.dispatch_name()`
    produces). Returns `{"posted": bool, "skipped_reason": str | None,
    "returncode": int | None, "stdout": str, "stderr": str}` — never
    raises; a board-sync failure must never abort a fleet run.
    """
    if not Path(config_path).is_file():
        return {"posted": False, "skipped_reason": "no project.json", "returncode": None, "stdout": "", "stderr": ""}
    if shutil.which(gh_bin) is None:
        return {"posted": False, "skipped_reason": "gh unavailable", "returncode": None, "stdout": "", "stderr": ""}
    script = find_project_sync_script()
    if script is None:
        return {"posted": False, "skipped_reason": "project_sync.py unresolvable", "returncode": None, "stdout": "", "stderr": ""}

    cmd = [
        sys.executable, str(script), "post",
        "--config", str(config_path), "--type", "task-progress",
        "--id", name, "--summary", summary,
    ]
    if commit:
        cmd += ["--commit", commit]
    if dry_run:
        cmd.append("--dry-run")

    try:
        proc = runner(cmd, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as e:
        return {"posted": False, "skipped_reason": f"exec failed: {e}", "returncode": None, "stdout": "", "stderr": ""}

    return {
        "posted": proc.returncode == 0, "skipped_reason": None if proc.returncode == 0 else "non-zero exit",
        "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr,
    }


def post_fleet_run_summary(results: list, *, config_path: "str | Path", dry_run: bool = False,
                            gh_bin: str = "gh", runner=subprocess.run) -> list[dict]:
    """Post one task-progress update per dispatched item in `results`
    (a list of objects/dicts each carrying `name` + a status string).
    Returns the list of individual `post_dispatch_progress()` outcomes —
    a fleet run's board reflects every dispatched item's state change
    without further manual intervention, per this task's own verification.
    """
    outcomes = []
    for r in results:
        name = r["name"] if isinstance(r, dict) else r.name
        status = r.get("status", "dispatched") if isinstance(r, dict) else getattr(r, "status", "dispatched")
        outcomes.append(post_dispatch_progress(
            name, summary=f"fleet dispatch: {status}", config_path=config_path,
            dry_run=dry_run, gh_bin=gh_bin, runner=runner,
        ))
    return outcomes
