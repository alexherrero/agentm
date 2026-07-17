#!/usr/bin/env python3
"""Unit tests for scripts/runner/ — the AgentM runner (agentm-runner.md,
Wave-B leader 1/5). Stdlib unittest, no network, no real vault.

Run directly: `cd scripts && python3 -m unittest test_runner -v`
Auto-discovered by `python3 -m unittest discover -p 'test_*.py'` (check-all.sh).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

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

    def test_missed_beyond_lookback_marker_stays_distinguishable_from_a_real_run(self):
        # 2026-07-17 honesty-surface finding: pre-fix, mark_done's marker for
        # a re-anchor was byte-identical to a real success -- a job that
        # never actually ran read exactly like one that did.
        with TemporaryDirectory() as td:
            root = Path(td)
            job = self._job(lookback="1h")
            state.mark_done("j", now=1000.0, state_root=root)
            now = 1000.0 + 86400 + (10 * 86400)
            cycle.is_due(job, now=now, state_root=root)
            marker = state.read_marker("j", state_root=root)
            self.assertTrue(state.was_last_advance_a_miss(marker))
            # last_run (the due-clock) advanced to `now` -- scheduling still works.
            self.assertEqual(state.last_run_epoch(marker), now)
            # last_real_run stays pinned to the prior GENUINE run, not `now`.
            self.assertEqual(state.last_real_run_epoch(marker), 1000.0)

    def test_a_real_run_after_a_miss_clears_the_flag_and_advances_last_real_run(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            state.mark_done("j", now=1000.0, state_root=root)
            state.mark_missed("j", now=2000.0, state_root=root)
            state.mark_done("j", now=3000.0, cost_usd=0.01, state_root=root)
            marker = state.read_marker("j", state_root=root)
            self.assertFalse(state.was_last_advance_a_miss(marker))
            self.assertEqual(state.last_real_run_epoch(marker), 3000.0)
            self.assertEqual(state.last_run_epoch(marker), 3000.0)

    def test_a_job_that_has_never_really_run_reports_no_last_real_run(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            state.mark_missed("j", now=1000.0, state_root=root)
            marker = state.read_marker("j", state_root=root)
            self.assertTrue(state.was_last_advance_a_miss(marker))
            self.assertIsNone(state.last_real_run_epoch(marker))
            # Due-clock still advanced, so scheduling is unaffected.
            self.assertEqual(state.last_run_epoch(marker), 1000.0)

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

    @unittest.skipUnless(os.name == "posix",
                          "reproduces a bash ctype mis-tokenization; "
                          "job.command runs under cmd.exe on Windows, not bash")
    def test_survives_child_locale_coercion(self):
        # A launchd-invoked process (no LANG/LC_ALL in its plist) gets
        # LC_CTYPE=C.UTF-8 written into os.environ by CPython's PEP 538
        # startup coercion. macOS system bash mis-tokenizes a `$var`
        # immediately followed by multibyte punctuation under that locale,
        # corrupting stderr bytes enough that decoding raises inside
        # subprocess.communicate() -- reproduced live 2026-07-15 via
        # run-fast-tier.sh:31's exact `$label…` pattern. Uncaught, that
        # exception used to fall into cycle.py's bare `except Exception`
        # and surface as an undiagnosable exit_code=-1.
        #
        # The command below is the REAL failure shape, not a simplified
        # stand-in: a `local` variable inside a function, under `set -u`,
        # immediately followed by multibyte punctuation -- exactly
        # run-fast-tier.sh's own `run_suite() { local label="$1"; ...;
        # echo "...$label…"; }`. A top-level (non-local, no `set -u`)
        # variable does NOT reproduce this -- it silently mis-renders
        # instead of raising "unbound variable", which is why the original
        # version of this test (a bare `label="x"; echo "$label…"`) passed
        # even against the broken `en_US.UTF-8` fix that shipped in #316
        # and didn't actually resolve the real job's failure. Re-verified
        # 2026-07-16: only `LC_ALL=C` (byte-oriented, no multibyte ctype
        # classification) avoids the mis-tokenization for this shape --
        # en_US.UTF-8 reproduces the same "unbound variable" failure C.UTF-8
        # does, just via a different corrupted byte.
        from unittest import mock
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            state_root = Path(td) / "state"
            report_path = Path(td) / "digest.jsonl"
            command = (
                'set -u; '
                'f() { local label="$1"; shift; echo "run: $label…" >&2; }; '
                'f x; echo ok'
            )
            _write_job(jobs_dir, "j", schedule="daily", lookback="6h",
                       command=command, tier="T3", dry_run=False)

            poisoned = {k: v for k, v in os.environ.items()
                        if k not in ("LANG", "LC_ALL", "LC_CTYPE")}
            poisoned["LC_CTYPE"] = "C.UTF-8"
            with mock.patch.dict("os.environ", poisoned, clear=True):
                report = cycle.run_cycle(jobs_dir, now=1000.0, state_root=state_root,
                                          report_path=report_path)
            self.assertEqual(report.outcomes[0].exit_code, 0)

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

    def test_budget_gate_fails_closed_with_no_config(self):
        # ROADMAP-TAIL-ADJUDICATIONS.md B3 / AA4 2026-07-08 fix: a stranger's
        # clone ships no .harness/budget.yaml at all -- the fleet ceiling
        # must still default to a safe cap and gate a fleet that has already
        # reported large spend, not skip the check entirely (the fail-open
        # bug this test guards against regressing).
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            state_root = Path(td) / "state"
            harness_dir = Path(td) / "harness"
            harness_dir.mkdir()  # exists, but no budget.yaml inside it
            _write_job(jobs_dir, "expensive", schedule="daily", lookback="6h",
                       command="true", tier="T3", dry_run=False)
            # Seed prior spend directly (no real subprocess needed) well
            # above any sane default ceiling.
            state.mark_done("expensive", now=500.0, cost_usd=100.0, state_root=state_root)

            # 90000s later: past the daily (86400s) interval, still inside
            # the 6h lookback window -- squarely "due", not "missed".
            report = cycle.run_cycle(jobs_dir, now=90000.0, state_root=state_root, harness_dir=harness_dir)
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
    """Locks the schema shape of `.harness/jobs/health-pass.yaml`
    (templates/jobs/health-pass.yaml, registered V8 proving Lane S,
    2026-07-13). The manifest itself is gitignored (`.harness/` is
    machine-local, per repo convention) so it can't be asserted against
    directly in a portable test — this fixture mirrors its exact content so a
    regression in `manifest.py`'s parsing of this job shape still fails loud."""

    _HEALTH_PASS_FIELDS = dict(
        schedule="daily",
        lookback="24h",
        command="bash health/run-fast-tier.sh | python3 health/health_score.py --history --html",
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
            self.assertEqual(job.tier, "T2")  # curated: appends the vault-resolved health-history ledger
            self.assertTrue(job.dry_run)  # ships dry-run; the operator promotes it
            self.assertEqual(
                job.command,
                "bash health/run-fast-tier.sh | python3 health/health_score.py --history --html",
            )


class RunnerEntrypointTests(unittest.TestCase):
    """End-to-end regression coverage for scripts/agentm-runner.sh itself --
    the actual launchd entry point, not just the cycle.py/manifest.py
    internals the rest of this file exercises against. Reproduces the exact
    invocation com.agentm.runner.plist uses (`bash agentm-runner.sh run`,
    PATH-only environment, cwd not the repo root -- launchd sets none)
    against an isolated fixture repo, so the two bugs this class guards
    against fail here the same way they silently failed for real (12 days,
    2026-07-05 through 2026-07-17: every launchd-triggered cycle completed
    exit-0 with zero jobs discovered)."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.repo_root = Path(self.tmp.name) / "repo"
        self.scripts_dir = self.repo_root / "scripts"
        real_scripts_dir = Path(__file__).resolve().parent
        # Only what agentm-runner.sh + runner.cli actually need -- not the
        # whole scripts/ tree, which would drag in unrelated sibling-import
        # surface this test has no business depending on.
        shutil.copytree(real_scripts_dir / "runner", self.scripts_dir / "runner")
        shutil.copy2(real_scripts_dir / "agentm-runner.sh", self.scripts_dir / "agentm-runner.sh")
        shutil.copy2(real_scripts_dir / "vault_lock.py", self.scripts_dir / "vault_lock.py")
        shutil.copy2(real_scripts_dir / "vault_project.py", self.scripts_dir / "vault_project.py")
        shutil.copy2(real_scripts_dir / "harness_memory.py", self.scripts_dir / "harness_memory.py")
        (self.scripts_dir / "_vault_probe.py").write_text(
            "import os, sys\n"
            "sys.exit(0 if os.environ.get('MEMORY_VAULT_PATH') == sys.argv[1] else 1)\n",
            encoding="utf-8",
        )
        self.jobs_dir = self.repo_root / ".harness" / "jobs"
        self.jobs_dir.mkdir(parents=True)
        self.fake_home = Path(self.tmp.name) / "home"
        self.fake_home.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, *, env_extra: "dict | None" = None) -> dict:
        # Put this same interpreter's directory first on PATH so the
        # subprocess's `python3` resolves to whichever one is actually
        # running this test suite -- the ambient shell's own PATH may
        # resolve `python3` to a different interpreter (e.g. the macOS
        # system stub). HOME is faked so the subprocess's runner state
        # writes (~/.cache/agentm/runner/*) never touch the real machine's
        # state -- but PyYAML here is resolved via *user* site-packages
        # (rooted at the real $HOME), so faking HOME would otherwise also
        # hide it; PYTHONPATH pointed straight at yaml's own actual
        # site-packages dir keeps it importable regardless.
        path = f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"
        env = {
            "PATH": path,
            "HOME": str(self.fake_home),
            "PYTHONPATH": str(Path(yaml.__file__).resolve().parent.parent),
        }
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(
            ["bash", str(self.scripts_dir / "agentm-runner.sh"), "run"],
            cwd=str(self.tmp.name),  # deliberately not the repo root -- launchd sets no cwd
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_discovers_jobs_at_the_real_repo_root_not_scripts_dot_harness(self) -> None:
        # Pre-fix: --jobs-dir defaulted to ".harness/jobs", resolved against
        # cwd=scripts/ (the cd agentm-runner.sh does for its own sibling
        # import) -- a directory that never exists, so load_manifests()
        # silently returned [] every single cycle.
        _write_job(self.jobs_dir, "probe", schedule="daily", lookback="6h",
                   command="true", tier="T3", dry_run=False)
        summary = self._run()
        self.assertEqual(len(summary["outcomes"]), 1, summary)
        self.assertEqual(summary["outcomes"][0]["job"], "probe")
        self.assertTrue(summary["outcomes"][0]["ran"], summary)
        self.assertEqual(summary["outcomes"][0]["exit_code"], 0, summary)

    def test_exports_memory_vault_path_when_launcher_does_not_set_it(self) -> None:
        # Pre-fix: a launchd LaunchAgent's plist sets no MEMORY_VAULT_PATH (its
        # EnvironmentVariables block on the real machine sets only PATH), so a
        # job command referencing "$MEMORY_VAULT_PATH" (e.g. inbox_digest.py
        # --vault-path) silently expanded to "" -- which Path("") resolves to
        # cwd, not "no vault".
        vault = Path(self.tmp.name) / "vault"
        vault.mkdir()
        prefix = Path(self.tmp.name) / "install-prefix"
        prefix.mkdir()
        (prefix / ".agentm-config.json").write_text(
            json.dumps({"plugins.obsidian-vault.vault_path": str(vault)}), encoding="utf-8",
        )
        _write_job(
            self.jobs_dir, "vault-probe", schedule="daily", lookback="6h", tier="T3", dry_run=False,
            command=f"{sys.executable} {self.scripts_dir / '_vault_probe.py'} {vault}",
        )
        summary = self._run(env_extra={"AGENTM_INSTALL_PREFIX": str(prefix)})
        self.assertEqual(summary["outcomes"][0]["exit_code"], 0, summary)

    def test_respects_an_already_set_memory_vault_path(self) -> None:
        override = Path(self.tmp.name) / "override-vault"
        override.mkdir()
        _write_job(
            self.jobs_dir, "vault-probe", schedule="daily", lookback="6h", tier="T3", dry_run=False,
            command=f"{sys.executable} {self.scripts_dir / '_vault_probe.py'} {override}",
        )
        summary = self._run(env_extra={"MEMORY_VAULT_PATH": str(override)})
        self.assertEqual(summary["outcomes"][0]["exit_code"], 0, summary)


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
