#!/usr/bin/env python3
"""Unit tests for machinery_doctor.py (Consolidation follow-ups batch,
machinery-integrity lane, piece 2). Hermetic — every check is exercised
against synthetic fixture trees, never the real repo/vault/telemetry dir,
except `RealRepoSmokeTests`, which confirms `run_inventory()` runs clean
(never raises) against this actual checkout.

Run: `cd scripts && python3 -m unittest test_machinery_doctor -v`
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import machinery_doctor as md


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


class StopHookWiredTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_settings(self, hooks_block: dict) -> None:
        (self.repo / ".claude").mkdir(parents=True, exist_ok=True)
        (self.repo / ".claude" / "settings.json").write_text(json.dumps(hooks_block), encoding="utf-8")

    def test_fail_no_settings_json(self):
        c = md.check_stop_hook_wired(self.repo)
        self.assertEqual(c.status, "FAIL")
        self.assertIn("not found", c.detail)

    def test_fail_invalid_json(self):
        (self.repo / ".claude").mkdir(parents=True)
        (self.repo / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
        c = md.check_stop_hook_wired(self.repo)
        self.assertEqual(c.status, "FAIL")

    def test_fail_no_stop_block(self):
        self._write_settings({"hooks": {}})
        c = md.check_stop_hook_wired(self.repo)
        self.assertEqual(c.status, "FAIL")
        self.assertIn("no Stop hook", c.detail)

    def test_fail_stop_block_present_but_wrong_command(self):
        self._write_settings({"hooks": {"Stop": [{"hooks": [{"command": "bash something-else.sh"}]}]}})
        c = md.check_stop_hook_wired(self.repo)
        self.assertEqual(c.status, "FAIL")

    def test_fail_wired_but_script_missing(self):
        self._write_settings({"hooks": {"Stop": [{"hooks": [{"command": "bash .claude/hooks/session-cost-capture.sh"}]}]}})
        c = md.check_stop_hook_wired(self.repo)
        self.assertEqual(c.status, "FAIL")
        self.assertIn("script missing", c.detail)

    def test_ok_wired_and_script_present(self):
        self._write_settings({"hooks": {"Stop": [{"hooks": [{"command": "bash .claude/hooks/session-cost-capture.sh"}]}]}})
        (self.repo / ".claude" / "hooks").mkdir(parents=True)
        (self.repo / ".claude" / "hooks" / "session-cost-capture.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        c = md.check_stop_hook_wired(self.repo, telemetry_root=self.repo / "no-telemetry")
        self.assertEqual(c.status, "OK")
        self.assertIsNone(c.last_fired)

    def test_ok_reports_last_fired_from_telemetry_log(self):
        self._write_settings({"hooks": {"Stop": [{"hooks": [{"command": "bash .claude/hooks/session-cost-capture.sh"}]}]}})
        (self.repo / ".claude" / "hooks").mkdir(parents=True)
        (self.repo / ".claude" / "hooks" / "session-cost-capture.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        telemetry = self.repo / "telemetry"
        telemetry.mkdir()
        (telemetry / "events-202607.jsonl").write_text(
            json.dumps({"ts": "2026-07-10T12:00:00Z", "event": "session-cost"}) + "\n"
            + json.dumps({"ts": "2026-07-11T08:30:00Z", "event": "session-cost"}) + "\n"
            + json.dumps({"ts": "2026-07-11T09:00:00Z", "event": "other-kind"}) + "\n",
            encoding="utf-8",
        )
        c = md.check_stop_hook_wired(self.repo, telemetry_root=telemetry)
        self.assertEqual(c.status, "OK")
        self.assertIsNotNone(c.last_fired)
        # The latest session-cost event, not the later other-kind one.
        expected = md.last_event_epoch("session-cost", telemetry_root=telemetry)
        self.assertEqual(c.last_fired, expected)


class LastEventEpochTests(unittest.TestCase):
    def test_absent_dir_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(md.last_event_epoch("session-cost", telemetry_root=Path(td) / "nope"))

    def test_ignores_unparseable_lines(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events-202607.jsonl").write_text("not json\n{}\n", encoding="utf-8")
            self.assertIsNone(md.last_event_epoch("session-cost", telemetry_root=root))

    def test_picks_max_across_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events-202606.jsonl").write_text(
                json.dumps({"ts": "2026-06-01T00:00:00Z", "event": "session-cost"}) + "\n", encoding="utf-8"
            )
            (root / "events-202607.jsonl").write_text(
                json.dumps({"ts": "2026-07-05T00:00:00Z", "event": "session-cost"}) + "\n", encoding="utf-8"
            )
            got = md.last_event_epoch("session-cost", telemetry_root=root)
            self.assertIsNotNone(got)
            self.assertGreater(got, time.mktime((2026, 6, 15, 0, 0, 0, 0, 0, 0)))


class GitHookInstalledTests(unittest.TestCase):
    def test_warn_when_not_installed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            c = md.check_git_hook_installed(repo, "commit-msg")
            self.assertEqual(c.status, "WARN")
            self.assertIn("not installed", c.detail)

    def test_ok_when_installed_and_executable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            hooks_dir = md.git_hooks_dir(repo)
            self.assertIsNotNone(hooks_dir)
            hook_path = hooks_dir / "commit-msg"
            hook_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            hook_path.chmod(0o755)
            c = md.check_git_hook_installed(repo, "commit-msg")
            self.assertEqual(c.status, "OK")

    @unittest.skipIf(os.name == "nt", "chmod executable-bit semantics are POSIX-only; Windows has no equivalent permission model")
    def test_warn_when_installed_but_not_executable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            hooks_dir = md.git_hooks_dir(repo)
            hook_path = hooks_dir / "commit-msg"
            hook_path.write_text("#!/bin/sh\n", encoding="utf-8")
            hook_path.chmod(0o644)
            c = md.check_git_hook_installed(repo, "commit-msg")
            self.assertEqual(c.status, "WARN")
            self.assertIn("not executable", c.detail)

    def test_unverified_when_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as td:
            c = md.check_git_hook_installed(Path(td), "commit-msg")
            self.assertEqual(c.status, "UNVERIFIED")

    def test_worktree_safe_resolution(self):
        """A real git worktree's `.git` is a FILE, not a directory -- confirms
        `git_hooks_dir` resolves through the shared common dir rather than
        silently failing on `repo / '.git' / 'hooks'`."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            main_repo = root / "main"
            main_repo.mkdir()
            _init_repo(main_repo)
            (main_repo / "f.txt").write_text("x", encoding="utf-8")
            subprocess.run(["git", "add", "f.txt"], cwd=main_repo, check=True)
            subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                             "commit", "-q", "-m", "init"], cwd=main_repo, check=True)
            wt = root / "wt"
            subprocess.run(["git", "worktree", "add", "-q", str(wt), "-b", "wt-branch"],
                            cwd=main_repo, check=True)
            self.assertTrue((wt / ".git").is_file(), "expected a worktree gitlink file")
            hooks_dir = md.git_hooks_dir(wt)
            self.assertIsNotNone(hooks_dir)
            self.assertEqual(hooks_dir, md.git_hooks_dir(main_repo))


class RunnerJobCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        (self.repo / "templates" / "jobs").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_template(self, name: str, *, dry_run: bool = True) -> None:
        (self.repo / "templates" / "jobs" / f"{name}.yaml").write_text(
            f"schedule: daily\nlookback: 24h\ncommand: python3 -c 'pass'\n"
            f"tier: T2\ndry_run: {'true' if dry_run else 'false'}\n",
            encoding="utf-8",
        )

    def test_unverified_no_template(self):
        c = md.check_runner_job(self.repo, "nonexistent")
        self.assertEqual(c.status, "UNVERIFIED")

    def test_warn_template_not_registered(self):
        self._write_template("myjob")
        c = md.check_runner_job(self.repo, "myjob", state_root=self.repo / "state")
        self.assertEqual(c.status, "WARN")
        self.assertIn("not registered", c.detail)

    def test_fail_registered_manifest_malformed(self):
        self._write_template("myjob")
        jobs_dir = self.repo / ".harness" / "jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "myjob.yaml").write_text("schedule: bogus-cadence\n", encoding="utf-8")
        c = md.check_runner_job(self.repo, "myjob", state_root=self.repo / "state")
        self.assertEqual(c.status, "FAIL")

    def test_warn_registered_never_fired(self):
        self._write_template("myjob")
        jobs_dir = self.repo / ".harness" / "jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "myjob.yaml").write_text(
            (self.repo / "templates" / "jobs" / "myjob.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        c = md.check_runner_job(self.repo, "myjob", state_root=self.repo / "state")
        self.assertEqual(c.status, "WARN")
        self.assertIn("never fired", c.detail)
        self.assertIsNone(c.last_fired)

    def test_ok_registered_and_fired(self):
        from runner import state as state_mod

        self._write_template("myjob")
        jobs_dir = self.repo / ".harness" / "jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "myjob.yaml").write_text(
            (self.repo / "templates" / "jobs" / "myjob.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        state_root = self.repo / "state"
        state_mod.mark_done("myjob", now=1_700_000_000.0, state_root=state_root)
        c = md.check_runner_job(self.repo, "myjob", state_root=state_root)
        self.assertEqual(c.status, "OK")
        self.assertEqual(c.last_fired, 1_700_000_000.0)

    def test_job_names_discovers_all_templates(self):
        self._write_template("a")
        self._write_template("b")
        self.assertEqual(md.job_names(self.repo), ["a", "b"])


class CrossRepoChecksTests(unittest.TestCase):
    def test_cross_review_unverified_when_no_sibling(self):
        c = md.check_cross_review_visible_degradation(None)
        self.assertEqual(c.status, "UNVERIFIED")
        self.assertEqual(c.owner, "crickets code-review plugin")

    def test_cross_review_ok_when_marker_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script_dir = root / "src" / "code-review" / "scripts"
            script_dir.mkdir(parents=True)
            (script_dir / "cross-review.sh").write_text('echo "CROSS-REVIEW-DEGRADED: no gemini"\n', encoding="utf-8")
            c = md.check_cross_review_visible_degradation(root)
            self.assertEqual(c.status, "OK")

    def test_cross_review_fail_when_marker_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script_dir = root / "src" / "code-review" / "scripts"
            script_dir.mkdir(parents=True)
            (script_dir / "cross-review.sh").write_text('echo "fallback"\n', encoding="utf-8")
            c = md.check_cross_review_visible_degradation(root)
            self.assertEqual(c.status, "FAIL")

    def test_coordination_suite_unverified_when_no_sibling(self):
        c = md.check_crickets_coordination_suite(None)
        self.assertEqual(c.status, "UNVERIFIED")

    def test_coordination_suite_ok_when_all_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scripts_dir = root / "src" / "development-lifecycle" / "scripts"
            scripts_dir.mkdir(parents=True)
            for n in ("preflight_reconcile.py", "check-plan-grounding.py", "escalation_tripwire.py",
                      "agentm_bridge.py", "doctor_worktrees.py"):
                (scripts_dir / n).write_text("", encoding="utf-8")
            c = md.check_crickets_coordination_suite(root)
            self.assertEqual(c.status, "OK")

    def test_coordination_suite_fail_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "development-lifecycle" / "scripts").mkdir(parents=True)
            c = md.check_crickets_coordination_suite(root)
            self.assertEqual(c.status, "FAIL")


class SummarizeAndRenderTests(unittest.TestCase):
    def test_summarize_counts_each_status(self):
        checks = [
            md.Check("a", "OK", "x"), md.Check("b", "WARN", "x"),
            md.Check("c", "FAIL", "x"), md.Check("d", "UNVERIFIED", "x"),
            md.Check("e", "OK", "x"),
        ]
        counts = md.summarize(checks)
        self.assertEqual(counts, {"OK": 2, "WARN": 1, "FAIL": 1, "UNVERIFIED": 1})

    def test_render_text_includes_summary_line(self):
        checks = [md.Check("a", "OK", "fine")]
        text = md.render_text(checks)
        self.assertIn("summary: 1 OK, 0 WARN, 0 FAIL, 0 UNVERIFIED", text)
        self.assertIn("a", text)

    def test_check_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            md.Check("x", "NOPE", "detail")


class RealRepoSmokeTests(unittest.TestCase):
    """Confirms the composed inventory runs clean (never raises) against
    this actual checkout -- the same "always degrades, never crashes"
    contract console.py's own sections hold."""

    def test_run_inventory_never_raises(self):
        checks = md.run_inventory(md.repo_root())
        self.assertGreater(len(checks), 0)
        for c in checks:
            self.assertIn(c.status, ("OK", "WARN", "FAIL", "UNVERIFIED"))

    def test_main_exits_zero(self):
        self.assertEqual(md.main([]), 0)

    def test_main_json_mode(self):
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = md.main(["--format", "json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIn("checks", payload)
        self.assertIn("summary", payload)


if __name__ == "__main__":
    unittest.main()
