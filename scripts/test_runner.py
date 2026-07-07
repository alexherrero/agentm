#!/usr/bin/env python3
"""Unit tests for scripts/runner/ — the AgentM runner (agentm-runner.md,
Wave-B leader 1/5). Stdlib unittest, no network, no real vault.

Run directly: `cd scripts && python3 -m unittest test_runner -v`
Auto-discovered by `python3 -m unittest discover -p 'test_*.py'` (check-all.sh).
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from runner import cycle, manifest, state, watchdog


def _write_job(jobs_dir: Path, name: str, **fields) -> Path:
    jobs_dir.mkdir(parents=True, exist_ok=True)
    body = {
        "schedule": fields.pop("schedule", "daily"),
        "lookback": fields.pop("lookback", "6h"),
        "command": fields.pop("command", "true"),
    }
    body.update(fields)
    lines = [f"{k}: {json.dumps(v)}" for k, v in body.items()]
    p = jobs_dir / f"{name}.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


class DurationParsingTests(unittest.TestCase):
    def test_named_schedules(self):
        self.assertEqual(manifest.schedule_interval_seconds("hourly"), 3600)
        self.assertEqual(manifest.schedule_interval_seconds("daily"), 86400)
        self.assertEqual(manifest.schedule_interval_seconds("weekly"), 604800)

    def test_raw_duration(self):
        self.assertEqual(manifest.parse_duration("24h"), 86400)
        self.assertEqual(manifest.parse_duration("7d"), 604800)
        self.assertEqual(manifest.parse_duration("30m"), 1800)

    def test_malformed_duration_raises(self):
        with self.assertRaises(manifest.ManifestError):
            manifest.parse_duration("not-a-duration")


class ManifestLoadTests(unittest.TestCase):
    def test_missing_jobs_dir_is_empty_not_an_error(self):
        with TemporaryDirectory() as td:
            jobs = manifest.load_manifests(Path(td) / "nonexistent")
            self.assertEqual(jobs, [])

    def test_loads_valid_manifest(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            _write_job(jobs_dir, "dreaming", schedule="daily", lookback="24h",
                       command="echo hi", tier="T3", dry_run=False)
            jobs = manifest.load_manifests(jobs_dir)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].name, "dreaming")
            self.assertEqual(jobs[0].interval_seconds, 86400)
            self.assertEqual(jobs[0].lookback_seconds, 86400)
            self.assertFalse(jobs[0].dry_run)

    def test_missing_required_field_raises(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            jobs_dir.mkdir(parents=True)
            (jobs_dir / "broken.yaml").write_text("schedule: daily\n", encoding="utf-8")
            with self.assertRaises(manifest.ManifestError):
                manifest.load_manifests(jobs_dir)

    def test_t1_tier_is_rejected(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            _write_job(jobs_dir, "personal-write-job", tier="T1")
            with self.assertRaises(manifest.ManifestError):
                manifest.load_manifests(jobs_dir)

    def test_default_dry_run_is_true(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            _write_job(jobs_dir, "fresh-job")
            jobs = manifest.load_manifests(jobs_dir)
            self.assertTrue(jobs[0].dry_run)


class DueDecisionTests(unittest.TestCase):
    def _job(self, **overrides) -> manifest.JobManifest:
        fields = dict(name="j", schedule="daily", lookback="6h", command="true", tier="T3", dry_run=False)
        fields.update(overrides)
        return manifest.JobManifest(**fields)

    def test_never_run_is_due(self):
        with TemporaryDirectory() as td:
            due, reason = cycle.is_due(self._job(), now=1000.0, state_root=Path(td))
            self.assertTrue(due)
            self.assertEqual(reason, "never-run")

    def test_not_yet_due(self):
        with TemporaryDirectory() as td:
            state.mark_done("j", now=1000.0, state_root=Path(td))
            due, reason = cycle.is_due(self._job(), now=1000.0 + 100, state_root=Path(td))
            self.assertFalse(due)
            self.assertEqual(reason, "not-due")

    def test_due_after_interval(self):
        with TemporaryDirectory() as td:
            state.mark_done("j", now=1000.0, state_root=Path(td))
            due, reason = cycle.is_due(self._job(), now=1000.0 + 86400 + 1, state_root=Path(td))
            self.assertTrue(due)
            self.assertEqual(reason, "due")

    def test_catch_up_within_lookback_after_sleep(self):
        # A device off over a long weekend: daily job overdue by 2 days,
        # well within a 6h... use a generous lookback to model "still catches up".
        with TemporaryDirectory() as td:
            job = self._job(lookback="3d")
            state.mark_done("j", now=1000.0, state_root=Path(td))
            now = 1000.0 + 86400 + (2 * 86400)  # 2 days late
            due, reason = cycle.is_due(job, now=now, state_root=Path(td))
            self.assertTrue(due)
            self.assertEqual(reason, "due")

    def test_missed_beyond_lookback_reanchors_without_running(self):
        with TemporaryDirectory() as td:
            job = self._job(lookback="1h")
            state.mark_done("j", now=1000.0, state_root=Path(td))
            now = 1000.0 + 86400 + (10 * 86400)  # way past lookback
            due, reason = cycle.is_due(job, now=now, state_root=Path(td))
            self.assertFalse(due)
            self.assertEqual(reason, "missed-beyond-lookback")
            # Re-anchored: immediately after, it should read as "not-due" again
            # rather than perpetually "missed" on every subsequent check.
            due2, reason2 = cycle.is_due(job, now=now + 1, state_root=Path(td))
            self.assertFalse(due2)
            self.assertEqual(reason2, "not-due")

    def test_orphaned_start_is_retried(self):
        with TemporaryDirectory() as td:
            state.mark_start("j", now=1000.0, state_root=Path(td))
            due, reason = cycle.is_due(self._job(), now=1000.5, state_root=Path(td))
            self.assertTrue(due)
            self.assertEqual(reason, "orphaned-start")


class CycleIdempotencyTests(unittest.TestCase):
    def test_repeat_cycle_on_same_instant_is_a_no_op_second_time(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            state_root = Path(td) / "state"
            report_path = Path(td) / "digest.jsonl"
            _write_job(jobs_dir, "j", schedule="daily", lookback="6h",
                       command="true", tier="T3", dry_run=False)

            r1 = cycle.run_cycle(jobs_dir, now=1000.0, state_root=state_root, report_path=report_path)
            self.assertEqual(len(r1.outcomes), 1)
            self.assertTrue(r1.outcomes[0].ran)

            # Re-run the identical cycle an instant later: nothing is due yet.
            r2 = cycle.run_cycle(jobs_dir, now=1000.5, state_root=state_root, report_path=report_path)
            self.assertEqual(len(r2.outcomes), 1)
            self.assertFalse(r2.outcomes[0].ran)
            self.assertEqual(r2.outcomes[0].skipped_reason, "not-due")

    def test_dry_run_job_writes_nothing_but_reports(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            state_root = Path(td) / "state"
            report_path = Path(td) / "digest.jsonl"
            _write_job(jobs_dir, "fresh", schedule="daily", lookback="6h", command="true")  # dry_run defaults True

            report = cycle.run_cycle(jobs_dir, now=1000.0, state_root=state_root, report_path=report_path)
            self.assertEqual(len(report.outcomes), 1)
            self.assertTrue(report.outcomes[0].dry_run)
            self.assertFalse(report.outcomes[0].ran)
            # No per-job marker written on a dry-run — it stays "never-run".
            marker = state.read_marker("fresh", state_root=state_root)
            self.assertEqual(marker, {})
            # But the report surface got the rendered command.
            lines = report_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertTrue(record["dry_run"])
            self.assertEqual(record["rendered_command"], "true")

    def test_budget_ceiling_stops_further_real_runs(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            state_root = Path(td) / "state"
            harness_dir = Path(td) / "harness"
            harness_dir.mkdir()
            (harness_dir / "budget.yaml").write_text("daily_usd_ceiling: 0.0\n", encoding="utf-8")
            _write_job(jobs_dir, "expensive", schedule="daily", lookback="6h",
                       command="true", tier="T3", dry_run=False)

            report = cycle.run_cycle(jobs_dir, now=1000.0, state_root=state_root, harness_dir=harness_dir)
            self.assertTrue(report.budget_ceiling_hit)
            self.assertFalse(report.outcomes[0].ran)
            self.assertEqual(report.outcomes[0].skipped_reason, "budget-ceiling")

    def test_t2_report_survives_concurrent_style_append(self):
        # T2 reports route through vault_lock.atomic_write; two sequential
        # cycles should both land, proving the write path doesn't clobber.
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            state_root = Path(td) / "state"
            report_path = Path(td) / "digest.jsonl"
            _write_job(jobs_dir, "curated-job", schedule="hourly", lookback="1h",
                       command="true", tier="T2", dry_run=False)

            cycle.run_cycle(jobs_dir, now=1000.0, state_root=state_root, report_path=report_path)
            cycle.run_cycle(jobs_dir, now=1000.0 + 3600 + 1, state_root=state_root, report_path=report_path)

            lines = report_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)


class HealthPassJobManifestTests(unittest.TestCase):
    """Locks the schema shape of `.harness/jobs/health-pass.yaml` (AG Wave E
    task 1, agentm-runner.md). The manifest itself is gitignored (`.harness/`
    is machine-local, per repo convention) so it can't be asserted against
    directly in a portable test — this fixture mirrors its exact content so a
    regression in `manifest.py`'s parsing of this job shape still fails loud."""

    _HEALTH_PASS_FIELDS = dict(
        schedule="daily",
        lookback="24h",
        command="bash health/run-fast-tier.sh | python3 health/health_score.py --history",
        tier="T2",
        dry_run=True,
    )

    def test_health_pass_manifest_shape_loads(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            _write_job(jobs_dir, "health-pass", **self._HEALTH_PASS_FIELDS)
            jobs = manifest.load_manifests(jobs_dir)
            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            self.assertEqual(job.name, "health-pass")
            self.assertEqual(job.interval_seconds, 86400)  # daily
            self.assertEqual(job.lookback_seconds, 86400)  # 24h — catches one missed daily run
            self.assertEqual(job.tier, "T2")  # curated: appends the tracked scripts/health/history.jsonl
            self.assertTrue(job.dry_run)  # ships dry-run; the operator promotes it
            self.assertEqual(
                job.command,
                "bash health/run-fast-tier.sh | python3 health/health_score.py --history",
            )


class WatchdogTests(unittest.TestCase):
    def test_healthy_by_default(self):
        with TemporaryDirectory() as td:
            health = watchdog.read_health("j", state_root=Path(td))
            self.assertEqual(health["rung"], "healthy")
            self.assertFalse(watchdog.is_paused("j", state_root=Path(td)))

    def test_escalates_through_the_ladder_on_a_failure_streak(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            for i in range(1, 9):
                health = watchdog.record_outcome("j", succeeded=False, now=float(i), state_root=root)
            self.assertEqual(health["consecutive_failures"], 8)
            self.assertEqual(health["rung"], "stop")
            self.assertTrue(watchdog.is_stopped("j", state_root=root))
            self.assertTrue(watchdog.is_paused("j", state_root=root))

    def test_a_success_resets_the_streak(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            for i in range(1, 4):
                watchdog.record_outcome("j", succeeded=False, now=float(i), state_root=root)
            health = watchdog.record_outcome("j", succeeded=True, now=100.0, state_root=root)
            self.assertEqual(health["rung"], "healthy")
            self.assertEqual(health["consecutive_failures"], 0)
            self.assertFalse(watchdog.is_paused("j", state_root=root))

    def test_cycle_halts_a_job_that_hits_the_stop_rung(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            state_root = Path(td) / "state"
            _write_job(jobs_dir, "flaky", schedule="hourly", lookback="1h",
                       command="false", tier="T3", dry_run=False)

            now = 1000.0
            for _ in range(8):
                report = cycle.run_cycle(jobs_dir, now=now, state_root=state_root)
                self.assertTrue(report.outcomes[0].ran, report.outcomes[0])  # throttle/pause still attempt
                now += 3601  # just past the hourly interval each time
            # 8 consecutive real failures ("false" always exits 1) trips the
            # stop rung; the 9th cycle is the first that's actually halted.
            report = cycle.run_cycle(jobs_dir, now=now, state_root=state_root)
            self.assertFalse(report.outcomes[0].ran)
            self.assertEqual(report.outcomes[0].skipped_reason, "watchdog-stop")


if __name__ == "__main__":
    unittest.main()
