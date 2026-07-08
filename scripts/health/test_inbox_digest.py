#!/usr/bin/env python3
"""Tests for scripts/health/inbox_digest.py (PLAN-observability-console
task 2)."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import inbox_digest as idg  # noqa: E402


def _make_rollup(path: Path, *, window_rows=()) -> None:
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
        for ws, cost, count in window_rows:
            conn.execute("INSERT INTO by_window VALUES (?, ?, ?)", (ws, cost, count))
        conn.commit()
    finally:
        conn.close()


_NOW = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)


class ComputeWindowSliceTests(unittest.TestCase):
    def test_includes_only_windows_within_lookback(self):
        rows = [
            {"window_start": "2026-07-07T10:00:00Z", "cost_usd": 1.0, "event_count": 1},  # in range (2h ago)
            {"window_start": "2026-07-01T00:00:00Z", "cost_usd": 99.0, "event_count": 9},  # out of range
        ]
        s = idg.compute_window_slice(rows, now=_NOW, lookback_seconds=86400)
        self.assertAlmostEqual(s["cost_usd"], 1.0, places=6)
        self.assertEqual(s["event_count"], 1)
        self.assertEqual(s["window_count"], 1)

    def test_malformed_timestamp_is_skipped(self):
        rows = [{"window_start": "not-a-date", "cost_usd": 5.0, "event_count": 1}]
        s = idg.compute_window_slice(rows, now=_NOW, lookback_seconds=86400)
        self.assertEqual(s["cost_usd"], 0.0)

    def test_empty_rows_yields_zero(self):
        s = idg.compute_window_slice([], now=_NOW, lookback_seconds=86400)
        self.assertEqual(s, {"cost_usd": 0.0, "event_count": 0, "window_count": 0})


class DigestHistoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.history_path = self.tmp / "digest-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_then_read_back(self):
        idg.append_digest_history("daily", {"cost_usd": 1.5, "event_count": 2, "window_count": 1}, now=_NOW, history_path=self.history_path)
        rows = idg.read_all_history(self.history_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cadence"], "daily")
        self.assertEqual(rows[0]["date"], "2026-07-07")

    def test_same_day_same_cadence_is_idempotent(self):
        idg.append_digest_history("daily", {"cost_usd": 1.0, "event_count": 1, "window_count": 1}, now=_NOW, history_path=self.history_path)
        idg.append_digest_history("daily", {"cost_usd": 999.0, "event_count": 999, "window_count": 999}, now=_NOW, history_path=self.history_path)
        rows = idg.read_all_history(self.history_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cost_usd"], 1.0)  # first write wins, no duplicate/overwrite

    def test_different_cadences_same_day_both_recorded(self):
        idg.append_digest_history("daily", {"cost_usd": 1.0, "event_count": 1, "window_count": 1}, now=_NOW, history_path=self.history_path)
        idg.append_digest_history("weekly", {"cost_usd": 5.0, "event_count": 3, "window_count": 2}, now=_NOW, history_path=self.history_path)
        rows = idg.read_all_history(self.history_path)
        self.assertEqual(len(rows), 2)

    def test_read_recent_history_filters_by_lookback(self):
        old_row = {"cadence": "daily", "date": "2026-06-01", "ts": "2026-06-01T00:00:00+00:00", "cost_usd": 1.0, "event_count": 1, "window_count": 1}
        import json
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(json.dumps(old_row) + "\n", encoding="utf-8")
        idg.append_digest_history("daily", {"cost_usd": 2.0, "event_count": 1, "window_count": 1}, now=_NOW, history_path=self.history_path)
        recent = idg.read_recent_history(self.history_path, now=_NOW, lookback_seconds=30 * 86400)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["cost_usd"], 2.0)


class RenderDigestBodyTests(unittest.TestCase):
    def test_daily_body_shows_slice_figures(self):
        body = idg.render_digest_body("daily", {"cost_usd": 3.5, "event_count": 4, "window_count": 2}, now=_NOW)
        self.assertIn("$3.5000", body)
        self.assertIn("4", body)

    def test_monthly_body_shows_trend_table(self):
        trend = [
            {"date": "2026-07-01", "cadence": "daily", "cost_usd": 1.0, "event_count": 1},
            {"date": "2026-07-02", "cadence": "daily", "cost_usd": 2.0, "event_count": 1},
        ]
        body = idg.render_digest_body("monthly", None, now=_NOW, trend_rows=trend)
        self.assertIn("2026-07-01", body)
        self.assertIn("2026-07-02", body)
        self.assertIn("$3.0000", body)  # total

    def test_monthly_body_with_no_history_is_graceful(self):
        body = idg.render_digest_body("monthly", None, now=_NOW, trend_rows=[])
        self.assertIn("No digest history recorded yet", body)


class WriteDigestNoteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vault = self.tmp / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_note_with_expected_frontmatter(self):
        target = idg.write_digest_note(self.vault, "daily", "body text\n", now=_NOW)
        self.assertIsNotNone(target)
        content = target.read_text(encoding="utf-8")
        self.assertIn("kind: telemetry", content)
        self.assertIn("status: inbox", content)
        self.assertIn("digest_cadence: daily", content)
        self.assertIn("body text", content)

    def test_same_day_rerun_does_not_duplicate(self):
        t1 = idg.write_digest_note(self.vault, "daily", "first\n", now=_NOW)
        t2 = idg.write_digest_note(self.vault, "daily", "second\n", now=_NOW)
        self.assertEqual(t1, t2)
        files = list((self.vault / "personal" / "_inbox").glob("*digest-daily*"))
        self.assertEqual(len(files), 1)
        self.assertIn("first", files[0].read_text(encoding="utf-8"))  # not overwritten

    def test_missing_vault_is_a_clean_noop(self):
        target = idg.write_digest_note(self.tmp / "no-such-vault", "daily", "x", now=_NOW)
        self.assertIsNone(target)

    def test_slug_naming_convention(self):
        self.assertEqual(idg.digest_slug("weekly", _NOW), "20260707-digest-weekly")


class RunDigestEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        self.db_path = self.tmp / "rollup.db"
        self.history_path = self.tmp / "digest-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_daily_cadence_end_to_end(self):
        _make_rollup(self.db_path, window_rows=[("2026-07-07T10:00:00Z", 2.0, 1)])
        target = idg.run_digest("daily", self.db_path, self.vault, now=_NOW, history_path=self.history_path)
        self.assertIsNotNone(target)
        self.assertIn("$2.0000", target.read_text(encoding="utf-8"))
        history = idg.read_all_history(self.history_path)
        self.assertEqual(len(history), 1)

    def test_monthly_cadence_reads_history_not_rollup_directly(self):
        idg.append_digest_history("daily", {"cost_usd": 1.0, "event_count": 1, "window_count": 1}, now=_NOW, history_path=self.history_path)
        _make_rollup(self.db_path)  # empty rollup -- monthly must still show the history-based trend
        target = idg.run_digest("monthly", self.db_path, self.vault, now=_NOW, history_path=self.history_path)
        content = target.read_text(encoding="utf-8")
        self.assertIn("$1.0000", content)

    def test_missing_rollup_is_a_clean_noop(self):
        target = idg.run_digest("daily", self.tmp / "nope.db", self.vault, now=_NOW, history_path=self.history_path)
        self.assertIsNone(target)


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        self.db_path = self.tmp / "rollup.db"
        _make_rollup(self.db_path, window_rows=[("2026-07-07T10:00:00Z", 1.0, 1)])

    def tearDown(self):
        self._tmp.cleanup()

    def test_main_writes_a_note(self):
        rc = idg.main([
            "--cadence", "daily", "--db-path", str(self.db_path), "--vault-path", str(self.vault),
            "--history-path", str(self.tmp / "hist.jsonl"),
        ])
        self.assertEqual(rc, 0)
        self.assertTrue(list((self.vault / "personal" / "_inbox").glob("*.md")))


if __name__ == "__main__":
    unittest.main()
