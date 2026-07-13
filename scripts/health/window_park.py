#!/usr/bin/env python3
"""window_park.py — the tidy stop when a subscription window runs out mid-run
(PLAN-observability-console task 3, `wiki/designs/agentm-autonomy.md`'s
"When the window runs out" section).

This module only produces the tidy stop -- it never resumes a run itself
("a loop that ate one window would simply eat the next one at reset"). The
rate-limit *detection* that triggers a park is the caller's concern (an
overnight-run driver, built by `PLAN-autonomy-control-plane`); this module
is what that caller invokes once it has detected one.

Writes two things:
  - a JSON state file on disk (`<park_dir>/<plan>-park-state.json`) --
    machine-readable, read back by anything that wants to know a run is
    parked without re-parsing the human note
  - a human-readable park note into the vault's `_briefs/` (the same home
    `inbox_digest.py` writes to, L1/F2 fix -- a `kind: telemetry`,
    `_inbox/`-routed note like this one used to be would hit the exact same
    auto-apply-expires-it-before-morning bug the digest notes did) naming
    where it stopped, when, and the exact resume command -- morning resume
    is one paste, by the operator.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def default_park_dir() -> Path:
    return Path.home() / ".cache" / "agentm" / "telemetry" / "park"


def write_park_state(
    plan_slug: str, *, reason: str, task_progress: str, resume_command: str,
    park_dir: "str | Path | None" = None, now: "datetime | None" = None,
) -> Path:
    """Write the machine-readable park state file. Always overwrites (a
    fresh park for the same plan supersedes an earlier one -- there is only
    ever one live park per plan)."""
    now = now if now is not None else datetime.now(timezone.utc)
    park_dir = Path(park_dir) if park_dir is not None else default_park_dir()
    park_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "plan": plan_slug,
        "reason": reason,
        "parked_at": now.isoformat(),
        "task_progress": task_progress,
        "resume_command": resume_command,
    }
    target = park_dir / f"{plan_slug}-park-state.json"
    target.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def read_park_state(plan_slug: str, *, park_dir: "str | Path | None" = None) -> "dict | None":
    """Read the park state back, or None if this plan has no live park (or
    was never parked). Never raises on a missing/malformed file."""
    park_dir = Path(park_dir) if park_dir is not None else default_park_dir()
    target = park_dir / f"{plan_slug}-park-state.json"
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_park_state(plan_slug: str, *, park_dir: "str | Path | None" = None) -> bool:
    """Remove a plan's park state (the operator has resumed it). Returns
    True if a file was removed, False if there was nothing to clear."""
    park_dir = Path(park_dir) if park_dir is not None else default_park_dir()
    target = park_dir / f"{plan_slug}-park-state.json"
    if not target.is_file():
        return False
    target.unlink()
    return True


def render_park_note(state: dict) -> str:
    """Render the human-readable park note body. Deterministic given `state`."""
    return (
        f"# Run parked — {state['reason']}\n\n"
        f"- Plan: {state['plan']}\n"
        f"- Parked at: {state['parked_at']}\n"
        f"- Progress: {state['task_progress']}\n\n"
        "## Resume\n\n"
        "Morning resume is one paste, by the operator — this run never resumes itself.\n\n"
        "```\n"
        f"{state['resume_command']}\n"
        "```\n"
    )


def _park_note_slug(plan_slug: str, now: datetime) -> str:
    return f"{now.strftime('%Y%m%d')}-park-{plan_slug}"


def write_park_note(vault_path: "str | Path", state: dict, *, now: "datetime | None" = None) -> "Path | None":
    """Write the park note into `<vault>/_briefs/`. Returns None if
    the vault directory doesn't exist (graceful-skip, matching
    `inbox_digest.write_digest_note`'s own contract)."""
    now = now if now is not None else datetime.now(timezone.utc)
    vault = Path(vault_path)
    if not vault.is_dir():
        return None
    briefs_dir = vault / "_briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)
    slug = _park_note_slug(state["plan"], now)
    target = briefs_dir / f"{slug}.md"

    fm = (
        "---\n"
        "kind: brief\n"
        "status: active\n"
        f"created: {now.strftime('%Y-%m-%d')}\n"
        f"slug: {slug}\n"
        f"park_reason: {state['reason']}\n"
        f"park_plan: {state['plan']}\n"
        "---\n"
    )
    target.write_text(fm + "\n" + render_park_note(state), encoding="utf-8")
    return target


def park_run(
    plan_slug: str, *, reason: str, task_progress: str, resume_command: str,
    vault_path: "str | Path | None" = None, park_dir: "str | Path | None" = None,
    now: "datetime | None" = None,
) -> dict:
    """End-to-end: write the state file, and (if a vault path is given) the
    human-readable note. Returns {"state_path": Path, "note_path": Path | None}."""
    now = now if now is not None else datetime.now(timezone.utc)
    state_path = write_park_state(
        plan_slug, reason=reason, task_progress=task_progress,
        resume_command=resume_command, park_dir=park_dir, now=now,
    )
    note_path = None
    if vault_path is not None:
        state = read_park_state(plan_slug, park_dir=park_dir)
        note_path = write_park_note(vault_path, state, now=now)
    return {"state_path": state_path, "note_path": note_path}


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Park a run that hit a rate-limit mid-run.")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--reason", default="rate-limit")
    ap.add_argument("--progress", required=True, help="one-line summary of where the run stopped")
    ap.add_argument("--resume-command", required=True)
    ap.add_argument("--vault-path", default=None)
    ap.add_argument("--park-dir", default=None)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    result = park_run(
        args.plan, reason=args.reason, task_progress=args.progress,
        resume_command=args.resume_command, vault_path=args.vault_path, park_dir=args.park_dir,
    )
    print(f"window_park: state written to {result['state_path']}", file=sys.stderr)
    if result["note_path"] is not None:
        print(f"window_park: note written to {result['note_path']}", file=sys.stderr)
    print(json.dumps({"total_cost_usd": 0.0}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
