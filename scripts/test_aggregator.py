#!/usr/bin/env python3
"""Unit tests for scripts/runner/aggregator.py -- the observability ledger
aggregator (PLAN-observability-ledger, agentm half, task 1).

Run directly: `cd scripts && python3 -m unittest test_aggregator -v`
Auto-discovered by `python3 -m unittest discover -p 'test_*.py'` (check-all.sh).

A hermetic fake `analyzer` module (matching crickets' real `MessageRecord` /
`_compute_windows` shape) is injected via `build_rollup(..., analyzer=fake)`
so these tests never require a real crickets sibling checkout. A separate
small class proves `load_analyzer_module()`'s own sibling-clone resolution
(env override -> conventional clone -> None) without needing the real
analyzer.py to exist at either path.
"""
from __future__ import annotations

import json
import os
import sqlite3
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from runner import aggregator


@dataclass
class _FakeMessageRecord:
    timestamp: str
    model: str
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost_usd: float
    is_floor: bool


@dataclass
class _FakeWindowSummary:
    start_ts: str
    message_count: int
    total_cost_usd: float


class _FakeAnalyzer:
    """Mirrors crickets' analyzer.py just enough for the aggregator: real
    five-hour bucketing logic (copied verbatim from analyzer._compute_windows
    for test hermeticity -- the aggregator itself never vendors this)."""

    MessageRecord = _FakeMessageRecord
    _WINDOW = timedelta(hours=5)

    @staticmethod
    def _parse_ts(ts):
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None

    @classmethod
    def _compute_windows(cls, messages):
        if not messages:
            return []
        windows = []
        win_start_ts = messages[0].timestamp
        win_start_dt = cls._parse_ts(win_start_ts)
        win_cost = 0.0
        win_count = 0
        for msg in messages:
            dt = cls._parse_ts(msg.timestamp)
            if win_start_dt is not None and dt is not None and dt - win_start_dt >= cls._WINDOW:
                windows.append(_FakeWindowSummary(win_start_ts, win_count, win_cost))
                win_start_ts = msg.timestamp
                win_start_dt = dt
                win_cost = 0.0
                win_count = 0
            win_cost += msg.cost_usd
            win_count += 1
        if win_count:
            windows.append(_FakeWindowSummary(win_start_ts, win_count, win_cost))
        return windows


_ANALYZER = _FakeAnalyzer()


def _event(ts, model, cost, *, event="session-cost", plan=None, task=None):
    return {
        "ts": ts, "schema_version": 1, "device": "devbox", "session_id": "s1",
        "parent_id": None, "event": event, "model": model,
        "tokens_by_kind": {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0},
        "cost_usd": cost, "tags": {"plan": plan, "task": task, "arc": None, "grade": None},
    }


class LoadEventsTests(unittest.TestCase):
    def test_missing_telemetry_dir_is_empty(self):
        with TemporaryDirectory() as td:
            self.assertEqual(aggregator.load_events(Path(td) / "nope"), [])

    def test_reads_across_multiple_files(self):
        with TemporaryDirectory() as td:
            d = Path(td)
            (d / "events-202606.jsonl").write_text(json.dumps(_event("2026-06-30T00:00:00Z", "a", 1.0)) + "\n", encoding="utf-8")
            (d / "events-202607.jsonl").write_text(json.dumps(_event("2026-07-01T00:00:00Z", "b", 2.0)) + "\n", encoding="utf-8")
            events = aggregator.load_events(d)
            self.assertEqual(len(events), 2)

    def test_malformed_line_is_skipped(self):
        with TemporaryDirectory() as td:
            d = Path(td)
            (d / "events-202607.jsonl").write_text("not json\n" + json.dumps(_event("2026-07-01T00:00:00Z", "a", 1.0)) + "\n", encoding="utf-8")
            events = aggregator.load_events(d)
            self.assertEqual(len(events), 1)


class BuildRollupTests(unittest.TestCase):
    def _query_all(self, db_path):
        conn = sqlite3.connect(str(db_path))
        try:
            out = {}
            for table in ("by_plan", "by_task", "by_model", "by_window"):
                cur = conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
                out[table] = cur.fetchall()
            return out
        finally:
            conn.close()

    def test_raises_without_a_resolvable_analyzer(self):
        # analyzer=None means "auto-resolve", not "force absent" -- stub out
        # the resolution path entirely (empty $HOME + no env override) so
        # this doesn't just find the real crickets sibling on this machine.
        with TemporaryDirectory() as td:
            aggregator._reset_cache_for_tests()
            empty_home = Path(td) / "empty_home"
            empty_home.mkdir()
            with mock.patch.object(Path, "home", return_value=empty_home):
                with mock.patch.dict(os.environ, {"CRICKETS_SCRIPTS_DIR": ""}):
                    with self.assertRaises(RuntimeError):
                        aggregator.build_rollup([], Path(td) / "rollup.db", analyzer=None)
            aggregator._reset_cache_for_tests()

    def test_aggregates_by_plan_task_model(self):
        events = [
            _event("2026-07-07T10:00:00Z", "claude-sonnet-5", 1.0, plan="p1", task="1"),
            _event("2026-07-07T10:05:00Z", "claude-sonnet-5", 2.0, plan="p1", task="1"),
            _event("2026-07-07T10:10:00Z", "claude-opus-4-8", 3.0, plan="p1", task="2"),
            _event("2026-07-07T10:15:00Z", "claude-opus-4-8", 4.0, plan="p2", task="1"),
        ]
        with TemporaryDirectory() as td:
            db_path = Path(td) / "rollup.db"
            aggregator.build_rollup(events, db_path, analyzer=_ANALYZER)
            rows = self._query_all(db_path)
            by_plan = dict((r[0], (r[1], r[2])) for r in rows["by_plan"])
            self.assertAlmostEqual(by_plan["p1"][0], 6.0, places=6)
            self.assertEqual(by_plan["p1"][1], 3)
            self.assertAlmostEqual(by_plan["p2"][0], 4.0, places=6)
            self.assertEqual(by_plan["p2"][1], 1)

            by_task = {(r[0], r[1]): (r[2], r[3]) for r in rows["by_task"]}
            self.assertAlmostEqual(by_task[("p1", "1")][0], 3.0, places=6)
            self.assertEqual(by_task[("p1", "1")][1], 2)
            self.assertAlmostEqual(by_task[("p1", "2")][0], 3.0, places=6)

            by_model = dict((r[0], (r[1], r[2])) for r in rows["by_model"])
            self.assertAlmostEqual(by_model["claude-sonnet-5"][0], 3.0, places=6)
            self.assertEqual(by_model["claude-sonnet-5"][1], 2)
            self.assertAlmostEqual(by_model["claude-opus-4-8"][0], 7.0, places=6)
            self.assertEqual(by_model["claude-opus-4-8"][1], 2)

    def test_events_with_no_plan_tag_skip_by_plan_and_by_task_but_count_in_by_model(self):
        events = [_event("2026-07-07T10:00:00Z", "claude-sonnet-5", 1.0, plan=None, task=None)]
        with TemporaryDirectory() as td:
            db_path = Path(td) / "rollup.db"
            aggregator.build_rollup(events, db_path, analyzer=_ANALYZER)
            rows = self._query_all(db_path)
            self.assertEqual(rows["by_plan"], [])
            self.assertEqual(rows["by_task"], [])
            self.assertEqual(len(rows["by_model"]), 1)

    def test_non_session_cost_events_are_excluded(self):
        events = [
            _event("2026-07-07T10:00:00Z", "claude-sonnet-5", 1.0, event="run-start", plan="p1", task="1"),
            _event("2026-07-07T10:05:00Z", "claude-sonnet-5", 2.0, event="session-cost", plan="p1", task="1"),
        ]
        with TemporaryDirectory() as td:
            db_path = Path(td) / "rollup.db"
            aggregator.build_rollup(events, db_path, analyzer=_ANALYZER)
            rows = self._query_all(db_path)
            by_plan = dict((r[0], (r[1], r[2])) for r in rows["by_plan"])
            self.assertAlmostEqual(by_plan["p1"][0], 2.0, places=6)
            self.assertEqual(by_plan["p1"][1], 1)

    def test_windows_bucket_across_five_hours(self):
        events = [
            _event("2026-07-07T00:00:00Z", "a", 1.0),
            _event("2026-07-07T02:00:00Z", "a", 1.0),   # same window
            _event("2026-07-07T06:00:00Z", "a", 1.0),   # new window (>=5h later)
        ]
        with TemporaryDirectory() as td:
            db_path = Path(td) / "rollup.db"
            aggregator.build_rollup(events, db_path, analyzer=_ANALYZER)
            rows = self._query_all(db_path)
            self.assertEqual(len(rows["by_window"]), 2)
            first_window = rows["by_window"][0]
            self.assertAlmostEqual(first_window[1], 2.0, places=6)
            self.assertEqual(first_window[2], 2)

    def test_rebuild_twice_is_byte_identical(self):
        events = [
            _event("2026-07-07T10:00:00Z", "claude-sonnet-5", 1.23456, plan="p1", task="1"),
            _event("2026-07-07T11:00:00Z", "claude-opus-4-8", 9.87654, plan="p2", task="3"),
        ]
        with TemporaryDirectory() as td:
            d = Path(td)
            db1 = d / "rollup1.db"
            db2 = d / "rollup2.db"
            aggregator.build_rollup(events, db1, analyzer=_ANALYZER)
            aggregator.build_rollup(events, db2, analyzer=_ANALYZER)
            self.assertEqual(db1.read_bytes(), db2.read_bytes())

    def test_rebuild_is_from_scratch_not_incremental(self):
        with TemporaryDirectory() as td:
            db_path = Path(td) / "rollup.db"
            aggregator.build_rollup(
                [_event("2026-07-07T10:00:00Z", "a", 1.0, plan="p1", task="1")],
                db_path, analyzer=_ANALYZER,
            )
            # Second build with a DISJOINT event set -- if it were
            # incremental, p1 would still show up.
            aggregator.build_rollup(
                [_event("2026-07-07T10:00:00Z", "b", 2.0, plan="p2", task="1")],
                db_path, analyzer=_ANALYZER,
            )
            rows = self._query_all(db_path)
            plans = {r[0] for r in rows["by_plan"]}
            self.assertEqual(plans, {"p2"})


class LoadAnalyzerModuleResolutionTests(unittest.TestCase):
    def setUp(self):
        aggregator._reset_cache_for_tests()

    def tearDown(self):
        aggregator._reset_cache_for_tests()

    def test_env_override_pointing_nowhere_yields_none_not_a_crash(self):
        # Also stub $HOME so this doesn't fall through to the real crickets
        # sibling checkout that happens to exist on this machine.
        with TemporaryDirectory() as td:
            empty_home = Path(td) / "empty_home"
            empty_home.mkdir()
            with mock.patch.object(Path, "home", return_value=empty_home):
                with mock.patch.dict(os.environ, {"CRICKETS_SCRIPTS_DIR": str(Path(td) / "nowhere")}):
                    self.assertIsNone(aggregator.load_analyzer_module())

    def test_env_override_pointing_at_a_real_analyzer_resolves(self):
        with TemporaryDirectory() as td:
            scripts_dir = Path(td) / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "analyzer.py").write_text(
                "SENTINEL = 'aggregator-test-resolved'\n", encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"CRICKETS_SCRIPTS_DIR": str(scripts_dir)}):
                mod = aggregator.load_analyzer_module()
            self.assertIsNotNone(mod)
            self.assertEqual(mod.SENTINEL, "aggregator-test-resolved")


class RealCricketsBridgeTests(unittest.TestCase):
    """Integration-style: proves the sibling-clone resolution finds the real
    crickets checkout (when present) and that `build_rollup()` produces
    correct, real windows against it -- not just the hermetic fake analyzer
    the other tests use. Skipped without a sibling checkout."""

    @classmethod
    def setUpClass(cls):
        aggregator._reset_cache_for_tests()
        if aggregator.load_analyzer_module() is None:
            raise unittest.SkipTest("crickets sibling checkout unavailable -- real-bridge test skipped")

    @classmethod
    def tearDownClass(cls):
        aggregator._reset_cache_for_tests()

    def test_real_analyzer_windows_match_hand_computed_sums(self):
        events = [
            _event("2026-07-07T00:00:00Z", "claude-sonnet-5", 1.0, plan="p1", task="1"),
            _event("2026-07-07T02:00:00Z", "claude-sonnet-5", 1.5, plan="p1", task="1"),
            _event("2026-07-07T06:00:00Z", "claude-opus-4-8", 3.0, plan="p1", task="2"),
        ]
        with TemporaryDirectory() as td:
            db_path = Path(td) / "rollup.db"
            aggregator.build_rollup(events, db_path)  # analyzer=None -> real resolution
            conn = sqlite3.connect(str(db_path))
            try:
                windows = conn.execute("SELECT window_start, cost_usd, event_count FROM by_window ORDER BY window_start").fetchall()
            finally:
                conn.close()
            self.assertEqual(len(windows), 2)
            self.assertAlmostEqual(windows[0][1], 2.5, places=6)
            self.assertEqual(windows[0][2], 2)
            self.assertAlmostEqual(windows[1][1], 3.0, places=6)
            self.assertEqual(windows[1][2], 1)


class MainCliTests(unittest.TestCase):
    def test_exits_nonzero_and_prints_stderr_when_analyzer_unresolvable(self):
        with TemporaryDirectory() as td:
            aggregator._reset_cache_for_tests()
            empty_home = Path(td) / "empty_home"
            empty_home.mkdir()
            with mock.patch.object(Path, "home", return_value=empty_home):
                with mock.patch.dict(os.environ, {"CRICKETS_SCRIPTS_DIR": ""}):
                    rc = aggregator.main([
                        "--telemetry-dir", str(Path(td) / "telemetry"),
                        "--db-path", str(Path(td) / "rollup.db"),
                    ])
            aggregator._reset_cache_for_tests()
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
