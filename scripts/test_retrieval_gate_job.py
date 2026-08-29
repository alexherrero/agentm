#!/usr/bin/env python3
"""The nightly gate wrapper and its scorecard reading.

The property under test throughout: a gate that stops running, or fails, is
*visible on the page somebody reads* — staleness and regression are renderings,
never silence. That is the whole difference from the CI SKIP this replaces.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import corpus_scorecard as sc  # noqa: E402
import retrieval_gate_job as job  # noqa: E402


class TheWrapperVerdicts(unittest.TestCase):
    """The verdict mapping, with subprocess mocked rather than a bash stub —
    the first version shelled real scripts and failed on the Windows runner,
    which has no bash on this job's PATH. The logic is what needs coverage
    everywhere; the wrapper itself only ever runs on the Mac the runner
    schedules it on."""

    def _run(self, *, exit_code: int, stdout: str = "") -> dict:
        done = subprocess.CompletedProcess([], exit_code, stdout=stdout, stderr="")
        with mock.patch.object(job.subprocess, "run", return_value=done):
            return job.run_gate()

    def test_a_clean_pass_is_a_pass(self):
        got = self._run(exit_code=0, stdout="check-retrieval-regression: clean\n")
        self.assertEqual(got["verdict"], "PASS")

    def test_a_skip_is_named_skip_not_pass(self):
        # The two share exit 0 and must not share a label — a skip counted as a
        # pass is the silent-tripwire failure all over again.
        got = self._run(exit_code=0,
                        stdout="check-retrieval-regression: SKIP — no daemon\n")
        self.assertEqual(got["verdict"], "SKIP")

    def test_a_regression_is_fail(self):
        got = self._run(exit_code=1, stdout="regressed\n")
        self.assertEqual(got["verdict"], "FAIL")

    def test_an_exit_the_gate_never_produces_reads_as_could_not_run(self):
        # 127 is command-not-found: a statement about the environment, and it
        # must not masquerade as a ranker verdict.
        got = self._run(exit_code=127)
        self.assertIn("could not run", got["verdict"])

    def test_a_gate_that_cannot_run_is_a_reading_not_a_crash(self):
        with mock.patch.object(job.subprocess, "run",
                               side_effect=OSError("no bash")):
            got = job.run_gate()
        self.assertIn("could not run", got["verdict"])


class TheScorecardReading(unittest.TestCase):
    def _artifact(self, tmp: Path, *, verdict: str, hours_ago: float) -> None:
        at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        (tmp / sc.GATE_ARTIFACT_NAME).write_text(json.dumps({
            "verdict": verdict, "exit": 0,
            "at": at.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")

    def test_a_fresh_pass_renders_with_its_age(self):
        with tempfile.TemporaryDirectory() as d:
            self._artifact(Path(d), verdict="PASS", hours_ago=2)
            r = sc.gate_reading(Path(d))
        self.assertEqual(r.value, "PASS")
        self.assertIn("h ago", r.note)
        self.assertNotIn("STALE", r.note)

    def test_a_stale_artifact_says_stale(self):
        # The job stopping IS the finding; the row must say so on its own.
        with tempfile.TemporaryDirectory() as d:
            self._artifact(Path(d), verdict="PASS", hours_ago=72)
            r = sc.gate_reading(Path(d))
        self.assertIn("STALE", r.note)

    def test_a_failure_names_the_regression(self):
        with tempfile.TemporaryDirectory() as d:
            self._artifact(Path(d), verdict="FAIL", hours_ago=1)
            r = sc.gate_reading(Path(d))
        self.assertIn("REGRESSION", r.note)

    def test_no_artifact_is_an_absence_with_the_fix_in_it(self):
        with tempfile.TemporaryDirectory() as d:
            r = sc.gate_reading(Path(d))
        self.assertTrue(r.missing)
        self.assertIn("retrieval-gate-nightly.yaml", r.missing)

    def test_a_corrupt_artifact_is_an_absence_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / sc.GATE_ARTIFACT_NAME).write_text("{ not json",
                                                         encoding="utf-8")
            r = sc.gate_reading(Path(d))
        self.assertTrue(r.missing)


if __name__ == "__main__":
    unittest.main()
