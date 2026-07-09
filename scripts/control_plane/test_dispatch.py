#!/usr/bin/env python3
"""Tests for scripts/control_plane/dispatch.py (PLAN-autonomy-control-plane
task 2). Uses a stubbed `runner` for build/classification logic (no real
`claude --bg` process spawned in unit tests) plus a real-bridge class that
resolves against the actual crickets sibling checkout."""
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

import dispatch as dp  # noqa: E402


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_runner(returncode=0, stdout="", stderr=""):
    calls = []

    def runner(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return _FakeCompletedProcess(returncode, stdout, stderr)

    runner.calls = calls
    return runner


class BuildDispatchCommandTests(unittest.TestCase):
    def test_command_shape(self):
        item = dp.WorkItem(plan="myplan", task="1", prompt="do the thing")
        classification = {"model_id": "claude-sonnet-5", "effort": "medium", "tier": "T1-Execute", "tier_source": "ROLE-MATCH", "model_alias": "sonnet"}
        cmd = dp.build_dispatch_command(item, classification)
        self.assertEqual(cmd, [
            "claude", "--bg", "--model", "sonnet", "--effort", "medium",
            "--name", "myplan-1", "do the thing",
        ])

    def test_falls_back_to_model_id_when_no_alias(self):
        item = dp.WorkItem(plan="p", task="1", prompt="x")
        classification = {"model_id": "opusplan", "effort": "medium", "tier": "T1-Execute", "tier_source": "FRONTMATTER", "model_alias": None}
        cmd = dp.build_dispatch_command(item, classification)
        self.assertIn("opusplan", cmd)

    def test_dispatch_name_is_plan_dash_task(self):
        item = dp.WorkItem(plan="myplan", task="3", prompt="x")
        self.assertEqual(dp.dispatch_name(item), "myplan-3")


class WriteActivePlanMarkerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_marker_written(self):
        dp._write_active_plan_marker(self.tmp, "myplan")
        self.assertEqual((self.tmp / ".harness" / "active-plan").read_text(encoding="utf-8"), "myplan\n")


class WriteActiveTaskMarkerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_marker_written(self):
        dp._write_active_task_marker(self.tmp, "3")
        self.assertEqual((self.tmp / ".harness" / "active-task").read_text(encoding="utf-8"), "3\n")


class ResolveDispatchClassificationTests(unittest.TestCase):
    def setUp(self):
        dp._reset_cache_for_tests()

    def tearDown(self):
        dp._reset_cache_for_tests()

    def test_crickets_unresolvable_yields_fixed_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            empty_home = Path(td) / "empty_home"
            empty_home.mkdir()
            with mock.patch.object(Path, "home", return_value=empty_home):
                with mock.patch.dict("os.environ", {"CRICKETS_SCRIPTS_DIR": ""}):
                    item = dp.WorkItem(plan="p", task="1", prompt="x", role_name="explorer")
                    c = dp.resolve_dispatch_classification(item)
        self.assertEqual(c["model_id"], dp._FALLBACK_MODEL_ID)
        self.assertEqual(c["tier_source"], dp._FALLBACK_TIER_SOURCE)


class RealCricketsClassificationBridgeTests(unittest.TestCase):
    """Real-bridge: skipped when no crickets sibling checkout is reachable
    (e.g. a clean CI runner that doesn't also clone crickets)."""

    @classmethod
    def setUpClass(cls):
        dp._reset_cache_for_tests()
        if dp.load_classify_module() is None:
            raise unittest.SkipTest("crickets sibling checkout unavailable -- real-bridge test skipped")

    @classmethod
    def tearDownClass(cls):
        dp._reset_cache_for_tests()

    def test_real_crickets_role_match_resolves(self):
        # Real bridge: 'explorer' is a known ROLE_TO_WORK_TYPE key.
        item = dp.WorkItem(plan="p", task="1", prompt="x", role_name="explorer")
        c = dp.resolve_dispatch_classification(item)
        self.assertIsNotNone(c["model_id"])
        self.assertIn(c["tier_source"], ("ROLE-MATCH", "FRONTMATTER", "UNCLASSIFIED-DEFAULT"))

    def test_real_crickets_declared_frontmatter_wins(self):
        item = dp.WorkItem(plan="p", task="1", prompt="x", declared={"model": "claude-opus-4-8", "effort": "high"})
        c = dp.resolve_dispatch_classification(item)
        self.assertEqual(c["model_id"], "claude-opus-4-8")
        self.assertEqual(c["effort"], "high")
        self.assertEqual(c["tier_source"], "FRONTMATTER")
        self.assertEqual(c["model_alias"], "opus")


class DispatchEndToEndStubbedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_dispatch_writes_marker_and_calls_runner(self):
        runner = _fake_runner(returncode=0, stdout="", stderr="")
        item = dp.WorkItem(
            plan="myplan", task="2", prompt="do it", cwd=str(self.tmp),
            declared={"model": "claude-sonnet-5", "effort": "medium", "tier": "T1-Execute"},
        )
        result = dp.dispatch(item, runner=runner)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.plan, "myplan")
        self.assertEqual(result.task, "2")
        self.assertEqual((self.tmp / ".harness" / "active-plan").read_text(encoding="utf-8"), "myplan\n")
        self.assertEqual((self.tmp / ".harness" / "active-task").read_text(encoding="utf-8"), "2\n")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0]["kwargs"]["cwd"], str(self.tmp))
        self.assertEqual(result.tier, "T1-Execute")  # carried through for handoff-pack labeling (task 4)

    def test_dispatch_surfaces_nonzero_returncode(self):
        runner = _fake_runner(returncode=1, stdout="", stderr="boom")
        item = dp.WorkItem(plan="p", task="1", prompt="x", cwd=str(self.tmp), declared={"model": "claude-sonnet-5", "effort": "medium"})
        result = dp.dispatch(item, runner=runner)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "boom")


class ListAgentViewSessionsTests(unittest.TestCase):
    def test_parses_json_array(self):
        runner = _fake_runner(returncode=0, stdout=json.dumps([{"pid": 1, "name": "p-1"}]), stderr="")
        sessions = dp.list_agent_view_sessions(runner=runner)
        self.assertEqual(sessions, [{"pid": 1, "name": "p-1"}])

    def test_malformed_json_yields_empty_list(self):
        runner = _fake_runner(returncode=0, stdout="not json", stderr="")
        self.assertEqual(dp.list_agent_view_sessions(runner=runner), [])

    def test_exec_failure_yields_empty_list(self):
        def failing_runner(cmd, **kwargs):
            raise OSError("no such command")
        self.assertEqual(dp.list_agent_view_sessions(runner=failing_runner), [])

    def test_non_list_json_yields_empty_list(self):
        runner = _fake_runner(returncode=0, stdout=json.dumps({"not": "a list"}), stderr="")
        self.assertEqual(dp.list_agent_view_sessions(runner=runner), [])


if __name__ == "__main__":
    unittest.main()
