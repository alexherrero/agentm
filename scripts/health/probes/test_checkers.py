#!/usr/bin/env python3
"""Unit coverage for scripts/health/probes/checkers.py (PLAN-r3-uplift-scoring
task 3 / R3.2a).

Run directly:
    cd scripts && python3 -m unittest health.probes.test_checkers
"""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import checkers  # noqa: E402
import run_all  # noqa: E402


class TestEachCheckerDiscriminates(unittest.TestCase):
    """Verification #1: each checker, run against its own hand-constructed
    fixture pair, correctly discriminates pass/fail — a checker that can't
    tell the two fixtures apart is not deterministic enough to ship."""

    def test_recall_a_prior_decision(self):
        probe = checkers.RecallAPriorDecision
        self.assertTrue(probe.check(probe.backed_fixture()))
        self.assertFalse(probe.check(probe.bare_fixture()))

    def test_find_the_planted_bug(self):
        probe = checkers.FindThePlantedBug
        self.assertTrue(probe.check(probe.backed_fixture()))
        self.assertFalse(probe.check(probe.bare_fixture()))

    def test_cold_resume_from_harness(self):
        probe = checkers.ColdResumeFromHarness
        self.assertTrue(probe.check(probe.backed_fixture()))
        self.assertFalse(probe.check(probe.bare_fixture()))

    def test_preference_adherence(self):
        probe = checkers.PreferenceAdherence
        self.assertTrue(probe.check(probe.backed_fixture()))
        self.assertFalse(probe.check(probe.bare_fixture()))

    def test_all_probes_registered(self):
        self.assertEqual(len(checkers.ALL_PROBES), 4)
        names = {p.NAME for p in checkers.ALL_PROBES}
        self.assertEqual(names, {
            "recall-a-prior-decision",
            "find-the-planted-bug",
            "cold-resume-from-.harness",
            "preference-adherence",
        })


class TestRunAllDryRun(unittest.TestCase):
    """Verification #2: `run_all.py --dry-run` exits 0 and reports 4/4 wired."""

    def test_dry_run_exits_0_and_reports_4_of_4(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_all.main(["--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("4/4 checkers wired", buf.getvalue())

    def test_missing_dry_run_flag_exits_2(self):
        rc = run_all.main([])
        self.assertEqual(rc, 2)

    def test_a_broken_checker_is_reported_as_a_failure(self):
        class BrokenProbe:
            NAME = "broken"

            @staticmethod
            def backed_fixture():
                return [{"role": "assistant", "content": "nothing distinguishing"}]

            @staticmethod
            def bare_fixture():
                return [{"role": "assistant", "content": "nothing distinguishing"}]

            @staticmethod
            def check(transcript):
                return True  # identical result for both fixtures — can't discriminate

        original = run_all.ALL_PROBES
        run_all.ALL_PROBES = [BrokenProbe]
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_all.run_dry_run()
            self.assertEqual(rc, 1)
            self.assertIn("0/1 checkers wired", buf.getvalue())
        finally:
            run_all.ALL_PROBES = original


if __name__ == "__main__":
    unittest.main()
