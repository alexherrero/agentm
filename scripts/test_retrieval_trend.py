#!/usr/bin/env python3
"""A slide in search that no single night proves (task 205).

The rule is held to the shape of what happened in November 2026, where the
weekly hit rate fell from 66% to 58% and every night's gate still passed, and
to the shapes that must not warn: flat weeks, one bad week that recovers, a
smaller gold set at the same rate, and readings that were never live.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (_SKILL, _HERE / "health", _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import corpus_scorecard as sc  # noqa: E402
import retrieval_gate_job as job  # noqa: E402
import retrieval_trend as rt  # noqa: E402

MONDAY = datetime(2026, 10, 5)  # ISO week 2026-W41


def row(hits: int, scored: int, day: str, live: bool = True) -> str:
    """The scorecard's gold-set row as it renders, live or a reprinted pin."""
    tail = (f"; measured {day} on 4300 documents; pinned 0.6667 (42 of 63) on 2026-09-18"
            if live else "")
    return f"| gold-set R@5 | {hits / scored:.4f} | {hits} of {scored} scored questions{tail} |"


def weeks_of(*weekly, scored: int = 60, nights: int = 3) -> list:
    """(day, text) pairs: `nights` nights a week at each week's hit count."""
    out = []
    for w, hits in enumerate(weekly):
        for n in range(nights):
            day = (MONDAY + timedelta(weeks=w, days=n)).strftime("%Y-%m-%d")
            out.append((day, row(hits, scored, day)))
    return out


class ReadingTests(unittest.TestCase):
    def test_a_live_row_is_read_and_a_reprinted_pin_or_a_dash_is_not(self):
        self.assertEqual(rt.reading_of(row(35, 60, "2026-11-13")), (35, 60))
        self.assertIsNone(rt.reading_of(row(42, 63, "2026-10-01", live=False)))
        self.assertIsNone(rt.reading_of("| gold-set R@5 | — | not measured: stale vectors |"))
        self.assertIsNone(rt.reading_of("no row at all"))

    def test_a_later_text_for_the_same_day_wins(self):
        nights = rt.nights_from([("2026-11-13", row(40, 60, "2026-11-13")),
                                 ("2026-11-13", row(35, 60, "2026-11-13"))])
        self.assertEqual([(n.day, n.hits) for n in nights], [("2026-11-13", 35)])


class RuleTests(unittest.TestCase):
    def trend(self, *weekly, **kw):
        return rt.trend(rt.nights_from(weeks_of(*weekly, **kw)))

    def test_flat_weeks_do_not_warn(self):
        t = self.trend(42, 42, 42, 42)
        self.assertFalse(t.sliding)
        self.assertEqual([round(w.rate, 4) for w in t.weeks], [0.7] * 4)

    def test_three_falling_weeks_warn_even_when_the_fall_is_small(self):
        # 42 → 41 → 40 → 39 of 60: 5 points in all, each week under the last.
        t = self.trend(42, 41, 40, 39)
        self.assertTrue(t.sliding)
        self.assertIn("fell 3 weeks running", t.reasons)

    def test_one_bad_week_that_recovers_does_not_warn(self):
        self.assertFalse(self.trend(42, 41, 42, 41).sliding)

    def test_a_drop_from_the_best_week_warns_without_a_run_of_falls(self):
        # Up, then down 5 points from the best: one rule fires, not the other.
        t = self.trend(40, 43, 40)
        self.assertTrue(t.sliding)
        self.assertEqual(len(t.reasons), 1)
        self.assertTrue(t.reasons[0].startswith("5.0 points under the best week, 2026-W42"), t.reasons)

    def test_a_drop_just_under_the_line_does_not(self):
        # 43/60 → 41/60 is 3.3 points.
        self.assertFalse(self.trend(43, 41).sliding)

    def test_a_smaller_gold_set_at_the_same_rate_is_flat(self):
        nights = rt.nights_from(weeks_of(42, scored=63)[:3] + weeks_of(0, 40)[3:])
        # 42 of 63 then 40 of 60: 66.7% and 66.7%.
        self.assertEqual(len(rt.weekly(nights)), 2)
        self.assertFalse(rt.trend(nights).sliding)

    def test_the_best_week_is_looked_for_only_in_the_window(self):
        # A best week nine weeks back is out of the eight-week window.
        t = self.trend(50, 40, 40, 40, 40, 40, 40, 40, 40)
        self.assertEqual(len(t.weeks), rt.SLIDE_WEEKS)
        self.assertFalse(t.sliding)

    def test_fewer_than_two_weeks_say_so_rather_than_warn(self):
        t = self.trend(30)
        self.assertEqual((len(t.weeks), t.sliding), (1, False))

    def test_november_2026_would_have_warned(self):
        # The nightly readings from the scorecard history, 10-31 to 11-13.
        days = [("2026-10-31", 39, 63), ("2026-11-01", 42, 60), ("2026-11-02", 43, 60),
                ("2026-11-03", 43, 60)] + [(f"2026-11-{d:02d}", 35, 60) for d in range(5, 14)]
        t = rt.trend(rt.nights_from([(d, row(h, s, d)) for d, h, s in days]))
        self.assertEqual([w.key for w in t.weeks], ["2026-W44", "2026-W45", "2026-W46"])
        self.assertTrue(t.sliding)
        self.assertTrue(t.reasons[0].startswith("7.6 points under the best week, 2026-W44"), t.reasons)


class ScorecardRowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "diagnostics" / "health"
        self.out.mkdir(parents=True)

    def section(self, hits, scored, day):
        s = sc.Section("Retrieval")
        s.readings.append(sc.Reading.measured(
            "gold-set R@5", hits / scored, source=sc.EVAL_SCRIPT,
            note=f"{hits} of {scored} scored questions; measured {day} on 4300 documents; pinned 0.6667"))
        return s

    def history(self, pairs):
        for day, text in pairs:
            sc.report_history.record(self.out, sc.HISTORY_REPORT, day, {"body": text})

    def test_the_row_reads_the_history_and_tonights_reading(self):
        self.history(weeks_of(42, 41, 40))
        tonight = (MONDAY + timedelta(weeks=3)).strftime("%Y-%m-%d")
        r = sc.trend_reading(self.section(39, 60, tonight), self.out, tonight)
        self.assertEqual(r.label, "hit rate, week by week")
        self.assertEqual(r.render(),
                         "| hit rate, week by week | 65.0% (2026-W44) | SLIDING: fell 3 weeks running; "
                         "5.0 points under the best week, 2026-W41. No single night is significant, so the "
                         "gate passes; the weeks together are the finding. W41 70.0% · W42 68.3% · W43 66.7% · "
                         "W44 65.0% |")

    def test_a_steady_rate_says_steady(self):
        self.history(weeks_of(42, 42))
        tonight = (MONDAY + timedelta(weeks=2)).strftime("%Y-%m-%d")
        r = sc.trend_reading(self.section(42, 60, tonight), self.out, tonight)
        self.assertTrue(r.note.startswith("steady: W41 70.0% · W42 70.0% · W43 70.0%"), r.note)

    def test_too_little_history_is_a_dash_with_the_reason(self):
        r = sc.trend_reading(self.section(42, 60, "2026-10-05"), self.out, "2026-10-05")
        self.assertEqual(r.render(),
                         "| hit rate, week by week | — | not measured: 1 week(s) of live readings; the trend needs two |")

    def test_a_dated_note_left_on_disk_counts_like_a_history_row(self):
        for day, text in weeks_of(42)[:3]:
            (self.out / f"{day}-health-scorecard.md").write_text(text + "\n", encoding="utf-8")
        tonight = (MONDAY + timedelta(weeks=1)).strftime("%Y-%m-%d")
        r = sc.trend_reading(self.section(39, 60, tonight), self.out, tonight)
        self.assertIn("W41 70.0% · W42 65.0%", r.note)
        self.assertTrue(r.note.startswith("SLIDING: 5.0 points under the best week"), r.note)

    def test_a_sliding_reading_leaves_the_pinned_baseline_as_it_was(self):
        # The constraint on this change: an advisory reading beside the gate,
        # never a re-pin. Reading a slide must not write the baseline.
        baseline = Path(__file__).resolve().parents[1] / sc.PINNED_BASELINE
        before = hashlib.sha256(baseline.read_bytes()).hexdigest()
        self.history(weeks_of(42, 41, 40))
        r = sc.trend_reading(self.section(39, 60, "2026-10-26"), self.out, "2026-10-26")
        self.assertTrue(r.note.startswith("SLIDING"))
        self.assertEqual(hashlib.sha256(baseline.read_bytes()).hexdigest(), before)


class GateHistoryTests(unittest.TestCase):
    def test_the_p_value_is_read_from_the_gates_tail(self):
        tail = "  exact paired p    : 0.0923\nno significant regression\ncheck-retrieval-regression: clean"
        self.assertEqual(job.p_value(tail), 0.0923)
        self.assertIsNone(job.p_value("check-retrieval-regression: SKIP — no reachable vault"))

    def test_each_night_is_a_row_and_the_artifact_keeps_the_last(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "diagnostics" / "health"
            artifact = out / job.ARTIFACT_NAME
            out.mkdir(parents=True)
            proc = mock.Mock(returncode=0, stdout="  exact paired p    : 0.1460\nno significant regression\n",
                             stderr="")
            with mock.patch.object(job.subprocess, "run", return_value=proc), \
                    mock.patch.object(job, "artifact_path", return_value=artifact):
                self.assertEqual(job.main(), 0)
            rows = sc.report_history.read_rows(out, job.HISTORY_REPORT)
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["verdict"], rows[0]["p"], rows[0]["measured"]), ("PASS", 0.146, True))
            self.assertEqual(json.loads(artifact.read_text(encoding="utf-8"))["verdict"], "PASS")
            # The gate's rows never mix into the scorecard's.
            self.assertEqual(sc.report_history.read_rows(out, sc.HISTORY_REPORT), [])

    def test_a_history_that_cannot_be_written_does_not_change_the_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            with mock.patch.object(job.report_history, "record", side_effect=OSError("disk full")):
                job.record_history(out, {"verdict": "FAIL", "tail": ""},
                                   datetime(2026, 11, 13, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
