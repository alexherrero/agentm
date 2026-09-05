#!/usr/bin/env python3
"""Corrections and the closed-day guard (filing v2 part 5, task 3).

A day before today is closed: an append is refused with the correction
command named. A correction is a new entry dated today in its own note,
carrying `supersedes:` back to the original — which the graph extractor
reads as a supersedes edge — and the original stays byte for byte, mtime
and all. Both days' indexes show the correction.
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
import graph  # noqa: E402

YESTERDAY = date(2026, 9, 3)
TODAY = date(2026, 9, 4)
THEN = datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 4, 9, 30, tzinfo=timezone.utc)


class _Rules:
    def facets(self):
        return ("meetings", "correspondence", "docs", "diary")


class _Nested(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="calendar-corr-"))
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        (self.top / ".obsidian").mkdir()
        self.vault = self.top / "Agent"
        (self.vault / "memory").mkdir(parents=True)
        (self.top / "Calendar").mkdir()
        self.rules = _Rules()
        # Yesterday's meeting, written yesterday.
        self.orig = cf.append(self.vault, "meetings", "Agreed the release date: Friday.", day=YESTERDAY, now=THEN, rules=self.rules).path

    def _files(self):
        return sorted(p.relative_to(self.top).as_posix() for p in (self.top / "Calendar").rglob("*.md"))


class TheClosedDayGuard(_Nested):
    def test_an_append_to_a_closed_day_is_refused_and_names_the_correction(self):
        with self.assertRaises(cf.ClosedDay) as cm:
            cf.append(self.vault, "meetings", "Actually Thursday.", day=YESTERDAY, now=NOW, rules=self.rules)
        self.assertIn("correct --facet meetings --day 2026-09-03", str(cm.exception))
        self.assertEqual(self.orig.read_text(encoding="utf-8").count("—"), 2)  # the heading dash + one entry

    def test_today_and_the_future_stay_open(self):
        cf.append(self.vault, "meetings", "Today's meeting.", day=TODAY, now=NOW, rules=self.rules)
        cf.append(self.vault, "docs", "Planned for tomorrow.", day=date(2026, 9, 5), now=NOW, rules=self.rules)
        self.assertIn("Calendar/2026/2026-09-05-docs.md", self._files())


class TheCorrection(_Nested):
    def test_a_correction_is_a_new_dated_entry_and_the_original_is_byte_identical(self):
        before = self.orig.read_bytes(); mtime = self.orig.stat().st_mtime_ns
        r = cf.correct(self.vault, "meetings", YESTERDAY, "The release date is Thursday, not Friday.", now=NOW, rules=self.rules)
        self.assertTrue(r.created)
        self.assertEqual(r.rel, "Calendar/2026/2026-09-04-meetings-corrects-2026-09-03.md")
        self.assertEqual(self.orig.read_bytes(), before)
        self.assertEqual(self.orig.stat().st_mtime_ns, mtime)
        text = r.path.read_text(encoding="utf-8")
        for line in ("kind: calendar-facet", "day: 2026-09-04", "facet: meetings", "corrects: 2026-09-03",
                     "supersedes: Calendar/2026/2026-09-03-meetings.md", "tags: [calendar, meetings, correction]"):
            self.assertIn(line + "\n", text)
        self.assertTrue(text.endswith("09:30 — The release date is Thursday, not Friday.\n"), text)

    def test_the_graph_extractor_sees_the_supersedes_edge(self):
        r = cf.correct(self.vault, "meetings", YESTERDAY, "Thursday.", now=NOW, rules=self.rules)
        edges = graph.extract_edges(r.rel, r.path.read_text(encoding="utf-8"))
        sup = [e for e in edges if e.edge_type == "supersedes"]
        self.assertTrue(sup, [e.edge_type for e in edges])
        self.assertTrue(any("2026-09-03-meetings" in e.target for e in sup), [e.target for e in sup])

    def test_both_days_indexes_show_the_correction(self):
        cf.correct(self.vault, "meetings", YESTERDAY, "Thursday.", now=NOW, rules=self.rules)
        y = (self.top / "Calendar" / "2026" / "2026-09-03.md").read_text(encoding="utf-8")
        t = (self.top / "Calendar" / "2026" / "2026-09-04.md").read_text(encoding="utf-8")
        self.assertIn("## Corrected later\n\n- [[2026-09-04-meetings-corrects-2026-09-03]] (meetings)\n", y)
        self.assertIn("## Corrections made today\n\n- [[2026-09-04-meetings-corrects-2026-09-03]] — corrects 2026-09-03 (meetings): Thursday.\n", t)

    def test_a_second_correction_the_same_day_appends_to_the_same_note(self):
        first = cf.correct(self.vault, "meetings", YESTERDAY, "Thursday.", now=NOW, rules=self.rules)
        again = cf.correct(self.vault, "meetings", YESTERDAY, "Thursday at ten.", now=NOW, rules=self.rules)
        self.assertFalse(again.created)
        self.assertEqual(again.path, first.path)
        self.assertEqual(len([l for l in again.path.read_text(encoding="utf-8").splitlines() if l.startswith("09:30 — ")]), 2)

    def test_an_open_day_is_appended_not_corrected(self):
        cf.append(self.vault, "meetings", "Today.", day=TODAY, now=NOW, rules=self.rules)
        with self.assertRaises(ValueError) as cm:
            cf.correct(self.vault, "meetings", TODAY, "x", now=NOW, rules=self.rules)
        self.assertIn("still open", str(cm.exception))

    def test_nothing_to_correct_is_said_plainly(self):
        with self.assertRaises(FileNotFoundError):
            cf.correct(self.vault, "docs", YESTERDAY, "x", now=NOW, rules=self.rules)
        self.assertEqual([f for f in self._files() if "corrects" in f], [])


if __name__ == "__main__":
    unittest.main()
