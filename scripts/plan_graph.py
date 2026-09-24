#!/usr/bin/env python3
"""plan_graph — shared map engine for the team-coordinator persona (V5-11).

Reads a project's plans — the tasks under its vault directory's `tasks/`, or,
for a project with no vault, the flat pairs in its repo-local `.harness/` and the
staged plans in `.harness/queued-plans/` — and returns a structured picture:
every plan, its status, step counts, last-touched timestamp (from the progress
log), plus any declared `depends_on` and `touches` metadata from the plan's YAML
frontmatter.

A plan's status is its tracker's — `tasks/<name>/tracker.md` beside a task's
plan, `tracker-<slug>.md` beside a repo-local pair — and the plan's own
`**Status:**` line only when no tracker parses (agentm-vault plan 15: the line
has one writer left, and it stops writing it).

The three team-coordinator capability scripts (standup, readiness, merge_order)
all call `build_plan_graph()` and work from the returned list — no re-reading
the vault.

**Pure stdlib, no model.  Read-only by contract.**  Zero writes to disk.

Usage (diagnostic, not a gate)::

    python3 scripts/plan_graph.py [--state-dir PATH]

Fields per plan:
    slug          A task's directory name ("042-build-the-brief"); "" for the
                  repo-local singleton PLAN.md; "foo" for PLAN-foo.md.
    filename      "tasks/<name>/plan.md", or a bare filename, e.g. "PLAN-foo.md".
    status        The tracker's status ("queued" / "active" / "parked" / "done"
                  / "dropped"), else the **Status:** line's value ("planning" /
                  "in-progress" / "done"), else "—".
    tasks_done    Count of [x] task checkboxes in the plan body.
    tasks_total   Count of [ ] + [x] task checkboxes.
    last_touched  datetime of the most-recent progress-log timestamp, or None.
    depends_on    List of plan slugs this plan must wait for (from frontmatter).
    touches       List of file globs this plan edits (from frontmatter).
    active        False for a staged plan: a task whose tracker is queued, or a
                  plan in queued-plans/; also False for a finished task.
    finished      True for a task whose tracker is done or dropped.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402

_QUEUED_SUBDIR = "queued-plans"

# Regex to parse a progress-log timestamp line: "YYYY-MM-DD HH:MM ..."
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})")


@dataclass
class PlanInfo:
    """Structured representation of one plan file."""

    slug: str
    filename: str
    status: str
    tasks_done: int
    tasks_total: int
    last_touched: Optional[datetime]
    depends_on: List[str] = field(default_factory=list)
    touches: List[str] = field(default_factory=list)
    active: bool = True  # False → a staged (queued) plan, or a finished task
    finished: bool = False  # True → a task whose tracker is done or dropped


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split ``---...---`` frontmatter from body.

    Returns ``(frontmatter_block, body)``; frontmatter is the raw text between
    the delimiters (empty string when absent).
    """
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    fm = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    return fm, body


def _parse_frontmatter_list(fm: str, key: str) -> list[str]:
    """Extract a YAML sequence value for *key* from a simple frontmatter block.

    Supports both inline (``key: [a, b]``) and block-sequence forms::

        key:
          - a
          - b

    Returns an empty list when the key is absent or the value is empty.
    """
    # Inline form: key: [a, b, c]
    inline = re.search(
        rf"^{re.escape(key)}\s*:\s*\[([^\]]*)\]",
        fm,
        re.MULTILINE,
    )
    if inline:
        raw = inline.group(1).strip()
        if not raw:
            return []
        return [s.strip().strip("\"'") for s in raw.split(",") if s.strip()]

    # Block-sequence form: find the key line, then consume "  - item" lines.
    block_key = re.search(
        rf"^{re.escape(key)}\s*:\s*$",
        fm,
        re.MULTILINE,
    )
    if not block_key:
        return []
    items: list[str] = []
    after = fm[block_key.end():]
    for line in after.splitlines():
        m = re.match(r"^\s+-\s+(.+)$", line)
        if m:
            items.append(m.group(1).strip().strip("\"'"))
        elif line.strip() and not line.startswith(" "):
            break  # next top-level key — stop
    return items


def _extract_status(body: str) -> str:
    """Value of the first ``Status:`` line (bold markers tolerated)."""
    for line in body.splitlines():
        stripped = line.strip().lstrip("*").strip()
        if stripped.lower().startswith("status:"):
            value = stripped[len("status:"):].strip().strip("*").strip()
            return value or "—"
    return "—"


def _count_tasks(body: str) -> tuple[int, int]:
    """Return ``(tasks_done, tasks_total)`` by counting checkboxes."""
    done = len(re.findall(r"\[x\]", body, re.IGNORECASE))
    undone = len(re.findall(r"\[ \]", body))
    return done, done + undone


def _last_touched(progress_path: Path) -> Optional[datetime]:
    """Most-recent timestamp in an append-only progress log, or None."""
    if not progress_path.is_file():
        return None
    try:
        text = progress_path.read_text(encoding="utf-8")
    except OSError:
        return None
    best: Optional[datetime] = None
    for line in text.splitlines():
        m = _TS_RE.match(line.strip())
        if not m:
            continue
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if best is None or dt > best:
            best = dt
    return best


def _slug_from_filename(name: str) -> str:
    """``PLAN.md`` → ``""``, ``PLAN-foo.md`` → ``"foo"``."""
    if name == "PLAN.md":
        return ""
    m = re.match(r"^PLAN-(.+)\.md$", name)
    return m.group(1) if m else name


def _progress_path_for(state: Path, plan_name: str) -> Path:
    """Map a repo-local plan filename to its progress file (both in *state*)."""
    norm = hm._normalize_plan_name(plan_name)
    progress_name = hm._plan_pair(norm)[1]
    return state / progress_name


def _is_task_plan(plan_path: Path) -> bool:
    """A task's plan: ``tasks/<slug>/plan.md`` (agentm-vault plan 09)."""
    return plan_path.name == "plan.md" and plan_path.parent.parent.name == hm._TASKS_DIRNAME


def _slug_for(plan_path: Path) -> str:
    """The plan's slug in either layout: a task directory's name, or the flat
    file name's."""
    return plan_path.parent.name if _is_task_plan(plan_path) else _slug_from_filename(plan_path.name)


def _progress_for(state: Path, plan_path: Path) -> Path:
    """The progress log beside a task's plan, or a repo-local pair's in *state*."""
    if _is_task_plan(plan_path):
        return plan_path.parent / "progress.md"
    return _progress_path_for(state, plan_path.name)


def _tracker_for(state: Path, plan_path: Path) -> Path:
    """The tracker beside a task's plan, or beside a repo-local pair in *state*."""
    if _is_task_plan(plan_path):
        return plan_path.parent / "tracker.md"
    return state / hm._tracker_name(hm._normalize_plan_name(plan_path.name))


def _tracker_status(tracker_path: Path) -> Optional[str]:
    """The tracker's status, or None when there is no tracker or it does not
    parse — an unreadable tracker is check-tracker-schema's to report."""
    if not tracker_path.is_file():
        return None
    try:
        import tracker  # the one tracker schema (agentm-vault plan 09)
        return tracker.read(tracker_path)[0].status
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def _parse_plan(plan_path: Path, progress_path: Path, active: bool,
                tracker_path: Optional[Path] = None) -> PlanInfo:
    """Parse one plan file into a ``PlanInfo``. The status is the tracker's when
    one parses, else the plan's own Status line. A task whose tracker is queued
    is staged, and one whose tracker is final is finished; neither is active."""
    try:
        text = plan_path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    fm, body = _split_frontmatter(text)
    slug = _slug_for(plan_path)
    tracked = _tracker_status(tracker_path) if tracker_path is not None else None
    status = tracked or _extract_status(body)
    finished = _is_task_plan(plan_path) and tracked in ("done", "dropped")
    if _is_task_plan(plan_path) and (tracked == "queued" or finished):
        active = False
    tasks_done, tasks_total = _count_tasks(body)
    touched = _last_touched(progress_path)
    depends_on = _parse_frontmatter_list(fm, "depends_on")
    touches = _parse_frontmatter_list(fm, "touches")
    return PlanInfo(
        slug=slug,
        filename=(f"{hm._TASKS_DIRNAME}/{slug}/plan.md" if _is_task_plan(plan_path)
                  else plan_path.name),
        status=status,
        tasks_done=tasks_done,
        tasks_total=tasks_total,
        last_touched=touched,
        depends_on=depends_on,
        touches=touches,
        active=active,
        finished=finished,
    )


def build_plan_graph(state: Path) -> list[PlanInfo]:
    """Return every plan under *state* — the directory ``hm.state_dir`` answers.

    A project's vault directory yields its tasks, sorted by name, finished ones
    included and marked so a dependency on one reads as met. A repo-local
    ``.harness/`` yields its flat pairs (singleton first, then named
    alphabetically), then its staged plans in ``queued-plans/``. The order is
    deterministic.
    """
    plans: list[PlanInfo] = []
    state = Path(state)

    # --- the resolver's own enumeration: tasks, or repo-local pairs ---
    for plan_path in sorted(
        hm.list_plan_files(state),
        key=lambda p: ((0, "") if p.name == "PLAN.md"
                       else (2, _slug_for(p)) if _is_task_plan(p) else (1, p.name)),
    ):
        progress = _progress_for(state, plan_path)
        plans.append(_parse_plan(plan_path, progress, active=True,
                                 tracker_path=_tracker_for(state, plan_path)))

    # --- staged plans (a repo-local .harness/ only; a task stages as queued) ---
    queued_dir = state / _QUEUED_SUBDIR
    if queued_dir.is_dir():
        queued_files: list[Path] = []
        singleton_q = queued_dir / "PLAN.md"
        if singleton_q.is_file():
            queued_files.append(singleton_q)
        for p in sorted(queued_dir.glob("PLAN-*.md")):
            if p.is_file() and hm._conflict_family(p.name) is None:
                queued_files.append(p)
        for plan_path in queued_files:
            # progress log lives in the active dir (staged plan has no run log yet)
            progress = _progress_path_for(state, plan_path.name)
            plans.append(_parse_plan(plan_path, progress, active=False))

    _name_dependencies_by_directory(plans)
    return plans


def _name_dependencies_by_directory(plans: list[PlanInfo]) -> None:
    """Rewrite each `depends_on` entry that is a task's verb slug to that task's
    directory name, so readiness and merge order find it under the key they use.

    A task's name is its directory or the verb slug inside it, and every plan
    written before the move names its dependencies by the bare slug. An entry
    that is already a plan's slug, or that two tasks share, is left as written:
    the first needs nothing, and the second is not ours to guess."""
    known = {p.slug for p in plans}
    by_verb: dict[str, list[str]] = {}
    for p in plans:
        m = hm._TASK_DIR.match(p.slug)
        if m is not None:
            by_verb.setdefault(m.group(2), []).append(p.slug)
    for p in plans:
        p.depends_on = [
            dep if dep in known or len(by_verb.get(dep, ())) != 1 else by_verb[dep][0]
            for dep in p.depends_on
        ]


# ---------------------------------------------------------------------------
# CLI (diagnostic)
# ---------------------------------------------------------------------------

def _main() -> None:
    ap = argparse.ArgumentParser(description="Dump the plan graph for a project.")
    ap.add_argument("--state-dir", "--harness-dir", dest="state_dir",
                    help="The project's vault directory, or a repo-local .harness/ "
                         "(default: resolve from cwd).")
    args = ap.parse_args()

    if args.state_dir:
        state = Path(args.state_dir)
    else:
        state = hm.state_dir(hm.resolve_project({"cwd": Path.cwd()}))
    if state is None:
        print("(no plan state directory resolves from here)")
        return

    plans = build_plan_graph(state)
    if not plans:
        print("(no plans found)")
        return
    for p in plans:
        label = "done" if p.finished else "active" if p.active else "queued"
        touched = p.last_touched.strftime("%Y-%m-%d %H:%M") if p.last_touched else "—"
        print(
            f"[{label}] {p.filename}  status={p.status!r}  "
            f"tasks={p.tasks_done}/{p.tasks_total}  touched={touched}"
        )
        if p.depends_on:
            print(f"         depends_on={p.depends_on}")
        if p.touches:
            print(f"         touches={p.touches}")


if __name__ == "__main__":
    _main()
