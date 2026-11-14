#!/usr/bin/env python3
"""A slide in search that no single night proves (task 205).

The nightly regression gate compares tonight's hit rate with a pinned baseline
through the exact paired test, and fails only when the drop is significant at
p < 0.05. With sixty questions, six net flips are the smallest drop it can
see. From late October to mid-November 2026 the hit rate went from 42 of 63
to 35 of 60 while the gate's p-value drifted from about 0.5 to 0.09 and never
crossed the line. Every night passed. Nothing warned.

This is the warning that was missing. It reads the scorecard's own nightly
readings from its history, averages them by ISO week, and says when the
weekly rate has been falling. It is advisory: it never changes the gate's
verdict, never re-pins the baseline, and never touches the registered test.

Two rules, either one warns:

  - **a run of falls:** the weekly rate fell in each of the last
    `SLIDE_STEPS` week-to-week steps;
  - **a drop from the best:** the newest week is `SLIDE_DROP_POINTS` or more
    percentage points under the best week of the last `SLIDE_WEEKS`.

Rates, not counts, so retiring a question from the gold set does not read as
a slide. Only live readings count: a scorecard row is live when it says it
was "measured <date> on <N> documents". Before 2026-10-31 the row reprinted
the pinned baseline instead, and a reprint is not a reading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

SLIDE_STEPS = 3
SLIDE_DROP_POINTS = 5.0
SLIDE_WEEKS = 8

# The scorecard's gold-set row, as it renders: label, value, then the note,
# which opens with "N of M scored questions" and, on a live reading,
# continues "measured YYYY-MM-DD on N documents".
_ROW_RE = re.compile(
    r"^\|\s*gold-set R@\d+\s*\|\s*[\d.]+\s*\|\s*(\d+) of (\d+) scored questions"
    r"(?P<rest>[^\n]*)$", re.M)
_LIVE_RE = re.compile(r"measured \d{4}-\d{2}-\d{2} on \d+ documents")


@dataclass
class Night:
    day: str
    hits: int
    scored: int

    @property
    def rate(self) -> float:
        return self.hits / self.scored


@dataclass
class Week:
    key: str          # ISO week, "2026-W45"
    rate: float       # mean of its nights' rates
    nights: int


@dataclass
class Trend:
    weeks: list = field(default_factory=list)
    sliding: bool = False
    reasons: list = field(default_factory=list)


def reading_of(text: str) -> "Optional[tuple]":
    """(hits, scored) from a scorecard's text when its gold-set row is a live
    reading; None for a dash, a reprinted pin, or no row."""
    m = _ROW_RE.search(text or "")
    if not m or not _LIVE_RE.search(m.group("rest")):
        return None
    hits, scored = int(m.group(1)), int(m.group(2))
    return (hits, scored) if scored > 0 else None


def nights_from(days_and_texts) -> list:
    """Every live night from (day, scorecard text) pairs, oldest first; a
    later text for the same day replaces an earlier one."""
    by_day = {}
    for day, text in days_and_texts:
        got = reading_of(text)
        if got:
            by_day[str(day)] = Night(str(day), *got)
    return [by_day[d] for d in sorted(by_day)]


def _week_key(day: str) -> str:
    y, w, _ = date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def weekly(nights: list) -> list:
    """The nights averaged by ISO week, oldest first."""
    groups: dict = {}
    for n in nights:
        groups.setdefault(_week_key(n.day), []).append(n.rate)
    return [Week(k, sum(v) / len(v), len(v)) for k, v in sorted(groups.items())]


def trend(nights: list, *, steps: int = SLIDE_STEPS, drop_points: float = SLIDE_DROP_POINTS,
          window: int = SLIDE_WEEKS) -> Trend:
    """Whether the weekly hit rate is sliding, and why."""
    weeks = weekly(nights)[-window:]
    out = Trend(weeks=weeks)
    if len(weeks) < 2:
        return out
    rates = [w.rate for w in weeks]
    tail = rates[-(steps + 1):]
    if len(tail) == steps + 1 and all(b < a for a, b in zip(tail, tail[1:])):
        out.sliding = True
        out.reasons.append(f"fell {steps} weeks running")
    best = max(range(len(weeks)), key=lambda i: rates[i])
    # Rounded before the comparison: 70% minus 65% is 4.9999… in floating
    # point, and a drop of exactly the line must reach it.
    gap = round((rates[best] - rates[-1]) * 100, 6)
    if gap >= drop_points:
        out.sliding = True
        out.reasons.append(f"{gap:.1f} points under the best week, {weeks[best].key}")
    return out
