#!/usr/bin/env python3
"""volume_gate.py — the capture-volume gate and the writes-per-day reading.

Filing v2, the write path (task 4). The predecessor inbox failed by
over-capture outrunning judgment — 2,652 notes, 61% expired, an auto-miner
writing thirteen copies of a candidate — and dispersing captures into six
class directories removes the pile that made that flood visible. So capture
volume is gated on its own, independent of where a note files, and reported
on the daily scorecard as writes per day with a week-over-week trend: the
alarm that replaces the pile.

The cap is the contract's `thresholds.daily_write_cap` — an edit to
`standards/storage-rules.md`, live at the next write; `0` disables the gate.
`AGENTM_DAILY_WRITE_CAP` overrides it for a test or an emergency. The count is
the corpus itself — every memory whose `captured` (else `created`) date is
today — so the gate cannot drift from the truth and needs no ledger. A
refusal is loud: a named error every writer relays verbatim, so a flood is
caught at the door rather than discovered in the corpus.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from filing_engine import _frontmatter  # noqa: E402  (same skill dir)

# The default when the contract does not name a cap. Grounded in the live
# corpus on 2026-09-04: over the previous 30 days the busiest day wrote 110
# memories (a migration day), the 90th percentile 47, the median 12; the
# all-time busiest day 110. Two hundred sits above every real day on record
# and well below what the last flood did.
DEFAULT_CAP = 200
THRESHOLD_KEY = "daily_write_cap"
ENV_CAP = "AGENTM_DAILY_WRITE_CAP"
CLASS_DIRS = ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs")


class VolumeCapRefused(RuntimeError):
    """A write refused at the volume gate. The message names the count, the
    cap, and the edit that raises it."""


def daily_cap(rules=None) -> "int | None":
    """The cap in force: the environment override, else the contract's
    threshold, else the default. None means the gate is disabled."""
    raw = os.environ.get(ENV_CAP)
    if raw is not None and raw.strip() != "":
        try:
            cap = int(float(raw))
        except ValueError:
            cap = DEFAULT_CAP
        return cap if cap > 0 else None
    try:
        import storage_rules  # same skill dir
        rules = rules or storage_rules.rules()
        value = rules.thresholds().get(THRESHOLD_KEY)
    except Exception:
        value = None
    if value is None:
        return DEFAULT_CAP
    try:
        cap = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_CAP
    return cap if cap > 0 else None


def _memory_notes(vault: Path):
    mem = Path(vault) / "memory"
    for cls in CLASS_DIRS:
        d = mem / cls
        if not d.is_dir():
            continue
        for p in d.rglob("*.md"):
            if p.name == "_index.md" or p.name.startswith("Icon"):
                continue
            yield p


def _note_day(p: Path) -> str:
    try:
        with p.open(encoding="utf-8", errors="replace") as fh:
            head = fh.read(2000)
    except OSError:
        return ""
    fm, _ = _frontmatter(head if "\n---" in head[4:] else head + "\n---\n")
    return (fm.get("captured") or fm.get("created") or "")[:10]


def writes_by_day(vault: "Path | str", *, days: int = 14, today: "date | None" = None) -> list:
    """[(YYYY-MM-DD, count)] for the last `days` days, oldest first, zero-filled.
    A memory counts on the day it was captured (its `captured` stamp, else
    `created`), wherever it files and whatever it has become since."""
    today = today or date.today()
    window = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    counts = {d: 0 for d in window}
    for p in _memory_notes(Path(vault)):
        day = _note_day(p)
        if day in counts:
            counts[day] += 1
    return [(d, counts[d]) for d in window]


def today_count(vault: "Path | str", *, today: "date | None" = None) -> int:
    today = today or date.today()
    key = today.isoformat()
    return sum(1 for p in _memory_notes(Path(vault)) if _note_day(p) == key)


def check(vault: "Path | str", *, today: "date | None" = None, cap: "int | None" = None,
          rules=None) -> int:
    """The number of memories written today, or a `VolumeCapRefused` when one
    more would pass the cap. Called by the writer before every add."""
    today = today or date.today()
    cap = daily_cap(rules) if cap is None else (cap if cap > 0 else None)
    count = today_count(vault, today=today)
    if cap is not None and count >= cap:
        raise VolumeCapRefused(
            f"capture refused: {count} memories already written today ({today.isoformat()}) "
            f"and the daily cap is {cap} — the volume gate (filing v2) stops a flood at the "
            f"door. If today is real, raise `thresholds.{THRESHOLD_KEY}` in "
            f"standards/storage-rules.md; it is live at the next write."
        )
    return count


def trend(vault: "Path | str", *, today: "date | None" = None, rules=None) -> dict:
    """Today, the last seven days against the seven before, the fortnight's
    peak, and the headroom under the cap — the scorecard's line."""
    today = today or date.today()
    by_day = writes_by_day(vault, days=14, today=today)
    week = sum(n for _, n in by_day[7:])
    previous = sum(n for _, n in by_day[:7])
    change = None if previous == 0 else round((week - previous) * 100.0 / previous)
    cap = daily_cap(rules)
    today_n = by_day[-1][1]
    return {
        "today": today_n,
        "by_day": by_day,
        "week": week,
        "previous_week": previous,
        "change_pct": change,
        "peak": max(n for _, n in by_day),
        "cap": cap,
        "headroom": None if cap is None else max(cap - today_n, 0),
    }


def describe(t: dict) -> str:
    """The trend as one scorecard note."""
    mean = t["week"] / 7.0
    if t["change_pct"] is None:
        delta = "no previous week to compare"
    else:
        delta = f"{'+' if t['change_pct'] >= 0 else ''}{t['change_pct']}% week over week"
    cap = "gate disabled" if t["cap"] is None else f"cap {t['cap']} (headroom {t['headroom']})"
    return (f"7-day mean {mean:.1f} · week {t['week']} vs previous {t['previous_week']} ({delta}) "
            f"· fortnight peak {t['peak']} · {cap}")


def main(argv: "list | None" = None) -> int:
    import argparse
    import json
    p = argparse.ArgumentParser(description="the capture-volume gate and the writes-per-day reading")
    p.add_argument("--vault", required=True, help="the memory root (the directory holding memory/)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    t = trend(Path(args.vault))
    if args.json:
        print(json.dumps(t, indent=2))
    else:
        print(f"writes today: {t['today']} — {describe(t)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
