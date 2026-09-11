#!/usr/bin/env python3
"""test_job_templates.py — every shipped runner job template loads through the
runner's own manifest parser.

The runner loads every manifest in `.harness/jobs/` or none: one bad file
stops every scheduled job on the machine, and the only trace is a traceback
in a launchd log nobody reads. `templates/jobs/dreaming.yaml` shipped with a
command that started with a quoted path and kept going — a YAML parse error —
and the runner ran nothing for the afternoon of 2026-09-05. This is the gate that
class of slip never passes again.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from runner import manifest  # noqa: E402

TEMPLATES = _HERE.parent / "templates" / "jobs"


class JobTemplatesLoad(unittest.TestCase):
    def test_every_template_loads_as_a_manifest(self):
        templates = sorted(TEMPLATES.glob("*.yaml"))
        self.assertTrue(templates, f"no templates under {TEMPLATES}")
        with tempfile.TemporaryDirectory() as td:
            jobs = Path(td) / "jobs"
            jobs.mkdir()
            for t in templates:
                shutil.copy(t, jobs / t.name)
            loaded = manifest.load_manifests(jobs)
        self.assertEqual(sorted(j.name for j in loaded), sorted(t.stem for t in templates))
        for j in loaded:
            self.assertTrue(j.command.strip(), f"{j.name}: empty command")

    def test_the_dreaming_job_applies_as_one_shell_command(self):
        with tempfile.TemporaryDirectory() as td:
            jobs = Path(td) / "jobs"
            jobs.mkdir()
            shutil.copy(TEMPLATES / "dreaming.yaml", jobs / "dreaming.yaml")
            (job,) = manifest.load_manifests(jobs)
        # Nightly (agentm-vault plan 04): the window makes it once a night, and
        # twelve hours sits between a night's spread and the gap between nights.
        self.assertEqual(job.command, "$HOME/.local/bin/agentmdream run -every 12h -apply")
        self.assertFalse(job.dry_run)

    def test_the_nightly_enrichment_job_is_registered_and_off(self):
        """Registered so the job has a name and a home; off because its
        budget, walk order and prompt are the night's plan to decide.

        `enabled: false` is not `dry_run: true` — a dry run still runs the
        command and asks it not to write, and this command has nothing
        decided for it yet.
        """
        with tempfile.TemporaryDirectory() as td:
            jobs = Path(td) / "jobs"
            jobs.mkdir()
            shutil.copy(TEMPLATES / "enrich-nightly.yaml", jobs / "enrich-nightly.yaml")
            (job,) = manifest.load_manifests(jobs)
        self.assertFalse(job.enabled)
        self.assertIn("agentmd enrich", job.command)
        # No `--yes`. That flag bypasses `daemon.enrich_enabled` outright, so a
        # scheduled command carrying it would spend every night with the
        # operator's standing spend switch still off — which is the one thing
        # that switch exists to prevent.
        self.assertNotIn("--yes", job.command)
        # It declares that it spends, so the fleet ceiling gates it (and only
        # it); the number is the strong tier's line (plan 04, task 2).
        self.assertEqual(job.budget_tokens, 1_000_000)

    def test_a_manifest_without_the_field_is_enabled(self):
        """Every manifest written before the field existed keeps running."""
        with tempfile.TemporaryDirectory() as td:
            jobs = Path(td) / "jobs"
            jobs.mkdir()
            shutil.copy(TEMPLATES / "dreaming.yaml", jobs / "dreaming.yaml")
            (job,) = manifest.load_manifests(jobs)
        self.assertTrue(job.enabled)

    def test_the_four_nightly_steps_share_the_window_in_the_night_order(self):
        """agentm-vault plan 04, task 1: enrichment, the binary, the Python
        cycle, the scorecards — inside 02:00-06:00, in that order, because each
        reads what the one before it wrote."""
        night = ["enrich-nightly", "dreaming", "dream", "corpus-scorecard"]
        with tempfile.TemporaryDirectory() as td:
            jobs = Path(td) / "jobs"
            jobs.mkdir()
            for t in TEMPLATES.glob("*.yaml"):
                shutil.copy(t, jobs / t.name)
            loaded = {j.name: j for j in manifest.load_manifests(jobs)}
        for name in night:
            self.assertEqual(loaded[name].window_minutes, (120, 360), name)
        self.assertEqual(sorted(night, key=lambda n: loaded[n].order), night)
        # Nothing else is windowed yet: the hourly sweep and the shepherds are
        # not night work, and a window on them would stall them all day.
        others = [n for n, j in loaded.items() if j.window and n not in night]
        self.assertEqual(others, [])


if __name__ == "__main__":
    unittest.main()
