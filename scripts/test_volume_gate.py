#!/usr/bin/env python3
"""Filing v2, the write path, task 4: the volume gate and the writes-per-day line.

A synthetic flood is caught at the gate with a named refusal, never
discovered in the corpus; the reading counts by capture day and trends week
over week; the cap comes from the contract, the environment overrides it,
and zero disables the gate.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import capture as cap  # noqa: E402
import corpus_scorecard  # noqa: E402
import reflect  # noqa: E402
import save  # noqa: E402
import volume_gate  # noqa: E402

_TODAY = date(2026, 9, 4)
_NOW = datetime(2026, 9, 4, 9, 0, 0, tzinfo=timezone.utc)


def _cand(body, *, confidence="HIGH", slug):
    return reflect.Candidate(category="preferences", confidence=confidence, slug=slug,
                             title=slug.replace("-", " "), body=body, rationale="test", excerpts=[])


class _Vault(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="volume-gate-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "memory").mkdir(parents=True)
        self._env = os.environ.get(volume_gate.ENV_CAP)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._env is None:
            os.environ.pop(volume_gate.ENV_CAP, None)
        else:
            os.environ[volume_gate.ENV_CAP] = self._env

    def _cap(self, n):
        os.environ[volume_gate.ENV_CAP] = str(n)

    def _plant(self, slug, day: date):
        """A memory captured on `day`, written directly so the gate never sees it."""
        p = save.save_entry(self.root, "preference", slug, f"Note {slug}.",
                            extra={"captured": f"{day.isoformat()}T09:00:00+00:00"})
        return p


class TheGate(_Vault):
    def test_a_flood_is_refused_at_the_door_with_a_named_message(self):
        self._cap(3)
        for i in range(3):
            r = cap.capture(self.root, f"thought {i}", now=_NOW)
            self.assertTrue(r.success, r.error)
        r = cap.capture(self.root, "one too many", now=_NOW)
        self.assertFalse(r.success)
        self.assertIn("capture refused", r.error)
        self.assertIn("daily cap is 3", r.error)
        self.assertIn("thresholds.daily_write_cap", r.error)
        # Caught at the gate: the corpus holds exactly the cap, nothing more.
        self.assertEqual(volume_gate.today_count(self.root, today=_TODAY), 3)

    def test_the_gate_counts_the_day_the_capture_is_stamped_for(self):
        # The wall clock may have rolled past the day a capture is stamped
        # for. The gate counts against the stamp — the same day the
        # writes-per-day reading files the note under — so a flood stamped
        # for one day is never counted against another (and the gate
        # cannot open by accident at midnight while a flood is in progress).
        self._cap(2)
        then = _NOW - timedelta(days=1)
        for i in range(2):
            self.assertTrue(cap.capture(self.root, f"then {i}", now=then).success)
        r = cap.capture(self.root, "then, one too many", now=then)
        self.assertFalse(r.success)
        self.assertIn(then.date().isoformat(), r.error)
        # Another day is another count: the gate opens again.
        self.assertTrue(cap.capture(self.root, "and now", now=_NOW).success)
        self.assertEqual(volume_gate.today_count(self.root, today=then.date()), 2)
        self.assertEqual(volume_gate.today_count(self.root, today=_NOW.date()), 1)

    def test_reflect_counts_a_refusal_apart_from_an_error(self):
        self._cap(2)
        cands = [_cand(f"User stated: fact number {i}.", slug=f"fact-{i}") for i in range(4)]
        err = io.StringIO()
        stats = reflect.route_candidates(
            cands, [], vault=self.root, mode=reflect.ROUTE_MODE_AUTO,
            stdin=io.StringIO(), stdout=io.StringIO(), stderr=err,
        )
        self.assertEqual(stats["auto_saved"], 2, stats)
        self.assertEqual(stats["refused"], 2, stats)
        self.assertEqual(stats["errors"], 0, stats)
        self.assertIn("capture refused", err.getvalue())

    def test_a_repeat_still_reinforces_when_the_gate_is_shut(self):
        # A noop writes nothing, so the gate has no say in it.
        self._cap(1)
        first = cap.capture(self.root, "the same thought", now=_NOW)
        again = cap.capture(self.root, "the same thought", now=_NOW)
        self.assertTrue(again.success, again.error)
        self.assertTrue(again.deduplicated)
        self.assertEqual(again.path, first.path)

    def test_zero_disables_the_gate(self):
        self._cap(0)
        self.assertIsNone(volume_gate.daily_cap())
        for i in range(5):
            self.assertTrue(cap.capture(self.root, f"thought {i}", now=_NOW).success)

    def test_the_cap_comes_from_the_contract_when_the_environment_is_silent(self):
        os.environ.pop(volume_gate.ENV_CAP, None)

        class _Rules:
            def thresholds(self):
                return {"daily_write_cap": 7}

        self.assertEqual(volume_gate.daily_cap(_Rules()), 7)

        class _Old:
            def thresholds(self):
                return {}

        self.assertEqual(volume_gate.daily_cap(_Old()), volume_gate.DEFAULT_CAP)


class TheReading(_Vault):
    def test_writes_count_by_capture_day_and_zero_fill(self):
        self._plant("a", _TODAY)
        self._plant("b", _TODAY)
        self._plant("c", _TODAY - timedelta(days=1))
        self._plant("old", _TODAY - timedelta(days=20))  # outside the fortnight
        by_day = volume_gate.writes_by_day(self.root, days=14, today=_TODAY)
        self.assertEqual(len(by_day), 14)
        self.assertEqual(by_day[-1], (_TODAY.isoformat(), 2))
        self.assertEqual(by_day[-2][1], 1)
        self.assertEqual(sum(n for _, n in by_day), 3)

    def test_the_trend_compares_this_week_with_the_last(self):
        self._cap(200)
        for i in range(4):
            self._plant(f"this-{i}", _TODAY - timedelta(days=i))
        for i in range(2):
            self._plant(f"last-{i}", _TODAY - timedelta(days=8 + i))
        t = volume_gate.trend(self.root, today=_TODAY)
        self.assertEqual((t["week"], t["previous_week"], t["change_pct"]), (4, 2, 100))
        self.assertEqual(t["today"], 1)
        self.assertEqual(t["headroom"], 199)
        note = volume_gate.describe(t)
        self.assertIn("week 4 vs previous 2 (+100% week over week)", note)
        self.assertIn("cap 200 (headroom 199)", note)

    def test_the_scorecard_carries_the_line(self):
        self._cap(200)
        self._plant("a", _TODAY)
        reading = corpus_scorecard._writes_reading(self.root, today=_TODAY)
        self.assertEqual(reading.label, "writes per day")
        self.assertEqual(reading.value, 1)
        # One planted day and nothing the week before: the note says so
        # rather than inventing a percentage over zero.
        self.assertIn("week 1 vs previous 0 (no previous week to compare)", reading.note)
        self.assertIn("cap 200 (headroom 199)", reading.note)

    def test_the_class_list_agrees_with_the_scorecard(self):
        self.assertEqual(volume_gate.CLASS_DIRS, corpus_scorecard.CLASS_DIRS)


if __name__ == "__main__":
    unittest.main()
