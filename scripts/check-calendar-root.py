#!/usr/bin/env python3
"""Gate: the calendar root holds years and their maps (agentm-vault plan 07).

`Calendar/` is facet notes under year directories, with each year's generated
map, `moc-calendar-YYYY.md`, beside its directory. Anything else at the calendar
root is a finding, and so is a year map with no year beside it.

Two things stay allowed there until plan 10 moves the daily note into its year:
Obsidian's daily-notes setting still names `Calendar` as its folder and
`Calendar/_daily-template` as its template, so the template stays, and the next
bare-date note you open (`YYYY-MM-DD.md`) lands at the root. Both are reported
as notes. So is a year whose map the night has not written yet: its first
facet note can land during the day, and the map follows that night.

It reads the live vault, resolved at runtime. Until the maps data run writes
`memory/.maps-and-root-notes-complete` it reports what it finds and exits 0;
once the marker exists it enforces.

Usage:
  python3 scripts/check-calendar-root.py                 # the resolved memory root
  python3 scripts/check-calendar-root.py --memory-root DIR
Exit: 0 clean (or nothing to check, or before the data run); 1 on a finding.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drive_artifacts  # noqa: E402
import maps_shape as ms  # noqa: E402
import vault_layout  # noqa: E402

CALENDAR = "Calendar"
IGNORABLE = {".DS_Store", ".gitkeep"}
DAILY_TEMPLATE = "_daily-template.md"
YEAR = re.compile(r"^\d{4}$")
YEAR_MAP = re.compile(r"^moc-calendar-(\d{4})\.md$")
BARE_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
FACET_NOTE = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")


def _is_dir_exact(path: Path) -> bool:
    try:
        return path.is_dir() and any(p.name == path.name for p in path.parent.iterdir())
    except OSError:
        return False


def calendar_root(memory_root: Path) -> Path | None:
    """The `Calendar/` space the dreaming binary writes: directly under the
    memory root, else beside it when the memory root sits inside an Obsidian
    vault. Discovered, never conjured."""
    memory_root = Path(memory_root)
    if _is_dir_exact(memory_root / CALENDAR):
        return memory_root / CALENDAR
    if (memory_root.parent / ".obsidian").is_dir() and not (memory_root / ".obsidian").is_dir():
        if _is_dir_exact(memory_root.parent / CALENDAR):
            return memory_root.parent / CALENDAR
    return None


def findings(memory_root: Path) -> tuple[list, list]:
    """`(failures, notes)` for the calendar root."""
    cal = calendar_root(memory_root)
    if cal is None:
        return [], []
    failures, notes = [], []
    years, maps = set(), set()
    for p in sorted(cal.iterdir()):
        name = p.name
        if name in IGNORABLE or drive_artifacts.is_artifact(p):
            continue
        if p.is_dir():
            if YEAR.match(name):
                years.add(name)
            else:
                failures.append(f"Calendar/{name}/: not a year directory")
            continue
        m = YEAR_MAP.match(name)
        if m:
            maps.add(m.group(1))
        elif name == DAILY_TEMPLATE or BARE_DAY.match(name):
            notes.append(f"Calendar/{name}: allowed at the root until plan 10 moves the daily note into its year")
        else:
            failures.append(f"Calendar/{name}: the calendar root holds years and their maps")
    for year in sorted(maps - years):
        failures.append(f"Calendar/moc-calendar-{year}.md: a year map with no {year}/ beside it")
    for year in sorted(years - maps):
        if any(FACET_NOTE.match(p.name) for p in (cal / year).iterdir()):
            notes.append(f"Calendar/{year}/: no moc-calendar-{year}.md yet; the night writes it")
    return failures, notes


def check(memory_root: Path, out=sys.stdout) -> int:
    failures, notes = findings(memory_root)
    for n in notes:
        print(f"check-calendar-root: note — {n}", file=out)
    if not failures:
        print("check-calendar-root: clean — the calendar root holds years and their maps", file=out)
        return 0
    if not ms.data_run_done(memory_root):
        print(f"check-calendar-root: before the maps data run — {len(failures)} finding(s), which the data run "
              f"clears; enforced once memory/{ms.MARKER_NAME} exists", file=out)
        for f in failures:
            print(f"  pending: {f}", file=out)
        return 0
    print(f"check-calendar-root: {len(failures)} finding(s)", file=out)
    for f in failures:
        print(f"  {f}", file=out)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-root", default=None,
                    help="memory root to check (default: $MEMORY_ROOT, else the configured one)")
    args = ap.parse_args(argv)
    root = _resolve(args.memory_root)
    if root is None or not (root / "memory").is_dir():
        print("check-calendar-root: no memory root resolves; nothing to check")
        return 0
    return check(root)


def _resolve(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    root = vault_layout.env_memory_root()
    if root is None:
        try:
            import harness_memory as hm  # noqa: E402
            root = hm.memory_root()
        except ImportError:
            root = None
    return Path(root) if root else None


if __name__ == "__main__":
    raise SystemExit(main())
