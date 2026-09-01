#!/usr/bin/env python3
"""The paired before/after comparison.

Two properties carry this, and both exist because of how the absolute
measurement failed. The arms must be **interleaved**, or drift within a run
lands unevenly and manufactures the very difference the tool looks for. And the
interval must be **on the difference**, because two overlapping rate intervals
can still be a real change and reading them separately is how that gets missed.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))

import online_recall_ab as ab  # noqa: E402


def turns(n, start_day, session="s"):
    return [{"session": f"{session}{i}", "ts": f"2026-08-{start_day + i:02d}"
                                              f"T00:00:00Z",
             "prompt_hash": f"h{session}{i}", "_prompt": f"question {i}",
             "_injected": "notes"}
            for i in range(n)]


class TheSplit(unittest.TestCase):
    def test_it_splits_on_the_boundary(self):
        ts = turns(5, 1) + turns(5, 20)
        before, after = ab.split_at(ts, "2026-08-15T00:00:00Z")
        self.assertEqual(len(before), 5)
        self.assertEqual(len(after), 5)

    def test_a_turn_exactly_on_the_boundary_counts_as_after(self):
        ts = [{"ts": "2026-08-15T00:00:00Z"}]
        before, after = ab.split_at(ts, "2026-08-15T00:00:00Z")
        self.assertEqual((len(before), len(after)), (0, 1))

    def test_a_turn_with_no_timestamp_lands_before(self):
        # Not silently dropped: an untimestamped turn is old data, and losing
        # it would shrink one arm without saying so.
        before, after = ab.split_at([{"ts": None}], "2026-08-15T00:00:00Z")
        self.assertEqual(len(before), 1)


class TheDraw(unittest.TestCase):
    def test_the_arms_are_interleaved(self):
        # Judging one arm then the other would let drift within the run land
        # unevenly and appear as a difference between them.
        order = ab.draw(turns(10, 1), turns(10, 20, "a"), per_arm=5, seed=1)
        arms = [a for a, _ in order]
        self.assertEqual(arms, ["before", "after"] * 5)

    def test_the_arms_are_balanced(self):
        order = ab.draw(turns(30, 1), turns(4, 20, "a"), per_arm=20, seed=1)
        arms = [a for a, _ in order]
        self.assertEqual(arms.count("before"), arms.count("after"))
        self.assertEqual(arms.count("before"), 4)

    def test_it_is_reproducible(self):
        b, a = turns(20, 1), turns(20, 20, "a")
        first = [t["prompt_hash"] for _, t in ab.draw(b, a, per_arm=5, seed=3)]
        second = [t["prompt_hash"] for _, t in ab.draw(b, a, per_arm=5, seed=3)]
        self.assertEqual(first, second)

    def test_a_different_seed_draws_differently(self):
        b, a = turns(20, 1), turns(20, 20, "a")
        self.assertNotEqual(
            [t["prompt_hash"] for _, t in ab.draw(b, a, per_arm=5, seed=1)],
            [t["prompt_hash"] for _, t in ab.draw(b, a, per_arm=5, seed=2)])

    def test_an_empty_arm_yields_nothing_rather_than_a_lopsided_draw(self):
        self.assertEqual(ab.draw(turns(10, 1), [], per_arm=5, seed=1), [])


class TheDifference(unittest.TestCase):
    def test_it_reports_an_interval_on_the_difference(self):
        got = ab.paired_difference(10, 40, 20, 40)
        self.assertAlmostEqual(got["difference"], 0.25)
        lo, hi = got["difference_ci"]
        self.assertLess(lo, 0.25)
        self.assertGreater(hi, 0.25)

    def test_a_clear_move_is_called_moved(self):
        got = ab.paired_difference(2, 60, 30, 60)
        self.assertTrue(got["moved"])

    def test_a_small_move_is_not(self):
        got = ab.paired_difference(20, 40, 22, 40)
        self.assertFalse(got["moved"])
        lo, hi = got["difference_ci"]
        self.assertLess(lo, 0)
        self.assertGreater(hi, 0)

    def test_overlapping_rate_intervals_can_still_be_a_real_change(self):
        # The whole reason the interval is on the difference. Each arm's own
        # Wilson interval overlaps here, and the difference is still clear.
        import agreement as ag
        k_b, n_b, k_a, n_a = 18, 100, 34, 100
        b_lo, b_hi = ag.wilson(k_b, n_b)
        a_lo, a_hi = ag.wilson(k_a, n_a)
        self.assertGreater(b_hi, a_lo, "the rate intervals should overlap")
        self.assertTrue(ab.paired_difference(k_b, n_b, k_a, n_a)["moved"])

    def test_an_empty_arm_is_a_note_not_a_zero(self):
        got = ab.paired_difference(0, 0, 5, 20)
        self.assertNotIn("difference", got)
        self.assertIn("nothing to compare", got["note"])

    def test_it_says_why_pairing_helps(self):
        # A reader has to know the difference is better resolved than the
        # rates, or they will apply the 10-point floor to it.
        got = ab.paired_difference(10, 40, 20, 40)
        self.assertIn("common-mode", got["drift_note"])
        self.assertIn("cancels", got["drift_note"])

    def test_it_names_the_confound_it_cannot_remove(self):
        got = ab.paired_difference(10, 40, 20, 40)
        self.assertIn("not on either rate", got["note"])


if __name__ == "__main__":
    unittest.main()
