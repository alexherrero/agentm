#!/usr/bin/env python3
"""Tests for scripts/health/morning_report.py (PLAN-observability-console
task 4). Four fixture scenarios, one per ending cause, per the plan's own
verification wording."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import morning_report as mr  # noqa: E402


def _make_rollup(path: Path, *, plan_rows=()) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE by_plan (plan TEXT PRIMARY KEY, cost_usd REAL NOT NULL, event_count INTEGER NOT NULL);
            CREATE TABLE by_task (plan TEXT NOT NULL, task TEXT NOT NULL, cost_usd REAL NOT NULL, event_count INTEGER NOT NULL, PRIMARY KEY (plan, task));
            CREATE TABLE by_model (model TEXT PRIMARY KEY, cost_usd REAL NOT NULL, event_count INTEGER NOT NULL);
            CREATE TABLE by_window (window_start TEXT PRIMARY KEY, cost_usd REAL NOT NULL, event_count INTEGER NOT NULL);
            """
        )
        for plan, cost, count in plan_rows:
            conn.execute("INSERT INTO by_plan VALUES (?, ?, ?)", (plan, cost, count))
        conn.commit()
    finally:
        conn.close()


class ComputeMorningReportFourScenarioTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "rollup.db"
        _make_rollup(self.db_path, plan_rows=[("myplan", 12.5, 30)])

    def tearDown(self):
        self._tmp.cleanup()

    def test_scenario_plan_finished(self):
        data = mr.compute_morning_report("plan-finished", plan_slug="myplan", db_path=self.db_path)
        self.assertEqual(data["ending_cause"], "plan-finished")
        self.assertIn("plan finished", data["ending_cause_label"])
        self.assertAlmostEqual(data["spend_usd"], 12.5, places=6)
        self.assertIsNone(data["park_state"])
        page = mr.render_morning_report(data)
        self.assertIn("plan finished", page)
        self.assertIn("$12.5000", page)

    def test_scenario_gates_green(self):
        data = mr.compute_morning_report("gates-green", plan_slug="myplan", db_path=self.db_path)
        self.assertEqual(data["ending_cause"], "gates-green")
        page = mr.render_morning_report(data)
        self.assertIn("gates went green", page)
        self.assertAlmostEqual(data["spend_usd"], 12.5, places=6)

    def test_scenario_escalation_parked(self):
        data = mr.compute_morning_report("escalation-parked", plan_slug="myplan", db_path=self.db_path)
        self.assertEqual(data["ending_cause"], "escalation-parked")
        page = mr.render_morning_report(data)
        self.assertIn("escalation was parked", page)

    def test_scenario_window_exhausted_with_park_state(self):
        park_state = {
            "parked_at": "2026-07-07T03:00:00+00:00",
            "task_progress": "task 3 of 5 done",
            "resume_command": "/work --name myplan task 4",
        }
        data = mr.compute_morning_report(
            "window-exhausted", plan_slug="myplan", db_path=self.db_path, park_state=park_state,
        )
        self.assertEqual(data["ending_cause"], "window-exhausted")
        self.assertIsNotNone(data["park_state"])
        self.assertEqual(data["park_state"]["resume_command"], "/work --name myplan task 4")
        page = mr.render_morning_report(data)
        self.assertIn("window ran out", page)
        self.assertIn("/work --name myplan task 4", page)
        self.assertIn("task 3 of 5 done", page)


class ComputeMorningReportEdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "rollup.db"
        _make_rollup(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_unrecognized_ending_cause_raises(self):
        with self.assertRaises(ValueError):
            mr.compute_morning_report("made-up-cause", plan_slug="myplan", db_path=self.db_path)

    def test_plan_with_no_rollup_rows_yields_zero_spend(self):
        data = mr.compute_morning_report("plan-finished", plan_slug="never-ran", db_path=self.db_path)
        self.assertEqual(data["spend_usd"], 0.0)

    def test_missing_rollup_yields_zero_spend_not_a_crash(self):
        data = mr.compute_morning_report(
            "plan-finished", plan_slug="myplan", db_path=self.tmp / "no-such.db",
        )
        self.assertEqual(data["spend_usd"], 0.0)

    def test_window_exhausted_without_park_state_omits_park_section(self):
        data = mr.compute_morning_report("window-exhausted", plan_slug="myplan", db_path=self.db_path)
        self.assertIsNone(data["park_state"])
        page = mr.render_morning_report(data)
        self.assertNotIn("## Park details", page)


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "rollup.db"
        _make_rollup(self.db_path, plan_rows=[("myplan", 5.0, 10)])

    def tearDown(self):
        self._tmp.cleanup()

    def test_main_renders_report(self):
        rc = mr.main(["--plan", "myplan", "--ending-cause", "plan-finished", "--db-path", str(self.db_path)])
        self.assertEqual(rc, 0)

    def test_main_rejects_bad_ending_cause(self):
        with self.assertRaises(SystemExit):
            mr.main(["--plan", "myplan", "--ending-cause", "bogus", "--db-path", str(self.db_path)])

    def test_main_window_exhausted_reads_real_park_state(self):
        sys.path.insert(0, str(_HERE))
        import window_park
        park_dir = self.tmp / "park"
        window_park.write_park_state(
            "myplan", reason="rate-limit", task_progress="x", resume_command="/work --name myplan",
            park_dir=park_dir,
        )
        rc = mr.main([
            "--plan", "myplan", "--ending-cause", "window-exhausted",
            "--db-path", str(self.db_path), "--park-dir", str(park_dir),
        ])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
