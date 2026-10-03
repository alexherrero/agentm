#!/usr/bin/env python3
"""Test for the weekly field brief's runner job manifest (task 185 step 4):
`templates/jobs/field-brief-weekly.yaml`.

`.harness/jobs/` is gitignored (per-project runtime state), so the template is
the tracked, shipped source. This proves the manifest parses by the runner's own
loader, says what the plan said (weekly, a two-day lookback, the evening
window, a declared budget, not a dry run), names a script that exists, and
behaves on the runner's own schedule logic the way the plan relies on: due on
the evening it is registered, not due again until a week later, and a missed
week is caught up inside two days.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from runner import cycle, manifest, state  # noqa: E402

_REPO = _HERE.parent
_TEMPLATE = _REPO / "templates" / "jobs" / "field-brief-weekly.yaml"


def _local(y, mo, d, h, mi=0) -> float:
    """An instant by the machine's local clock: the window is read in local time."""
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


class FieldBriefJobManifestTests(unittest.TestCase):
    def _load(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "field-brief-weekly.yaml").write_text(_TEMPLATE.read_text(encoding="utf-8"),
                                                              encoding="utf-8")
            jobs = manifest.load_manifests(Path(tmp))
        self.assertEqual(len(jobs), 1)
        return jobs[0]

    def test_it_parses_by_the_runners_own_loader_and_says_what_the_plan_said(self):
        job = self._load()
        self.assertEqual(job.name, "field-brief-weekly")
        self.assertEqual(job.schedule, "weekly")
        self.assertEqual(job.lookback, "2d")
        self.assertEqual(job.window, "18:00-23:00")
        self.assertEqual(job.tier, "T3")
        self.assertFalse(job.dry_run)
        self.assertTrue(job.enabled)

    def test_it_declares_a_budget_so_the_fleet_ceiling_sees_it_as_a_spender(self):
        job = self._load()
        self.assertTrue(cycle._spends(job))
        self.assertGreater(job.budget_tokens, 0)

    def test_its_command_runs_the_shipped_script_from_scripts_as_cwd(self):
        job = self._load()
        self.assertEqual(job.command, "python3 ../harness/skills/memory/scripts/field_brief.py")
        script = (_REPO / "scripts" / "../harness/skills/memory/scripts/field_brief.py").resolve()
        self.assertTrue(script.is_file(), f"the command names a script that is missing: {script}")

    def test_it_is_on_the_runners_list_of_shipped_templates(self):
        # The doctor reports a job registered or not by enumerating this folder.
        names = sorted(p.stem for p in (_REPO / "templates" / "jobs").glob("*.yaml"))
        self.assertIn("field-brief-weekly", names)


class FieldBriefJobScheduleTests(unittest.TestCase):
    """The runner's `is_due`, on this manifest: the plan relies on these."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_root = Path(self._tmp.name)
        with tempfile.TemporaryDirectory() as t2:
            (Path(t2) / "field-brief-weekly.yaml").write_text(_TEMPLATE.read_text(encoding="utf-8"),
                                                             encoding="utf-8")
            self.job = manifest.load_manifests(Path(t2))[0]

    def due(self, when):
        return cycle.is_due(self.job, now=when, state_root=self.state_root)[0]

    def test_it_is_due_on_the_evening_it_is_registered_and_not_in_the_afternoon(self):
        self.assertFalse(self.due(_local(2026, 10, 4, 12)))   # outside the window: a job waiting is not late
        self.assertTrue(self.due(_local(2026, 10, 4, 19)))    # never run, in the window

    def test_after_a_run_it_waits_a_week(self):
        ran = _local(2026, 10, 4, 19)
        state.mark_done("field-brief-weekly", now=ran, state_root=self.state_root)
        for days in (1, 3, 6):
            with self.subTest(days=days):
                self.assertFalse(self.due(_local(2026, 10, 4 + days, 19)))
        self.assertTrue(self.due(_local(2026, 10, 11, 19)))   # the next Sunday evening

    def test_a_week_slept_through_is_caught_up_inside_two_days_and_not_after(self):
        state.mark_done("field-brief-weekly", now=_local(2026, 10, 4, 19), state_root=self.state_root)
        self.assertTrue(self.due(_local(2026, 10, 12, 19)))    # Monday: one day late, inside the lookback
        state.mark_done("field-brief-weekly", now=_local(2026, 10, 4, 19), state_root=self.state_root)
        self.assertFalse(self.due(_local(2026, 10, 14, 19)))   # Wednesday: past it, re-anchored not run


if __name__ == "__main__":
    unittest.main()
