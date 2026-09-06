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
from unittest import mock

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


class UnattendedMergeGateTests(unittest.TestCase):
    """check_unattended_merge_gate: the V8-proving item-19 gate. Reads the
    *global* settings.json via an injected path (never the real ~/.claude one),
    and keys off whether the n1-overnight job is registered in .harness/jobs/."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.settings = self.repo / "global-settings.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _register_job(self) -> None:
        jobs = self.repo / ".harness" / "jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        (jobs / f"{md._UNATTENDED_DISPATCH_JOB}.yaml").write_text("schedule: daily\n", encoding="utf-8")

    def _write_settings(self, perms: dict) -> None:
        self.settings.write_text(json.dumps({"permissions": perms}), encoding="utf-8")

    def test_ok_when_no_job_registered(self):
        # No .harness/jobs/n1-overnight.yaml — the gate isn't exercised here.
        c = md.check_unattended_merge_gate(self.repo, settings_path=self.settings)
        self.assertEqual(c.status, "OK")
        self.assertIn("no unattended-dispatch job registered", c.detail)

    def test_warn_when_registered_but_settings_absent(self):
        self._register_job()
        c = md.check_unattended_merge_gate(self.repo, settings_path=self.settings)
        self.assertEqual(c.status, "WARN")
        self.assertIn("is absent", c.detail)

    def test_warn_when_registered_but_settings_invalid_json(self):
        self._register_job()
        self.settings.write_text("{not json", encoding="utf-8")
        c = md.check_unattended_merge_gate(self.repo, settings_path=self.settings)
        self.assertEqual(c.status, "WARN")
        self.assertIn("invalid JSON", c.detail)

    def test_warn_when_rule_in_ask(self):
        self._register_job()
        self._write_settings({"allow": ["Bash(gh *)"], "ask": [md._GH_PR_MERGE_RULE]})
        c = md.check_unattended_merge_gate(self.repo, settings_path=self.settings)
        self.assertEqual(c.status, "WARN")
        self.assertIn("`ask`", c.detail)
        self.assertIn("deny>ask>allow", c.detail)

    def test_ok_when_rule_in_allow_not_ask(self):
        self._register_job()
        self._write_settings({"allow": [md._GH_PR_MERGE_RULE], "ask": ["Bash(rm:*)"]})
        c = md.check_unattended_merge_gate(self.repo, settings_path=self.settings)
        self.assertEqual(c.status, "OK")
        self.assertIn("allowed at global scope", c.detail)

    def test_warn_when_rule_in_deny(self):
        self._register_job()
        self._write_settings({"allow": [md._GH_PR_MERGE_RULE], "deny": [md._GH_PR_MERGE_RULE]})
        c = md.check_unattended_merge_gate(self.repo, settings_path=self.settings)
        self.assertEqual(c.status, "WARN")
        self.assertIn("`deny`", c.detail)

    def test_warn_when_rule_in_neither(self):
        self._register_job()
        self._write_settings({"allow": ["Bash(gh *)"], "ask": ["Bash(rm:*)"]})
        c = md.check_unattended_merge_gate(self.repo, settings_path=self.settings)
        self.assertEqual(c.status, "WARN")
        self.assertIn("neither", c.detail)

    def test_default_settings_path_is_user_scope(self):
        # Sanity: the resolver points at the user-scope file, not a repo file.
        self.assertEqual(md.global_claude_settings_path(), Path.home() / ".claude" / "settings.json")


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


class JobConfigTests(unittest.TestCase):
    """`registered` and `able to do anything` are different questions.

    The 2026-08-02 installer regression stripped every `plugins.autonomy.*`
    key out of `.agentm-config.json` while both delivery jobs kept reporting
    `registered (live)` — the jobs fired daily and delivered nothing, and
    nothing in this doctor said so. These pin the row that now does.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.prefix = self.root / "prefix"
        self.prefix.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _register(self, job_name: str) -> None:
        jobs = self.repo / ".harness" / "jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        (jobs / f"{job_name}.yaml").write_text("name: x\n", encoding="utf-8")

    def _write_config(self, data: dict) -> None:
        (self.prefix / ".agentm-config.json").write_text(json.dumps(data), encoding="utf-8")

    def _check(self, job_name: str) -> "md.Check":
        label = dict((n, l) for n, l in md._JOB_CONFIG_CHECKS)[job_name]
        return md.check_job_config(self.repo, job_name, label, install_prefix=self.prefix)

    def test_unverified_when_job_not_registered(self):
        c = self._check("observability-notify-daily")
        self.assertEqual(c.status, "UNVERIFIED")
        self.assertIn("not registered", c.detail)

    def test_notify_warns_when_registered_but_key_absent(self):
        self._register("observability-notify-daily")
        self._write_config({"schema_version": 2, "mode": "release"})
        c = self._check("observability-notify-daily")
        self.assertEqual(c.status, "WARN")
        self.assertIn("silently delivers nothing", c.detail)

    def test_notify_warns_when_config_file_missing_entirely(self):
        self._register("observability-notify-daily")
        self.assertEqual(self._check("observability-notify-daily").status, "WARN")

    def test_notify_ok_when_opted_in(self):
        self._register("observability-notify-daily")
        self._write_config({"plugins.autonomy.notify_enabled": True})
        self.assertEqual(self._check("observability-notify-daily").status, "OK")

    def test_notify_warns_when_explicitly_opted_out(self):
        self._register("observability-notify-daily")
        self._write_config({"plugins.autonomy.notify_enabled": False})
        self.assertEqual(self._check("observability-notify-daily").status, "WARN")

    def test_email_ok_when_both_required_keys_present(self):
        self._register("observability-email-daily")
        self._write_config({
            "plugins.autonomy.email_to": "ops@example.com",
            "plugins.autonomy.email_smtp_url": "smtp://relay@localhost:587",
        })
        self.assertEqual(self._check("observability-email-daily").status, "OK")

    def test_email_warns_when_only_one_required_key_present(self):
        """Half-configured is the same silence as unconfigured — the sender
        graceful-skips unless BOTH keys are set."""
        self._register("observability-email-daily")
        self._write_config({"plugins.autonomy.email_to": "ops@example.com"})
        self.assertEqual(self._check("observability-email-daily").status, "WARN")

    def test_email_warns_when_keys_were_wiped(self):
        """The literal regression: a config that kept its install fields but
        lost the autonomy family."""
        self._register("observability-email-daily")
        self._write_config({
            "schema_version": 2, "mode": "release", "source_clones": {},
            "installed_at": "2026-08-02T12:00:00Z", "harness_version": "v9.6.0",
            "vault_path": None, "installer_source": "/srv/install.sh",
        })
        self.assertEqual(self._check("observability-email-daily").status, "WARN")

    def test_corrupt_config_reads_as_unconfigured_not_crash(self):
        self._register("observability-notify-daily")
        (self.prefix / ".agentm-config.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self._check("observability-notify-daily").status, "WARN")

    def test_inventory_includes_a_config_row_per_autonomy_job(self):
        checks = md.run_inventory(self.repo, install_prefix=self.prefix)
        names = {c.name for c in checks}
        for job_name, _ in md._JOB_CONFIG_CHECKS:
            self.assertIn(f"{job_name}:config", names)


@unittest.skipIf(os.name == "nt", "the resolver the check delegates to is the POSIX/bash half")
class MemoryHookInterpreterTests(unittest.TestCase):
    """The check that would have caught a silent multi-year outage: the memory
    hooks ran an interpreter whose sqlite3 cannot load sqlite-vec, so the vector
    index was unreachable and every caller read that as an empty index.

    Hermetic. Each case builds a fixture repo with a fake
    `harness/hooks/lib/resolve-python.sh` and a fake interpreter, so what is
    asserted is the check's *verdict* given a known interpreter capability —
    not which Pythons happen to be on the machine running the suite.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.libdir = self.repo / "harness" / "hooks" / "lib"
        self.libdir.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_interpreter(self, *, version: str = "3.13.13") -> Path:
        """Stands in for a runnable Python. The check runs `<interp> -c
        <probe>` and parses stdout as JSON, so emitting that JSON directly is
        the same contract a real interpreter satisfies."""
        p = self.repo / "fake-python"
        payload = json.dumps({"version": version})
        p.write_text(f"#!/bin/sh\ncat <<'EOF'\n{payload}\nEOF\n", encoding="utf-8")
        p.chmod(0o755)
        return p

    def _write_resolver(self, prints: str) -> None:
        r = self.libdir / "resolve-python.sh"
        r.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' '{prints}'\n", encoding="utf-8")
        r.chmod(0o755)

    def test_ok_when_the_resolved_interpreter_runs(self):
        """The sqlite-vec half of this row went with the vector index; what
        remains is the half that made the original outage possible, which is
        that the hooks run whatever the resolver prints."""
        interp = self._fake_interpreter()
        self._write_resolver(str(interp))
        c = md.check_memory_hook_interpreter(self.repo)
        self.assertEqual(c.status, "OK")
        self.assertIn("working interpreter", c.detail)

    def test_warn_when_resolver_is_absent(self):
        c = md.check_memory_hook_interpreter(self.repo)
        self.assertEqual(c.status, "WARN")
        self.assertIn("bare `python3`", c.detail)

    def test_fail_when_resolver_prints_nothing(self):
        r = self.libdir / "resolve-python.sh"
        r.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        r.chmod(0o755)
        c = md.check_memory_hook_interpreter(self.repo)
        self.assertEqual(c.status, "FAIL")
        self.assertIn("printed nothing", c.detail)

    def test_fail_when_resolved_interpreter_is_unrunnable(self):
        self._write_resolver("/nonexistent/python3")
        c = md.check_memory_hook_interpreter(self.repo)
        self.assertEqual(c.status, "FAIL")
        self.assertIn("could not run it", c.detail)

    def test_names_the_override_as_the_cause_when_one_is_set(self):
        """An operator who pointed $AGENTM_PYTHON somewhere must be told that
        is what selected the interpreter, or the row sends them hunting the
        wrong thing."""
        interp = self._fake_interpreter()
        self._write_resolver(str(interp))
        prior = os.environ.get("AGENTM_PYTHON")
        os.environ["AGENTM_PYTHON"] = str(interp)
        try:
            c = md.check_memory_hook_interpreter(self.repo)
        finally:
            if prior is None:
                os.environ.pop("AGENTM_PYTHON", None)
            else:
                os.environ["AGENTM_PYTHON"] = prior
        self.assertEqual(c.status, "OK")
        self.assertIn("override", c.detail)

    def test_delegates_rather_than_reimplementing_the_probe(self):
        """The check must ask the real resolver, so it cannot report a healthy
        interpreter the hooks never actually pick. Proven by making the fixture
        resolver the *only* thing that could have produced the answer: it names
        an interpreter that exists nowhere in normal resolution."""
        interp = self._fake_interpreter(version="9.9.9")
        self._write_resolver(str(interp))
        c = md.check_memory_hook_interpreter(self.repo)
        self.assertEqual(c.status, "OK")
        self.assertIn("9.9.9", c.detail)
        self.assertIn(str(interp), c.detail)


class ProjectJsonPointerTests(unittest.TestCase):
    """The 2026-08-14 staleness shape: a pointer at a path that still EXISTS
    but sits outside the live vault, so every read returns a frozen tree and
    nothing ever raises."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.vault = root / "Vault"
        self.mem = self.vault / "Agent"
        self.mem.mkdir(parents=True)
        # A real, populated tree OUTSIDE the vault -- the retired vault root.
        self.stale = root / "OldDrive" / "Agent"
        self.stale.mkdir(parents=True)
        (self.stale / "board-items.json").write_text("[]", encoding="utf-8")
        self.cfg = root / "project.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _check(self, payload: dict):
        self.cfg.write_text(json.dumps(payload), encoding="utf-8")
        return md.check_project_json_pointers(
            self.cfg, "test", vault_root=self.vault, mem_root=self.mem,
        )

    def test_ok_when_every_pointer_is_inside_the_vault(self):
        items = self.mem / "desk" / "projects" / "p" / "_harness"
        items.mkdir(parents=True)
        (items / "board-items.json").write_text("[]", encoding="utf-8")
        (self.vault / "Ideas.md").write_text("# Ideas", encoding="utf-8")
        c = self._check({
            "items_source": str(items / "board-items.json"),
            "env": {"MEMORY_VAULT_PATH": str(self.mem),
                    "IDEAS_SURFACE_PATH": str(self.vault / "Ideas.md")},
        })
        self.assertEqual(c.status, "OK")
        self.assertIn("3 vault pointer(s)", c.detail)

    def test_fail_when_pointer_exists_but_is_outside_the_vault(self):
        c = self._check({"env": {"MEMORY_VAULT_PATH": str(self.stale)}})
        self.assertEqual(c.status, "FAIL")
        self.assertIn("outside", c.detail)
        self.assertIn("reads as valid", c.detail)

    def test_fail_when_pointer_names_a_missing_path(self):
        c = self._check({"items_source": str(self.mem / "gone" / "board-items.json")})
        self.assertEqual(c.status, "FAIL")
        self.assertIn("does not exist", c.detail)

    def test_memory_and_vault_surfaces_are_distinguished(self):
        """A file at the vault root is legitimate for IDEAS_SURFACE_PATH and
        wrong for items_source. Collapsing both onto one root -- in either
        direction -- flips exactly one of these two assertions."""
        stray = self.vault / "Ideas.md"
        stray.write_text("# Ideas", encoding="utf-8")
        ok = self._check({"env": {"IDEAS_SURFACE_PATH": str(stray)}})
        self.assertEqual(ok.status, "OK")
        bad = self._check({"items_source": str(stray)})
        self.assertEqual(bad.status, "FAIL")
        self.assertIn("outside", bad.detail)

    def test_warn_when_no_vault_resolves(self):
        """Drives the real resolution path with the resolver returning nothing,
        rather than passing None -- which the signature reads as "not supplied,
        go resolve" and would quietly measure this machine's own vault."""
        import harness_memory
        self.cfg.write_text(json.dumps({"items_source": "/nowhere"}), encoding="utf-8")
        with mock.patch.object(harness_memory, "vault_path", return_value=None), \
             mock.patch.object(harness_memory, "memory_root", return_value=None):
            c = md.check_project_json_pointers(self.cfg, "test")
        self.assertEqual(c.status, "WARN")
        self.assertIn("no vault resolves", c.detail)

    def test_warn_when_the_resolver_raises(self):
        """On a machine with no storage backend installed `vault_path()` raises
        rather than returning None. A doctor row degrades to WARN there; it must
        not propagate and take the whole inventory down with it."""
        import harness_memory
        self.cfg.write_text(json.dumps({"items_source": "/nowhere"}), encoding="utf-8")
        boom = harness_memory.StorageBackendNotInstalledError("no backend")
        with mock.patch.object(harness_memory, "vault_path", side_effect=boom):
            c = md.check_project_json_pointers(self.cfg, "test")
        self.assertEqual(c.status, "WARN")

    def test_warn_on_unparseable_json(self):
        self.cfg.write_text("{not json", encoding="utf-8")
        c = md.check_project_json_pointers(
            self.cfg, "test", vault_root=self.vault, mem_root=self.mem,
        )
        self.assertEqual(c.status, "WARN")

    def test_ok_when_config_carries_no_pointer_keys(self):
        c = self._check({"vault_project": "p", "github": {"number": 2}})
        self.assertEqual(c.status, "OK")
        self.assertIn("no vault pointers", c.detail)

    def test_discovery_falls_back_to_main_clone_from_a_worktree(self):
        """`.harness/` is gitignored, so a linked worktree has none. Discovery
        must still find the main clone's config rather than emitting no row."""
        main = Path(self._tmp.name) / "clone"
        main.mkdir()
        _init_repo(main)
        (main / "README.md").write_text("x", encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
        subprocess.run(["git", "add", "-A"], cwd=main, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=main, check=True, env=env)
        (main / ".harness").mkdir()
        (main / ".harness" / "project.json").write_text(
            json.dumps({"vault_project": "p"}), encoding="utf-8")
        wt = Path(self._tmp.name) / "wt"
        subprocess.run(["git", "worktree", "add", "-q", str(wt), "-b", "b"],
                       cwd=main, check=True, env=env)
        self.assertFalse((wt / ".harness" / "project.json").is_file())
        found = md.project_json_configs(wt, mem_root=self.mem)
        # Resolved both sides: on macOS a tempdir is /var/... but git reports
        # the /private/var/... realpath, and that difference is not the subject.
        self.assertEqual(
            [((main / ".harness" / "project.json").resolve(), "repo")],
            [(p.resolve(), label) for p, label in found],
        )


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


class RunnerCycleRowTests(unittest.TestCase):
    """Filing-v2 remainders task 1: the doctor reads the runner's last cycle."""

    def test_the_three_states(self):
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            row = md.check_runner_cycle(state_root=root)
            self.assertEqual(row.status, "UNVERIFIED")
            (root / "last-cycle.json").write_text(_json.dumps({
                "at": 1000.0, "loaded": 2, "refused": [{"file": "dreaming.yaml", "reason": "invalid YAML"}],
                "outcomes": [{"job": "dream", "ran": True}]}), encoding="utf-8")
            row = md.check_runner_cycle(state_root=root)
            self.assertEqual(row.status, "FAIL")
            self.assertIn("dreaming.yaml", row.detail)
            self.assertIn("every other job still ran", row.detail)
            (root / "last-cycle.json").write_text(_json.dumps({
                "at": 1000.0, "loaded": 2, "refused": [], "outcomes": [{"job": "dream", "ran": True}, {"job": "x", "ran": False}]}),
                encoding="utf-8")
            row = md.check_runner_cycle(state_root=root)
            self.assertEqual(row.status, "OK")
            self.assertIn("loaded 2 manifest(s), 1 ran", row.detail)

    def test_a_siblings_refusal_is_not_this_jobs_failure(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "templates" / "jobs").mkdir(parents=True)
            (repo / ".harness" / "jobs").mkdir(parents=True)
            good = "schedule: daily\nlookback: 6h\ncommand: true\ntier: T3\n"
            (repo / "templates" / "jobs" / "vault-lint.yaml").write_text(good, encoding="utf-8")
            (repo / ".harness" / "jobs" / "vault-lint.yaml").write_text(good, encoding="utf-8")
            (repo / ".harness" / "jobs" / "broken.yaml").write_text("schedule: daily\ncommand: [\n", encoding="utf-8")
            row = md.check_runner_job(repo, "vault-lint", state_root=repo / "state")
            self.assertNotEqual(row.status, "FAIL", row.detail)
            # This job's own manifest refused: its row says so, with the reason.
            (repo / ".harness" / "jobs" / "vault-lint.yaml").write_text(good.replace("T3", "T1"), encoding="utf-8")
            row = md.check_runner_job(repo, "vault-lint", state_root=repo / "state")
            self.assertEqual(row.status, "FAIL")
            self.assertIn("never a job target", row.detail)


if __name__ == "__main__":
    unittest.main()
