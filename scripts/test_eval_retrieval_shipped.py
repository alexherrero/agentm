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
import recall  # noqa: E402

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


class TheSearchMatchesTheHook(unittest.TestCase):
    """The eval must issue the query the hook issues, and do what the hook does
    with the answer.

    It did neither for a long time, and the gap was worth about five points: the
    old `search()` asked for exactly `k`, kept whatever came back, and never
    passed a temporal bound. The gold set marks `dt01`, `ep10` and `ep12`
    `hook_reachable: false`; the baseline counted all three as hits.

    Each test below constructs the case where the two behaviours actually
    differ. A test that only asserts "same arguments" would pass against a copy
    of the bug.
    """

    def _daemon_returning(self, paths: list):
        """A fake daemon that answers with these paths, and records the argv."""
        import unittest.mock as mock
        seen = {}

        def fake_run(argv, *a, **kw):
            seen["argv"] = argv
            payload = {"results": [{"path": p} for p in paths]}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload),
                                               stderr="")
        return mock.patch.object(subprocess, "run", side_effect=fake_run), seen

    def test_an_inadmissible_hit_is_replaced_from_deeper_in_the_ranking(self):
        # The differing case, and the reason over-fetch and filtering have to
        # arrive together: with k=2 the old code asked for 2 rows, got two
        # `_inbox` paths, and would now return an empty list. The hook asks for
        # 4, drops the two, and still fills both slots.
        patcher, _seen = self._daemon_returning([
            "Agent/memory/_inbox/noise-one.md",
            "Agent/memory/_inbox/noise-two.md",
            "Agent/memory/2026/08/real-one.md",
            "Agent/memory/2026/08/real-two.md",
        ])
        with patcher:
            got = ev.search("agentmd", "what did we decide about the ranker", k=2)
        self.assertEqual(got, ["Agent/memory/2026/08/real-one.md",
                               "Agent/memory/2026/08/real-two.md"],
                         "an inadmissible path was kept, or its slot was lost")

    def test_the_overfetch_multiplier_is_the_hook_s(self):
        patcher, seen = self._daemon_returning(["Agent/memory/2026/08/a.md"])
        with patcher:
            ev.search("agentmd", "what did we decide about the ranker", k=5)
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("-k") + 1],
                         str(5 * recall.DAEMON_OVERFETCH))

    def test_the_result_is_still_truncated_to_k(self):
        patcher, _ = self._daemon_returning(
            [f"Agent/memory/2026/08/n{i}.md" for i in range(10)])
        with patcher:
            got = ev.search("agentmd", "what did we decide about the ranker", k=3)
        self.assertEqual(len(got), 3, "over-fetch leaked past k into the result")

    def test_a_dated_question_carries_the_temporal_bound(self):
        # The episodic-temporal stratum is twelve of sixty-four questions, and
        # the hook adds these flags unconditionally where the eval never did.
        question = "what did we change in July 2026"
        bound = recall._extract_temporal_bound(question)
        if bound is None:
            self.skipTest("this phrasing carries no bound for the extractor")
        patcher, seen = self._daemon_returning(["Agent/memory/2026/07/a.md"])
        with patcher:
            ev.search("agentmd", question, k=5)
        argv = seen["argv"]
        self.assertTrue("-after" in argv or "-before" in argv,
                        f"no temporal flag passed for a dated question: {argv}")

    def test_an_undated_question_carries_no_temporal_bound(self):
        # The guard on the rule above: passing a bound unconditionally would
        # silently narrow every other question in the set.
        patcher, seen = self._daemon_returning(["Agent/memory/2026/08/a.md"])
        with patcher:
            ev.search("agentmd", "how does the ranker weight titles", k=5)
        argv = seen["argv"]
        self.assertNotIn("-after", argv)
        self.assertNotIn("-before", argv)


class TheReportStatesItsResolution(unittest.TestCase):
    """Every report prints its own CI and MDE, so a bar below the instrument's
    resolution cannot be pre-registered by someone who never saw the number.
    Two of this arc's probe bars were exactly that."""

    def test_the_wilson_interval_matches_the_audited_value(self):
        # 50/64 is the old baseline, and [0.666, 0.865] is the width the harness
        # audit computed independently — pinned here so the formula can't drift.
        lo, hi = ev.wilson_ci(50, 64)
        self.assertAlmostEqual(lo, 0.666, places=3)
        self.assertAlmostEqual(hi, 0.865, places=3)

    def test_an_empty_run_has_no_interval_rather_than_a_fake_one(self):
        self.assertEqual(ev.wilson_ci(0, 0), (0.0, 0.0))

    def test_the_mde_is_derived_from_the_test_not_hardcoded(self):
        # Six at alpha 0.05 — and a stricter alpha must move it, or the function
        # is a constant wearing a derivation's clothes.
        self.assertEqual(ev.min_detectable_flips(), 6)
        self.assertEqual(ev.min_detectable_flips(alpha=0.01), 8)

    def test_the_report_prints_both_lines(self):
        out = ev.render(result({f"q{i}": {"hit": True, "negative": False, "rank": 1}
                                for i in range(64)}), "test corpus")
        self.assertIn("Wilson 95% CI", out)
        self.assertIn("6 flips one way (+9.4%)", out)

    def test_six_one_way_flips_is_an_improvement(self):
        before = result({f"q{i}": {"hit": i >= 6, "negative": False,
                                   "rank": None if i < 6 else 1} for i in range(64)})
        after = result({f"q{i}": {"hit": True, "negative": False, "rank": 1}
                        for i in range(64)})
        cmp = ev.compare(before, after)
        self.assertEqual(cmp["flips_for"], 6)
        self.assertTrue(cmp["improved"])
        self.assertFalse(cmp["regressed"])

    def test_three_one_way_flips_is_not(self):
        # p = 0.25 — direction without significance is a story, not a verdict.
        before = result({f"q{i}": {"hit": i >= 3, "negative": False,
                                   "rank": None if i < 3 else 1} for i in range(64)})
        after = result({f"q{i}": {"hit": True, "negative": False, "rank": 1}
                        for i in range(64)})
        cmp = ev.compare(before, after)
        self.assertEqual(cmp["flips_for"], 3)
        self.assertFalse(cmp["improved"])

    def test_improvement_never_fires_alongside_regression(self):
        before = result({f"q{i}": {"hit": i < 6, "negative": False,
                                   "rank": 1 if i < 6 else None} for i in range(64)})
        after = result({f"q{i}": {"hit": False, "negative": False, "rank": None}
                        for i in range(64)})
        cmp = ev.compare(before, after)
        self.assertTrue(cmp["regressed"])
        self.assertFalse(cmp["improved"])


class TheCorpusFingerprint(unittest.TestCase):
    """No number without its provenance, and no comparison across corpora.

    The old baseline recorded seven scores and nothing else, and the corpus
    halved underneath it — six of nine flips in the task-1 re-run were drift the
    file had no way to even report. These tests pin the refusal semantics.
    """

    FP = {"documents": 7400, "embedded_in_scope": 7350, "gold_sha": "aa-bb-cc"}

    def test_a_baseline_without_provenance_is_refused(self):
        with self.assertRaises(ev.Refused) as caught:
            ev.check_comparable({"per_question": {}}, self.FP, drifted_ok=False)
        self.assertIn("no corpus fingerprint", str(caught.exception))

    def test_a_moved_corpus_is_refused_by_default(self):
        pinned = {"corpus": {**self.FP, "documents": 15029}}
        with self.assertRaises(ev.Refused) as caught:
            ev.check_comparable(pinned, self.FP, drifted_ok=False)
        msg = str(caught.exception)
        self.assertIn("15029", msg)
        self.assertIn("7400", msg, "the refusal must show both sides of the drift")

    def test_drifted_ok_compares_and_reports_the_drift(self):
        pinned = {"corpus": {**self.FP, "documents": 15029}}
        note = ev.check_comparable(pinned, self.FP, drifted_ok=True)
        self.assertIsNotNone(note)
        self.assertIn("15029", note)
        self.assertIn("7400", note)

    def test_a_different_gold_set_is_never_comparable(self):
        # Two gold sets are two question papers. `--drifted-ok` must not turn
        # their scores into one experiment.
        pinned = {"corpus": {**self.FP, "gold_sha": "dd-ee-ff"}}
        with self.assertRaises(ev.Refused):
            ev.check_comparable(pinned, self.FP, drifted_ok=True)

    def test_matching_fingerprints_compare_silently(self):
        self.assertIsNone(ev.check_comparable({"corpus": dict(self.FP)},
                                              self.FP, drifted_ok=False))

    def test_the_written_baseline_carries_the_fingerprint(self):
        # End-to-end through main(): the daemon is faked, the baseline file is
        # real, and the fingerprint must land in it — a pure-function test on
        # check_comparable says nothing about whether --baseline ever writes one.
        import tempfile
        import unittest.mock as mock

        status = {"health": {"embedder": {"state": "warm", "vectors": 100,
                                          "in_scope": 100, "stale": 0}},
                  "index_detail": {"documents": 4321}}
        search = {"results": []}

        def fake_run(argv, *a, **kw):
            payload = status if "status" in argv else search
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload),
                                               stderr="")

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "pin.json"
            with mock.patch.object(subprocess, "run", side_effect=fake_run):
                rc = ev.main(["--baseline", str(out)])
            self.assertEqual(rc, 0)
            pinned = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(pinned["corpus"]["documents"], 4321)
        self.assertEqual(pinned["corpus"]["gold_sha"], ev.gold_sha())
        self.assertIn("pinned", pinned["corpus"])

    def test_a_refused_comparison_exits_3_not_2(self):
        # Exit 2 is the gate's SKIP. A refusal that shared it would let the
        # tripwire die silently on the first drifted day.
        import tempfile
        import unittest.mock as mock

        status = {"health": {"embedder": {"state": "warm", "vectors": 100,
                                          "in_scope": 100, "stale": 0}},
                  "index_detail": {"documents": 4321}}

        def fake_run(argv, *a, **kw):
            payload = status if "status" in argv else {"results": []}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload),
                                               stderr="")

        with tempfile.TemporaryDirectory() as d:
            stale = Path(d) / "no-provenance.json"
            stale.write_text(json.dumps({"per_question": {}, "r_at_k": 0.5}),
                             encoding="utf-8")
            with mock.patch.object(subprocess, "run", side_effect=fake_run):
                rc = ev.main(["--compare", str(stale)])
        self.assertEqual(rc, 3)


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
