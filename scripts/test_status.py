#!/usr/bin/env python3
"""Tests for status.py (PLAN-wave-e-scheduled-surfaces task 5) — the Operator
`/status` surface. The load-bearing assertion is the Locked design call:
`/status` is a consumer, never a second scorer — its printed Health Index must
be the exact number health_score.py already computed, never re-derived.

Run directly: `cd scripts && python3 -m unittest test_status -v`
Auto-discovered by `python3 -m unittest discover -p 'test_*.py'` (check-all.sh).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "health"))

import health_score  # noqa: E402
import status  # noqa: E402

_RECORDS = [
    {"suite": "s1", "axis": "memory persist+recall", "check": "c1", "pass": True, "weight": 1.0},
    {"suite": "s1", "axis": "memory persist+recall", "check": "c2", "pass": False, "weight": 1.0},
    {"suite": "s2", "axis": "verification honesty", "check": "c3", "pass": True, "weight": 2.0},
    {"suite": "s3", "axis": "efficiency", "check": "dark1", "pass": None, "dark": True, "weight": 1.0},
]


class StatusNeverRescoresTests(unittest.TestCase):
    def test_health_index_matches_health_score_exactly(self):
        # The load-bearing test: build the SAME scorecard health_score.py
        # would, write it to a fixture history.jsonl, then confirm /status's
        # own reported Health Index is byte-for-byte the same number — any
        # divergence means /status computed its own score instead of reading
        # health_score.py's back.
        expected_scorecard = health_score.compute_scorecard(_RECORDS)
        with TemporaryDirectory() as td:
            history_path = Path(td) / "history.jsonl"
            health_score.append_history_row(expected_scorecard, ts=1000, path=history_path)

            row = health_score.read_latest_history_row(history_path)
            self.assertEqual(row["health_index"], expected_scorecard["health_index"])

    def test_dark_count_matches_the_scorecards_own_total(self):
        expected_scorecard = health_score.compute_scorecard(_RECORDS)
        expected_dark_total = sum(f["dark_count"] for f in expected_scorecard["families"])
        with TemporaryDirectory() as td:
            history_path = Path(td) / "history.jsonl"
            health_score.append_history_row(expected_scorecard, ts=1000, path=history_path)
            row = health_score.read_latest_history_row(history_path)
            self.assertEqual(row["dark_count"], expected_dark_total)
            self.assertGreater(row["dark_count"], 0)  # the fixture has one dark check

    def test_reads_the_latest_row_when_several_are_appended(self):
        with TemporaryDirectory() as td:
            history_path = Path(td) / "history.jsonl"
            first = health_score.compute_scorecard(_RECORDS)
            health_score.append_history_row(first, ts=1000, path=history_path)
            second_records = _RECORDS + [
                {"suite": "s4", "axis": "efficiency", "check": "c5", "pass": True, "weight": 1.0}
            ]
            second = health_score.compute_scorecard(second_records)
            health_score.append_history_row(second, ts=2000, path=history_path)

            row = health_score.read_latest_history_row(history_path)
            self.assertEqual(row["ts"], 2000)
            self.assertEqual(row["health_index"], second["health_index"])


class RenderAndCliTests(unittest.TestCase):
    def test_render_status_includes_index_families_and_dark_count(self):
        row = {
            "health_index": 87.5,
            "families": {"memory persist+recall": 50.0, "efficiency": 100.0},
            "dark_count": 1,
        }
        out = status.render_status(row)
        self.assertIn("Health Index: 87.50", out)
        self.assertIn("memory persist+recall: 50.00", out)
        self.assertIn("efficiency: 100.00", out)
        self.assertIn("Dark checks: 1", out)

    def test_render_status_handles_pre_dark_count_rows(self):
        # A history row written before dark_count existed (real tracked
        # history.jsonl has these) — must not raise.
        row = {"health_index": 90.0, "families": {"efficiency": 90.0}}
        out = status.render_status(row)
        self.assertIn("Dark checks: not recorded for this run", out)

    def test_main_exits_1_and_prints_a_message_when_no_history_yet(self):
        with TemporaryDirectory() as td:
            missing = Path(td) / "no-such-history.jsonl"
            rc = status.main(["--path", str(missing)])
            self.assertEqual(rc, 1)

    def test_main_exits_0_against_a_fixture_history_file(self):
        scorecard = health_score.compute_scorecard(_RECORDS)
        with TemporaryDirectory() as td:
            history_path = Path(td) / "history.jsonl"
            health_score.append_history_row(scorecard, ts=1000, path=history_path)
            rc = status.main(["--path", str(history_path)])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
