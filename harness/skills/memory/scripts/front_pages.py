#!/usr/bin/env python3
"""Front pages that have fallen behind the work under them (task 205).

A project's `charter.md` and `tracker.md`, and a system's `system.md`, are the
pages a person opens first. The vault audit of 2026-11-13 found them weeks
behind the records beneath them: detail pages current, a charter still
describing an offer on a house that had been passed on. Nothing noticed,
because nothing compared the two.

This compares them. A front page is stale when its own date is more than
`GRACE_DAYS` older than the newest record beneath it:

  - a project's `charter.md` against every note under the project, other than
    the pages the night generates (its map, its project tracker);
  - a project's `tracker.md` against its task records, under `tasks/` and
    `completed/tasks/`. The night regenerates it from the task trackers, so it
    falls behind only when that stops or a task record moves without its
    tracker;
  - a system's `system.md` against every other note in that system's folder.

Dates are read from frontmatter and never from the file's modification time:
migrations have rewritten thousands of notes, and a file time says when a
script last touched a note, not when anyone worked on it. The rules for which
date counts as work are the dreaming binary's own (`projects_axis.go`,
`readActivity`): a map's dates never count, an `updated` that only records
the enrichment pass does not count, and a generated blueprint counts once
someone has edited it.

Read-only. The nightly scorecard prints what this finds
(`corpus_scorecard.section_front_pages`).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

# How far behind a front page may be before it is flagged. A charter does not
# change every time a task record does, and the night regenerates a project
# tracker a day after the work, so some lag is normal. A week is the line:
# measured on the vault on 2026-11-13, two weeks flagged nothing, the house
# project's charter included, which was ten days behind and described an offer
# that had since been passed on.
GRACE_DAYS = 7

_HEAD_BYTES = 16384
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
_DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def read_head(path: Path) -> dict:
    """A note's top-level frontmatter keys, first value wins; {} when it has
    none. Only scalars matter here, so a block list's items are skipped."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(_HEAD_BYTES)
    except OSError:
        return {}
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out: dict = {}
    for line in text[4:end].split("\n"):
        m = _KEY_RE.match(line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2).strip().strip("\"'")
    return out


def _day(value) -> str:
    m = _DAY_RE.match(str(value or "").strip().strip("\"'"))
    if not m:
        return ""
    try:
        date.fromisoformat(m.group(1))
    except ValueError:
        return ""
    return m.group(1)


def work_dates(fm: dict) -> list:
    """The dates in a record's frontmatter that mean someone worked on it."""
    kind = fm.get("kind", "")
    if kind == "moc":
        return []
    created, updated = _day(fm.get("created")), _day(fm.get("updated"))
    if kind == "blueprint":
        if updated == created:
            updated = ""
        created = ""
    if updated and updated == _day(fm.get("enriched_at")):
        updated = ""
    dates = [updated, created, _day(fm.get("date"))]
    if kind == "tracker":
        dates += [_day(fm.get("opened")), _day(fm.get("closed"))]
    return [d for d in dates if d]


def page_date(fm: dict) -> str:
    """A front page's own date: when it was last updated, else when it was
    made."""
    return (_day(fm.get("updated")) or _day(fm.get("created"))
            or _day(fm.get("opened")) or _day(fm.get("date")))


@dataclass
class Stale:
    page: str          # vault-relative path of the front page
    page_date: str
    newest: str        # vault-relative path of the newest record beneath it
    newest_date: str
    days_behind: int
    newer: int = 0     # how many records beneath it are newer than the page


def _notes(folder: Path):
    """Every `.md` under a folder, skipping hidden directories."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.endswith(".md") and not name.startswith("."):
                yield Path(root) / name


def records_beneath(folders: list, skip: set, today: str) -> list:
    """(date, path) for every record under the folders that carries a work
    date, each at its own newest. A date after `today` is a typo or a
    period's last day, and is not work yet."""
    out = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for note in _notes(folder):
            if note in skip:
                continue
            dates = [d for d in work_dates(read_head(note)) if d <= today]
            if dates:
                out.append((max(dates), note))
    return out


def newest_beneath(folders: list, skip: set, today: str) -> "Optional[tuple]":
    """(date, path) of the newest work under the folders, or None."""
    records = records_beneath(folders, skip, today)
    return max(records, key=lambda r: (r[0], r[1].as_posix())) if records else None


def _compare(vault_root: Path, page: Path, folders: list, skip: set, today: str,
             grace: int) -> "tuple[bool, Optional[Stale]]":
    """(was it checked, the finding or None)."""
    own = page_date(read_head(page))
    if not own:
        return False, None
    records = records_beneath(folders, skip | {page}, today)
    if not records:
        return True, None
    found = max(records, key=lambda r: (r[0], r[1].as_posix()))
    behind = (date.fromisoformat(found[0]) - date.fromisoformat(own)).days
    if behind <= grace:
        return True, None
    rel = lambda p: p.relative_to(vault_root).as_posix()  # noqa: E731
    return True, Stale(page=rel(page), page_date=own, newest=rel(found[1]),
                       newest_date=found[0], days_behind=behind,
                       newer=sum(1 for d, _p in records if d > own))


def stale_front_pages(vault_root, *, today: "Optional[date]" = None,
                      grace: int = GRACE_DAYS) -> "tuple[list, int]":
    """Every front page more than `grace` days behind the newest record
    beneath it, most behind first, and how many front pages were checked."""
    vault_root = Path(vault_root)
    day = (today or date.today()).isoformat()
    stale, checked = [], 0

    def check(page: Path, folders: list, skip: set) -> None:
        nonlocal checked
        if not page.is_file():
            return
        did, finding = _compare(vault_root, page, folders, skip, day, grace)
        checked += int(did)
        if finding:
            stale.append(finding)

    projects = vault_root / "projects"
    if projects.is_dir():
        for project in sorted(p for p in projects.iterdir() if p.is_dir()):
            # `projects/completed/` holds finished projects, not a project.
            if project.name.startswith(".") or project.name == "completed":
                continue
            generated = {project / "tracker.md"} | set(project.glob("moc-*.md"))
            check(project / "charter.md", [project], generated)
            check(project / "tracker.md",
                  [project / "tasks", project / "completed" / "tasks"], set())

    systems = vault_root / "systems"
    if systems.is_dir():
        for system in sorted(p for p in systems.iterdir() if p.is_dir()):
            if system.name.startswith("."):
                continue
            check(system / "system.md", [system], set())

    stale.sort(key=lambda s: (-s.days_behind, s.page))
    return stale, checked
