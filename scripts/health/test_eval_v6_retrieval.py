#!/usr/bin/env python3
"""Tests for scripts/health/eval_v6_retrieval.py's decay-curve gate (auto-
organization part 1, task 2).

The V6-3 old-formula-vs-new-formula comparison (this module's original
behavior) needs a real vault with a populated vec-index and is exercised
against the operator's real vault, not unit-tested here (matches this
module's existing convention — no test file existed for it before this
task). What's unit-testable without any vault at all is the NEW seam this
task adds: run_eval()'s injectable old_top_k_fn/new_top_k_fn parameters,
the decay-curve context manager, and the --decay-curve CLI dispatch — all
exercised here with fake, deterministic top-k functions so no recall.py /
vec-index dependency is needed.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_MEMORY_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
for p in (_HERE, _MEMORY_SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import eval_v6_retrieval as ev  # noqa: E402
import lifecycle  # noqa: E402


def _write_query_set(path: Path) -> None:
    path.write_text(json.dumps({
        "entries": [
            {"id": "q1", "query": "alpha", "expected_notes": ["a.md"]},
            {"id": "q2", "query": "beta", "expected_notes": ["b.md", "c.md"]},
        ]
    }), encoding="utf-8")


class TestSteppedDecayCurveContextManager(unittest.TestCase):
    def test_swaps_and_restores_on_normal_exit(self):
        original = lifecycle.compute_decay_score
        with ev._stepped_decay_curve():
            self.assertIs(lifecycle.compute_decay_score, lifecycle.compute_decay_score_stepped)
        self.assertIs(lifecycle.compute_decay_score, original)

    def test_restores_even_on_exception(self):
        original = lifecycle.compute_decay_score
        with self.assertRaises(ValueError):
            with ev._stepped_decay_curve():
                self.assertIs(lifecycle.compute_decay_score, lifecycle.compute_decay_score_stepped)
                raise ValueError("boom")
        self.assertIs(lifecycle.compute_decay_score, original)

    def test_new_formula_top_k_with_stepped_decay_wraps_the_swap(self):
        calls = []

        def fake_new_formula_top_k(vault, query_text, k=5):
            calls.append(lifecycle.compute_decay_score is lifecycle.compute_decay_score_stepped)
            return ["x.md"]

        original = lifecycle.compute_decay_score
        with mock.patch.object(ev, "_new_formula_top_k", fake_new_formula_top_k):
            result = ev._new_formula_top_k_with_stepped_decay(Path("/nonexistent"), "q", k=5)
        self.assertEqual(result, ["x.md"])
        self.assertEqual(calls, [True])  # curve was swapped DURING the call
        self.assertIs(lifecycle.compute_decay_score, original)  # and restored after


class TestRunEvalInjectableTopKFns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self.query_set = self.vault / "queries.json"
        _write_query_set(self.query_set)

    def tearDown(self):
        self.tmp.cleanup()

    def test_identical_top_k_fns_show_no_delta(self):
        def same(vault, query_text, k=5):
            return ["a.md"] if query_text == "alpha" else ["b.md", "c.md"]

        result = ev.run_eval(self.vault, self.query_set, old_top_k_fn=same, new_top_k_fn=same)
        acc = result["accuracy"]
        self.assertEqual(acc["old_r_at_5"], acc["new_r_at_5"])
        self.assertEqual(acc["old_p_at_5"], acc["new_p_at_5"])
        self.assertEqual(result["discovery_rate"]["rate"], 0.0)

    def test_new_fn_finding_more_improves_accuracy_and_discovery(self):
        def old_fn(vault, query_text, k=5):
            return []  # finds nothing

        def new_fn(vault, query_text, k=5):
            return ["a.md"] if query_text == "alpha" else ["b.md", "c.md"]

        result = ev.run_eval(self.vault, self.query_set, old_top_k_fn=old_fn, new_top_k_fn=new_fn)
        acc = result["accuracy"]
        self.assertEqual(acc["old_r_at_5"], 0.0)
        self.assertGreater(acc["new_r_at_5"], 0.0)
        self.assertGreater(result["discovery_rate"]["rate"], 0.0)

    def test_default_top_k_fns_are_the_legacy_v6_3_pair(self):
        # Unchanged default behavior — no --decay-curve flag, no injected
        # fns — must still resolve to the original V6-3 comparison pair.
        import inspect
        sig = inspect.signature(ev.run_eval)
        self.assertIs(sig.parameters["old_top_k_fn"].default, ev._old_formula_top_k)
        self.assertIs(sig.parameters["new_top_k_fn"].default, ev._new_formula_top_k)


_FAKE_RESULT = {
    "accuracy": {"old_p_at_5": 0.0, "new_p_at_5": 0.0, "old_r_at_5": 0.0, "new_r_at_5": 0.0},
    "compression": {
        "old_avg_rank_to_first_hit": None, "new_avg_rank_to_first_hit": None,
        "old_queries_with_a_hit": 0, "new_queries_with_a_hit": 0,
    },
    "discovery_rate": {"new_found_old_missed_pairs": 0, "total_expected_pairs": 0, "rate": 0.0},
    "per_query": [],
}


class TestDecayCurveCLIDispatch(unittest.TestCase):
    """Verifies main() selects the right top_k_fn pair without ever running
    a real query — run_eval() itself is mocked out entirely, so no
    recall.py / vec-index dependency is needed for these CLI-dispatch tests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self.query_set = self.vault / "queries.json"
        _write_query_set(self.query_set)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_mode_uses_legacy_top_k_fns(self):
        with mock.patch.object(ev, "run_eval", return_value=dict(_FAKE_RESULT)) as spy:
            ev.main(["--vault-path", str(self.vault), "--query-set", str(self.query_set)])
        _, kwargs = spy.call_args
        self.assertEqual(kwargs, {})  # no override kwargs passed -> defaults apply

    def test_stepped_mode_selects_the_decay_curve_pair(self):
        with mock.patch.object(ev, "run_eval", return_value=dict(_FAKE_RESULT)) as spy:
            ev.main([
                "--vault-path", str(self.vault), "--query-set", str(self.query_set),
                "--decay-curve", "stepped",
            ])
        _, kwargs = spy.call_args
        self.assertIs(kwargs["old_top_k_fn"], ev._new_formula_top_k)
        self.assertIs(kwargs["new_top_k_fn"], ev._new_formula_top_k_with_stepped_decay)

    def test_stepped_mode_never_regresses_when_curves_are_functionally_identical(self):
        # If the two top-k functions happen to return the same results (the
        # honest case when a vault has no lifecycle sidecar history at all,
        # so both curves fall back to the same "no basis, fully fresh"
        # 1.0 score), the gate must not report a regression.
        def same(vault, query_text, k=5):
            return ["a.md"] if query_text == "alpha" else ["b.md", "c.md"]

        with mock.patch.object(ev, "_new_formula_top_k", same), \
             mock.patch.object(ev, "_new_formula_top_k_with_stepped_decay", same):
            exit_code = ev.main([
                "--vault-path", str(self.vault), "--query-set", str(self.query_set),
                "--decay-curve", "stepped",
            ])
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
