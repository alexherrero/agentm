#!/usr/bin/env python3
"""runner_pulse: is the runner still cycling? (task 198, `#917`)

The grade is the age of the last cycle's start against the runner's interval,
measured from the latest of that start, a cycle still running, and the
machine's last wake. The Go side (`daemon/internal/health`, `TestRunnerPulse*`)
is pinned to the same cases.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

import runner_pulse as rp  # noqa: E402

IV = rp.RUNNER_INTERVAL
T0 = 1_800_000_000.0


class PulseTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name) / "runner"
        self.root.mkdir()
        self.log = Path(self._td.name) / "runner.log"
        (self.root / "last-cycle.json").write_text(json.dumps({"at": T0}), encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def grade(self, now, wake=None):
        return rp.grade(self.root, now=now, wake=wake, log=self.log, read_wake=False)

    def test_each_grade(self):
        self.assertEqual(self.grade(T0 + 60).level, "OK")
        self.assertEqual(self.grade(T0 + IV).level, "OK")
        self.assertEqual(self.grade(T0 + IV + 1).level, "WARN")
        self.assertEqual(self.grade(T0 + 2 * IV).level, "WARN")
        self.assertEqual(self.grade(T0 + 2 * IV + 1).level, "FAIL")

    def test_no_cycle_is_unverified(self):
        (self.root / "last-cycle.json").unlink()
        self.assertEqual(self.grade(T0).level, "UNVERIFIED")

    def test_a_startup_error_is_named(self):
        self.log.write_text('{\n  "at": 1\n}\nTraceback (most recent call last):\n  File "x"\n'
                            "runner.manifest.ManifestError: PyYAML not available — cannot parse job manifests\n",
                            encoding="utf-8")
        p = self.grade(T0 + 3 * IV)
        self.assertEqual(p.level, "FAIL")
        self.assertIn("startup error: runner.manifest.ManifestError: PyYAML not available", p.detail)

    def test_a_log_ending_in_a_cycle_summary_names_no_error(self):
        self.log.write_text('{\n  "skipped_reason": "not available",\n  "at": 1\n}\n', encoding="utf-8")
        p = self.grade(T0 + 3 * IV)
        self.assertEqual(p.level, "FAIL")
        self.assertNotIn("startup error", p.detail)

    def test_a_running_cycle_is_not_a_silent_runner(self):
        (self.root / "enrich-nightly.json").write_text(
            json.dumps({"status": "start", "started_at": T0 + 2 * IV}), encoding="utf-8")
        p = self.grade(T0 + 3 * IV)
        self.assertEqual(p.level, "OK")
        self.assertIn("running `enrich-nightly`", p.detail)

    def test_an_orphaned_start_older_than_the_cycle_does_not_count(self):
        (self.root / "dream.json").write_text(
            json.dumps({"status": "start", "started_at": T0 - IV}), encoding="utf-8")
        self.assertEqual(self.grade(T0 + 3 * IV).level, "FAIL")

    def test_watchdog_files_are_not_markers(self):
        (self.root / "dream.watchdog.json").write_text(
            json.dumps({"status": "start", "started_at": T0 + 2 * IV}), encoding="utf-8")
        self.assertEqual(self.grade(T0 + 3 * IV).level, "FAIL")

    def test_the_age_counts_from_the_last_wake(self):
        """launchd counts the interval in awake time: eight hours asleep and
        ten minutes awake is a runner on schedule."""
        now = T0 + 8 * 3600
        p = self.grade(now, wake=now - 600)
        self.assertEqual(p.level, "OK")
        self.assertIn("woke 10m ago", p.detail)
        self.assertEqual(self.grade(now, wake=now - 2 * IV - 60).level, "FAIL")

    def test_a_wake_before_the_cycle_changes_nothing(self):
        self.assertEqual(self.grade(T0 + 3 * IV, wake=T0 - 60).level, "FAIL")


if __name__ == "__main__":
    unittest.main()
