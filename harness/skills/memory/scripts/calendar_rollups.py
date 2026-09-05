#!/usr/bin/env python3
"""calendar_rollups.py — the register's weekly and monthly reviews.

Filing v2 part 5, task 4. `Calendar/YYYY/YYYY-Www-review.md` and
`Calendar/YYYY/YYYY-MM-review.md` are generated on the dreaming cadence
whether or not anyone remembered to want them: a closed week gets its
review, the running month gets its review refreshed as days accrue. A
sparse week reads sparse — the days that had entries, and one honest line
naming the days that had none — never padded.

Everything here is derived from the day indexes and the facet notes; a
review carries the period's own dates as `created`/`updated`, so a
regeneration on an unchanged period is byte-identical and the file is left
alone. Nothing is deleted.

Usage:
    calendar_rollups.py --vault <memory-root> [--today YYYY-MM-DD] [--weeks 8]
    calendar_rollups.py --vault <memory-root> week 2026-W36
    calendar_rollups.py --vault <memory-root> month 2026-09
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import calendar_facets as cf  # noqa: E402
import calendar_index as ci  # noqa: E402
from vault_lock import atomic_write, vault_mutex  # noqa: E402

REVIEW_KIND = "calendar-review"
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def week_days(year: int, week: int) -> list:
    return [date.fromisocalendar(year, week, d) for d in range(1, 8)]


def month_days(year: int, month: int) -> list:
    first = date(year, month, 1)
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    return [first + timedelta(days=i) for i in range((nxt - first).days)]


def _day_line(vault: Path, day: date) -> "str | None":
    """`- [[YYYY-MM-DD]] — meetings (2), diary (1)` when the day has facet
    notes; None when it has nothing (no index, no notes)."""
    notes = cf.notes_for_day(vault, day)
    if not notes:
        return None
    parts = []
    for facet, path in notes:
        _phrase, n = ci._phrase(path)
        parts.append(f"{facet} ({n})")
    return f"- [[{day.isoformat()}]] — {', '.join(parts)}"


def _frontmatter(kind_tags: str, period: str, key: str, stamp: date, extra: list) -> list:
    return ["---", f"kind: {REVIEW_KIND}", "status: active", "altitude: artifact",
            f"created: {stamp.isoformat()}", f"updated: {stamp.isoformat()}",
            f"tags: [calendar, review, {kind_tags}]", "group: calendar", f"slug: {key}-review",
            f"period: {period}", f"{period}: {key}"] + extra + ["generated_by: calendar_rollups.py", "---", ""]


def render_week(vault: "Path | str", year: int, week: int) -> str:
    vault = Path(vault)
    days = week_days(year, week)
    key = f"{year:04d}-W{week:02d}"
    lines = _frontmatter("week", "week", key, days[-1], [f"from: {days[0].isoformat()}", f"to: {days[-1].isoformat()}"])
    lines += [f"# Week {key} — {days[0].isoformat()} to {days[-1].isoformat()}", "",
              "Generated from the week's day indexes; the facet notes are the source.", ""]
    filled, empty, corrections = [], [], []
    for d in days:
        line = _day_line(vault, d)
        if line:
            filled.append(line)
        else:
            empty.append(_DOW[d.weekday()])
        corrections += [(d, c) for c in ci._corrections_written_on(vault, d)]
    if filled:
        lines += ["## Days", ""] + filled + [""]
    if empty:
        lines += [f"Nothing recorded on {', '.join(empty)}." if len(empty) < 7 else "Nothing recorded this week.", ""]
    if corrections:
        lines += ["## Corrections", ""] + [f"- [[{p.stem}]] — corrects {corrected} ({facet})" for _d, (facet, corrected, p) in corrections] + [""]
    lines += [f"{len(filled)} of 7 days with entries.", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def render_month(vault: "Path | str", year: int, month: int) -> str:
    vault = Path(vault)
    days = month_days(year, month)
    key = f"{year:04d}-{month:02d}"
    lines = _frontmatter("month", "month", key, days[-1], [f"from: {days[0].isoformat()}", f"to: {days[-1].isoformat()}"])
    lines += [f"# {key}", "", "Generated from the month's day indexes and week reviews; the facet notes are the source.", ""]
    weeks = []
    for d in days:
        y, w, _ = d.isocalendar()
        if (y, w) not in weeks:
            weeks.append((y, w))
    root = cf.calendar_root(vault)
    week_lines = []
    for y, w in weeks:
        wkey = f"{y:04d}-W{w:02d}"
        exists = root is not None and (root / f"{y:04d}" / f"{wkey}-review.md").is_file()
        in_month = [d for d in week_days(y, w) if d.month == month and d.year == year]
        n = sum(1 for d in in_month if cf.notes_for_day(vault, d))
        label = f"[[{wkey}-review]]" if exists else wkey
        week_lines.append(f"- {label} — {n} of {len(in_month)} days with entries")
    lines += ["## Weeks", ""] + week_lines + [""]
    filled = [l for l in (_day_line(vault, d) for d in days) if l]
    if filled:
        lines += ["## Days", ""] + filled + [""]
    lines += [f"{len(filled)} of {len(days)} days with entries.", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _write_if_changed(vault: Path, target: Path, text: str, lock_timeout: float) -> bool:
    with vault_mutex(vault, timeout=lock_timeout):
        try:
            current = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current = None
        if current == text:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, text)
        return True


def week_review(vault: "Path | str", year: int, week: int, *, lock_timeout: float = 10.0) -> "tuple[Path, bool] | None":
    root = cf.calendar_root(vault)
    if root is None:
        return None
    target = root / f"{year:04d}" / f"{year:04d}-W{week:02d}-review.md"
    changed = _write_if_changed(Path(vault), target, render_week(vault, year, week), lock_timeout)
    return target, changed


def month_review(vault: "Path | str", year: int, month: int, *, lock_timeout: float = 10.0) -> "tuple[Path, bool] | None":
    root = cf.calendar_root(vault)
    if root is None:
        return None
    target = root / f"{year:04d}" / f"{year:04d}-{month:02d}-review.md"
    changed = _write_if_changed(Path(vault), target, render_month(vault, year, month), lock_timeout)
    return target, changed


def catch_up(vault: "Path | str", *, today: "date | None" = None, weeks: int = 8, lock_timeout: float = 10.0) -> dict:
    """The cadence's one call: every closed ISO week in the last `weeks`
    weeks gets its review (written when missing or changed), and the running
    month and the one before get theirs — unconditionally, sparse or not."""
    today = today or date.today()
    written, refreshed = [], 0
    if cf.calendar_root(vault) is None:
        return {"written": [], "refreshed": 0, "skipped": "no Calendar/ space"}
    y, w, _ = today.isocalendar()
    cursor = date.fromisocalendar(y, w, 1)
    for i in range(1, weeks + 1):
        monday = cursor - timedelta(weeks=i)
        wy, ww, _ = monday.isocalendar()
        if week_days(wy, ww)[-1] >= today:
            continue                         # still open
        target, changed = week_review(vault, wy, ww, lock_timeout=lock_timeout)
        (written if changed else []).append(target.name)
        refreshed += 1
    prev = (today.replace(day=1) - timedelta(days=1))
    for (my, mm) in ((prev.year, prev.month), (today.year, today.month)):
        target, changed = month_review(vault, my, mm, lock_timeout=lock_timeout)
        (written if changed else []).append(target.name)
        refreshed += 1
    return {"written": written, "refreshed": refreshed}


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="the register's weekly and monthly reviews")
    ap.add_argument("--vault", required=True, help="the memory root (the directory holding memory/)")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--weeks", type=int, default=8)
    sub = ap.add_subparsers(dest="cmd")
    wk = sub.add_parser("week"); wk.add_argument("key", help="YYYY-Www")
    mo = sub.add_parser("month"); mo.add_argument("key", help="YYYY-MM")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "week":
        y, w = args.key.split("-W")
        out = week_review(args.vault, int(y), int(w))
    elif args.cmd == "month":
        y, m = args.key.split("-")
        out = month_review(args.vault, int(y), int(m))
    else:
        today = date.fromisoformat(args.today) if args.today else None
        print(catch_up(args.vault, today=today, weeks=args.weeks))
        return 0
    print(out if out else "no Calendar/ space")
    return 0


if __name__ == "__main__":
    sys.exit(main())
