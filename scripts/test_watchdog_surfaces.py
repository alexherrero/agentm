#!/usr/bin/env python3
"""A parked job says so, somewhere a person will read it.

The watchdog's `stop` rung is the runner's only hard gate. It wrote to
`~/.cache/agentm/runner/<job>.watchdog.json` and no surface read it, so
`health-pass` sat parked from 2026-07-25 to 2026-09-07 — six weeks with no
local health scorecard, and no line anywhere saying why — while the doctor's
row went on reporting "registered (live), last fired" against a six-week-old
timestamp. True, and the opposite of the point.

Worse, the ladder had no documented way out: `is_stopped`'s own docstring said
a stopped job waits for "an operator to clear its watchdog state", which meant
deleting a file whose path nothing printed.

These tests pin the three surfaces that close it — the CLI, the doctor's row
and the session brief — plus the reset itself.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "scripts" / "health"))

from runner import cli as cli_mod  # noqa: E402
from runner import watchdog as watchdog_mod  # noqa: E402

import session_brief as brief_mod  # noqa: E402
import machinery_doctor as doctor_mod  # noqa: E402


def park(state_root: Path, job: str, *, failures: int = 8,
         last_success: "float | None" = None) -> None:
    """Put a job at the stop rung the way a real failure streak would."""
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / f"{job}.watchdog.json").write_text(json.dumps({
        "rung": "stop", "consecutive_failures": failures,
        "last_success": last_success,
    }), encoding="utf-8")


class TheLadderReachesTheStopRung(unittest.TestCase):
    """The behaviour the surfaces are reporting, so they report something real."""

    def test_eight_consecutive_failures_park_a_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for _ in range(8):
                watchdog_mod.record_outcome("j", succeeded=False, now=time.time(),
                                            state_root=root)
            self.assertTrue(watchdog_mod.is_stopped("j", state_root=root))

    def test_seven_do_not(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for _ in range(7):
                watchdog_mod.record_outcome("j", succeeded=False, now=time.time(),
                                            state_root=root)
            self.assertFalse(watchdog_mod.is_stopped("j", state_root=root))


class TheEnumeration(unittest.TestCase):
    def test_it_names_every_parked_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            park(root, "health-pass")
            park(root, "forward-learning")
            watchdog_mod.record_outcome("dreaming", succeeded=True, now=time.time(),
                                        state_root=root)
            self.assertEqual([n for n, _ in watchdog_mod.stopped_jobs(state_root=root)],
                             ["forward-learning", "health-pass"])

    def test_a_healthy_machine_names_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(watchdog_mod.stopped_jobs(state_root=Path(td)), [])

    def test_a_missing_state_dir_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                watchdog_mod.stopped_jobs(state_root=Path(td) / "never-existed"), [])


class TheWayOut(unittest.TestCase):
    def test_clearing_lets_the_job_run_again(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            park(root, "health-pass")
            self.assertTrue(watchdog_mod.is_stopped("health-pass", state_root=root))
            self.assertTrue(watchdog_mod.clear("health-pass", state_root=root))
            self.assertFalse(watchdog_mod.is_stopped("health-pass", state_root=root))

    # A round epoch on purpose: the real recorded value carried enough digits
    # after the decimal point that check-no-pii read it as a US phone number.
    LAST_SUCCESS = 1700000000.0

    def test_it_keeps_the_last_success(self):
        # The streak is what the ladder punishes; when the job last worked is
        # history, and erasing it would erase the evidence of how long it sat.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            park(root, "health-pass", last_success=self.LAST_SUCCESS)
            watchdog_mod.clear("health-pass", state_root=root)
            record = watchdog_mod.read_health("health-pass", state_root=root)
            self.assertEqual(record["last_success"], self.LAST_SUCCESS)
            self.assertEqual(record["consecutive_failures"], 0)

    def test_clearing_a_job_with_no_record_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(watchdog_mod.clear("never-ran", state_root=Path(td)))

    def test_a_cleared_job_parks_again_if_it_keeps_failing(self):
        # Resuming is not an exemption.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            park(root, "j")
            watchdog_mod.clear("j", state_root=root)
            for _ in range(8):
                watchdog_mod.record_outcome("j", succeeded=False, now=time.time(),
                                            state_root=root)
            self.assertTrue(watchdog_mod.is_stopped("j", state_root=root))


class TheCli(unittest.TestCase):
    def test_health_reports_a_parked_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            park(root, "health-pass", last_success=1700000000.0)
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli_mod.main(["health", "--state-root", str(root)])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("health-pass", out)
        self.assertIn("8 consecutive failures", out)
        self.assertIn("resume", out)

    def test_health_exits_zero_when_something_is_parked(self):
        # Parked is a state to report, not an error. A non-zero exit would make
        # every caller read "something is parked" as "the runner is broken".
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            park(root, "health-pass")
            import io
            import contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli_mod.main(["health", "--state-root", str(root)]), 0)

    def test_resume_clears_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            park(root, "health-pass")
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli_mod.main(["resume", "health-pass", "--state-root", str(root)])
            self.assertEqual(rc, 0)
            self.assertIn("cleared", buf.getvalue())
            self.assertFalse(watchdog_mod.is_stopped("health-pass", state_root=root))

    def test_health_on_a_clean_machine_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli_mod.main(["health", "--state-root", str(td)])
        self.assertIn("nothing parked", buf.getvalue())


class TheDoctorsRow(unittest.TestCase):
    """The row said "registered (live), last fired" for six weeks of not running."""

    def _repo_with_job(self, tmp: Path, name: str) -> Path:
        (tmp / "templates" / "jobs").mkdir(parents=True)
        (tmp / ".harness" / "jobs").mkdir(parents=True)
        manifest = ("schedule: daily\nlookback: 24h\n"
                    "command: echo hello\ntier: T2\ndry_run: false\n")
        (tmp / "templates" / "jobs" / f"{name}.yaml").write_text(manifest, encoding="utf-8")
        (tmp / ".harness" / "jobs" / f"{name}.yaml").write_text(manifest, encoding="utf-8")
        return tmp

    def test_a_parked_job_fails_the_row_and_names_the_remedy(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = self._repo_with_job(tmp / "repo", "health-pass")
            state = tmp / "state"
            park(state, "health-pass")
            check = doctor_mod.check_runner_job(repo, "health-pass", state_root=state)
        self.assertEqual(check.status, "FAIL")
        self.assertIn("PARKED", check.detail)
        self.assertIn("resume health-pass", check.detail)

    def test_a_healthy_registered_job_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = self._repo_with_job(tmp / "repo", "health-pass")
            state = tmp / "state"
            state.mkdir()
            check = doctor_mod.check_runner_job(repo, "health-pass", state_root=state)
        self.assertIn(check.status, ("OK", "WARN"))
        self.assertNotIn("PARKED", check.detail)


class TheSessionBrief(unittest.TestCase):
    def test_it_names_parked_jobs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            park(root, "health-pass")
            park(root, "forward-learning")
            self.assertEqual(brief_mod.parked_jobs(root),
                             ["forward-learning", "health-pass"])

    def test_the_clause_names_them_and_the_way_out(self):
        clause = brief_mod._parked_clause(["health-pass", "forward-learning"])
        self.assertIn("health-pass", clause)
        self.assertIn("2 jobs parked", clause)
        self.assertIn("resume", clause)

    def test_no_parked_jobs_adds_nothing(self):
        # Honest-quiet: the brief says nothing when there is nothing to say.
        self.assertEqual(brief_mod._parked_clause([]), "")

    def test_it_never_fails_a_session(self):
        # This runs on the session-start path. A broken state dir costs the
        # brief a clause, never the session its start.
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "not-a-directory"
            bad.write_text("", encoding="utf-8")
            self.assertEqual(brief_mod.parked_jobs(bad), [])


if __name__ == "__main__":
    unittest.main()
