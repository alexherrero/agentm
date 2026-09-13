#!/usr/bin/env python3
"""plan_graph — shared map engine for the team-coordinator persona (V5-11).

Reads `_harness/` (active plans) and `_harness/queued-plans/` (staged plans)
and returns a structured picture: every plan, its status, task counts,
last-touched timestamp (from the progress log), plus any declared `depends_on`
and `touches` metadata from the plan's YAML frontmatter.

The three team-coordinator capability scripts (standup, readiness, merge_order)
all call `build_plan_graph()` and work from the returned list — no re-reading
the vault.

**Pure stdlib, no model.  Read-only by contract.**  Zero writes to disk.

Usage (diagnostic, not a gate)::

    python3 scripts/plan_graph.py [--harness-dir PATH]

Fields per plan:
    slug          "" for the singleton PLAN.md; "foo" for PLAN-foo.md.
    filename      Bare filename, e.g. "PLAN-foo.md".
    status        Value of the **Status:** line: "planning" / "in-progress" /
                  "done" / "—".
    tasks_done    Count of [x] task checkboxes in the plan body.
    tasks_total   Count of [ ] + [x] task checkboxes.
    last_touched  datetime of the most-recent progress-log timestamp, or None.
    depends_on    List of plan slugs this plan must wait for (from frontmatter).
    touches       List of file globs this plan edits (from frontmatter).
    active        True → active plan dir; False → queued-plans dir.
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
    active: bool = True  # False → queued plan


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


def _progress_path_for(harness_dir: Path, plan_name: str) -> Path:
    """Map a plan filename to its progress file (both in *harness_dir*)."""
    norm = hm._normalize_plan_name(plan_name)
    progress_name = hm._plan_pair(norm)[1]
    return harness_dir / progress_name


def _is_task_plan(plan_path: Path) -> bool:
    """A task's plan: ``tasks/<slug>/plan.md`` (agentm-vault plan 09)."""
    return plan_path.name == "plan.md" and plan_path.parent.parent.name == hm._TASKS_DIRNAME


def _slug_for(plan_path: Path) -> str:
    """The plan's slug in either layout: a task directory's name, or the flat
    file name's."""
    return plan_path.parent.name if _is_task_plan(plan_path) else _slug_from_filename(plan_path.name)


def _progress_for(harness_dir: Path, plan_path: Path) -> Path:
    """The progress log beside a task's plan, or the flat pair's in *harness_dir*."""
    if _is_task_plan(plan_path):
        return plan_path.parent / "progress.md"
    return _progress_path_for(harness_dir, plan_path.name)


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def _parse_plan(plan_path: Path, progress_path: Path, active: bool) -> PlanInfo:
    """Parse one plan file into a ``PlanInfo``."""
    try:
        text = plan_path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    fm, body = _split_frontmatter(text)
    slug = _slug_for(plan_path)
    status = _extract_status(body)
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
    )


def build_plan_graph(harness_dir: Path) -> list[PlanInfo]:
    """Return every plan (active + queued) from *harness_dir*.

    Active plans come first (singleton first, then named alphabetically),
    followed by queued plans (alphabetically).  The order is deterministic.
    """
    plans: list[PlanInfo] = []

    # --- active plans ---
    for plan_path in sorted(
        _list_active_plans(harness_dir),
        key=lambda p: ((0, "") if p.name == "PLAN.md"
                       else (2, _slug_for(p)) if _is_task_plan(p) else (1, p.name)),
    ):
        progress = _progress_for(harness_dir, plan_path)
        plans.append(_parse_plan(plan_path, progress, active=True))

    # --- queued plans ---
    queued_dir = harness_dir / _QUEUED_SUBDIR
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
            progress = _progress_path_for(harness_dir, plan_path.name)
            plans.append(_parse_plan(plan_path, progress, active=False))

    return plans


def _list_active_plans(harness_dir: Path) -> list[Path]:
    """Fallback list_plan_files in case harness_memory doesn't export it."""
    files: list[Path] = []
    singleton = harness_dir / "PLAN.md"
    if singleton.is_file():
        files.append(singleton)
    for p in harness_dir.glob("PLAN-*.md"):
        if p.is_file() and hm._conflict_family(p.name) is None:
            files.append(p)
    # A task's plan beside a vault `_harness/` (agentm-vault plan 09).
    if harness_dir.name == "_harness":
        for p in sorted((harness_dir.parent / hm._TASKS_DIRNAME).glob("*/plan.md")):
            if p.is_file() and hm._is_safe_plan_slug(p.parent.name):
                files.append(p)
    return files


# ---------------------------------------------------------------------------
# CLI (diagnostic)
# ---------------------------------------------------------------------------

def _main() -> None:
    ap = argparse.ArgumentParser(description="Dump the plan graph for a _harness/ dir.")
    ap.add_argument("--harness-dir", help="Path to the _harness/ directory.")
    args = ap.parse_args()

    if args.harness_dir:
        harness_dir = Path(args.harness_dir)
    else:
        harness_dir = hm.harness_state_dir()

    plans = build_plan_graph(harness_dir)
    if not plans:
        print("(no plans found)")
        return
    for p in plans:
        state = "active" if p.active else "queued"
        touched = p.last_touched.strftime("%Y-%m-%d %H:%M") if p.last_touched else "—"
        print(
            f"[{state}] {p.filename}  status={p.status!r}  "
            f"tasks={p.tasks_done}/{p.tasks_total}  touched={touched}"
        )
        if p.depends_on:
            print(f"         depends_on={p.depends_on}")
        if p.touches:
            print(f"         touches={p.touches}")


if __name__ == "__main__":
    _main()
