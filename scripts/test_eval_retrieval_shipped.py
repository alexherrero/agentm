#!/usr/bin/env python3
"""The retrieval eval's logic, tested without the live corpus.

`check-retrieval-regression.sh` needs a daemon, a vault and a warm embedder, none
of which a CI runner has — so the gate itself can only ever skip there. This file
is the wrapper that makes CI cover it anyway: everything the gate decides *with*
is exercised here against fixtures, so a bug in the comparison, the statistic or
the refusal is caught on every push rather than only on the one machine that can
run the real thing.

That distinction matters more than usual here. The eval this replaces was never
run by anything, and its promotion criterion had no reader at all. A gate that
can only run locally is one step away from the same fate; a unit wrapper is what
keeps it honest on the days nobody runs the battery by hand.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import eval_retrieval_shipped as ev  # noqa: E402

GATE = _REPO / "scripts" / "check-retrieval-regression.sh"


class TheGateExists(unittest.TestCase):
    """The allowlist entry in test_ci_consistency.py points here, and an entry
    whose wrapper stopped mentioning its gate is exactly the silent rot that
    contract exists to prevent.

    The two assertions that execute the script are POSIX-only, and the skip is
    narrow rather than convenient: the gate is a bash script run from
    `check-all.sh`, which is itself a bash battery Windows never runs. Asserting
    an exec bit on a filesystem with no exec bit, about a file that platform
    never executes, would be testing the test. Everything the gate *decides
    with* — the comparison, the statistic, the refusal — runs on every platform
    below, and that is the part a Windows runner can meaningfully check."""

    def test_the_gate_script_is_present(self):
        self.assertTrue(GATE.is_file(), f"{GATE} is missing")

    @unittest.skipIf(os.name == "nt", "POSIX exec bit; the gate is a bash script "
                                      "run from a battery Windows does not run")
    def test_the_gate_script_is_executable(self):
        self.assertTrue(GATE.stat().st_mode & 0o111,
                        "check-retrieval-regression.sh is not executable")

    @unittest.skipIf(os.name == "nt", "runs the gate under bash with a POSIX PATH")
    def test_the_gate_skips_rather_than_passes_without_a_daemon(self):
        """A skip is never silent. A gate that went quiet on the machines it
        cannot measure would be indistinguishable from one that passes."""
        proc = subprocess.run(["bash", str(GATE)], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "AGENTMD": "/nonexistent/agentmd"},
                              timeout=120)
        self.assertEqual(proc.returncode, 0, "a missing daemon should skip, not fail")
        self.assertIn("SKIP", proc.stdout + proc.stderr)


class ExactPairedTest(unittest.TestCase):
    """Only the questions that flipped carry information. The ones that agree say
    nothing about which ranker is better, which is why this is McNemar's shape
    and not a two-sample test over run averages."""

    def test_no_flips_is_no_evidence(self):
        self.assertEqual(ev.mcnemar_exact(0, 0), 1.0)

    def test_a_lopsided_split_is_significant(self):
        self.assertLess(ev.mcnemar_exact(0, 14), 0.001)

    def test_an_even_split_is_not(self):
        self.assertGreater(ev.mcnemar_exact(7, 7), 0.05)

    def test_it_is_symmetric(self):
        self.assertAlmostEqual(ev.mcnemar_exact(3, 11), ev.mcnemar_exact(11, 3))

    def test_a_small_lopsided_split_is_not_oversold(self):
        """Two flips one way is not evidence, and a test that called it one would
        promote noise."""
        self.assertGreater(ev.mcnemar_exact(0, 2), 0.05)

    def test_the_known_boundary(self):
        """Six-nil is the smallest all-one-way split that reaches significance;
        five-nil does not. Two-sided exact binomial: 5 flips gives 2 x 1/32 =
        0.0625, and 6 gives 2 x 1/64 = 0.03125.

        This is the gate's real sensitivity, pinned rather than left implicit —
        fewer than six questions moving one way cannot fail it, whatever the
        direction looks like by eye. On a 64-question scored set that is roughly a
        9% swing, which is the price of not promoting noise."""
        self.assertGreater(ev.mcnemar_exact(0, 5), 0.05)
        self.assertLess(ev.mcnemar_exact(0, 6), 0.05)


def result(per_question: dict, r: float = 0.5) -> dict:
    return {"k": 5, "scored": len(per_question), "hits": 0, "r_at_k": r,
            "avg_rank_to_first_hit": 1.5, "negatives": 0, "false_positives": 0,
            "per_question": per_question}


class Comparison(unittest.TestCase):
    def test_a_clear_regression_is_flagged(self):
        before = result({f"q{i}": {"hit": True, "negative": False, "rank": 1} for i in range(14)})
        after = result({f"q{i}": {"hit": False, "negative": False, "rank": None} for i in range(14)})
        cmp = ev.compare(before, after)
        self.assertEqual(cmp["flips_against"], 14)
        self.assertEqual(cmp["flips_for"], 0)
        self.assertTrue(cmp["regressed"])

    def test_a_clear_improvement_is_not_a_regression(self):
        before = result({f"q{i}": {"hit": False, "negative": False, "rank": None} for i in range(14)})
        after = result({f"q{i}": {"hit": True, "negative": False, "rank": 1} for i in range(14)})
        cmp = ev.compare(before, after)
        self.assertEqual(cmp["flips_for"], 14)
        self.assertFalse(cmp["regressed"])

    def test_an_identical_run_is_not_a_regression(self):
        same = result({f"q{i}": {"hit": i % 2 == 0, "negative": False, "rank": 1 if i % 2 == 0 else None}
                       for i in range(20)})
        cmp = ev.compare(same, same)
        self.assertEqual((cmp["flips_for"], cmp["flips_against"]), (0, 0))
        self.assertFalse(cmp["regressed"])

    def test_two_flips_against_is_noise_not_a_regression(self):
        """The bar is significance, not direction. A ranking change that moves
        two questions the wrong way and nothing else has not been shown to be
        worse, and failing on it would make the gate unusable."""
        pq_before, pq_after = {}, {}
        for i in range(40):
            hit = i >= 2
            pq_before[f"q{i}"] = {"hit": True, "negative": False, "rank": 1}
            pq_after[f"q{i}"] = {"hit": hit, "negative": False, "rank": 1 if hit else None}
        cmp = ev.compare(result(pq_before), result(pq_after))
        self.assertEqual(cmp["flips_against"], 2)
        self.assertFalse(cmp["regressed"])

    def test_negatives_are_excluded_from_the_paired_comparison(self):
        """Counting them would let a ranker that found less look better."""
        before = result({
            "a": {"hit": True, "negative": False, "rank": 1},
            "n": {"hit": True, "negative": True, "rank": None},
        })
        after = result({
            "a": {"hit": True, "negative": False, "rank": 1},
            "n": {"hit": False, "negative": True, "rank": None},
        })
        cmp = ev.compare(before, after)
        self.assertEqual(cmp["compared"], 1)

    def test_questions_missing_from_one_side_are_skipped(self):
        before = result({"a": {"hit": True, "negative": False, "rank": 1},
                         "b": {"hit": True, "negative": False, "rank": 1}})
        after = result({"a": {"hit": True, "negative": False, "rank": 1}})
        self.assertEqual(ev.compare(before, after)["compared"], 1)


class RefusalToMeasure(unittest.TestCase):
    """The guard the inherited eval lacked. A lexical-only run reported as a
    hybrid result is not a weaker measurement, it is a different one — and the
    arm that is missing is the one a paraphrase question depends on."""

    def _fake_status(self, payload: dict):
        import unittest.mock as mock
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
        return mock.patch.object(subprocess, "run", return_value=completed)

    def test_a_cold_embedder_refuses(self):
        with self._fake_status({"health": {"embedder": {"state": "off"}}}):
            with self.assertRaises(ev.Setup) as caught:
                ev.require_warm_embedder("agentmd")
        self.assertIn("lexical-only", str(caught.exception))

    def test_too_many_stale_vectors_refuses(self):
        with self._fake_status({"health": {"embedder": {
                "state": "warm", "vectors": 3934, "in_scope": 15129, "stale": 8361}}}):
            with self.assertRaises(ev.Setup) as caught:
                ev.require_warm_embedder("agentmd")
        self.assertIn("half-embedded", str(caught.exception))

    def test_a_fully_embedded_corpus_is_accepted(self):
        with self._fake_status({"health": {"embedder": {
                "state": "warm", "vectors": 15135, "in_scope": 15135, "stale": 0}}}):
            provenance = ev.require_warm_embedder("agentmd")
        self.assertIn("15135", provenance)

    def test_a_few_stale_vectors_is_tolerated(self):
        """An exact-zero requirement would make the gate unrunnable: the corpus
        is live and a note captured a second ago is legitimately unembedded."""
        with self._fake_status({"health": {"embedder": {
                "state": "warm", "vectors": 15000, "in_scope": 15100, "stale": 100}}}):
            ev.require_warm_embedder("agentmd")


class TheFixtureField(unittest.TestCase):
    def test_the_expected_field_is_the_one_the_fixture_uses(self):
        """The fixture says `expected_note_paths`; output rows elsewhere in this
        repo say `expected`. Reading the wrong one scores a silent, total null
        that reads as a finding rather than as a bug."""
        gold = json.loads(ev.GOLD_SET.read_text(encoding="utf-8"))
        entry = gold["entries"][0]
        self.assertIn(ev.EXPECTED_FIELD, entry)
        self.assertNotIn("expected", entry)

    def test_the_pinned_baseline_matches_the_gold_set(self):
        """A baseline scored against a different question set is not a baseline."""
        baseline_path = _REPO / "scripts" / "health" / "fixtures" / "week1-gold" / "shipped-baseline.json"
        if not baseline_path.is_file():
            self.skipTest("no pinned baseline in this tree")
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        gold = json.loads(ev.GOLD_SET.read_text(encoding="utf-8"))
        self.assertEqual(set(baseline["per_question"]), {e["id"] for e in gold["entries"]})


if __name__ == "__main__":
    unittest.main()
