#!/usr/bin/env python3
"""Cohen's κ and PPI, checked where the answer is known by construction.

An estimator is not trustworthy because its formula resembles the paper's. The
PPI tests below build populations whose true mean is known before the estimator
runs, give the judge a deliberate bias, and check that the correction removes
it — and that the interval covers the truth across many draws, which is the
only property a confidence interval actually claims.
"""
from __future__ import annotations

import pathlib
import random
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))

import agreement as ag  # noqa: E402


class TheKappa(unittest.TestCase):
    def test_perfect_agreement_is_one(self):
        a = ["sufficient", "insufficient", "n/a", "sufficient"]
        self.assertEqual(ag.cohen_kappa(a, list(a))["kappa"], 1.0)

    def test_chance_level_agreement_is_about_zero(self):
        # Two raters with the same marginals, paired so they agree exactly as
        # often as independence predicts. Raw agreement is high; kappa is not.
        a = ["s"] * 50 + ["i"] * 50
        b = ["s"] * 25 + ["i"] * 25 + ["s"] * 25 + ["i"] * 25
        got = ag.cohen_kappa(a, b)
        self.assertAlmostEqual(got["kappa"], 0.0, places=6)
        self.assertEqual(got["raw_agreement"], 0.5)

    def test_raw_agreement_can_be_high_while_kappa_is_zero(self):
        # The reason raw is never reported as kappa. Both raters say "n/a" 90%
        # of the time and are otherwise independent: they match on most items
        # while agreeing about nothing.
        a, b = [], []
        for i in range(100):
            a.append("n/a" if i < 90 else "s")
        for i in range(100):
            b.append("n/a" if i % 10 != 0 else "s")
        got = ag.cohen_kappa(a, b)
        self.assertGreater(got["raw_agreement"], 0.7)
        self.assertLess(abs(got["kappa"]), 0.25)

    def test_systematic_disagreement_is_negative(self):
        a = ["s", "s", "i", "i"]
        b = ["i", "i", "s", "s"]
        self.assertLess(ag.cohen_kappa(a, b)["kappa"], 0)

    def test_a_single_category_makes_kappa_undefined_not_perfect(self):
        # Both raters said "n/a" to everything. They agree completely and have
        # demonstrated nothing; reporting kappa = 1 here would be a lie.
        got = ag.cohen_kappa(["n/a"] * 20, ["n/a"] * 20)
        self.assertIsNone(got["kappa"])
        self.assertIn("undefined, not perfect", got["note"])

    def test_the_interval_is_reported_with_the_point(self):
        a = ["s"] * 30 + ["i"] * 30
        b = ["s"] * 25 + ["i"] * 5 + ["i"] * 25 + ["s"] * 5
        got = ag.cohen_kappa(a, b)
        lo, hi = got["kappa_ci"]
        self.assertLess(lo, got["kappa"])
        self.assertGreater(hi, got["kappa"])

    def test_raw_is_labelled_as_not_kappa(self):
        got = ag.cohen_kappa(["s", "i"], ["s", "s"])
        self.assertIn("not kappa", got["raw_agreement_note"])

    def test_unpaired_labels_are_refused(self):
        with self.assertRaises(ValueError):
            ag.cohen_kappa(["s", "i"], ["s"])


class ThePPIEstimator(unittest.TestCase):
    """Populations whose true mean is known before the estimator runs."""

    def _population(self, n, true_rate, judge_bias, seed):
        """A judge that is wrong in a known direction on a known share.

        `judge_bias` is the probability the judge flips a 0 to a 1, so the
        judge's own mean overstates the truth by a known amount.
        """
        rng = random.Random(seed)
        truth = [1.0 if rng.random() < true_rate else 0.0 for _ in range(n)]
        pred = []
        for t in truth:
            if t == 0.0 and rng.random() < judge_bias:
                pred.append(1.0)
            else:
                pred.append(t)
        return truth, pred

    def test_it_removes_a_known_judge_bias(self):
        # True rate 0.30; the judge calls a third of the negatives positive, so
        # its own estimate lands near 0.53. PPI has to come back to 0.30.
        truth, pred = self._population(4000, 0.30, 0.33, seed=11)
        n_lab = 300
        got = ag.ppi_mean(truth[:n_lab], pred[:n_lab], pred[n_lab:])
        true_mean = sum(truth) / len(truth)
        self.assertGreater(got["judge_only_estimate"], true_mean + 0.15)
        self.assertLess(abs(got["estimate"] - true_mean), 0.03)

    def test_the_interval_covers_the_truth_across_repeated_draws(self):
        # The only property a 95% interval actually claims. Anything above
        # roughly 90 of 100 is consistent with it; well below means the
        # variance is understated and the estimate reads confident when it is
        # not.
        covered = 0
        for seed in range(100):
            truth, pred = self._population(2000, 0.25, 0.30, seed=seed)
            got = ag.ppi_mean(truth[:200], pred[:200], pred[200:])
            true_mean = sum(truth) / len(truth)
            lo, hi = got["ci"]
            covered += lo <= true_mean <= hi
        self.assertGreater(covered, 88, f"only {covered}/100 intervals covered")

    def test_a_perfect_judge_leaves_the_estimate_alone(self):
        truth, pred = self._population(1000, 0.4, 0.0, seed=3)
        got = ag.ppi_mean(truth[:100], pred[:100], pred[100:])
        self.assertEqual(got["measured_judge_bias"], 0.0)
        self.assertEqual(got["estimate"], got["judge_only_estimate"])

    def test_it_beats_using_the_labels_alone(self):
        # If PPI's interval were not narrower than the labelled subset's own,
        # there would be no reason to run it — the labels alone are unbiased.
        truth, pred = self._population(4000, 0.3, 0.1, seed=7)
        got = ag.ppi_mean(truth[:200], pred[:200], pred[200:])
        self.assertTrue(got["interval_narrower_than_labels_only"])
        self.assertLess(got["se"], got["labels_only_se"])

    def test_fewer_labels_widen_the_interval_above_the_floor(self):
        truth, pred = self._population(4000, 0.3, 0.3, seed=5)
        wide = ag.ppi_mean(truth[:100], pred[:100], pred[100:])
        tight = ag.ppi_mean(truth[:400], pred[:400], pred[400:])
        self.assertGreater(wide["se"], tight["se"])

    def test_below_the_floor_there_is_no_interval_at_all(self):
        # The failure that put the floor there: at 10 labels against this
        # judge, the subset held zero judge errors, the residual variance came
        # out zero, and the estimator reported se=0.0079 — tighter than the
        # same estimator on 400 labels. It was announcing certainty about a
        # bias it had never seen.
        truth, pred = self._population(4000, 0.3, 0.3, seed=5)
        got = ag.ppi_mean(truth[:10], pred[:10], pred[10:])
        self.assertIsNone(got["ci"])
        self.assertIn("never saw", got["ci_note"])
        self.assertIsNotNone(got["estimate"])
        self.assertFalse(got["interval_narrower_than_labels_only"])

    def test_the_floor_is_where_a_near_right_judge_stops_breaking_it(self):
        # The dangerous case is an *accurate* judge, not a bad one: a small
        # labelled set drawn against it probably contains no errors at all.
        # Measured coverage of the nominal-95% interval with a judge flipping
        # 5% was 31% at 10 labels, 67% at 30, 85% at 50, and 95% at 100.
        # This checks the two ends of that, cheaply.
        def coverage(n_lab, trials=120):
            hit = 0
            for seed in range(trials):
                truth, pred = self._population(2000, 0.25, 0.05, seed=seed)
                g = ag.ppi_mean(truth[:n_lab], pred[:n_lab], pred[n_lab:])
                if g["ci"] is None:
                    # Refused, so it cannot miss — that is the floor working.
                    hit += 1
                    continue
                tm = sum(truth) / len(truth)
                lo, hi = g["ci"]
                hit += lo <= tm <= hi
            return hit / trials

        self.assertGreater(coverage(ag.PPI_MIN_LABELS), 0.90)
        # And an interval offered one label below the floor would not hold up,
        # which is why none is offered.
        self.assertIsNone(
            ag.ppi_mean(*self._split(ag.PPI_MIN_LABELS - 1))["ci"])

    def _split(self, n_lab):
        truth, pred = self._population(2000, 0.25, 0.05, seed=1)
        return truth[:n_lab], pred[:n_lab], pred[n_lab:]

    def test_it_reports_the_two_naive_estimates_beside_its_own(self):
        # A reader has to be able to see what PPI changed. Both naive numbers
        # are wrong in different ways and neither is hidden.
        truth, pred = self._population(1000, 0.3, 0.25, seed=2)
        got = ag.ppi_mean(truth[:100], pred[:100], pred[100:])
        self.assertIn("judge_only_estimate", got)
        self.assertIn("labels_only_estimate", got)

    def test_no_labels_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            ag.ppi_mean([], [], [1.0, 0.0])

    def test_unpaired_labelled_input_is_refused(self):
        with self.assertRaises(ValueError):
            ag.ppi_mean([1.0, 0.0], [1.0], [0.0])


class TheWilson(unittest.TestCase):
    def test_it_brackets_the_point(self):
        lo, hi = ag.wilson(3, 10)
        self.assertLess(lo, 0.3)
        self.assertGreater(hi, 0.3)

    def test_zero_of_n_has_a_nonzero_upper_bound(self):
        lo, hi = ag.wilson(0, 20)
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0.0)

    def test_an_empty_denominator_is_none(self):
        self.assertIsNone(ag.wilson(0, 0))


if __name__ == "__main__":
    unittest.main()
