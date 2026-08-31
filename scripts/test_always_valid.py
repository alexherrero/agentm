#!/usr/bin/env python3
"""Does the interval survive being looked at? Simulated, not asserted.

The whole claim of a confidence sequence is one thing: check it after every
observation, stop whenever you like, and it still covers the truth at the
stated rate. That is a property of repeated use, so it is tested by repeated
use — and the same simulation is run against a fixed-horizon interval, which
must fail it. A test that only showed the sequence passing would not
demonstrate that anything was gained.
"""
from __future__ import annotations

import pathlib
import random
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))

import always_valid as av  # noqa: E402


def peeking_trial(true_p, n_max, seed, radius_fn):
    """One stream, checked after every observation.

    Returns True if the interval ever excluded the truth — that is, whether a
    reader watching this stream would have been misled at some point.
    """
    rng = random.Random(seed)
    k = 0
    for n in range(1, n_max + 1):
        k += 1 if rng.random() < true_p else 0
        r = radius_fn(k, n)
        if r is None:
            continue
        p = k / n
        if not (p - r <= true_p <= p + r):
            return True
    return False


class TheSequenceUnderPeeking(unittest.TestCase):
    TRIALS = 400
    N_MAX = 200

    def _miss_rate(self, radius_fn, true_p=0.25):
        missed = sum(peeking_trial(true_p, self.N_MAX, seed, radius_fn)
                     for seed in range(self.TRIALS))
        return missed / self.TRIALS

    def test_the_sequence_holds_while_being_watched(self):
        # The guarantee: at most alpha of streams are ever misled, however
        # often they are checked.
        rate = self._miss_rate(lambda k, n: av.sequence_radius(n))
        self.assertLess(rate, 0.05, f"missed on {rate:.1%} of streams")

    def test_a_fixed_horizon_interval_does_not(self):
        # The reason the sequence exists. A textbook Wald interval checked
        # after every observation is wrong far more often than its label says,
        # and this is the comparison that shows the width is buying something.
        rate = self._miss_rate(
            lambda k, n: av.fixed_horizon_radius(k, n) if n >= 10 else None)
        self.assertGreater(rate, 0.20,
                           f"expected a fixed interval to fail under peeking, "
                           f"missed only {rate:.1%}")

    def test_it_holds_at_a_different_true_rate(self):
        # A single rate could be luck; the arc's own figure sits near 0.10.
        rate = self._miss_rate(lambda k, n: av.sequence_radius(n), true_p=0.10)
        self.assertLess(rate, 0.05)

    def test_it_is_wider_than_the_fixed_interval_it_replaces(self):
        # If it were not, it would not be buying anything.
        for n in (30, 100, 400):
            self.assertGreater(av.sequence_radius(n),
                               av.fixed_horizon_radius(n // 4, n))


class TheRadius(unittest.TestCase):
    def test_it_narrows_as_evidence_accumulates(self):
        self.assertGreater(av.sequence_radius(20), av.sequence_radius(200))
        self.assertGreater(av.sequence_radius(200), av.sequence_radius(2000))

    def test_no_observations_is_no_radius(self):
        self.assertIsNone(av.sequence_radius(0))
        self.assertIsNone(av.sequence_interval(0, 0))

    def test_a_tighter_alpha_widens_it(self):
        self.assertGreater(av.sequence_radius(100, alpha=0.01),
                           av.sequence_radius(100, alpha=0.05))

    def test_the_interval_stays_inside_zero_and_one(self):
        lo, hi = av.sequence_interval(0, 5)
        self.assertGreaterEqual(lo, 0.0)
        lo, hi = av.sequence_interval(5, 5)
        self.assertLessEqual(hi, 1.0)


class TheTuning(unittest.TestCase):
    def test_it_is_usable_at_the_size_it_was_tuned_for(self):
        # rho was 1/300 from a half-remembered heuristic and about forty times
        # too small: +/-0.178 at n=300 where a swept rho gives +/-0.095. A
        # parameter chosen by reasoning and never measured is how that happens.
        self.assertLess(av.sequence_radius(av.RHO_TUNED_FOR_N), 0.12)

    def test_it_is_not_wildly_off_at_the_sizes_actually_seen(self):
        # This arc has 43 to 90 turns. The interval need not be tight there,
        # but a radius above 1.0 means the parameter is simply wrong.
        for n in (43, 79, 90):
            self.assertLess(av.sequence_radius(n), 0.6, f"n={n}")


class TheMuteCase(unittest.TestCase):
    def test_a_sample_too_small_says_so_instead_of_showing_a_range(self):
        # An interval of [0, 1] excludes nothing, and printing it reads as a
        # measurement. The scorecard's own rule: render honestly as unmeasured
        # rather than fabricate.
        got = av.report(3, 12)
        self.assertTrue(got["ci_uninformative"])
        self.assertIn("too small to support a claim", got["ci_note"])

    def test_a_large_enough_sample_carries_no_such_marker(self):
        got = av.report(75, 300)
        self.assertNotIn("ci_uninformative", got)
        self.assertIn("holds at every sample size", got["ci_note"])

    def test_the_marker_tracks_the_radius_not_the_rate(self):
        # Two very different rates at one sample size must agree about
        # whether the sample is big enough — it is a property of n alone.
        a = av.report(1, 12)
        b = av.report(11, 12)
        self.assertEqual(a.get("ci_uninformative"), b.get("ci_uninformative"))

    def test_the_radius_is_reported_so_a_reader_can_judge_for_themselves(self):
        got = av.report(20, 100)
        self.assertIsNotNone(got["always_valid_radius"])
        self.assertAlmostEqual(got["always_valid_radius"],
                               av.sequence_radius(100), places=4)


class TheReport(unittest.TestCase):
    def test_drift_is_reported_beside_the_interval_not_inside_it(self):
        # Merging them would let a reader take instrument drift for sampling
        # error, or the reverse. They answer different questions.
        got = av.report(10, 80)
        self.assertIn("always_valid_ci", got)
        self.assertIn("drift_band", got)
        self.assertNotEqual(got["always_valid_ci"], got["drift_band"])
        self.assertIn("about the instrument", got["drift_note"])

    def test_the_honest_range_covers_both(self):
        got = av.report(10, 80)
        lo, hi = got["honest_range"]
        self.assertLessEqual(lo, min(got["always_valid_ci"][0],
                                     got["drift_band"][0]))
        self.assertGreaterEqual(hi, max(got["always_valid_ci"][1],
                                        got["drift_band"][1]))

    def test_the_drift_figure_names_the_runs_it_came_from(self):
        # Measured, not assumed — and a reader should be able to see the
        # measurements rather than trust the constant.
        got = av.report(10, 80)
        self.assertIn("8.0%", got["drift_note"])
        self.assertIn("13.0%", got["drift_note"])

    def test_no_observations_is_a_note_not_a_rate(self):
        got = av.report(0, 0)
        self.assertNotIn("rate", got)
        self.assertIn("absence of data", got["note"])

    def test_zero_successes_is_still_a_measurement(self):
        # Distinct from the case above: nothing succeeded, which is a result.
        got = av.report(0, 40)
        self.assertEqual(got["rate"], 0.0)
        self.assertIsNotNone(got["always_valid_ci"])


if __name__ == "__main__":
    unittest.main()
