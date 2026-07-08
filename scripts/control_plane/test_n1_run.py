#!/usr/bin/env python3
"""Tests for scripts/control_plane/n1_run.py (PLAN-autonomy-control-plane
task 6 orchestration wiring — NOT the acceptance demo itself, which
requires a real unattended overnight run; see this plan's own progress log
for why that's parked rather than simulated here)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import n1_run as n1  # noqa: E402
import dispatch as dp  # noqa: E402


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_dispatcher(item, **kwargs):
    return dp.DispatchResult(
        name=dp.dispatch_name(item), plan=item.plan, task=item.task,
        model_alias="sonnet", model_id="claude-sonnet-5", effort="medium", tier="T1-Execute",
        tier_source="FRONTMATTER", cwd=str(item.cwd), returncode=0, stdout="", stderr="",
    )


def _fake_board_runner(returncode=0):
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(returncode)

    runner.calls = calls
    return runner


class RunN1SequenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.telemetry_root = self.tmp / "telemetry"

    def tearDown(self):
        self._tmp.cleanup()

    def test_dispatches_every_work_item(self):
        items = [
            dp.WorkItem(plan="n1", task="1", prompt="do a", cwd=str(self.tmp)),
            dp.WorkItem(plan="n1", task="2", prompt="do b", cwd=str(self.tmp)),
        ]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root)
        report = n1.run_n1_sequence(config, dispatcher=_fake_dispatcher)
        self.assertEqual(len(report.dispatch_results), 2)
        self.assertEqual({r.task for r in report.dispatch_results}, {"1", "2"})

    def test_grade_event_declared_when_crickets_resolvable(self):
        items = [dp.WorkItem(plan="n1", task="1", prompt="x", cwd=str(self.tmp))]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root, grade="G-ship")
        report = n1.run_n1_sequence(config, dispatcher=_fake_dispatcher)
        self.assertIsNotNone(report.grade_event)
        self.assertEqual(report.grade_event["tags"]["grade"], "G-ship")

    def test_no_board_outcomes_without_a_project_config(self):
        items = [dp.WorkItem(plan="n1", task="1", prompt="x", cwd=str(self.tmp))]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root)
        report = n1.run_n1_sequence(config, dispatcher=_fake_dispatcher)
        self.assertEqual(report.board_outcomes, [])

    def test_board_outcomes_one_per_dispatched_item(self):
        config_path = self.tmp / "project.json"
        config_path.write_text("{}", encoding="utf-8")
        items = [
            dp.WorkItem(plan="n1", task="1", prompt="x", cwd=str(self.tmp)),
            dp.WorkItem(plan="n1", task="2", prompt="y", cwd=str(self.tmp)),
        ]
        config = n1.N1Config(
            plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root,
            project_config_path=config_path, dry_run_board=True,
        )
        runner = _fake_board_runner(returncode=0)
        report = n1.run_n1_sequence(config, dispatcher=_fake_dispatcher, board_runner=runner)
        self.assertEqual(len(report.board_outcomes), 2)
        self.assertEqual(len(runner.calls), 2)
        for cmd in runner.calls:
            self.assertIn("--dry-run", cmd)

    def test_handoff_manifest_built_for_the_batch(self):
        items = [dp.WorkItem(plan="n1", task="1", prompt="x", cwd=str(self.tmp))]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root)
        report = n1.run_n1_sequence(config, dispatcher=_fake_dispatcher)
        self.assertIsNotNone(report.handoff_manifest)
        self.assertEqual(len(report.handoff_manifest["prompts"]), 1)
        dest = self.tmp / "_n1_handoff"
        self.assertTrue((dest / "prompts.json").is_file())

    def test_empty_work_items_is_a_clean_run(self):
        config = n1.N1Config(plan="n1", work_items=[], cwd=self.tmp, telemetry_root=self.telemetry_root)
        report = n1.run_n1_sequence(config, dispatcher=_fake_dispatcher)
        self.assertEqual(report.dispatch_results, [])
        self.assertIsNotNone(report.grade_event)


if __name__ == "__main__":
    unittest.main()
