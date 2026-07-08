#!/usr/bin/env python3
"""Tests for scripts/health/observability_console.py (PLAN-observability-
console task 1)."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import observability_console as oc  # noqa: E402


def _make_rollup(path: Path, *, plan_rows=(), task_rows=(), model_rows=(), window_rows=()) -> None:
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
        for plan, task, cost, count in task_rows:
            conn.execute("INSERT INTO by_task VALUES (?, ?, ?, ?)", (plan, task, cost, count))
        for model, cost, count in model_rows:
            conn.execute("INSERT INTO by_model VALUES (?, ?, ?)", (model, cost, count))
        for ws, cost, count in window_rows:
            conn.execute("INSERT INTO by_window VALUES (?, ?, ?)", (ws, cost, count))
        conn.commit()
    finally:
        conn.close()


class ReadRollupTests(unittest.TestCase):
    def test_missing_db_raises_value_error(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                oc.compute_console_data(Path(td) / "no-such.db")


class ComputeConsoleDataTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "rollup.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_rollup_yields_zeroed_data(self):
        _make_rollup(self.db_path)
        data = oc.compute_console_data(self.db_path)
        self.assertEqual(data["total_spend_usd"], 0.0)
        self.assertEqual(data["plan_count"], 0)
        self.assertEqual(data["cost_per_plan_usd"], 0.0)
        self.assertIsNone(data["current_window"])

    def test_aggregates_match_hand_computed_sums(self):
        _make_rollup(
            self.db_path,
            plan_rows=[("p1", 3.0, 2), ("p2", 7.0, 1)],
            task_rows=[("p1", "1", 3.0, 2)],
            model_rows=[("claude-sonnet-5", 10.0, 3)],
            window_rows=[("2026-07-07T00:00:00Z", 10.0, 3)],
        )
        data = oc.compute_console_data(self.db_path)
        self.assertAlmostEqual(data["total_spend_usd"], 10.0, places=6)
        self.assertEqual(data["plan_count"], 2)
        self.assertAlmostEqual(data["cost_per_plan_usd"], 5.0, places=6)
        self.assertEqual(data["current_window"]["cost_usd"], 10.0)

    def test_current_window_is_the_latest_by_start(self):
        _make_rollup(
            self.db_path,
            window_rows=[
                ("2026-07-07T00:00:00Z", 1.0, 1),
                ("2026-07-07T06:00:00Z", 2.0, 1),
            ],
        )
        data = oc.compute_console_data(self.db_path)
        self.assertEqual(data["current_window"]["window_start"], "2026-07-07T06:00:00Z")

    def test_window_utilization_uses_budget_config(self):
        _make_rollup(self.db_path, window_rows=[("2026-07-07T00:00:00Z", 5.0, 1)])
        budget = self.tmp / "budget.yaml"
        budget.write_text("window_usd_ceiling: 10.0\n", encoding="utf-8")
        data = oc.compute_console_data(self.db_path, budget_config=budget)
        self.assertEqual(data["window_utilization_pct"], 50.0)

    def test_window_utilization_none_without_budget_config(self):
        _make_rollup(self.db_path, window_rows=[("2026-07-07T00:00:00Z", 5.0, 1)])
        data = oc.compute_console_data(self.db_path)
        self.assertIsNone(data["window_utilization_pct"])

    def test_missing_table_raises_value_error(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE TABLE by_plan (plan TEXT, cost_usd REAL, event_count INTEGER)")
        conn.commit()
        conn.close()
        with self.assertRaises(ValueError):
            oc.compute_console_data(self.db_path)


class RenderHtmlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "rollup.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_render_is_deterministic(self):
        _make_rollup(
            self.db_path,
            plan_rows=[("p1", 3.0, 2)],
            task_rows=[("p1", "1", 3.0, 2)],
            model_rows=[("claude-sonnet-5", 3.0, 2)],
            window_rows=[("2026-07-07T00:00:00Z", 3.0, 2)],
        )
        data = oc.compute_console_data(self.db_path)
        self.assertEqual(oc.render_html(data), oc.render_html(data))

    def test_render_has_no_network_or_ai_calls(self):
        source = (Path(oc.__file__)).read_text(encoding="utf-8")
        for banned in ("requests.", "urllib.request", "http.client", "subprocess.run", "anthropic"):
            self.assertNotIn(banned, source)

    def test_render_contains_expected_figures(self):
        _make_rollup(self.db_path, plan_rows=[("p1", 3.0, 2)])
        data = oc.compute_console_data(self.db_path)
        page = oc.render_html(data)
        self.assertIn("$3.0000", page)
        self.assertIn("p1", page)

    def test_render_escapes_html_in_plan_names(self):
        _make_rollup(self.db_path, plan_rows=[("<script>alert(1)</script>", 1.0, 1)])
        data = oc.compute_console_data(self.db_path)
        page = oc.render_html(data)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_render_handles_empty_rollup_gracefully(self):
        _make_rollup(self.db_path)
        data = oc.compute_console_data(self.db_path)
        page = oc.render_html(data)
        self.assertIn("no data yet", page)
        self.assertIn("no window data yet", page)


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "rollup.db"
        _make_rollup(self.db_path, plan_rows=[("p1", 1.0, 1)])

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_output_file(self):
        out = self.tmp / "console.html"
        rc = oc.main(["--db-path", str(self.db_path), "--output", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.is_file())
        self.assertIn("AgentM Observability Console", out.read_text(encoding="utf-8"))

    def test_check_determinism_exits_zero(self):
        rc = oc.main(["--db-path", str(self.db_path), "--check-determinism"])
        self.assertEqual(rc, 0)

    def test_missing_db_exits_two(self):
        rc = oc.main(["--db-path", str(self.tmp / "nope.db"), "--output", str(self.tmp / "out.html")])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
