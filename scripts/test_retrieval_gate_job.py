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
        # A skip has its own exit code since 2026-09-18. It must not share a
        # label with a pass — a skip counted as a pass is the silent-tripwire
        # failure all over again.
        got = self._run(exit_code=2,
                        stdout="check-retrieval-regression: SKIP — no daemon\n")
        self.assertEqual(got["verdict"], "SKIP")

    def test_a_skip_is_read_from_the_exit_code_not_the_log_text(self):
        # The defect this pins: the verdict used to be decided by grepping the
        # gate's last three lines for "SKIP". Reword the log and a run that
        # measured nothing is recorded as a clean gate — which is how a decay
        # flip nearly went in behind `no reachable vault`. Exit 2 with a tail
        # that never says the word is still a skip.
        got = self._run(exit_code=2,
                        stdout="no reachable vault\nnothing was measured\n")
        self.assertEqual(got["verdict"], "SKIP")
        self.assertFalse(got["measured"])

    def test_a_pass_whose_log_merely_mentions_the_word_is_still_a_pass(self):
        # And the inverse: the old string match would have called this a skip.
        got = self._run(exit_code=0,
                        stdout="1 optional check was a SKIP\n"
                               "check-retrieval-regression: clean\n")
        self.assertEqual(got["verdict"], "PASS")
        self.assertTrue(got["measured"])

    def test_only_a_run_that_scored_is_marked_measured(self):
        self.assertTrue(self._run(exit_code=0)["measured"])
        self.assertTrue(self._run(exit_code=1)["measured"])
        self.assertFalse(self._run(exit_code=2)["measured"])
        self.assertFalse(self._run(exit_code=127)["measured"])

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

    def test_a_skip_on_the_page_says_it_measured_nothing(self):
        # The row a human reads has to distinguish "the bar held" from "nobody
        # took a reading". Anything flipped behind the second is unmeasured.
        with tempfile.TemporaryDirectory() as d:
            self._artifact(Path(d), verdict="SKIP", hours_ago=1)
            r = sc.gate_reading(Path(d))
        self.assertEqual(r.value, "SKIP")
        self.assertIn("MEASURED NOTHING", r.note)

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


class TheArtifactRoot(unittest.TestCase):
    """The gate's artifact belongs at the memory root, not the vault root.

    Both joins produce a path that exists and looks plausible; only one of them
    is read by anything. This one wrote `<vault>/diagnostics/health/` — a second
    diagnostics tree beside the memory space, which nothing renders and nobody
    opened for two days — because it asked the daemon where the *vault* was and
    joined a memory-root-relative directory onto the answer.

    The status below is the live shape, with the two roots deliberately
    different: a fixture where the memory space sits at the vault root would
    pass either way and prove nothing.
    """

    STATUS = {"vault": None, "spaces": {"memory": "agent/memory",
                                        "projects": "projects"}}

    def _status(self, vault: Path) -> dict:
        st = dict(self.STATUS)
        st["vault"] = str(vault)
        return st

    def test_the_artifact_lands_under_the_memory_root(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            with mock.patch.object(sc, "_agentmd", return_value=self._status(vault)):
                got = job.artifact_path()
            self.assertEqual(got, vault / "agent" / "diagnostics" / "health"
                             / job.ARTIFACT_NAME)

    def test_nothing_is_written_at_the_vault_root(self):
        # The regression stated as the defect was: a `diagnostics/` directory
        # appearing directly under the vault. artifact_path() creates the
        # directory it returns, so this is a real check on the filesystem.
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            with mock.patch.object(sc, "_agentmd", return_value=self._status(vault)):
                job.artifact_path()
            self.assertFalse((vault / "diagnostics").exists(),
                             "a second diagnostics tree appeared at the vault root")

    def test_the_scorecard_and_the_gate_agree_on_the_directory(self):
        # The gate's artifact is read by the scorecard's gate_reading(). They
        # have to resolve the same directory or the row reads "no artifact"
        # while the artifact sits one root away.
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            with mock.patch.object(sc, "_agentmd", return_value=self._status(vault)):
                gate_dir = job.artifact_path().parent
                scorecard_dir = Path(sc.memory_root_from_daemon()) / sc.diagnostics_dir()
            self.assertEqual(gate_dir, scorecard_dir)

    def test_an_unresolvable_root_writes_nothing(self):
        # A guess is worse than a refusal: an artifact under an invented root
        # reads as a passing gate to anything that finds it.
        with mock.patch.object(sc, "_agentmd", return_value={}):
            with self.assertRaises(SystemExit):
                job.artifact_path()



if __name__ == "__main__":
    unittest.main()
