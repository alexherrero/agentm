#!/usr/bin/env python3
"""Tests for scripts/control_plane/grade.py (PLAN-autonomy-control-plane
task 5)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import grade as g  # noqa: E402


class DeclareRunStartTests(unittest.TestCase):
    def setUp(self):
        g._reset_cache_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.telemetry_root = self.tmp / "telemetry"

    def tearDown(self):
        g._reset_cache_for_tests()
        self._tmp.cleanup()

    def test_run_start_event_carries_declared_grade(self):
        record = g.declare_run_start(
            "myplan", grade="G-ship", session_id="s1", root=self.tmp, telemetry_root=self.telemetry_root,
        )
        self.assertIsNotNone(record)
        self.assertEqual(record["event"], "run-start")
        self.assertEqual(record["tags"]["grade"], "G-ship")
        self.assertEqual(record["session_id"], "s1")

    def test_default_grade_is_g_ship(self):
        self.assertEqual(g.DEFAULT_GRADE, "G-ship")

    def test_event_lands_in_the_real_telemetry_file(self):
        g.declare_run_start("myplan", root=self.tmp, telemetry_root=self.telemetry_root)
        files = list(self.telemetry_root.glob("events-*.jsonl"))
        self.assertEqual(len(files), 1)
        lines = [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["event"], "run-start")

    def test_plan_tag_reads_the_active_plan_marker(self):
        harness = self.tmp / ".harness"
        harness.mkdir()
        (harness / "active-plan").write_text("myplan\n", encoding="utf-8")
        record = g.declare_run_start("myplan", root=self.tmp, telemetry_root=self.telemetry_root)
        self.assertEqual(record["tags"]["plan"], "myplan")

    def test_unresolvable_crickets_yields_none_not_a_crash(self):
        empty_home = self.tmp / "empty_home"
        empty_home.mkdir()
        with mock.patch.object(Path, "home", return_value=empty_home):
            with mock.patch.dict(os.environ, {"CRICKETS_SCRIPTS_DIR": ""}):
                record = g.declare_run_start("myplan", telemetry_root=self.telemetry_root)
        self.assertIsNone(record)


class RunActionsUnderDoctrineTests(unittest.TestCase):
    """The fixture scenario task 5 asks for: a doctrine-stop mid-run parks
    that one action, and the run continues its other work."""

    def test_all_recoverable_actions_execute(self):
        executed = []
        actions = [g.Action("a", recoverable=True), g.Action("b", recoverable=True)]
        report = g.run_actions_under_doctrine(actions, executor=lambda a: executed.append(a.name))
        self.assertEqual(executed, ["a", "b"])
        self.assertEqual(len(report.executed_actions), 2)
        self.assertEqual(len(report.parked_actions), 0)

    def test_unrecoverable_action_is_parked_not_executed(self):
        executed = []
        actions = [g.Action("risky", recoverable=False)]
        report = g.run_actions_under_doctrine(actions, executor=lambda a: executed.append(a.name))
        self.assertEqual(executed, [])
        self.assertEqual(len(report.parked_actions), 1)
        self.assertEqual(report.parked_actions[0].name, "risky")

    def test_unrecoverable_action_mid_sequence_does_not_halt_the_run(self):
        # The doctrine's own claim under test: a park is an action-level
        # stop, never a run-kill -- actions after the parked one still run.
        executed = []
        actions = [
            g.Action("safe-1", recoverable=True),
            g.Action("risky", recoverable=False),
            g.Action("safe-2", recoverable=True),
        ]
        report = g.run_actions_under_doctrine(actions, executor=lambda a: executed.append(a.name))
        self.assertEqual(executed, ["safe-1", "safe-2"])
        self.assertEqual([o.name for o in report.parked_actions], ["risky"])
        self.assertEqual(len(report.outcomes), 3)

    def test_multiple_unrecoverable_actions_all_park_independently(self):
        executed = []
        actions = [
            g.Action("risky-1", recoverable=False),
            g.Action("safe", recoverable=True),
            g.Action("risky-2", recoverable=False),
        ]
        report = g.run_actions_under_doctrine(actions, executor=lambda a: executed.append(a.name))
        self.assertEqual(executed, ["safe"])
        self.assertEqual({o.name for o in report.parked_actions}, {"risky-1", "risky-2"})

    def test_empty_action_list_is_a_clean_no_op(self):
        report = g.run_actions_under_doctrine([])
        self.assertEqual(report.outcomes, [])

    def test_report_carries_the_declared_grade(self):
        report = g.run_actions_under_doctrine([], grade="G-ship")
        self.assertEqual(report.grade, "G-ship")


if __name__ == "__main__":
    unittest.main()
