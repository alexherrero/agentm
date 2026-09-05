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
        self.assertEqual(job.command, "$HOME/.local/bin/agentmdream run -every 168h -apply")
        self.assertFalse(job.dry_run)


if __name__ == "__main__":
    unittest.main()
