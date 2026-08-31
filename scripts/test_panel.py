#!/usr/bin/env python3
"""The panel's arithmetic, and the places it must refuse to decide.

The failure this guards is a coin-flip wearing a decision's clothes. A three-way
split, a two-way tie, a single grader answering alone — each of those has to
come back visibly unresolved rather than as a label with a confident basis
string attached.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))

import panel  # noqa: E402


class TheCoalescing(unittest.TestCase):
    def test_unanimity_is_named_as_such(self):
        got = panel.coalesce({"claude": "sufficient", "gemini": "sufficient",
                              "fable": "sufficient"})
        self.assertEqual(got["label"], "sufficient")
        self.assertTrue(got["unanimous"])
        self.assertIn("all 3", got["basis"])

    def test_a_majority_names_its_dissent(self):
        got = panel.coalesce({"claude": "insufficient", "gemini": "sufficient",
                              "fable": "insufficient"})
        self.assertEqual(got["label"], "insufficient")
        self.assertFalse(got.get("unanimous"))
        self.assertIn("gemini=sufficient", got["basis"])

    def test_a_two_way_tie_is_not_decided(self):
        # Two graders, two answers. Picking one would be a coin flip with a
        # basis string attached, which is the exact failure this arc keeps
        # finding in its own work.
        got = panel.coalesce({"claude": "sufficient", "gemini": "insufficient"})
        self.assertIsNone(got["label"])
        self.assertTrue(got["contested"])

    def test_a_three_way_split_is_not_decided(self):
        got = panel.coalesce({"claude": "sufficient", "gemini": "insufficient",
                              "fable": "n/a"})
        self.assertIsNone(got["label"])
        self.assertTrue(got["contested"])

    def test_one_grader_alone_is_labelled_but_says_so(self):
        got = panel.coalesce({"claude": "sufficient"})
        self.assertEqual(got["label"], "sufficient")
        self.assertIn("only claude", got["basis"])
        self.assertFalse(got.get("unanimous"))

    def test_no_grader_is_no_label(self):
        got = panel.coalesce({})
        self.assertIsNone(got["label"])
        self.assertIn("no grader", got["basis"])


class TheTargeting(unittest.TestCase):
    def _rows(self, n_agree, n_differ):
        rows = [{"id": f"a{i}", "claude": "n/a", "gemini": "n/a"}
                for i in range(n_agree)]
        rows += [{"id": f"d{i}", "claude": "sufficient",
                  "gemini": "insufficient"} for i in range(n_differ)]
        return rows

    def test_it_finds_every_contested_turn(self):
        rows = self._rows(30, 7)
        self.assertEqual(len(panel.contested(rows)), 7)

    def test_the_control_samples_the_agreements_not_the_conflicts(self):
        # The point of the control: two models of one lineage agreeing is what
        # a shared blind spot looks like, and a tiebreaker never sees the cases
        # nobody tied on.
        rows = self._rows(40, 7)
        ctrl = panel.control(rows, n=10)
        self.assertEqual(len(ctrl), 10)
        self.assertTrue(all(i.startswith("a") for i in ctrl))
        self.assertEqual(set(ctrl) & set(panel.contested(rows)), set())

    def test_the_control_is_reproducible(self):
        rows = self._rows(40, 7)
        self.assertEqual(panel.control(rows, n=10), panel.control(rows, n=10))

    def test_the_control_does_not_depend_on_input_order(self):
        rows = self._rows(40, 7)
        self.assertEqual(panel.control(rows, n=10),
                         panel.control(list(reversed(rows)), n=10))

    def test_a_small_pool_does_not_over_draw(self):
        self.assertEqual(len(panel.control(self._rows(3, 2), n=10)), 3)


class TheSummary(unittest.TestCase):
    def test_it_counts_what_the_panel_could_not_settle(self):
        rows = [{"id": "1", "claude": "sufficient", "gemini": "sufficient"},
                {"id": "2", "claude": "sufficient", "gemini": "insufficient"}]
        got = panel.summarise(rows)
        self.assertEqual(got["labelled"], 1)
        self.assertEqual(got["contested_no_majority"], 1)

    def test_the_judges_match_rate_is_not_called_accuracy(self):
        # The panel contains the judge and is two-thirds its own family. A
        # reader meeting this number must not take it for correctness.
        rows = [{"id": "1", "claude": "sufficient", "gemini": "sufficient"}]
        got = panel.summarise(rows)
        self.assertIn("not accuracy", got["production_judge_note"])
        self.assertIn("same model family", got["production_judge_note"])

    def test_it_reports_each_graders_own_distribution(self):
        rows = [{"id": "1", "claude": "n/a", "gemini": "sufficient"},
                {"id": "2", "claude": "n/a", "gemini": "n/a"}]
        got = panel.summarise(rows)
        self.assertEqual(got["per_grader"]["claude"]["n/a"], 2)
        self.assertEqual(got["per_grader"]["gemini"]["sufficient"], 1)


class TheDisagreementReport(unittest.TestCase):
    def test_unsettled_turns_come_before_settled_ones(self):
        rows = [
            {"id": "majority", "claude": "sufficient", "gemini": "insufficient",
             "fable": "sufficient"},
            {"id": "tied", "claude": "sufficient", "gemini": "insufficient"},
        ]
        got = panel.disagreement_report(rows)
        self.assertEqual(got[0]["id"], "tied")

    def test_agreed_turns_are_not_in_it(self):
        rows = [{"id": "x", "claude": "n/a", "gemini": "n/a", "fable": "n/a"}]
        self.assertEqual(panel.disagreement_report(rows), [])

    def test_the_widest_split_ranks_first_among_settled(self):
        rows = [
            {"id": "narrow", "claude": "insufficient", "gemini": "n/a",
             "fable": "insufficient"},
            {"id": "wide", "claude": "sufficient", "gemini": "n/a",
             "fable": "sufficient"},
        ]
        got = [g["id"] for g in panel.disagreement_report(rows)]
        self.assertEqual(got[0], "wide")


if __name__ == "__main__":
    unittest.main()
