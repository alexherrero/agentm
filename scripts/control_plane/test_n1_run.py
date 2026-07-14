#!/usr/bin/env python3
"""Tests for scripts/control_plane/n1_run.py (PLAN-autonomy-control-plane
task 6 orchestration wiring — NOT the acceptance demo itself, which
requires a real unattended overnight run; see this plan's own progress log
for why that's parked rather than simulated here).

The main test class is fully hermetic (fake grade-declarer, fake handoff-
builder, fake dispatcher/board-runner) so it runs on a clean CI runner
with no crickets sibling checkout. A separate, skip-guarded class proves
the same orchestration against the real crickets-backed functions when a
sibling checkout is reachable."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import n1_run as n1  # noqa: E402
import dispatch as dp  # noqa: E402
import grade as gr  # noqa: E402
import handoff as hf  # noqa: E402
import board_sync as bs  # noqa: E402


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


def _fake_grade_declarer(plan, *, grade, root, telemetry_root):
    return {"event": "run-start", "tags": {"plan": plan, "grade": grade}}


def _fake_handoff_builder(results, session_outputs, dest_dir):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"prompts": [{"title": r.name} for r in results], "snapshotted_files": sorted(session_outputs)}
    (dest_dir / "prompts.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class RunN1SequenceTests(unittest.TestCase):
    """Fully hermetic -- fakes every crickets-backed collaborator."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.telemetry_root = self.tmp / "telemetry"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, config, **overrides):
        kwargs = dict(dispatcher=_fake_dispatcher, grade_declarer=_fake_grade_declarer, handoff_builder=_fake_handoff_builder)
        kwargs.update(overrides)
        return n1.run_n1_sequence(config, **kwargs)

    def test_dispatches_every_work_item(self):
        items = [
            dp.WorkItem(plan="n1", task="1", prompt="do a", cwd=str(self.tmp)),
            dp.WorkItem(plan="n1", task="2", prompt="do b", cwd=str(self.tmp)),
        ]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root)
        report = self._run(config)
        self.assertEqual(len(report.dispatch_results), 2)
        self.assertEqual({r.task for r in report.dispatch_results}, {"1", "2"})

    def test_work_item_with_no_cwd_dispatches_under_config_cwd(self):
        # V8 proving Phase 3, 2026-07-13 -- confirmed live: a work item with
        # no cwd of its own used to fall through to dispatch()'s bare
        # Path.cwd() (wherever *this process* happens to be running from,
        # e.g. scripts/ per the runner's own invocation convention), not
        # the project root a fleet-dispatched session actually needs.
        items = [dp.WorkItem(plan="n1", task="1", prompt="x", cwd=None)]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root)
        report = self._run(config)
        self.assertEqual(report.dispatch_results[0].cwd, str(self.tmp))

    def test_work_item_with_its_own_cwd_is_not_overridden(self):
        own = self.tmp / "own-subdir"
        own.mkdir()
        items = [dp.WorkItem(plan="n1", task="1", prompt="x", cwd=str(own))]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root)
        report = self._run(config)
        self.assertEqual(report.dispatch_results[0].cwd, str(own))

    def test_grade_event_declared(self):
        items = [dp.WorkItem(plan="n1", task="1", prompt="x", cwd=str(self.tmp))]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root, grade="G-ship")
        report = self._run(config)
        self.assertIsNotNone(report.grade_event)
        self.assertEqual(report.grade_event["tags"]["grade"], "G-ship")

    def test_no_board_outcomes_without_a_project_config(self):
        items = [dp.WorkItem(plan="n1", task="1", prompt="x", cwd=str(self.tmp))]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root)
        report = self._run(config)
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
        # board_sync.post_dispatch_progress resolves project_sync.py before
        # ever reaching the (already-faked) runner -- stub that resolution
        # too so this stays hermetic without a real crickets sibling.
        with mock.patch.object(bs, "find_project_sync_script", return_value=Path("/fake/project_sync.py")):
            report = self._run(config, board_runner=runner)
        self.assertEqual(len(report.board_outcomes), 2)
        self.assertEqual(len(runner.calls), 2)
        for cmd in runner.calls:
            self.assertIn("--dry-run", cmd)

    def test_handoff_manifest_built_for_the_batch(self):
        items = [dp.WorkItem(plan="n1", task="1", prompt="x", cwd=str(self.tmp))]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root)
        report = self._run(config)
        self.assertIsNotNone(report.handoff_manifest)
        self.assertEqual(len(report.handoff_manifest["prompts"]), 1)
        dest = self.tmp / "_n1_handoff"
        self.assertTrue((dest / "prompts.json").is_file())

    def test_empty_work_items_is_a_clean_run(self):
        config = n1.N1Config(plan="n1", work_items=[], cwd=self.tmp, telemetry_root=self.telemetry_root)
        report = self._run(config)
        self.assertEqual(report.dispatch_results, [])
        self.assertIsNotNone(report.grade_event)


class CliTests(unittest.TestCase):
    """n1_run.py's CLI (CONS-7 task 6): argument parsing + work-items-file
    loading + report serialization. `run_n1_sequence` itself is mocked here
    (already covered end-to-end by `RunN1SequenceTests` above) so these stay
    hermetic and never risk a real `claude --bg` dispatch."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_work_items(self, items) -> Path:
        path = self.tmp / "work-items.json"
        path.write_text(json.dumps({"work_items": items}), encoding="utf-8")
        return path

    def test_load_work_items_builds_workitem_objects(self):
        path = self._write_work_items([{"plan": "n1", "task": "1", "prompt": "do a"}])
        items = n1._load_work_items(path)
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], dp.WorkItem)
        self.assertEqual(items[0].plan, "n1")

    def test_load_work_items_missing_file_raises_value_error(self):
        with self.assertRaises(ValueError):
            n1._load_work_items(self.tmp / "nope.json")

    def test_load_work_items_malformed_shape_raises_value_error(self):
        path = self.tmp / "bad.json"
        path.write_text(json.dumps({"not_work_items": []}), encoding="utf-8")
        with self.assertRaises(ValueError):
            n1._load_work_items(path)

    def test_load_work_items_bad_field_raises_value_error(self):
        path = self._write_work_items([{"plan": "n1", "not_a_real_field": "x"}])
        with self.assertRaises(ValueError):
            n1._load_work_items(path)

    def test_main_builds_config_and_prints_json_report(self):
        path = self._write_work_items([{"plan": "n1", "task": "1", "prompt": "x", "cwd": str(self.tmp)}])
        fake_report = n1.N1Report(grade_event={"event": "run-start"})
        with mock.patch.object(n1, "run_n1_sequence", return_value=fake_report) as run_mock, \
                mock.patch("builtins.print") as print_mock:
            rc = n1.main(["--plan", "n1", "--work-items", str(path), "--cwd", str(self.tmp)])
        self.assertEqual(rc, 0)
        run_mock.assert_called_once()
        called_config = run_mock.call_args[0][0]
        self.assertEqual(called_config.plan, "n1")
        self.assertEqual(len(called_config.work_items), 1)
        self.assertTrue(called_config.dry_run_board)  # --live-board not passed -> stays dry-run
        printed = print_mock.call_args[0][0]
        parsed = json.loads(printed)
        self.assertEqual(parsed["grade_event"]["event"], "run-start")

    def test_main_live_board_flag_disables_dry_run(self):
        path = self._write_work_items([{"plan": "n1", "task": "1", "prompt": "x"}])
        fake_report = n1.N1Report(grade_event=None)
        with mock.patch.object(n1, "run_n1_sequence", return_value=fake_report) as run_mock, \
                mock.patch("builtins.print"):
            n1.main(["--plan", "n1", "--work-items", str(path), "--live-board"])
        called_config = run_mock.call_args[0][0]
        self.assertFalse(called_config.dry_run_board)

    def test_main_bad_work_items_file_returns_2_not_a_traceback(self):
        with mock.patch("builtins.print"):
            rc = n1.main(["--plan", "n1", "--work-items", str(self.tmp / "missing.json")])
        self.assertEqual(rc, 2)


class RealCricketsN1BridgeTests(unittest.TestCase):
    """Real-bridge: exercises the actual grade.declare_run_start +
    handoff.build_fleet_handoff_pack (dispatcher/board still faked --
    those need a real claude --bg / gh call, out of scope for a unit
    test). Skipped when no crickets sibling checkout is reachable."""

    @classmethod
    def setUpClass(cls):
        gr._reset_cache_for_tests()
        hf._reset_cache_for_tests()
        if gr.load_event_log_module() is None or hf.load_handoff_pack_module() is None:
            raise unittest.SkipTest("crickets sibling checkout unavailable -- real-bridge test skipped")

    @classmethod
    def tearDownClass(cls):
        gr._reset_cache_for_tests()
        hf._reset_cache_for_tests()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.telemetry_root = self.tmp / "telemetry"

    def tearDown(self):
        self._tmp.cleanup()

    def test_real_grade_and_handoff_pack_produced(self):
        items = [dp.WorkItem(plan="n1", task="1", prompt="x", cwd=str(self.tmp))]
        config = n1.N1Config(plan="n1", work_items=items, cwd=self.tmp, telemetry_root=self.telemetry_root, grade="G-ship")
        report = n1.run_n1_sequence(config, dispatcher=_fake_dispatcher)
        self.assertIsNotNone(report.grade_event)
        self.assertEqual(report.grade_event["event"], "run-start")
        self.assertEqual(report.grade_event["tags"]["grade"], "G-ship")
        self.assertIsNotNone(report.handoff_manifest)


if __name__ == "__main__":
    unittest.main()
