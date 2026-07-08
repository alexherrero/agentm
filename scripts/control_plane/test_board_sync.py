#!/usr/bin/env python3
"""Tests for scripts/control_plane/board_sync.py (PLAN-autonomy-control-plane
task 3)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import board_sync as bs  # noqa: E402

_REPO_ROOT = _HERE.parent.parent  # scripts/control_plane -> scripts -> repo root
_REAL_PROJECT_JSON = _REPO_ROOT / ".harness" / "project.json"


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_runner(returncode=0, stdout="", stderr=""):
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(returncode, stdout, stderr)

    runner.calls = calls
    return runner


class FindProjectSyncScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if bs.find_project_sync_script() is None:
            raise unittest.SkipTest("crickets sibling checkout unavailable -- real-bridge test skipped")

    def test_resolves_real_sibling_checkout(self):
        script = bs.find_project_sync_script()
        self.assertIsNotNone(script)
        self.assertTrue(script.is_file())
        self.assertEqual(script.name, "project_sync.py")


class BoardSyncAvailableTests(unittest.TestCase):
    def test_missing_config_is_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(bs.board_sync_available(config_path=Path(td) / "no-such.json"))

    def test_missing_gh_is_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "project.json"
            config.write_text("{}", encoding="utf-8")
            self.assertFalse(bs.board_sync_available(config_path=config, gh_bin="no-such-binary-xyz"))


class PostDispatchProgressTests(unittest.TestCase):
    """Hermetic: `find_project_sync_script()` is stubbed so these never
    depend on a real crickets sibling checkout being reachable -- only the
    `runner` (also faked) would ever touch a real process."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = self.tmp / "project.json"
        self.config.write_text("{}", encoding="utf-8")
        self._script_patcher = mock.patch.object(bs, "find_project_sync_script", return_value=Path("/fake/project_sync.py"))
        self._script_patcher.start()

    def tearDown(self):
        self._script_patcher.stop()
        self._tmp.cleanup()

    def test_missing_config_is_a_clean_skip(self):
        result = bs.post_dispatch_progress(
            "p-1", summary="x", config_path=self.tmp / "nope.json", runner=_fake_runner(),
        )
        self.assertFalse(result["posted"])
        self.assertEqual(result["skipped_reason"], "no project.json")

    def test_missing_gh_is_a_clean_skip(self):
        result = bs.post_dispatch_progress(
            "p-1", summary="x", config_path=self.config, gh_bin="no-such-binary-xyz", runner=_fake_runner(),
        )
        self.assertFalse(result["posted"])
        self.assertEqual(result["skipped_reason"], "gh unavailable")

    def test_successful_post_calls_runner_with_expected_shape(self):
        runner = _fake_runner(returncode=0, stdout="posted", stderr="")
        result = bs.post_dispatch_progress(
            "myplan-1", summary="dispatched", config_path=self.config, commit="abc123", runner=runner,
        )
        self.assertTrue(result["posted"])
        self.assertEqual(len(runner.calls), 1)
        cmd = runner.calls[0]
        self.assertIn("post", cmd)
        self.assertIn("--type", cmd)
        self.assertIn("task-progress", cmd)
        self.assertIn("--id", cmd)
        self.assertIn("myplan-1", cmd)
        self.assertIn("--commit", cmd)
        self.assertIn("abc123", cmd)

    def test_dry_run_flag_is_passed_through(self):
        runner = _fake_runner(returncode=0)
        bs.post_dispatch_progress("p-1", summary="x", config_path=self.config, dry_run=True, runner=runner)
        self.assertIn("--dry-run", runner.calls[0])

    def test_nonzero_exit_is_not_posted(self):
        runner = _fake_runner(returncode=1, stdout="", stderr="boom")
        result = bs.post_dispatch_progress("p-1", summary="x", config_path=self.config, runner=runner)
        self.assertFalse(result["posted"])
        self.assertEqual(result["skipped_reason"], "non-zero exit")

    def test_exec_failure_is_a_clean_skip_not_a_raise(self):
        def failing_runner(cmd, **kwargs):
            raise OSError("no such command")
        result = bs.post_dispatch_progress("p-1", summary="x", config_path=self.config, runner=failing_runner)
        self.assertFalse(result["posted"])
        self.assertIn("exec failed", result["skipped_reason"])


class PostFleetRunSummaryTests(unittest.TestCase):
    """Hermetic for the same reason as PostDispatchProgressTests above."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = self.tmp / "project.json"
        self.config.write_text("{}", encoding="utf-8")
        self._script_patcher = mock.patch.object(bs, "find_project_sync_script", return_value=Path("/fake/project_sync.py"))
        self._script_patcher.start()

    def tearDown(self):
        self._script_patcher.stop()
        self._tmp.cleanup()

    def test_one_outcome_per_dispatched_item(self):
        runner = _fake_runner(returncode=0)
        results = [{"name": "p-1"}, {"name": "p-2", "status": "completed"}]
        outcomes = bs.post_fleet_run_summary(results, config_path=self.config, runner=runner)
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(o["posted"] for o in outcomes))
        self.assertEqual(len(runner.calls), 2)

    def test_accepts_dataclass_like_objects_with_attrs(self):
        class _Fake:
            def __init__(self, name):
                self.name = name
        runner = _fake_runner(returncode=0)
        outcomes = bs.post_fleet_run_summary([_Fake("p-1")], config_path=self.config, runner=runner)
        self.assertEqual(len(outcomes), 1)


class RealDryRunAgainstAgentmProjectJsonTests(unittest.TestCase):
    """Integration-style: a real (--dry-run, no actual GitHub write) call
    against this repo's own real .harness/project.json + real gh CLI."""

    @classmethod
    def setUpClass(cls):
        if not _REAL_PROJECT_JSON.is_file():
            raise unittest.SkipTest("no real .harness/project.json in this checkout")
        if not bs.board_sync_available(config_path=_REAL_PROJECT_JSON):
            raise unittest.SkipTest("board-sync preconditions (gh / project_sync.py) unavailable")

    def test_real_dry_run_post_reaches_the_real_script(self):
        # Proves the wiring (subprocess invocation, argv shape, --dry-run
        # passthrough) reaches the real project_sync.py and gets a coherent
        # response -- not that this specific made-up id resolves to a real
        # board item (it doesn't), so a clean "no item with id" rejection
        # counts as success here: the call reached real code and got a real,
        # well-formed answer back, never a crash or a silent no-op.
        result = bs.post_dispatch_progress(
            "no-such-fleet-item-xyz", summary="board-sync dogfood (dry-run, no real write)",
            config_path=_REAL_PROJECT_JSON, dry_run=True,
        )
        self.assertIsNotNone(result["returncode"])
        self.assertIn("no item with id", result["stderr"])


if __name__ == "__main__":
    unittest.main()
