#!/usr/bin/env python3
"""The doctor's `daemon-commits` row: does the resident daemon keep committing?

On 2026-10-04 the daemon failed every commit cycle for eleven hours, and the
only trace was a warning line in its log. The daemon now reports its run of
failed cycles as `health.git.commit_failures` in `agentmd status --json`, and
this row fails whenever that run is longer than zero. The payloads below are
the wire shape the Go side pins in `TestTheCommitStallFieldsOnTheWire`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

import machinery_doctor as doctor_mod  # noqa: E402


def status(**git) -> dict:
    block = {"state": "healthy"}
    block.update(git)
    return {"version": "test", "uptime": "1h0m0s", "health": {"level": "ok", "git": block}}


class TheDaemonCommitsRow(unittest.TestCase):

    def test_a_clean_run_is_ok(self):
        check = doctor_mod.check_daemon_commits(status=status(commit_failures=0))
        self.assertEqual(check.status, "OK", check.detail)

    def test_any_failed_cycle_fails_and_names_the_error(self):
        check = doctor_mod.check_daemon_commits(status=status(
            commit_failures=3,
            first_commit_failure="2026-10-04T00:32:14Z",
            last_commit_error="status: object not found"))
        self.assertEqual(check.status, "FAIL")
        self.assertIn("3 commit cycle(s)", check.detail)
        self.assertIn("2026-10-04T00:32:14Z", check.detail)
        self.assertIn("object not found", check.detail)

    def test_a_daemon_that_predates_the_field_is_unverified_not_ok(self):
        check = doctor_mod.check_daemon_commits(status=status())
        self.assertEqual(check.status, "UNVERIFIED")
        self.assertIn("rebuild", check.detail)

    def test_degraded_git_is_unverified(self):
        check = doctor_mod.check_daemon_commits(status=status(
            state="degraded", detail="not a repository", commit_failures=0))
        self.assertEqual(check.status, "UNVERIFIED")
        self.assertIn("not a repository", check.detail)

    def test_a_daemon_that_cannot_be_asked_is_unverified(self):
        missing = Path("/nonexistent/agentmd")
        check = doctor_mod.check_daemon_commits(binary=missing)
        self.assertEqual(check.status, "UNVERIFIED")

    def test_a_count_that_is_not_a_count_is_unverified(self):
        for bad in ("3", -1, True, 2.5):
            with self.subTest(bad=bad):
                check = doctor_mod.check_daemon_commits(status=status(commit_failures=bad))
                self.assertEqual(check.status, "UNVERIFIED")

    def test_the_row_is_in_the_inventory(self):
        names = [c.name for c in doctor_mod.run_inventory(_REPO)]
        self.assertIn("daemon-commits", names)


if __name__ == "__main__":
    unittest.main()
