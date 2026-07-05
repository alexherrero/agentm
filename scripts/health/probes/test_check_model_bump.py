#!/usr/bin/env python3
"""Unit coverage for scripts/health/probes/check_model_bump.py
(PLAN-r3-uplift-scoring task 4 / R3.2b).

A scripted config-diff test — never a live model swap: `trigger_fn` is
always injected as a fake, so the battery-trigger path is exercised without
ever shelling out to the real `claude` CLI.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import check_model_bump as cmb  # noqa: E402


class TestModelBumpedPureFunction(unittest.TestCase):
    def test_no_previous_state_is_not_a_bump(self):
        self.assertFalse(cmb.model_bumped(None, "claude-sonnet-5"))

    def test_same_model_is_not_a_bump(self):
        self.assertFalse(cmb.model_bumped("claude-sonnet-5", "claude-sonnet-5"))

    def test_different_model_is_a_bump(self):
        self.assertTrue(cmb.model_bumped("claude-sonnet-5", "claude-opus-4-8"))

    def test_whitespace_only_difference_is_not_a_bump(self):
        self.assertFalse(cmb.model_bumped("claude-sonnet-5\n", "claude-sonnet-5"))


class TestMainCLIDrivesTheTrigger(unittest.TestCase):
    """A scripted config-diff test — trigger_fn is always a fake."""

    def test_bump_invokes_trigger_fn_and_persists_new_state(self):
        calls = []
        trigger_fn = lambda: (calls.append(1), 0)[1]
        state = {"model": "claude-sonnet-5"}
        rc = cmb.main(
            ["--current-model", "claude-opus-4-8"],
            trigger_fn=trigger_fn,
            state_reader=lambda: state["model"],
            state_writer=lambda m: state.__setitem__("model", m),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(state["model"], "claude-opus-4-8")

    def test_no_bump_never_invokes_trigger_fn(self):
        calls = []
        trigger_fn = lambda: (calls.append(1), 0)[1]
        state = {"model": "claude-sonnet-5"}
        rc = cmb.main(
            ["--current-model", "claude-sonnet-5"],
            trigger_fn=trigger_fn,
            state_reader=lambda: state["model"],
            state_writer=lambda m: state.__setitem__("model", m),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 0)

    def test_first_run_ever_seeds_state_without_triggering(self):
        calls = []
        trigger_fn = lambda: (calls.append(1), 0)[1]
        state = {"model": None}
        rc = cmb.main(
            ["--current-model", "claude-sonnet-5"],
            trigger_fn=trigger_fn,
            state_reader=lambda: state["model"],
            state_writer=lambda m: state.__setitem__("model", m),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 0)
        self.assertEqual(state["model"], "claude-sonnet-5")

    def test_triggered_battery_failure_propagates_as_exit_1(self):
        trigger_fn = lambda: 1  # simulates the triggered run_live.main() itself failing
        state = {"model": "claude-sonnet-5"}
        rc = cmb.main(
            ["--current-model", "claude-opus-4-8"],
            trigger_fn=trigger_fn,
            state_reader=lambda: state["model"],
            state_writer=lambda m: state.__setitem__("model", m),
        )
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
