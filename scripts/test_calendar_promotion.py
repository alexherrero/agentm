#!/usr/bin/env python3
"""The facet-promotion trigger (filing v2 part 5, task 5): a diary label
recurring on three distinct days trips the suggestion, two does not, three
entries on one day do not, a registered facet is never re-proposed, and the
proposal is one line under `facets:` in the contract — staged for the
operator through the dreaming cycle's confirm flow, never applied on its own.
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
import calendar_promotion as cp  # noqa: E402
import dream  # noqa: E402
import dream_confirm  # noqa: E402

TODAY = date(2026, 9, 10)
CONTRACT = ("# Storage rules\n\n```storage-rules\nclasses:\n  semantic: x\n\nfacets:\n  - meetings\n  - correspondence\n"
            "  - docs\n  - diary\n\nthresholds:\n  low_confidence: 0.65\n```\n")


class _Rules:
    def __init__(self, src):
        self._src = src

    def facets(self):
        return ("meetings", "correspondence", "docs", "diary")

    def source(self):
        return str(self._src)


def _at(d: date, hour=9):
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc)


class _Nested(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="calendar-promo-"))
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        (self.top / ".obsidian").mkdir()
        self.vault = self.top / "Agent"
        (self.vault / "memory").mkdir(parents=True)
        (self.top / "Calendar").mkdir()
        (self.top / "standards").mkdir()
        self.contract = self.top / "standards" / "storage-rules.md"
        self.contract.write_text(CONTRACT, encoding="utf-8")
        self.rules = _Rules(self.contract)

    def _diary(self, day: date, text: str):
        cf.append(self.vault, "diary", text, day=day, now=_at(day), rules=self.rules)


class TheDetector(_Nested):
    def test_three_distinct_days_trip_the_suggestion(self):
        for i in (1, 3, 5):
            self._diary(TODAY - __import__("datetime").timedelta(days=i), "gym: 40 minutes on the rower")
        found = cp.detect(self.vault, today=TODAY, rules=self.rules)
        self.assertEqual([(s.label, s.entries, s.days) for s in found], [("gym", 3, 3)])
        self.assertEqual((found[0].first, found[0].last), ("2026-09-05", "2026-09-09"))

    def test_two_days_do_not(self):
        for i in (1, 3):
            self._diary(TODAY - __import__("datetime").timedelta(days=i), "gym: 40 minutes")
        self.assertEqual(cp.detect(self.vault, today=TODAY, rules=self.rules), [])

    def test_three_entries_on_one_day_do_not(self):
        d = TODAY - __import__("datetime").timedelta(days=1)
        for _ in range(3):
            self._diary(d, "gym: again")
        self.assertEqual(cp.detect(self.vault, today=TODAY, rules=self.rules), [])

    def test_a_registered_facet_is_never_re_proposed(self):
        for i in (1, 2, 3):
            self._diary(TODAY - __import__("datetime").timedelta(days=i), "meetings: another one")
        self.assertEqual(cp.detect(self.vault, today=TODAY, rules=self.rules), [])

    def test_a_sentence_is_not_a_label(self):
        for i in (1, 2, 3):
            self._diary(TODAY - __import__("datetime").timedelta(days=i), "I think the release went well today: no incidents")
        self.assertEqual(cp.detect(self.vault, today=TODAY, rules=self.rules), [])

    def test_outside_the_window_does_not_count(self):
        for i in (1, 2, 40):
            self._diary(TODAY - __import__("datetime").timedelta(days=i), "gym: x")
        self.assertEqual(cp.detect(self.vault, today=TODAY, rules=self.rules), [])


class TheProposal(_Nested):
    def test_the_proposal_adds_one_line_under_facets_and_nothing_else(self):
        new = cp.proposal_text(CONTRACT, "gym")
        self.assertIsNotNone(new)
        self.assertEqual(new.replace("  - diary\n  - gym\n", "  - diary\n"), CONTRACT)
        self.assertIsNone(cp.proposal_text(new, "gym"), "an already-registered label yields no proposal")
        self.assertIsNone(cp.proposal_text("no facets block here\n", "gym"))

    def test_proposals_name_the_contract_and_its_new_text(self):
        for i in (1, 3, 5):
            self._diary(TODAY - __import__("datetime").timedelta(days=i), "gym: 40 minutes")
        out = cp.proposals(self.vault, today=TODAY, rules=self.rules)
        self.assertEqual(len(out), 1)
        label, s, path, new_text = out[0]
        self.assertEqual((label, path), ("gym", self.contract))
        self.assertIn("  - gym\n", new_text)
        self.assertEqual(self.contract.read_text(encoding="utf-8"), CONTRACT, "nothing is applied by proposing")


class TheDreamStage(_Nested):
    def test_the_stage_is_a_proposal_and_never_auto_applies(self):
        self.assertNotIn("facet_promotion", dream_confirm.AUTO_APPLY_STAGES)
        for i in (1, 3, 5):
            self._diary(TODAY - __import__("datetime").timedelta(days=i), "gym: 40 minutes")
        props = dream._stage_facet_promotion(self.vault, today=TODAY, rules=self.rules)
        self.assertEqual(len(props), 1)
        p = props[0]
        self.assertEqual((p.stage, p.kind), ("facet_promotion", "promote-facet"))
        self.assertIn("gym", p.summary)
        self.assertIn("3 days", p.summary)
        self.assertEqual(p.paths, [str(self.contract)])
        (path, content), = p.mutations
        self.assertEqual(Path(path), self.contract)
        self.assertIn("  - gym\n", content)
        self.assertEqual(self.contract.read_text(encoding="utf-8"), CONTRACT)


if __name__ == "__main__":
    unittest.main()
