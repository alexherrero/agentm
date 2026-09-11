#!/usr/bin/env python3
"""Tests for the Python cycle's scheduled runner job: `templates/jobs/dream.yaml`
and its wiring through `scripts/runner/` (AG Wave E dreaming plan, task 4;
reshaped in agentm-vault plan 04).

`.harness/jobs/` is gitignored (per-project runtime state, same as every
other `.harness/*` file) — `templates/jobs/dream.yaml` is the tracked,
shipped source; a repo registers the job by copying it in. These tests load
that tracked template directly rather than assuming a `.harness/jobs/`
exists in this checkout.

Covers:
  - the manifest parses per `scripts/runner/manifest.py`'s schema, nightly,
    inside the night's window, third in its order, with no batch cap;
  - the shipped template's `dry_run: true` means a due cycle reports
    `ran=False, dry_run=True` and the command never executes;
  - a cycle where the same command is actually run (dry_run overridden to
    False for this test only, proving the wiring rather than the shipped
    posture) produces the same digest as calling `dream.run_dream()` by hand.

Every cycle here runs at 03:00 local time: the template carries the night's
`window:`, and a clock outside it would leave the job not due and the test
reading an empty outcome list.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import dream  # noqa: E402
from runner import cycle, manifest  # noqa: E402

_TEMPLATE_PATH = _HERE.parent / "templates" / "jobs" / "dream.yaml"
_DREAM_PY = _SKILL_SCRIPTS / "dream.py"

# 03:00 local on an ordinary night, inside the template's 02:00-06:00 window
# whatever the machine's time zone.
_IN_WINDOW = datetime(2026, 9, 11, 3, 0).timestamp()


def _engine() -> Path:
    return Path(os.environ["AGENTM_STATE_DIR"])


def _shape(digest_text: str) -> list:
    """The digest's structure without the run id or the vault path: its
    section headers and each finding's stage, kind and summary."""
    lines = []
    for line in digest_text.splitlines():
        if line.startswith("## ") or line.startswith("# Dream digest"):
            lines.append(re.sub(r"run \S+", "run <id>", line))
        elif line.startswith("- ") and " · " in line:
            lines.append(line.split(" (", 1)[0])
    return lines


class ManifestParsesTests(unittest.TestCase):
    def test_template_parses_per_runner_schema(self) -> None:
        self.assertTrue(_TEMPLATE_PATH.exists(), f"missing {_TEMPLATE_PATH}")
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            (jobs_dir / "dream.yaml").write_text(_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            jobs = manifest.load_manifests(jobs_dir)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.name, "dream")
        self.assertEqual(job.schedule, "daily")
        self.assertEqual(job.window, "02:00-06:00")
        self.assertEqual(job.order, 3)
        self.assertEqual(job.tier, "T3")
        self.assertNotIn("--batch-cap", job.command)
        self.assertTrue(job.dry_run, "the shipped manifest stays in dry-run")


class StaysInDryRunTests(unittest.TestCase):
    def test_due_dry_run_job_never_executes_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs_dir = root / "jobs"
            jobs_dir.mkdir()
            (jobs_dir / "dream.yaml").write_text(_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

            os.environ["AGENTM_STATE_DIR"] = str(root / "job-state")
            report = cycle.run_cycle(jobs_dir, now=_IN_WINDOW, state_root=root / "state",
                                     report_path=root / "digest.jsonl")

            self.assertEqual(len(report.outcomes), 1)
            outcome = report.outcomes[0]
            self.assertTrue(outcome.dry_run)
            self.assertFalse(outcome.ran)
            # The command was never executed at all: dry_run short-circuits
            # before subprocess.run, not just "ran quietly".
            self.assertFalse(any(_engine().glob("dream-runs/*")))


class SameShapeAsManualRunTests(unittest.TestCase):
    """When the job does run (dry_run overridden for this test only), it
    produces the same digest as the manual `run_dream()`."""

    def _seed_fixture_corpus(self, vault: Path) -> None:
        (vault / "a.md").write_text(
            "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today.\n", encoding="utf-8"
        )
        (vault / "b.md").write_text(
            "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today!\n", encoding="utf-8"
        )

    def test_job_invoked_command_matches_manual_run_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manual_vault = root / "manual-vault"
            manual_vault.mkdir()
            self._seed_fixture_corpus(manual_vault)
            os.environ["AGENTM_STATE_DIR"] = str(root / "manual-state")
            manual_digest = dream.run_dream(manual_vault, run_id="manual-run")
            manual_shape = _shape(manual_digest.digest_path.read_text(encoding="utf-8"))
            self.assertIn("- dedup · possible-twin: a.md and b.md are 98% alike — merge by hand, "
                          "or write `superseded_by` on the one that should go", manual_shape)

            job_vault = root / "job-vault"
            job_vault.mkdir()
            self._seed_fixture_corpus(job_vault)
            jobs_dir = root / "jobs"
            jobs_dir.mkdir()
            template = _TEMPLATE_PATH.read_text(encoding="utf-8")
            shipped = "command: python3 ../harness/skills/memory/scripts/dream.py\n"
            self.assertIn(shipped, template)
            live_manifest = template.replace("dry_run: true", "dry_run: false").replace(
                shipped, f'command: python3 "{_DREAM_PY}" --vault-path "{job_vault}"\n')
            (jobs_dir / "dream.yaml").write_text(live_manifest, encoding="utf-8")

            os.environ["AGENTM_STATE_DIR"] = str(root / "job-state")
            report = cycle.run_cycle(jobs_dir, now=_IN_WINDOW, state_root=root / "state",
                                     report_path=root / "digest.jsonl")

            self.assertEqual(len(report.outcomes), 1)
            outcome = report.outcomes[0]
            self.assertTrue(outcome.ran)
            self.assertEqual(outcome.exit_code, 0)

            runs = list((_engine() / "dream-runs").iterdir())
            self.assertEqual(len(runs), 1)
            job_shape = _shape((runs[0] / "digest.md").read_text(encoding="utf-8"))
            self.assertEqual(job_shape, manual_shape)


# Every test here gets its own engine state dir.
import os.path as _osp  # noqa: E402
import sys as _sys  # noqa: E402

if _osp.dirname(_osp.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _osp.dirname(_osp.abspath(__file__)))
from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main()
