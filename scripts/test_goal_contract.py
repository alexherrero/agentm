#!/usr/bin/env python3
"""Tests for goal_contract.py (PLAN-wave-e-scheduled-surfaces task 3) — the
goal-contract's two anti-gaming invariants as a runtime guard.

Run directly: `cd scripts && python3 -m unittest test_goal_contract -v`
Auto-discovered by `python3 -m unittest discover -p 'test_*.py'` (check-all.sh).
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import goal_contract as gc


class DoneCheckIntegrityTests(unittest.TestCase):
    def test_untampered_file_matches_its_snapshot(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "accept-test.sh"
            p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            snap = gc.snapshot_done_check(p)
            self.assertFalse(gc.done_check_tampered(p, snap))

    def test_edited_file_no_longer_matches(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "accept-test.sh"
            p.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            snap = gc.snapshot_done_check(p)
            # The running agent "weakens" its own acceptance test to force green.
            p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            self.assertTrue(gc.done_check_tampered(p, snap))


class DecideStepTests(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.done_check_path = Path(self._td.name) / "accept-test.sh"
        self.done_check_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.snapshot = gc.snapshot_done_check(self.done_check_path)

    def tearDown(self):
        self._td.cleanup()

    def test_a_running_agents_edit_to_its_own_done_check_is_rejected(self):
        # Red-test: even with gates green AND a cold review confirmed, a
        # done-check that was edited since the goal started is refused —
        # tamper detection is checked first, before either other condition.
        self.done_check_path.write_text("#!/bin/sh\nexit 1\n# tampered\n", encoding="utf-8")
        decision = gc.decide(
            gates_green=True,
            done_check_path=self.done_check_path,
            done_check_snapshot=self.snapshot,
            cold_review_confirmed=True,
        )
        self.assertEqual(decision.exit, "needs-operator-decision")
        self.assertIn("cannot edit its own done-check", decision.reason)

    def test_green_gates_alone_never_reach_done_without_cold_review(self):
        # Red-test: no self-certification. Gates green + no cold /review yet
        # must never produce "done".
        decision = gc.decide(
            gates_green=True,
            done_check_path=self.done_check_path,
            done_check_snapshot=self.snapshot,
            cold_review_confirmed=False,
        )
        self.assertEqual(decision.exit, "continue")
        self.assertNotEqual(decision.exit, "done")

    def test_gates_not_green_never_reaches_done_even_with_review_confirmed(self):
        decision = gc.decide(
            gates_green=False,
            done_check_path=self.done_check_path,
            done_check_snapshot=self.snapshot,
            cold_review_confirmed=True,
        )
        self.assertEqual(decision.exit, "continue")

    def test_done_when_both_conditions_hold(self):
        # Positive path: untampered done-check + green gates + cold /review
        # confirmation together, and only together, reach "done".
        decision = gc.decide(
            gates_green=True,
            done_check_path=self.done_check_path,
            done_check_snapshot=self.snapshot,
            cold_review_confirmed=True,
        )
        self.assertEqual(decision.exit, "done")


if __name__ == "__main__":
    unittest.main()
