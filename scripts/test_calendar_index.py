#!/usr/bin/env python3
"""The generated day index (filing v2 part 5, task 2): it lists exactly what
exists — the facet notes with a context phrase, the day's episodic traces,
the digest — regenerates to the same bytes on an unchanged day, writes
nothing for an empty day, and follows every append.
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
import calendar_index as ci  # noqa: E402

NOON = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
DAY = date(2026, 9, 4)


class _Rules:
    def facets(self):
        return ("meetings", "correspondence", "docs", "diary")


class _Nested(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="calendar-index-"))
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        (self.top / ".obsidian").mkdir()
        self.vault = self.top / "Agent"
        (self.vault / "memory" / "episodic").mkdir(parents=True)
        (self.top / "Calendar").mkdir()
        self.rules = _Rules()

    def _files(self):
        return sorted(p.relative_to(self.top).as_posix() for p in (self.top / "Calendar").rglob("*.md"))

    def _index(self):
        return (self.top / "Calendar" / "2026" / "2026-09-04.md").read_text(encoding="utf-8")


class TheDayIndex(_Nested):
    def test_a_one_meeting_day_yields_exactly_two_files(self):
        cf.append(self.vault, "meetings", "Sync with the team about the release.", day=DAY, now=NOON, rules=self.rules)
        self.assertEqual(self._files(), ["Calendar/2026/2026-09-04-meetings.md", "Calendar/2026/2026-09-04.md"])
        text = self._index()
        self.assertIn("kind: day-index\n", text)
        self.assertIn("slug: 2026-09-04\n", text)
        self.assertIn("## meetings\n", text)
        self.assertIn("- [[2026-09-04-meetings]] — Sync with the team about the release. (1 entry)\n", text)
        self.assertNotIn("## diary", text)
        self.assertNotIn("## Session traces", text)
        self.assertNotIn("## Digest", text)

    def test_an_empty_day_has_no_index(self):
        self.assertIsNone(ci.regenerate(self.vault, date(2026, 9, 5)))
        self.assertEqual(self._files(), [])

    def test_regeneration_is_a_no_op_on_an_unchanged_day(self):
        cf.append(self.vault, "diary", "A line.", day=DAY, now=NOON, rules=self.rules)
        p = self.top / "Calendar" / "2026" / "2026-09-04.md"
        before = p.read_text(encoding="utf-8"); mtime = p.stat().st_mtime_ns
        ci.regenerate(self.vault, DAY)
        self.assertEqual(p.read_text(encoding="utf-8"), before)
        self.assertEqual(p.stat().st_mtime_ns, mtime, "an unchanged day must not be rewritten")

    def test_an_append_updates_the_phrase_count_in_registry_order(self):
        cf.append(self.vault, "diary", "First.", day=DAY, now=NOON, rules=self.rules)
        cf.append(self.vault, "meetings", "A meeting.", day=DAY, now=NOON, rules=self.rules)
        cf.append(self.vault, "diary", "Second.", day=DAY, now=NOON, rules=self.rules)
        text = self._index()
        self.assertLess(text.index("## meetings"), text.index("## diary"))
        self.assertIn("- [[2026-09-04-diary]] — First. (2 entries)\n", text)

    def test_the_context_phrase_is_cut_on_a_word_boundary(self):
        cf.append(self.vault, "docs", "word " * 60, day=DAY, now=NOON, rules=self.rules)
        text = self._index()
        line = next(l for l in text.splitlines() if l.startswith("- [[2026-09-04-docs]]"))
        self.assertIn(" …", line)
        self.assertLess(len(line), 200)

    def test_the_days_episodic_traces_are_linked(self):
        ep = self.vault / "memory" / "episodic"
        (ep / "_index.md").write_text("---\nkind: dir-index\n---\n", encoding="utf-8")
        (ep / "session-abc.md").write_text("---\ntype: trace\nstatus: active\nday: 2026-09-04\nslug: session-abc\ntitle: the release session\n---\n\nwhat happened\n", encoding="utf-8")
        (ep / "session-old.md").write_text("---\ntype: trace\nstatus: active\ncreated: 2026-09-01\nslug: session-old\n---\n\nold\n", encoding="utf-8")
        ci.regenerate(self.vault, DAY)
        text = self._index()
        self.assertIn("## Session traces\n", text)
        self.assertIn("- [[session-abc]] — the release session\n", text)
        self.assertNotIn("session-old", text)
        self.assertEqual(self._files(), ["Calendar/2026/2026-09-04.md"])

    def test_the_digest_is_embedded_vault_root_relative_when_it_exists(self):
        dg = self.vault / "diagnostics" / "digests"; dg.mkdir(parents=True)
        (dg / "20260904-digest-daily.md").write_text("# digest\n", encoding="utf-8")
        cf.append(self.vault, "diary", "A line.", day=DAY, now=NOON, rules=self.rules)
        text = self._index()
        self.assertIn("## Digest\n\n![[Agent/diagnostics/digests/20260904-digest-daily]]\n", text)

    def test_the_index_never_lists_a_facet_that_has_no_note(self):
        cf.append(self.vault, "correspondence", "Replied to the vendor.", day=DAY, now=NOON, rules=self.rules)
        text = self._index()
        for absent in ("## meetings", "## docs", "## diary"):
            self.assertNotIn(absent, text)


if __name__ == "__main__":
    unittest.main()
