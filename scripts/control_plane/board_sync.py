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

Also sets `item.track` in `board-items.json` ahead of a fleet-dispatched
item's progress post (PLAN-board-tracking-model task 2, the decided
tracking model: repurpose the board's `Track` field for dispatch tier,
since no by-agent-identity concept exists anywhere in the dispatch
substrate to track by instead). `project_sync.py post` already syncs
`item.track` to the real board field automatically via its own
`sync_fields()` call — this module only needs to write the value into
`board-items.json` before invoking `post`, never to duplicate the sync
logic itself.

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

_project_model_module = None
_project_model_loaded = False


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


def find_project_model_script() -> "Path | None":
    for candidate in _candidate_project_sync_dirs():
        p = candidate / "project_model.py"
        if p.is_file():
            return p
    return None


def load_project_model_module():
    """Return crickets' project_model module, loaded once and cached. None
    if crickets is unresolvable (graceful-return; `set_item_tier()` treats
    that as a no-op, matching this module's own contract)."""
    global _project_model_module, _project_model_loaded
    if _project_model_loaded:
        return _project_model_module
    _project_model_loaded = True
    script = find_project_model_script()
    if script is None:
        _project_model_module = None
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("crickets_project_model_bridge", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["crickets_project_model_bridge"] = module
    spec.loader.exec_module(module)
    _project_model_module = module
    return module


def _reset_cache_for_tests() -> None:
    global _project_model_module, _project_model_loaded
    _project_model_module = None
    _project_model_loaded = False


def board_sync_available(*, config_path: "str | Path", gh_bin: str = "gh") -> bool:
    """True iff `project.json` exists, `gh` is on PATH, and crickets'
    `project_sync.py` is resolvable — the three preconditions every
    github-projects entrypoint already gates on."""
    if not Path(config_path).is_file():
        return False
    if shutil.which(gh_bin) is None:
        return False
    return find_project_sync_script() is not None


def _resolve_items_path(config_path: "str | Path") -> "Path | None":
    """Mirror `project_sync.py`'s own `_items_path_from_cfg()`: an explicit
    `items_source` in the config, else a sibling `board-items.json` next
    to it. Returns None on any read/parse failure — never raises."""
    config_path = Path(config_path)
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    src = cfg.get("items_source")
    return Path(src) if src else config_path.resolve().parent / "board-items.json"


def set_item_tier(item_id: str, tier: str, *, config_path: "str | Path") -> bool:
    """Set `board-items.json`'s `item.track = tier` for `item_id`, ahead of
    a `project_sync.py post` call so its own `sync_fields()` picks up the
    new value and pushes it to the real board (once the tier's option
    exists on the field — this function only writes the vault-side value,
    it never touches the live board directly). Returns True if the item
    was found and updated; False on any graceful-skip condition (crickets
    unresolvable, items file missing/malformed, item not found) — never
    raises, matching this module's own contract.
    """
    pm = load_project_model_module()
    if pm is None:
        return False
    items_path = _resolve_items_path(config_path)
    if items_path is None or not items_path.is_file():
        return False
    try:
        graph = pm.load(items_path)
    except Exception:
        return False
    item = graph.get(item_id)
    if item is None:
        return False
    item.track = tier
    pm.dump(graph, items_path)
    return True


def post_dispatch_progress(
    name: str, *, summary: str, config_path: "str | Path",
    commit: "str | None" = None, dry_run: bool = False,
    gh_bin: str = "gh", runner=subprocess.run, tier: "str | None" = None,
) -> dict:
    """Post a `task-progress` board update for a dispatched fleet work item
    (`name`, the same `<plan>-<task>` identifier `dispatch.dispatch_name()`
    produces). If `tier` is given, `board-items.json`'s `item.track` is set
    to it first (best-effort — a failure there doesn't block the post
    itself; `sync_fields()` just won't have a new value to push this
    time). Returns `{"posted": bool, "skipped_reason": str | None,
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

    if tier is not None:
        set_item_tier(name, tier, config_path=config_path)

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
    (a list of objects/dicts each carrying `name` + a status string, and
    optionally `tier` — e.g. a real `dispatch.DispatchResult`, which
    already carries one). Returns the list of individual
    `post_dispatch_progress()` outcomes — a fleet run's board reflects
    every dispatched item's state change without further manual
    intervention, per this task's own verification.
    """
    outcomes = []
    for r in results:
        name = r["name"] if isinstance(r, dict) else r.name
        status = r.get("status", "dispatched") if isinstance(r, dict) else getattr(r, "status", "dispatched")
        tier = r.get("tier") if isinstance(r, dict) else getattr(r, "tier", None)
        outcomes.append(post_dispatch_progress(
            name, summary=f"fleet dispatch: {status}", config_path=config_path,
            dry_run=dry_run, gh_bin=gh_bin, runner=runner, tier=tier,
        ))
    return outcomes
