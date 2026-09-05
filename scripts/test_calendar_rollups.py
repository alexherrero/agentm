#!/usr/bin/env python3
"""The rollups (filing v2 part 5, task 4): a closed week gets its review on
the cadence with no operator action, a sparse week reads sparse rather than
padded, the running month is refreshed as days accrue, and an unchanged
period regenerates to the same bytes.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import calendar_facets as cf  # noqa: E402
import calendar_rollups as cr  # noqa: E402


class _Rules:
    def facets(self):
        return ("meetings", "correspondence", "docs", "diary")


def _at(d: date, hour=10):
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc)


class _Nested(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="calendar-rollups-"))
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        (self.top / ".obsidian").mkdir()
        self.vault = self.top / "Agent"
        (self.vault / "memory").mkdir(parents=True)
        (self.top / "Calendar").mkdir()
        self.rules = _Rules()
        # ISO week 2026-W36 runs Mon 2026-08-31 .. Sun 2026-09-06. Two days had entries.
        self.tue = date(2026, 9, 1); self.fri = date(2026, 9, 4)
        cf.append(self.vault, "meetings", "Standup.", day=self.tue, now=_at(self.tue), rules=self.rules)
        cf.append(self.vault, "meetings", "Planning.", day=self.tue, now=_at(self.tue, 14), rules=self.rules)
        cf.append(self.vault, "diary", "Shipped the release.", day=self.fri, now=_at(self.fri), rules=self.rules)

    def _read(self, name):
        return (self.top / "Calendar" / "2026" / name).read_text(encoding="utf-8")


class TheWeekReview(_Nested):
    def test_a_sparse_week_reads_sparse_not_padded(self):
        target, changed = cr.week_review(self.vault, 2026, 36)
        self.assertTrue(changed)
        text = self._read("2026-W36-review.md")
        self.assertIn("kind: calendar-review\n", text)
        self.assertIn("week: 2026-W36\n", text)
        self.assertIn("# Week 2026-W36 — 2026-08-31 to 2026-09-06\n", text)
        self.assertIn("- [[2026-09-01]] — meetings (2)\n", text)
        self.assertIn("- [[2026-09-04]] — diary (1)\n", text)
        self.assertIn("Nothing recorded on Mon, Wed, Thu, Sat, Sun.\n", text)
        self.assertIn("2 of 7 days with entries.\n", text)
        self.assertLess(len(text.splitlines()), 30, "a sparse week must not be padded")

    def test_an_empty_week_says_so_once(self):
        cr.week_review(self.vault, 2026, 30)
        text = self._read("2026-W30-review.md")
        self.assertIn("Nothing recorded this week.\n", text)
        self.assertIn("0 of 7 days with entries.\n", text)
        self.assertNotIn("## Days", text)

    def test_regeneration_is_a_no_op_on_an_unchanged_week(self):
        target, _ = cr.week_review(self.vault, 2026, 36)
        before = target.read_text(encoding="utf-8"); mtime = target.stat().st_mtime_ns
        target2, changed = cr.week_review(self.vault, 2026, 36)
        self.assertFalse(changed)
        self.assertEqual(target2.read_text(encoding="utf-8"), before)
        self.assertEqual(target2.stat().st_mtime_ns, mtime)

    def test_a_correction_made_in_the_week_is_listed(self):
        cf.correct(self.vault, "meetings", self.tue, "Planning was Wednesday.", now=_at(self.fri), rules=self.rules)
        cr.week_review(self.vault, 2026, 36)
        self.assertIn("## Corrections\n\n- [[2026-09-04-meetings-corrects-2026-09-01]] — corrects 2026-09-01 (meetings)\n", self._read("2026-W36-review.md"))


class TheCadence(_Nested):
    def test_catch_up_writes_closed_weeks_and_both_months_with_no_operator_action(self):
        out = cr.catch_up(self.vault, today=date(2026, 9, 8), weeks=3)
        names = sorted(p.name for p in (self.top / "Calendar" / "2026").glob("*-review.md"))
        self.assertIn("2026-W36-review.md", names)
        self.assertIn("2026-W35-review.md", names)       # closed, empty, still written
        self.assertNotIn("2026-W37-review.md", names)    # the running week is not reviewed yet
        self.assertIn("2026-09-review.md", names)
        self.assertIn("2026-08-review.md", names)
        self.assertEqual(out["refreshed"], 5)
        self.assertEqual(len(out["written"]), 5)
        again = cr.catch_up(self.vault, today=date(2026, 9, 8), weeks=3)
        self.assertEqual(again["written"], [], "a second cadence run rewrites nothing on an unchanged register")

    def test_the_month_review_links_the_week_reviews_that_exist(self):
        cr.catch_up(self.vault, today=date(2026, 9, 8), weeks=3)
        text = self._read("2026-09-review.md")
        self.assertIn("period: month\n", text)
        self.assertIn("- [[2026-W36-review]] — 2 of 6 days with entries\n", text)   # Sep 1–6 of W36
        self.assertIn("- 2026-W37 — 0 of 7 days with entries\n", text)              # not reviewed yet: no link
        self.assertIn("- [[2026-09-01]] — meetings (2)\n", text)
        self.assertIn("2 of 30 days with entries.\n", text)

    def test_no_register_means_nothing_written(self):
        shutil.rmtree(self.top / "Calendar")
        out = cr.catch_up(self.vault, today=date(2026, 9, 8))
        self.assertEqual(out["written"], [])
        self.assertFalse((self.top / "Calendar").exists())


if __name__ == "__main__":
    unittest.main()
