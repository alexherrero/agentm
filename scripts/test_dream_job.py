#!/usr/bin/env python3
"""Tests for the dreaming pipeline's scheduled runner job (AG Wave E
dreaming plan, task 4): `templates/jobs/dream.yaml` + its wiring through
`scripts/runner/`.

`.harness/jobs/` is gitignored (per-project runtime state, same as every
other `.harness/*` file) — `templates/jobs/dream.yaml` is the tracked,
shipped source; a repo registers the job by copying it in. These tests load
that tracked template directly rather than assuming a `.harness/jobs/`
exists in this checkout.

Covers (plan task 4 verification):
  - the manifest parses per `scripts/runner/manifest.py`'s schema
  - the shipped template's `dry_run: true` means a due cycle reports
    `ran=False, dry_run=True` and the command NEVER executes — no
    `_dream-staging/` directory is created (proves "stays in dry-run, no
    live promotion")
  - a cycle where the SAME command is actually run (dry_run overridden to
    False for this test only, proving the wiring — not the shipped
    manifest's own posture) against a seeded fixture corpus produces a
    digest with the SAME shape (section headers, proposal stage/kind lines)
    as calling `dream.run_dream()` directly (task 2's manual run)
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
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


def _shape(digest_text: str) -> list:
    """Strip run-id/timestamp-specific text, keep the structural markers
    (section headers, `### N. stage — kind` lines) so two digests from
    different run_ids can be compared for "same shape"."""
    lines = []
    for line in digest_text.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            lines.append(re.sub(r"\d+\.\s", "N. ", line.split(" — ")[0] if " — " not in line else line))
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
        self.assertEqual(job.schedule, "weekly")
        self.assertEqual(job.tier, "T3")
        self.assertTrue(job.dry_run, "shipped manifest must stay in dry-run (plan Constraints)")


class StaysInDryRunTests(unittest.TestCase):
    """The plan's constraint: 'the job stays in dry-run (no live promotion)
    until task 2's calibration evidence... is explicitly recorded as
    sufficient'."""

    def test_due_dry_run_job_never_executes_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            (vault / "solo.md").write_text("---\nkind: workflow\n---\nOnly one entry.\n", encoding="utf-8")

            jobs_dir = root / "jobs"
            jobs_dir.mkdir()
            (jobs_dir / "dream.yaml").write_text(_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

            report = cycle.run_cycle(jobs_dir, now=1_000_000.0, state_root=root / "state", report_path=root / "digest.jsonl")

            self.assertEqual(len(report.outcomes), 1)
            outcome = report.outcomes[0]
            self.assertTrue(outcome.dry_run)
            self.assertFalse(outcome.ran)
            # The command was never executed at all — proves dry_run truly
            # short-circuits before subprocess.run, not just "ran quietly".
            self.assertFalse((vault / "_dream-staging").exists())


class SameShapeAsManualRunTests(unittest.TestCase):
    """Proves the job-command wiring is correct: WHEN the job does run
    (dry_run overridden False for this test — the shipped template itself
    stays dry_run: true per StaysInDryRunTests above), it produces the same
    digest shape as task 2's manual-run surface.

    The manual-run reference is `dream.run_dream_and_auto_apply()`, not the
    older bare `dream.run_dream()` — the job's shipped `command:` invokes
    `dream.py`'s CLI, and that CLI's default entry point auto-applies
    compression ("expire") proposals as of the 2026-07-11 operator ruling
    (`--no-auto-apply` opts back into the old propose-only shape). Both
    sides use a scratch `RevertLog` (via `--log-root`/`--lock-root` on the
    job side, `revert_log=` on the manual side) so neither ever touches the
    real `~/.cache` during the test."""

    def _seed_fixture_corpus(self, vault: Path) -> None:
        (vault / "a.md").write_text(
            "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today.\n", encoding="utf-8"
        )
        (vault / "b.md").write_text(
            "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today!\n", encoding="utf-8"
        )

    def test_job_invoked_command_matches_manual_run_shape(self) -> None:
        from revert_log import RevertLog  # noqa: E402  (same cross-dir import pattern as test_dream_confirm.py)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # Manual run (task 2's surface, now via the auto-apply wrapper
            # dream.py's CLI itself calls by default) — the reference shape.
            manual_vault = root / "manual-vault"
            manual_vault.mkdir()
            self._seed_fixture_corpus(manual_vault)
            manual_revert_log = RevertLog(
                manual_vault, log_root=root / "manual-scratch" / "revert-log",
                lock_root=root / "manual-scratch" / "locks",
            )
            manual_digest, _manual_batch = dream.run_dream_and_auto_apply(
                manual_vault, run_id="manual-run", revert_log=manual_revert_log,
            )
            manual_shape = _shape(manual_digest.digest_path.read_text(encoding="utf-8"))

            # Job-invoked run — same corpus shape, executed via the runner's
            # subprocess command (not a direct Python call), with dry_run
            # forced False purely to prove the wiring for this test.
            job_vault = root / "job-vault"
            job_vault.mkdir()
            self._seed_fixture_corpus(job_vault)

            jobs_dir = root / "jobs"
            jobs_dir.mkdir()
            template = _TEMPLATE_PATH.read_text(encoding="utf-8")
            job_scratch = root / "job-scratch"
            live_manifest = template.replace("dry_run: true", "dry_run: false").replace(
                "command: python3 ../harness/skills/memory/scripts/dream.py --batch-cap 25",
                f'command: python3 "{_DREAM_PY}" --vault-path "{job_vault}" --batch-cap 25 '
                f'--log-root "{job_scratch / "revert-log"}" --lock-root "{job_scratch / "locks"}"',
            )
            (jobs_dir / "dream.yaml").write_text(live_manifest, encoding="utf-8")

            report = cycle.run_cycle(jobs_dir, now=1_000_000.0, state_root=root / "state", report_path=root / "digest.jsonl")

            self.assertEqual(len(report.outcomes), 1)
            outcome = report.outcomes[0]
            self.assertTrue(outcome.ran)
            self.assertEqual(outcome.exit_code, 0)

            staging_runs = list((job_vault / "_dream-staging").iterdir())
            # A weekly cycle now stages TWO runs -- dream's own plus the
            # folded inbox-triage sub-run (auto-org part 3 task 4). This
            # test's intent is the DREAM digest's shape parity; select it
            # by its own header rather than assuming it's alone.
            dream_runs = [
                d for d in staging_runs
                if (d / "digest.md").read_text(encoding="utf-8").startswith("# Dream digest")
            ]
            self.assertEqual(len(dream_runs), 1)
            job_digest_text = (dream_runs[0] / "digest.md").read_text(encoding="utf-8")
            job_shape = _shape(job_digest_text)

            self.assertEqual(job_shape, manual_shape)


if __name__ == "__main__":
    unittest.main()
