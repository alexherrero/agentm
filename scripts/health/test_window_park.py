#!/usr/bin/env python3
"""Tests for scripts/health/window_park.py (PLAN-observability-console
task 3)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import window_park as wp  # noqa: E402

_NOW = datetime(2026, 7, 7, 3, 0, 0, tzinfo=timezone.utc)


class ParkStateRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.park_dir = Path(self._tmp.name) / "park"

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_then_read_back(self):
        wp.write_park_state(
            "myplan", reason="rate-limit", task_progress="task 3 of 5 done",
            resume_command="/work --name myplan", park_dir=self.park_dir, now=_NOW,
        )
        state = wp.read_park_state("myplan", park_dir=self.park_dir)
        self.assertIsNotNone(state)
        self.assertEqual(state["plan"], "myplan")
        self.assertEqual(state["reason"], "rate-limit")
        self.assertEqual(state["resume_command"], "/work --name myplan")
        self.assertEqual(state["parked_at"], _NOW.isoformat())

    def test_read_missing_plan_is_none(self):
        self.assertIsNone(wp.read_park_state("no-such-plan", park_dir=self.park_dir))

    def test_second_park_overwrites_the_first(self):
        wp.write_park_state("p", reason="rate-limit", task_progress="first", resume_command="cmd1", park_dir=self.park_dir, now=_NOW)
        wp.write_park_state("p", reason="rate-limit", task_progress="second", resume_command="cmd2", park_dir=self.park_dir, now=_NOW)
        state = wp.read_park_state("p", park_dir=self.park_dir)
        self.assertEqual(state["task_progress"], "second")

    def test_clear_park_state_removes_the_file(self):
        wp.write_park_state("p", reason="rate-limit", task_progress="x", resume_command="cmd", park_dir=self.park_dir, now=_NOW)
        self.assertTrue(wp.clear_park_state("p", park_dir=self.park_dir))
        self.assertIsNone(wp.read_park_state("p", park_dir=self.park_dir))

    def test_clear_missing_park_returns_false(self):
        self.assertFalse(wp.clear_park_state("no-such-plan", park_dir=self.park_dir))


class RenderParkNoteTests(unittest.TestCase):
    def test_note_names_where_when_and_resume_command(self):
        state = {
            "plan": "myplan", "reason": "rate-limit", "parked_at": _NOW.isoformat(),
            "task_progress": "task 2 of 4 done", "resume_command": "/work --name myplan",
        }
        note = wp.render_park_note(state)
        self.assertIn("myplan", note)
        self.assertIn("rate-limit", note)
        self.assertIn(_NOW.isoformat(), note)
        self.assertIn("task 2 of 4 done", note)
        self.assertIn("/work --name myplan", note)
        self.assertIn("never resumes itself", note)


class WriteParkNoteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vault = self.tmp / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_note_with_frontmatter(self):
        state = {
            "plan": "myplan", "reason": "rate-limit", "parked_at": _NOW.isoformat(),
            "task_progress": "x", "resume_command": "cmd",
        }
        target = wp.write_park_note(self.vault, state, now=_NOW)
        self.assertIsNotNone(target)
        self.assertEqual(target.parent, self.vault / "_briefs")
        content = target.read_text(encoding="utf-8")
        self.assertIn("kind: brief", content)
        self.assertIn("park_plan: myplan", content)
        self.assertIn("cmd", content)

    def test_missing_vault_is_a_clean_noop(self):
        state = {"plan": "p", "reason": "r", "parked_at": _NOW.isoformat(), "task_progress": "x", "resume_command": "c"}
        target = wp.write_park_note(self.tmp / "no-such-vault", state, now=_NOW)
        self.assertIsNone(target)


class ParkRunEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        self.park_dir = self.tmp / "park"

    def tearDown(self):
        self._tmp.cleanup()

    def test_park_run_writes_both_state_and_note(self):
        result = wp.park_run(
            "myplan", reason="rate-limit", task_progress="task 3 of 5 done",
            resume_command="/work --name myplan", vault_path=self.vault,
            park_dir=self.park_dir, now=_NOW,
        )
        self.assertTrue(result["state_path"].is_file())
        self.assertIsNotNone(result["note_path"])
        self.assertTrue(result["note_path"].is_file())

    def test_park_run_without_vault_only_writes_state(self):
        result = wp.park_run(
            "myplan", reason="rate-limit", task_progress="x",
            resume_command="cmd", park_dir=self.park_dir, now=_NOW,
        )
        self.assertTrue(result["state_path"].is_file())
        self.assertIsNone(result["note_path"])

    def test_resume_command_survives_round_trip_and_is_the_recorded_one(self):
        # Proxy for the plan's "manual dogfood" acceptance: the resume
        # command recorded in the park state is exactly what the operator
        # would paste -- verified by round-tripping it back out, not just
        # asserting a write succeeded.
        result = wp.park_run(
            "myplan", reason="rate-limit", task_progress="task 3 of 5 done",
            resume_command='/work --name myplan task 4', park_dir=self.park_dir, now=_NOW,
        )
        state = wp.read_park_state("myplan", park_dir=self.park_dir)
        self.assertEqual(state["resume_command"], "/work --name myplan task 4")


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_main_writes_state_file(self):
        rc = wp.main([
            "--plan", "myplan", "--progress", "task 2 of 4", "--resume-command", "/work --name myplan",
            "--park-dir", str(self.tmp / "park"),
        ])
        self.assertEqual(rc, 0)
        self.assertTrue((self.tmp / "park" / "myplan-park-state.json").is_file())


if __name__ == "__main__":
    unittest.main()
