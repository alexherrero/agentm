#!/usr/bin/env python3
"""The calendar's facet writers (filing v2 part 5, task 1).

A facet note exists only on a day that had content for it; a note is created
lazily with its year directory; an append is a new paragraph and nothing
earlier changes; the operator's quick capture lands in the diary; a facet the
contract does not register is refused with the registry named; and the space
is discovered at the vault root through the Obsidian witness, never conjured.
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

NOON = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 4, 14, 37, tzinfo=timezone.utc)
DAY = date(2026, 9, 4)


class _Rules:
    def facets(self):
        return ("meetings", "correspondence", "docs", "diary")


class _Nested(unittest.TestCase):
    """The operator's layout: `<vault>/.obsidian`, the memory root at `<vault>/Agent`,
    the register at `<vault>/Calendar`."""

    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="calendar-"))
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        (self.top / ".obsidian").mkdir()
        self.vault = self.top / "Agent"
        (self.vault / "memory").mkdir(parents=True)
        (self.top / "Calendar").mkdir()
        self.rules = _Rules()

    def _files(self):
        return sorted(p.relative_to(self.top).as_posix() for p in (self.top / "Calendar").rglob("*.md"))


class TheSpace(_Nested):
    def test_the_register_is_the_vault_root_sibling_under_the_witness(self):
        self.assertEqual(cf.calendar_root(self.vault), self.top / "Calendar")
        self.assertEqual(cf.vault_root_of(self.vault), self.top)

    def test_a_flat_layout_finds_the_register_beside_memory(self):
        flat = Path(tempfile.mkdtemp(prefix="calendar-flat-"))
        self.addCleanup(shutil.rmtree, flat, ignore_errors=True)
        (flat / "memory").mkdir(); (flat / "Calendar").mkdir()
        self.assertEqual(cf.calendar_root(flat), flat / "Calendar")

    def test_the_register_is_never_conjured(self):
        shutil.rmtree(self.top / "Calendar")
        self.assertIsNone(cf.calendar_root(self.vault))
        with self.assertRaises(FileNotFoundError):
            cf.append(self.vault, "meetings", "a meeting", day=DAY, now=NOON, rules=self.rules)
        self.assertFalse((self.top / "Calendar").exists())

    def test_without_the_witness_a_parent_calendar_is_not_ours(self):
        (self.top / ".obsidian").rmdir()
        self.assertIsNone(cf.calendar_root(self.vault))


class TheFacetNotes(_Nested):
    def test_a_one_meeting_day_yields_exactly_the_meetings_note(self):
        r = cf.append(self.vault, "meetings", "Sync with the team about the release.", day=DAY, now=NOON, rules=self.rules)
        self.assertTrue(r.created)
        self.assertEqual(r.rel, "Calendar/2026/2026-09-04-meetings.md")
        self.assertEqual(self._files(), ["Calendar/2026/2026-09-04-meetings.md"])
        text = r.path.read_text(encoding="utf-8")
        for line in ("kind: calendar-facet", "status: active", "slug: 2026-09-04-meetings",
                     "day: 2026-09-04", "facet: meetings", "group: calendar", "tags: [calendar, meetings]"):
            self.assertIn(line + "\n", text)
        self.assertIn("# 2026-09-04 — meetings\n", text)
        self.assertTrue(text.endswith("12:00 — Sync with the team about the release.\n"), text)

    def test_an_append_is_a_new_paragraph_and_nothing_earlier_changes(self):
        first = cf.append(self.vault, "meetings", "First meeting.", day=DAY, now=NOON, rules=self.rules)
        before = first.path.read_text(encoding="utf-8")
        second = cf.append(self.vault, "meetings", "Second meeting,\nspanning two lines.", day=DAY, now=LATER, rules=self.rules)
        self.assertFalse(second.created)
        self.assertEqual(second.path, first.path)
        after = second.path.read_text(encoding="utf-8")
        self.assertTrue(after.startswith(before), "the earlier text must be byte-identical")
        self.assertEqual(after[len(before):], "\n14:37 — Second meeting, spanning two lines.\n")
        self.assertEqual(self._files(), ["Calendar/2026/2026-09-04-meetings.md"])

    def test_quick_capture_lands_in_todays_diary(self):
        r = cf.quick(self.vault, "Realised the purge count had drifted.", now=NOON, rules=self.rules)
        self.assertEqual(r.facet, "diary")
        self.assertEqual(self._files(), ["Calendar/2026/2026-09-04-diary.md"])

    def test_an_unregistered_facet_is_refused_with_the_registry_named(self):
        with self.assertRaises(cf.UnknownFacet) as cm:
            cf.append(self.vault, "errands", "buy milk", day=DAY, now=NOON, rules=self.rules)
        self.assertIn("meetings, correspondence, docs, diary", str(cm.exception))
        self.assertIn("storage-rules.md", str(cm.exception))
        self.assertEqual(self._files(), [])

    def test_empty_text_records_nothing(self):
        with self.assertRaises(ValueError):
            cf.append(self.vault, "diary", "   \n", day=DAY, now=NOON, rules=self.rules)
        self.assertEqual(self._files(), [])

    def test_notes_for_a_day_are_exactly_the_files_in_registry_order(self):
        cf.append(self.vault, "diary", "d", day=DAY, now=NOON, rules=self.rules)
        cf.append(self.vault, "meetings", "m", day=DAY, now=NOON, rules=self.rules)
        self.assertEqual([f for f, _ in cf.notes_for_day(self.vault, DAY)], ["meetings", "diary"])
        self.assertEqual(cf.notes_for_day(self.vault, date(2026, 9, 5)), [])

    def test_the_year_directory_is_created_lazily(self):
        self.assertFalse((self.top / "Calendar" / "2027").exists())
        cf.append(self.vault, "docs", "Shipped the thing.", day=date(2027, 1, 2), now=NOON, rules=self.rules)
        self.assertTrue((self.top / "Calendar" / "2027" / "2027-01-02-docs.md").is_file())


class TheRegistry(unittest.TestCase):
    def test_the_contract_is_the_source_and_the_fallback_is_the_ruled_four(self):
        self.assertEqual(cf.facets(_Rules()), ("meetings", "correspondence", "docs", "diary"))
        self.assertEqual(cf.DEFAULT_FACETS, ("meetings", "correspondence", "docs", "diary"))


if __name__ == "__main__":
    unittest.main()
