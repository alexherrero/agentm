#!/usr/bin/env python3
"""Tests for the completeness grader's plumbing.

Everything here stubs the model, so these check arithmetic, parsing and
aggregation — not whether the judge is any good. That question cannot be answered
by a test that supplies its own answers, and it is answered instead by the live
gutted-note check recorded in `scripts/health/fixtures/completeness-v1/`.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "health"))

import completeness_grade as cg  # noqa: E402


def pair(n=4, rel="m/a.md", cls="fact"):
    return {"rel": rel, "class": cls, "rewrite": "whatever",
            "claims": [f"claim number {i} says a thing" for i in range(1, n + 1)]}


def replying(*texts):
    """A caller that returns each text in turn, then repeats the last."""
    seq = list(texts)

    def caller(_prompt):
        return seq.pop(0) if len(seq) > 1 else seq[0]
    return caller


class ParseTests(unittest.TestCase):
    def test_an_empty_list_is_an_answer(self):
        self.assertEqual(cg.parse_kept('{"kept": []}', 4), set())

    def test_a_malformed_reply_is_not_an_answer(self):
        # None and set() mean opposite things: "the judge did not say" versus
        # "the rewrite kept nothing". Collapsing them scores a broken call as
        # total loss, which is how an outage becomes a corpus-quality finding.
        self.assertIsNone(cg.parse_kept("I think it kept most of them", 4))
        self.assertIsNone(cg.parse_kept("", 4))
        self.assertIsNone(cg.parse_kept('{"kept": "one and two"}', 4))

    def test_prose_around_the_json_is_tolerated(self):
        self.assertEqual(cg.parse_kept('Sure!\n{"kept": [1, 3]}\n', 4), {1, 3})

    def test_a_number_outside_the_claims_is_dropped(self):
        self.assertEqual(cg.parse_kept('{"kept": [1, 9, 0, -2]}', 4), {1})

    def test_a_boolean_is_not_a_claim_number(self):
        # `True == 1` in Python, so an unguarded membership test scores a reply
        # of [true, true] as claim 1 surviving.
        self.assertEqual(cg.parse_kept('{"kept": [true, false]}', 4), set())


class GradeTests(unittest.TestCase):
    def test_coverage_is_kept_over_total(self):
        got = cg.grade_pair(pair(4), replicates=1,
                            caller=replying('{"kept": [1, 2, 3]}'))
        self.assertEqual(got["coverage"], 0.75)

    def test_the_median_of_the_replicates_is_reported(self):
        got = cg.grade_pair(pair(4), replicates=3, caller=replying(
            '{"kept": [1]}', '{"kept": [1, 2]}', '{"kept": [1, 2, 3, 4]}'))
        self.assertEqual(got["coverage"], 0.50)
        self.assertEqual(got["spread"], 0.75)
        self.assertEqual(got["replicates"], 3)

    def test_a_failed_call_is_excluded_not_scored_zero(self):
        got = cg.grade_pair(pair(4), replicates=3, caller=replying(
            "the judge fell over", '{"kept": [1, 2, 3, 4]}',
            '{"kept": [1, 2, 3, 4]}'))
        self.assertEqual(got["failures"], 1)
        self.assertEqual(got["replicates"], 2)
        self.assertEqual(got["coverage"], 1.0,
                         "a failed call was averaged in as a zero")

    def test_a_note_with_no_answer_at_all_has_no_coverage(self):
        got = cg.grade_pair(pair(4), replicates=2, caller=replying("nope"))
        self.assertNotIn("coverage", got)
        self.assertEqual(got["failures"], 2)


class AggregateTests(unittest.TestCase):
    def test_coverage_is_reported_by_class(self):
        rows = [
            {"rel": "a", "class": "fact", "coverage": 1.0},
            {"rel": "b", "class": "fact", "coverage": 0.5},
            {"rel": "c", "class": "workflow", "coverage": 0.25},
        ]
        got = cg.aggregate(rows)
        self.assertEqual(got["by_class"]["fact"], {"n": 2, "coverage": 0.75})
        self.assertEqual(got["by_class"]["workflow"], {"n": 1, "coverage": 0.25})

    def test_an_ungraded_note_is_counted_not_averaged(self):
        rows = [
            {"rel": "a", "class": "fact", "coverage": 1.0},
            {"rel": "b", "class": "fact"},
        ]
        got = cg.aggregate(rows)
        self.assertEqual(got["ungraded"], 1)
        self.assertEqual(got["scored"], 1)
        self.assertEqual(got["coverage"], 1.0,
                         "an ungraded note was averaged in")

    def test_nothing_graded_is_no_number_rather_than_zero(self):
        got = cg.aggregate([{"rel": "a", "class": "fact"}])
        self.assertIsNone(got["coverage"],
                          "a corpus nobody could grade reported as 0% complete")


class PromptTests(unittest.TestCase):
    def test_the_claims_are_numbered_from_one(self):
        # The numbering is the contract with the judge: it answers in indices, so
        # an off-by-one here silently shifts every verdict by one claim.
        p = cg.build_prompt(pair(3))
        self.assertIn("1. claim number 1 says a thing", p)
        self.assertIn("3. claim number 3 says a thing", p)
        self.assertNotIn("0. ", p)

    def test_the_rewrite_is_in_the_prompt(self):
        p = cg.build_prompt({"rel": "a", "claims": ["one claim here now"],
                             "rewrite": "THE-REWRITE-TEXT"})
        self.assertIn("THE-REWRITE-TEXT", p)


if __name__ == "__main__":
    unittest.main()
