#!/usr/bin/env python3
"""Tests for `templates/jobs/vault-lint.yaml` — vault_lint.py's `--audit`
mode had shipped since V4 #33 but was never registered as a scheduled
runner job. This locks the manifest's shape (mirrors
HealthPassSiblingDocsDriftManifestTests in test_docs_drift_job.py), proves
the shipped `dry_run: true` template never executes the command on a due
cycle (mirrors StaysInDryRunTests in test_dream_job.py), and separately
proves the exact registered command runs clean end-to-end against a
fixture vault when dry_run is overridden for evidence-gathering only —
the shipped template itself stays dry-run pending a future, explicitly
recorded promotion decision, same as every sibling job template.

Run: `cd scripts && python3 -m unittest test_vault_lint_job -v`
Auto-discovered by `python3 -m unittest discover -p 'test_*.py'` (check-all.sh).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from runner import cycle, manifest

_HERE = Path(__file__).resolve().parent
_TEMPLATE_PATH = _HERE.parent / "templates" / "jobs" / "vault-lint.yaml"
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))


class ManifestParsesTests(unittest.TestCase):
    def test_template_parses_per_runner_schema(self) -> None:
        self.assertTrue(_TEMPLATE_PATH.exists(), f"missing {_TEMPLATE_PATH}")
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            (jobs_dir / "vault-lint.yaml").write_text(
                _TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
            jobs = manifest.load_manifests(jobs_dir)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.name, "vault-lint")
        self.assertEqual(job.schedule, "weekly")
        self.assertEqual(job.interval_seconds, 604800)
        self.assertEqual(job.lookback, "7d")
        self.assertEqual(job.tier, "T2")
        self.assertEqual(job.command, "python3 ../harness/skills/memory/scripts/vault_lint.py --audit")
        self.assertTrue(job.dry_run, "shipped manifest must stay in dry-run, matching every sibling job template")

    def test_t1_tier_is_rejected_for_this_job_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            body = _TEMPLATE_PATH.read_text(encoding="utf-8").replace("tier: T2", "tier: T1")
            (jobs_dir / "vault-lint.yaml").write_text(body, encoding="utf-8")
            with self.assertRaises(manifest.ManifestError):
                manifest.load_manifests(jobs_dir)


class StaysInDryRunTests(unittest.TestCase):
    """The shipped manifest (dry_run: true) never executes the command on a
    due cycle -- mirrors StaysInDryRunTests in test_dream_job.py."""

    def test_due_dry_run_job_never_executes_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs_dir = root / "jobs"
            jobs_dir.mkdir()
            (jobs_dir / "vault-lint.yaml").write_text(
                _TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )

            report = cycle.run_cycle(
                jobs_dir, now=1_000_000.0, state_root=root / "state", report_path=root / "digest.jsonl"
            )

            self.assertEqual(len(report.outcomes), 1)
            outcome = report.outcomes[0]
            self.assertTrue(outcome.dry_run)
            self.assertFalse(outcome.ran)


class SameShapeAsManualRunTests(unittest.TestCase):
    """Proves the exact registered command runs clean end-to-end against a
    fixture vault when dry_run is overridden False for this test only (the
    shipped template itself stays dry_run: true per StaysInDryRunTests
    above) -- mirrors SameShapeAsManualRunTests in test_dream_job.py. This
    is the evidence a future, explicit promotion decision would need, not
    proof the shipped manifest runs live today."""

    def test_registered_command_runs_and_writes_audit_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            (vault / "personal").mkdir(parents=True)
            (vault / "personal" / "solo.md").write_text(
                "---\nkind: convention\nstatus: active\ncreated: 2026-01-01\n"
                "updated: 2026-01-01\ntags: [dev-flow]\ngroup: personal\n"
                "slug: solo\n---\n\nOnly one entry.\n",
                encoding="utf-8",
            )

            jobs_dir = root / "jobs"
            jobs_dir.mkdir()
            template = _TEMPLATE_PATH.read_text(encoding="utf-8")
            live_manifest = template.replace("dry_run: true", "dry_run: false").replace(
                "command: python3 ../harness/skills/memory/scripts/vault_lint.py --audit",
                f'command: python3 "{_SKILL_SCRIPTS / "vault_lint.py"}" --audit --vault "{vault}"',
            )
            (jobs_dir / "vault-lint.yaml").write_text(live_manifest, encoding="utf-8")

            report = cycle.run_cycle(
                jobs_dir, now=1_000_000.0, state_root=root / "state", report_path=root / "digest.jsonl"
            )

            self.assertEqual(len(report.outcomes), 1)
            outcome = report.outcomes[0]
            self.assertFalse(outcome.dry_run)
            self.assertTrue(outcome.ran)
            self.assertEqual(outcome.exit_code, 0)

            audit_files = list((vault / "_meta").glob("vault-lint-*.md"))
            self.assertEqual(len(audit_files), 1)
            content = audit_files[0].read_text(encoding="utf-8")
            self.assertIn("MemoryVault lint audit", content)


if __name__ == "__main__":
    unittest.main()
