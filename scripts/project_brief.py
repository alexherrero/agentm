#!/usr/bin/env python3
"""The opening brief: where the bound project and task stand, in under twenty lines.

agentm-vault § Projects and tasks, "A session opens on twenty lines". For the
project and task a session is bound to (`session_binding`), this prints the
project tracker's State and Next, the task tracker's State and Next, the last
three progress lines, the count of open follow-ups, and the count of unfiled
captures carrying this `project:`. The session-start surface collapses anything
larger unread, so the brief never passes MAX_LINES.

With no tracker for the task or the project there is no brief: this prints
nothing and exits 3, and the brief hook prints today's plan block instead — the
one `harness-context-session-start` renders — so a session never opens on less
than it does now.

Usage:
  python3 scripts/project_brief.py [--cwd DIR]
Exit: 0 a brief was printed · 3 no brief (unbound, or no tracker)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePath
from typing import Optional

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tracker as tk  # noqa: E402

MAX_LINES = 20
LINE_WIDTH = 160
NO_BRIEF = 3

_STATUS = re.compile(r"^status:[ \t]*(.+?)[ \t]*$", re.M)
_PROJECT = re.compile(r"^project:[ \t]*(.+?)[ \t]*$", re.M)
_HEAD_BYTES = 4096


def _clip(line: str) -> str:
    line = line.rstrip()
    return line if len(line) <= LINE_WIDTH else line[: LINE_WIDTH - 1] + "…"


def _lines(text: str, limit: int) -> list:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()][:limit]


def read_tracker(path: Optional[Path]) -> Optional[tk.Tracker]:
    """A tracker that exists and parses, else None; a malformed one is the gate's
    to report, not the brief's."""
    if path is None or not Path(path).is_file():
        return None
    try:
        return tk.read(Path(path))[0]
    except (tk.TrackerError, OSError, UnicodeDecodeError):
        return None


def last_progress_lines(path: Optional[Path], n: int = 3) -> list:
    """The last `n` non-empty lines of an append-only progress log."""
    if path is None or not Path(path).is_file():
        return []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()][-n:]


def count_open_followups(text: str) -> int:
    """Open follow-ups: an unchecked box, or a row of a table whose header names a
    Status column and whose status cell carries no ✅. Any other table in the file
    is content inside an entry, not a list of items."""
    rows = [ln.strip() for ln in text.splitlines()]

    def cells(row: str) -> list:
        return [c.strip() for c in row.strip("|").split("|")]

    def is_separator(row: str) -> bool:
        return row.startswith("|") and all(c and set(c) <= set("-: ") for c in cells(row))

    n = 0
    status_col = None
    for i, row in enumerate(rows):
        if row.startswith("- [ ]"):
            n += 1
            continue
        if not (row.startswith("|") and row.endswith("|") and len(row) > 1):
            status_col = None  # a table ends at the first line that is not a row
            continue
        if is_separator(row):
            continue
        if i + 1 < len(rows) and is_separator(rows[i + 1]):
            header = [c.lower() for c in cells(row)]
            status_col = header.index("status") if "status" in header else None
            continue
        if status_col is not None:
            row_cells = cells(row)
            if status_col < len(row_cells) and "✅" not in row_cells[status_col]:
                n += 1
    return n


def count_unfiled(memory_root: Optional[Path], project: str) -> int:
    """Captures still `unfiled` that carry this `project:`, over the class
    directories (the maps' `mocs/` excepted)."""
    if memory_root is None:
        return 0
    base = Path(memory_root) / "memory"
    if not base.is_dir():
        return 0
    n = 0
    want = project.strip().lower()
    for cls in sorted(p for p in base.iterdir() if p.is_dir() and p.name != "mocs"):
        for note in cls.glob("*.md"):
            try:
                with note.open("rb") as fh:
                    head = fh.read(_HEAD_BYTES).decode("utf-8", errors="replace")
            except OSError:
                continue
            head = head.replace("\r\n", "\n")  # a card saved on Windows ends its lines in CRLF
            if not head.startswith("---\n"):
                continue
            block = head[4:head.find("\n---", 3)] if "\n---" in head[3:] else head[4:]
            status = _STATUS.search(block)
            proj = _PROJECT.search(block)
            if (status and status.group(1).strip("'\"").lower() == "unfiled" and proj
                    and proj.group(1).strip("'\"").lower() == want):
                n += 1
    return n


def render(*, project: str, task: Optional[str], project_tracker: Optional[tk.Tracker],
           task_tracker: Optional[tk.Tracker], progress: list, open_followups: int,
           unfiled: int, plan_path: Optional[Path]) -> Optional[list]:
    """The brief's lines, or None when neither tracker exists."""
    if project_tracker is None and task_tracker is None:
        return None
    head = f"[agentm] {project}" + (f" · task {task}" if task else "")
    out = [head + " — pass both as project and task when you capture"]
    if project_tracker is not None:
        state = _lines(project_tracker.state, 2) or ["(no State written)"]
        out.append(f"Project state: {state[0]}")
        out += [f"  {ln}" for ln in state[1:]]
        nxt = _lines(project_tracker.next_steps, 1)
        if nxt:
            out.append(f"Project next: {nxt[0]}")
    if task_tracker is not None:
        state = _lines(task_tracker.state, 4) or ["(no State written)"]
        out.append(f"Task state ({task_tracker.status}): {state[0]}")
        out += [f"  {ln}" for ln in state[1:]]
        nxt = _lines(task_tracker.next_steps, 3)
        if nxt:
            out.append(f"Task next: {nxt[0]}")
            out += [f"  {ln}" for ln in nxt[1:]]
    if progress:
        out.append("Recent progress:")
        out += [f"  {ln}" for ln in progress]
    out.append(f"Open follow-ups: {open_followups} · unfiled captures with project {project}: {unfiled}")
    if plan_path is not None:
        # Forward slashes on every platform, so the brief reads the same on Windows,
        # where the session's tools accept them.
        shown = plan_path.as_posix() if isinstance(plan_path, PurePath) else str(plan_path)
        out.append(f"Plan: {shown}")
    return [_clip(ln) for ln in out][:MAX_LINES]


def brief_for(cwd: Path) -> Optional[list]:
    """Resolve the bound project's and task's files from `cwd`, and render."""
    import harness_memory as hm  # noqa: E402
    import session_binding  # noqa: E402

    binding = session_binding.read_binding(cwd)
    if not binding.project:
        return None
    resolution = hm.resolve_project({"cwd": Path(cwd)})
    state_dir = hm.harness_state_dir(resolution)
    project_dir = state_dir.parent if state_dir is not None and state_dir.name == "_harness" else None
    try:
        paths = hm.active_plan_paths(resolution)
    except (hm.ActivePlanError, ValueError):
        paths = None
    plan_path, progress_path, tracker_path = paths if paths else (None, None, None)
    if binding.task is None:
        tracker_path = None  # an unbound session has a project, not a task
    followups = 0
    for candidate in ((project_dir / "followups.md") if project_dir else None,
                      (state_dir / "FOLLOWUPS.md") if state_dir else None):
        if candidate is not None and candidate.is_file():
            try:
                followups = count_open_followups(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                followups = 0
            break
    try:
        memory_root = hm.memory_root()
    except Exception:
        memory_root = None
    return render(
        project=binding.project, task=binding.task,
        project_tracker=read_tracker(project_dir / "tracker.md" if project_dir else None),
        task_tracker=read_tracker(tracker_path), progress=last_progress_lines(progress_path),
        open_followups=followups, unfiled=count_unfiled(memory_root, binding.project),
        plan_path=plan_path if binding.task else None,
    )


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cwd", default=None, help="the session's directory (default: this process's)")
    args = ap.parse_args(argv)
    try:
        lines = brief_for(Path(args.cwd) if args.cwd else Path.cwd())
    except Exception as exc:  # never block a session's start
        print(f"[project-brief] skipped ({type(exc).__name__}: {exc})", file=sys.stderr)
        return NO_BRIEF
    if not lines:
        return NO_BRIEF
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
