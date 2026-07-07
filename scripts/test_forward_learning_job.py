#!/usr/bin/env python3
"""Test for the forward-learning pipeline's scheduled runner job manifest
(AG Wave E experience plan, task 1): `templates/jobs/forward-learning.yaml`.

`.harness/jobs/` is gitignored (per-project runtime state) —
`templates/jobs/forward-learning.yaml` is the tracked, shipped source,
mirroring the sibling `templates/jobs/dream.yaml` precedent from
PLAN-wave-e-dreaming. This test only proves the manifest is well-formed and
opt-in-safe; the plan's own scorecard note says this plan does NOT flip a
dark-checks.jsonl row (none exists yet for forward-learning), so there is
no scorecard-wiring verify-*.sh counterpart here the way there was for
dreaming's task 4.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from runner import manifest  # noqa: E402

_TEMPLATE_PATH = _HERE.parent / "templates" / "jobs" / "forward-learning.yaml"


class ForwardLearningJobManifestTests(unittest.TestCase):
    def test_template_parses_per_runner_schema(self) -> None:
        self.assertTrue(_TEMPLATE_PATH.exists(), f"missing {_TEMPLATE_PATH}")
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            (jobs_dir / "forward-learning.yaml").write_text(
                _TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
            jobs = manifest.load_manifests(jobs_dir)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.name, "forward-learning")
        self.assertEqual(job.schedule, "daily")
        self.assertEqual(job.tier, "T3")
        self.assertTrue(job.dry_run, "shipped manifest must stay opt-in / dry-run")


if __name__ == "__main__":
    unittest.main()
