#!/usr/bin/env python3
"""Unit coverage for scripts/health/health_score.py (R1.8).

Run directly:
    cd scripts && python3 -m unittest test_health_score
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE / "health") not in sys.path:
    sys.path.insert(0, str(_HERE / "health"))

import health_score  # noqa: E402


def _rec(axis, check, passed, weight=1.0, suite="s"):
    return {"suite": suite, "axis": axis, "check": check, "pass": passed, "weight": weight}


class TestScoreAxis(unittest.TestCase):
    def test_all_pass_scores_100(self):
        score, live, dark = health_score.score_axis([_rec("x", "a", True), _rec("x", "b", True)])
        self.assertEqual(score, 100.0)
        self.assertEqual(live, 2)
        self.assertEqual(dark, 0)

    def test_all_fail_scores_0(self):
        score, live, dark = health_score.score_axis([_rec("x", "a", False)])
        self.assertEqual(score, 0.0)

    def test_weighted_mix(self):
        score, _, _ = health_score.score_axis([_rec("x", "a", True, weight=2.0), _rec("x", "b", False, weight=1.0)])
        self.assertAlmostEqual(score, 100.0 * 2 / 3)

    def test_dark_checks_excluded_from_denominator(self):
        records = [_rec("x", "a", True, weight=1.0), {"suite": "s", "axis": "x", "check": "b", "pass": None, "dark": True, "weight": 5.0}]
        score, live, dark = health_score.score_axis(records)
        self.assertEqual(score, 100.0)
        self.assertEqual(live, 1)
        self.assertEqual(dark, 1)

    def test_empty_list_scores_0(self):
        score, live, dark = health_score.score_axis([])
        self.assertEqual((score, live, dark), (0.0, 0, 0))


class TestComputeScorecard(unittest.TestCase):
    def test_health_index_excludes_unrepresented_families(self):
        records = [_rec("memory persist+recall", "a", True)]
        sc = health_score.compute_scorecard(records)
        self.assertEqual(sc["health_index"], 100.0)

    def test_health_index_weighted_across_families(self):
        records = [
            _rec("memory persist+recall", "a", True),   # weight 25, score 100
            _rec("efficiency", "b", False),              # weight 10, score 0
        ]
        sc = health_score.compute_scorecard(records)
        self.assertAlmostEqual(sc["health_index"], (25 * 100 + 10 * 0) / 35, places=2)

    def test_unknown_axis_excluded_and_reported(self):
        records = [_rec("not-a-real-family", "a", True)]
        sc = health_score.compute_scorecard(records)
        self.assertEqual(sc["health_index"], 0.0)
        self.assertIn("not-a-real-family", sc["unknown_axes"])

    def test_no_records_at_all_zeroes_index(self):
        sc = health_score.compute_scorecard([])
        self.assertEqual(sc["health_index"], 0.0)


class TestReadRecords(unittest.TestCase):
    def test_reads_jsonl_from_path(self, ):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_rec("x", "a", True)) + "\n\n")  # blank line must be skipped
            path = f.name
        try:
            records = health_score.read_records(path)
            self.assertEqual(len(records), 1)
        finally:
            Path(path).unlink()

    def test_invalid_json_raises_value_error(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("not json\n")
            path = f.name
        try:
            with self.assertRaises(ValueError):
                health_score.read_records(path)
        finally:
            Path(path).unlink()


class TestDeterminism(unittest.TestCase):
    def test_two_runs_produce_identical_markdown(self):
        records = [_rec("memory persist+recall", "a", True), _rec("efficiency", "b", False)]
        out1 = health_score.render_markdown(health_score.compute_scorecard(records))
        out2 = health_score.render_markdown(health_score.compute_scorecard(records))
        self.assertEqual(out1, out2)


class TestMainCLI(unittest.TestCase):
    def test_main_renders_markdown_to_stdout(self):
        buf = io.StringIO()
        stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(_rec("memory persist+recall", "a", True)) + "\n")
        try:
            with redirect_stdout(buf):
                rc = health_score.main([])
        finally:
            sys.stdin = stdin_backup
        self.assertEqual(rc, 0)
        self.assertIn("Health Index", buf.getvalue())

    def test_main_no_records_exits_2(self):
        stdin_backup = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            rc = health_score.main([])
        finally:
            sys.stdin = stdin_backup
        self.assertEqual(rc, 2)

    def test_check_determinism_flag_exits_0(self):
        buf = io.StringIO()
        stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(_rec("memory persist+recall", "a", True)) + "\n")
        try:
            with redirect_stdout(buf):
                rc = health_score.main(["--check-determinism"])
        finally:
            sys.stdin = stdin_backup
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
