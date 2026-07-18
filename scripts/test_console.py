#!/usr/bin/env python3
"""Unit tests for harness/skills/console/scripts/console.py (CONS-7,
Consolidation arc Wave 2 -- CONSOLIDATION-VERDICT.md Ruling 7; extended by
the Consolidation follow-ups batch's machinery-integrity lane, pieces 3-5).

Covers: repo/vault resolution, each section's graceful-degradation path
(injected fake subprocess runner -- never a real network/subprocess call),
the memory-activity helpers against a hermetic tmp-vault fixture (exercised
against the REAL heat_policy.py / watchlist_review.py sibling modules --
no fakes needed there, since those are pure-stdlib and already hermetic),
the machinery/vault-doctor/vault-lint/dreaming freshness sections (each
exercised live/dark/last-fired), the rich-view-link footer, and the
terminal/HTML renderers.

Run: python3 scripts/test_console.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_CONSOLE_SCRIPTS = _HERE.parent / "harness" / "skills" / "console" / "scripts"
if str(_CONSOLE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_SCRIPTS))

import console as c  # noqa: E402


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_runner(returncode=0, stdout="", stderr=""):
    def runner(cmd, **kwargs):
        return _FakeCompletedProcess(returncode, stdout, stderr)
    return runner


def _raising_runner(exc):
    def runner(cmd, **kwargs):
        raise exc
    return runner


class FindRepoRootTests(unittest.TestCase):
    def test_finds_the_real_agentm_checkout(self):
        # This test file itself lives inside a real agentm checkout.
        root = c.find_repo_root(_HERE)
        self.assertIsNotNone(root)
        self.assertTrue((root / "scripts" / "check-all.sh").is_file())

    def test_none_outside_a_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(c.find_repo_root(Path(td)))


class ResolveVaultPathTests(unittest.TestCase):
    """Outside an agentm dev checkout, resolve_vault_path() must still find
    the vault via the on-device .agentm-config.json -- the same fallback
    doctor_vault.py's own _resolve_vault_path() already relies on. Regression
    coverage for the gap where /console run from a non-agentm repo left
    vault-doctor resolving the vault fine while memory-activity/vault-lint/
    dreaming all reported "no vault resolved", because resolve_vault_path()
    only tried the config file when a repo_root was already found."""

    def test_falls_back_to_config_plugin_key_outside_a_checkout(self):
        with tempfile.TemporaryDirectory() as prefix_td, tempfile.TemporaryDirectory() as vault_td:
            prefix, vault = Path(prefix_td), Path(vault_td)
            (prefix / ".agentm-config.json").write_text(
                json.dumps({"plugins.obsidian-vault.vault_path": str(vault)}), encoding="utf-8"
            )
            with patch.dict(os.environ, {"AGENTM_INSTALL_PREFIX": str(prefix)}, clear=False), \
                    patch.object(c, "find_repo_root", return_value=None):
                os.environ.pop("MEMORY_VAULT_PATH", None)
                self.assertEqual(c.resolve_vault_path(), vault)

    def test_falls_back_to_legacy_flat_key(self):
        with tempfile.TemporaryDirectory() as prefix_td, tempfile.TemporaryDirectory() as vault_td:
            prefix, vault = Path(prefix_td), Path(vault_td)
            (prefix / ".agentm-config.json").write_text(
                json.dumps({"vault_path": str(vault)}), encoding="utf-8"
            )
            with patch.dict(os.environ, {"AGENTM_INSTALL_PREFIX": str(prefix)}, clear=False), \
                    patch.object(c, "find_repo_root", return_value=None):
                os.environ.pop("MEMORY_VAULT_PATH", None)
                self.assertEqual(c.resolve_vault_path(), vault)

    def test_env_var_takes_precedence_over_config(self):
        with tempfile.TemporaryDirectory() as prefix_td, tempfile.TemporaryDirectory() as config_vault_td, \
                tempfile.TemporaryDirectory() as env_vault_td:
            prefix, config_vault, env_vault = Path(prefix_td), Path(config_vault_td), Path(env_vault_td)
            (prefix / ".agentm-config.json").write_text(
                json.dumps({"vault_path": str(config_vault)}), encoding="utf-8"
            )
            with patch.dict(
                os.environ,
                {"AGENTM_INSTALL_PREFIX": str(prefix), "MEMORY_VAULT_PATH": str(env_vault)},
                clear=False,
            ), patch.object(c, "find_repo_root", return_value=None):
                self.assertEqual(c.resolve_vault_path(), env_vault)

    def test_none_when_no_config_and_no_env(self):
        with tempfile.TemporaryDirectory() as prefix_td:
            prefix = Path(prefix_td)  # no .agentm-config.json written
            with patch.dict(os.environ, {"AGENTM_INSTALL_PREFIX": str(prefix)}, clear=False), \
                    patch.object(c, "find_repo_root", return_value=None):
                os.environ.pop("MEMORY_VAULT_PATH", None)
                self.assertIsNone(c.resolve_vault_path())


class SectionDegradationTests(unittest.TestCase):
    """Every section must degrade to a clean 'n/a' string, never raise, when
    its underlying surface is absent -- exercised with repo_root=None or an
    OSError-raising fake runner (simulates the target script not existing)."""

    def test_health_none_repo_root(self):
        self.assertIn("n/a", c.section_health(None))

    def test_health_runner_missing_script(self):
        out = c.section_health(Path("/nonexistent"), runner=_raising_runner(FileNotFoundError("no such file")))
        self.assertIn("n/a", out)

    def test_health_nonzero_exit_reports_no_history(self):
        out = c.section_health(Path("/fake"), runner=_fake_runner(returncode=1))
        self.assertIn("no scorecard history", out)

    def test_health_success_passes_through_stdout(self):
        # An explicit resolve_fn pointing at a definitely-absent path --
        # the freshness line degrades to "unknown" rather than being
        # silently omitted (this is the honest-dark contract this section
        # grew in piece 3a). Without an injected resolve_fn this would fall
        # through to health_score.resolve_history_path() -- which, once the
        # real module is cached in sys.modules by another test file in the
        # same process, reads this machine's REAL health-history ledger
        # regardless of the fake /fake repo_root, since the vault it
        # resolves is genuinely global rather than scoped per repo_root.
        missing = Path("/fake/nonexistent-history.jsonl")
        out = c.section_health(
            Path("/fake"), runner=_fake_runner(returncode=0, stdout="Health Index: 88.00\n"),
            resolve_fn=lambda: missing,
        )
        self.assertIn("Health Index: 88.00", out)
        self.assertIn("Last nightly run: unknown", out)

    def test_health_appends_last_run_freshness_from_history_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            history_path = repo_root / "history.jsonl"
            now = time.time()
            row = {"ts": now - 3600, "health_index": 88.0}
            history_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            out = c.section_health(
                repo_root, runner=_fake_runner(returncode=0, stdout="Health Index: 88.00\n"), now=now,
                resolve_fn=lambda: history_path,
            )
            self.assertIn("Health Index: 88.00", out)
            self.assertIn("Last nightly run:", out)
            self.assertIn("ago)", out)
            self.assertNotIn("unknown", out)

    def test_read_health_history_ts_picks_last_row(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            history_path = repo_root / "history.jsonl"
            history_path.write_text(
                json.dumps({"ts": 1000}) + "\n" + json.dumps({"ts": 2000}) + "\n", encoding="utf-8"
            )
            self.assertEqual(
                c._read_health_history_ts(repo_root, resolve_fn=lambda: history_path), 2000.0
            )

    def test_read_health_history_ts_none_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "history.jsonl"
            self.assertIsNone(c._read_health_history_ts(Path(td), resolve_fn=lambda: missing))

    def test_health_history_path_honors_injected_resolve_fn(self):
        # The real production path (health_score.resolve_history_path() via
        # a real repo_root import) is covered by health_score's own tests;
        # this only proves the injection seam itself is honored, avoiding
        # any dependence on sys.modules/sys.path caching state shared with
        # other test files in the same process.
        with tempfile.TemporaryDirectory() as td:
            sentinel = Path(td) / "sentinel.jsonl"
            got = c._health_history_path(Path("/unused"), resolve_fn=lambda: sentinel)
            self.assertEqual(got, sentinel)

    def test_plans_none_repo_root(self):
        self.assertIn("n/a", c.section_plans(None))

    def test_plans_empty_output_reports_no_plans(self):
        out = c.section_plans(Path("/fake"), runner=_fake_runner(returncode=0, stdout=""))
        self.assertIn("no active plans", out)

    def test_board_drift_no_crickets_sibling(self):
        # Force resolution failure by pointing CRICKETS_SCRIPTS_DIR nowhere.
        import os
        old = os.environ.get("CRICKETS_SCRIPTS_DIR")
        os.environ["CRICKETS_SCRIPTS_DIR"] = "/definitely/not/a/real/path"
        try:
            out = c.section_board_drift(Path("/fake"))
            self.assertIn("n/a", out)
        finally:
            if old is None:
                os.environ.pop("CRICKETS_SCRIPTS_DIR", None)
            else:
                os.environ["CRICKETS_SCRIPTS_DIR"] = old

    def test_spend_none_repo_root(self):
        self.assertIn("n/a", c.section_spend(None))

    def test_memory_none_vault(self):
        self.assertIn("n/a", c.section_memory(None))

    def test_machinery_none_repo_root(self):
        self.assertIn("n/a", c.section_machinery(None))

    def test_machinery_runner_missing_script(self):
        out = c.section_machinery(Path("/nonexistent"), runner=_raising_runner(FileNotFoundError("no such file")))
        self.assertIn("n/a", out)

    def test_machinery_unparseable_output(self):
        out = c.section_machinery(Path("/fake"), runner=_fake_runner(returncode=0, stdout="not json"))
        self.assertIn("n/a", out)

    def test_vault_doctor_no_crickets_sibling(self):
        import os
        old = os.environ.get("CRICKETS_SCRIPTS_DIR")
        os.environ["CRICKETS_SCRIPTS_DIR"] = "/definitely/not/a/real/path"
        try:
            out = c.section_vault_doctor(None)
            self.assertIn("n/a", out)
        finally:
            if old is None:
                os.environ.pop("CRICKETS_SCRIPTS_DIR", None)
            else:
                os.environ["CRICKETS_SCRIPTS_DIR"] = old


class MemoryActivityTests(unittest.TestCase):
    """Real vault-layout fixtures -- these exercise the actual real paths
    (`personal/_inbox`, root `_idea-incubator`) console.py reads directly,
    distinct from orchestration_briefing.py's own (mismatched) assumptions."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_count_inbox_absent_is_zero(self):
        self.assertEqual(c.count_inbox(self.vault), 0)

    def test_count_inbox_counts_personal_inbox_excludes_index(self):
        d = self.vault / "personal" / "_inbox"
        d.mkdir(parents=True)
        (d / "a.md").write_text("x", encoding="utf-8")
        (d / "b.md").write_text("x", encoding="utf-8")
        (d / "_index.md").write_text("x", encoding="utf-8")
        self.assertEqual(c.count_inbox(self.vault), 2)

    def test_count_inbox_ignores_root_level_inbox(self):
        # Confirms this reads personal/_inbox, not <vault>/_inbox.
        d = self.vault / "_inbox"
        d.mkdir(parents=True)
        (d / "a.md").write_text("x", encoding="utf-8")
        self.assertEqual(c.count_inbox(self.vault), 0)

    def test_count_incubator_root_level(self):
        (self.vault / "_idea-incubator" / "idea-one").mkdir(parents=True)
        (self.vault / "_idea-incubator" / "idea-two").mkdir(parents=True)
        self.assertEqual(c.count_incubator(self.vault), 2)

    def test_count_incubator_ignores_personal_nested(self):
        # Confirms this reads root _idea-incubator, not personal/_idea-incubator.
        (self.vault / "personal" / "_idea-incubator" / "idea-one").mkdir(parents=True)
        self.assertEqual(c.count_incubator(self.vault), 0)

    def test_newest_curated_entries_excludes_staging_dirs(self):
        personal = self.vault / "personal"
        (personal / "insight").mkdir(parents=True)
        (personal / "insight" / "keep.md").write_text("x", encoding="utf-8")
        (personal / "_inbox").mkdir(parents=True)
        (personal / "_inbox" / "skip.md").write_text("x", encoding="utf-8")
        names = c.newest_curated_entries(self.vault)
        self.assertIn("insight/keep.md", names)
        self.assertTrue(all("_inbox" not in n for n in names))

    def test_heat_policy_report_never_raises_on_empty_vault(self):
        out = c.heat_policy_report(self.vault)
        self.assertTrue(out.startswith("Heat-policy"))

    def test_watchlist_summary_empty_vault(self):
        out = c.watchlist_summary(self.vault)
        self.assertTrue(out.startswith("Watchlist"))
        self.assertIn("0 entries", out)


class MachinerySectionTests(unittest.TestCase):
    """`section_machinery` composes over `machinery_doctor.py --format json`
    -- exercised with a fake runner returning canned JSON, never a real
    subprocess call."""

    def _payload(self, **overrides):
        base = {
            "checks": [
                {"name": "stop-hook:x.sh", "status": "OK", "detail": "wired", "last_fired": None, "owner": None},
            ],
            "summary": {"OK": 1, "WARN": 0, "FAIL": 0, "UNVERIFIED": 0},
        }
        base.update(overrides)
        return base

    def test_all_ok_summary_line_no_detail_rows(self):
        out = c.section_machinery(Path("/fake"), runner=_fake_runner(returncode=0, stdout=json.dumps(self._payload())))
        self.assertIn("1 OK, 0 WARN, 0 FAIL, 0 UNVERIFIED", out)
        self.assertNotIn("[OK]", out)  # concerning-row detail only prints for FAIL/UNVERIFIED

    def test_fail_row_detail_included(self):
        payload = self._payload(
            checks=[{"name": "stop-hook:x.sh", "status": "FAIL", "detail": "script missing", "last_fired": None, "owner": None}],
            summary={"OK": 0, "WARN": 0, "FAIL": 1, "UNVERIFIED": 0},
        )
        out = c.section_machinery(Path("/fake"), runner=_fake_runner(returncode=0, stdout=json.dumps(payload)))
        self.assertIn("1 FAIL", out)
        self.assertIn("[FAIL] stop-hook:x.sh: script missing", out)

    def test_unverified_row_detail_included(self):
        payload = self._payload(
            checks=[{"name": "cross-review-marker", "status": "UNVERIFIED", "detail": "no sibling", "last_fired": None, "owner": "crickets"}],
            summary={"OK": 0, "WARN": 0, "FAIL": 0, "UNVERIFIED": 1},
        )
        out = c.section_machinery(Path("/fake"), runner=_fake_runner(returncode=0, stdout=json.dumps(payload)))
        self.assertIn("[UNVERIFIED] cross-review-marker: no sibling", out)


class RunnerJobsSectionTests(unittest.TestCase):
    """`section_runner_jobs` (2026-07-17 honesty-surface finding): reads real
    job manifests + runner state markers directly (no subprocess), so these
    tests build a fake repo_root's `.harness/jobs/*.yaml` and pass an
    isolated `state_root` -- never the real machine's `~/.cache/agentm/
    runner/`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name) / "repo"
        self.jobs_dir = self.repo_root / ".harness" / "jobs"
        self.jobs_dir.mkdir(parents=True)
        self.state_root = Path(self._tmp.name) / "state"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_job(self, name, *, dry_run=False, schedule="daily", lookback="24h"):
        (self.jobs_dir / f"{name}.yaml").write_text(
            f"schedule: {schedule}\nlookback: {lookback}\ncommand: \"true\"\n"
            f"tier: T3\ndry_run: {'true' if dry_run else 'false'}\n",
            encoding="utf-8",
        )

    def test_none_repo_root(self):
        self.assertIn("n/a", c.section_runner_jobs(None))

    def test_no_jobs_registered(self):
        out = c.section_runner_jobs(self.repo_root, state_root=self.state_root)
        self.assertIn("none registered", out)

    def test_dry_run_jobs_excluded(self):
        self._write_job("still-dry", dry_run=True)
        out = c.section_runner_jobs(self.repo_root, state_root=self.state_root)
        self.assertIn("none registered", out)
        self.assertNotIn("still-dry", out)

    def test_never_run_job(self):
        self._write_job("fresh")
        out = c.section_runner_jobs(self.repo_root, state_root=self.state_root)
        self.assertIn("fresh: never run for real", out)

    def test_real_run_shows_age_without_overdue_flag(self):
        from runner import state as rstate

        self._write_job("healthy")
        now = 2_000_000.0
        rstate.mark_done("healthy", now=now - 3600, state_root=self.state_root)
        out = c.section_runner_jobs(self.repo_root, state_root=self.state_root, now=now)
        self.assertIn("healthy: last real run", out)
        self.assertIn("1.0h ago", out)
        self.assertNotIn("OVERDUE", out)

    def test_degrades_on_a_directory_matching_the_yaml_glob(self):
        # load_manifests() globs *.yaml/*.yml and reads each match as a
        # file with no is_file() guard -- a directory entry (editor
        # artifact, bad mkdir) raises IsADirectoryError, an OSError that
        # ManifestError alone doesn't cover. This section must degrade like
        # every other one in console.py, never raise past gather_report().
        (self.jobs_dir / "oops.yaml").mkdir()
        out = c.section_runner_jobs(self.repo_root, state_root=self.state_root)
        self.assertIn("n/a", out)

    def test_missed_reanchor_flags_overdue_and_reports_the_prior_real_run(self):
        from runner import state as rstate

        self._write_job("stalled")
        now = 2_000_000.0
        rstate.mark_done("stalled", now=now - (10 * 86400), state_root=self.state_root)
        rstate.mark_missed("stalled", now=now - 60, state_root=self.state_root)  # re-anchored a minute ago
        out = c.section_runner_jobs(self.repo_root, state_root=self.state_root, now=now)
        self.assertIn("stalled: last real run", out)
        self.assertIn("10.0d ago", out)  # the REAL run's age, not the re-anchor's
        self.assertIn("OVERDUE", out)


class VaultDoctorSectionTests(unittest.TestCase):
    """`section_vault_doctor` resolves a crickets sibling before it ever
    reaches the injected runner (mirrors `section_board_drift`'s own
    resolution-then-run shape) -- exercised hermetically by pointing
    `$CRICKETS_SCRIPTS_DIR` at a synthetic fixture tree (the CI runner has
    no real crickets checkout, so this must never depend on one)."""

    def _fixture_crickets_root(self, tmp: Path) -> Path:
        root = tmp / "crickets"
        (root / "src" / "github-projects").mkdir(parents=True)
        script_dir = root / "src" / "obsidian-vault" / "scripts"
        script_dir.mkdir(parents=True)
        (script_dir / "doctor_vault.py").write_text("# fixture placeholder\n", encoding="utf-8")
        return root

    def test_live_output_reformatted_with_timestamp(self):
        import os

        canned = (
            "[doctor_vault] obsidian-vault backing-plugin health:\n"
            "  [OK] vault-path  looks fine\n"
            "  [OK] backend     looks fine\n"
        )
        old = os.environ.get("CRICKETS_SCRIPTS_DIR")
        with tempfile.TemporaryDirectory() as td:
            os.environ["CRICKETS_SCRIPTS_DIR"] = str(self._fixture_crickets_root(Path(td)))
            try:
                out = c.section_vault_doctor(
                    Path("/fake-vault"), runner=_fake_runner(returncode=0, stdout=canned), now=1_700_000_000.0
                )
            finally:
                if old is None:
                    os.environ.pop("CRICKETS_SCRIPTS_DIR", None)
                else:
                    os.environ["CRICKETS_SCRIPTS_DIR"] = old
        self.assertIn("Vault doctor (live check,", out)
        self.assertIn("vault-path", out)
        self.assertNotIn("[doctor_vault]", out)  # header line stripped, rows kept

    def test_no_crickets_sibling_degrades_to_n_a(self):
        import os

        old = os.environ.get("CRICKETS_SCRIPTS_DIR")
        os.environ["CRICKETS_SCRIPTS_DIR"] = "/definitely/not/a/real/path"
        try:
            out = c.section_vault_doctor(Path("/fake-vault"))
            self.assertIn("n/a", out)
        finally:
            if old is None:
                os.environ.pop("CRICKETS_SCRIPTS_DIR", None)
            else:
                os.environ["CRICKETS_SCRIPTS_DIR"] = old


class VaultLintSectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_none_vault(self):
        self.assertIn("n/a", c.section_vault_lint(None))

    def test_dark_when_no_report(self):
        out = c.section_vault_lint(self.vault)
        self.assertIn("dark", out)
        self.assertIn("vault-lint.yaml", out)

    def test_picks_latest_report_and_extracts_summary(self):
        meta = self.vault / "_meta"
        meta.mkdir()
        (meta / "vault-lint-2026-07-01.md").write_text(
            "# MemoryVault lint audit -- 2026-07-01\n\n**Summary:** 1 error across 5 entries.\n",
            encoding="utf-8",
        )
        (meta / "vault-lint-2026-07-10.md").write_text(
            "# MemoryVault lint audit -- 2026-07-10\n\n**Summary:** 0 error · 2 warn · 0 info across 9 entries (0 skipped).\n",
            encoding="utf-8",
        )
        out = c.section_vault_lint(self.vault, now=time.time())
        self.assertIn("vault-lint-2026-07-10.md", out)
        self.assertIn("0 error", out)
        self.assertIn("2 warn", out)
        self.assertNotIn("vault-lint-2026-07-01.md", out)


class BriefSectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_none_vault(self):
        self.assertIn("n/a", c.section_brief(None))

    def test_dark_when_no_briefs(self):
        out = c.section_brief(self.vault)
        self.assertIn("dark", out)
        self.assertIn("_briefs", out)

    def test_picks_latest_by_filename_and_extracts_title(self):
        briefs = self.vault / "_briefs"
        briefs.mkdir()
        (briefs / "20260711-digest-daily.md").write_text(
            "---\nkind: brief\n---\n\n# Observability digest — daily (spend and run summary)\n",
            encoding="utf-8",
        )
        (briefs / "20260712-digest-daily.md").write_text(
            "---\nkind: brief\n---\n\n# Observability digest — daily (spend and run summary)\n",
            encoding="utf-8",
        )
        out = c.section_brief(self.vault, now=time.time())
        self.assertIn("20260712-digest-daily.md", out)
        self.assertNotIn("20260711-digest-daily.md", out)
        self.assertIn("Observability digest", out)


class DreamExpireSectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        (self.vault / "_meta").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _write_pointer(self, **fields):
        (self.vault / "_meta" / "dream-auto-expired-latest.json").write_text(json.dumps(fields), encoding="utf-8")

    def test_none_vault(self):
        self.assertIn("n/a", c.section_dream_expire(None))

    def test_dark_when_pointer_absent(self):
        out = c.section_dream_expire(self.vault)
        self.assertIn("dark", out)
        self.assertIn("dream.yaml", out)

    def test_present_with_items_includes_count_and_revert(self):
        now = time.time()
        self._write_pointer(
            run_id="20260711-abcd1234", applied_at=now - 3600, stages=["compression"], batch_cap=25, count=2,
            items=[{"index": 1, "entry_id": "e1"}],
            revert={"how": "RevertLog(vault_path).revert('20260711-abcd1234', entry_id)", "run_id": "20260711-abcd1234"},
        )
        out = c.section_dream_expire(self.vault, now=now)
        self.assertIn("20260711-abcd1234", out)
        self.assertIn("2 item(s)", out)
        self.assertIn("revert:", out)
        self.assertIn("RevertLog", out)

    def test_empty_batch_reports_zero_not_dark(self):
        now = time.time()
        self._write_pointer(
            run_id="run-empty", applied_at=now, stages=["compression"], batch_cap=25, count=0, items=[],
            revert={"how": "n/a", "run_id": "run-empty"},
        )
        out = c.section_dream_expire(self.vault, now=now)
        self.assertIn("0 item(s)", out)
        self.assertNotIn("dark", out)

    def test_malformed_pointer_degrades_gracefully(self):
        (self.vault / "_meta" / "dream-auto-expired-latest.json").write_text("not json", encoding="utf-8")
        out = c.section_dream_expire(self.vault)
        self.assertIn("n/a", out)


class RichViewLineTests(unittest.TestCase):
    def test_not_yet_rendered(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "console.html"
            out = c.rich_view_line(path)
            self.assertIn("not yet rendered", out)
            self.assertIn(str(path), out)

    def test_rendered_reports_age(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "console.html"
            path.write_text("<html></html>", encoding="utf-8")
            out = c.rich_view_line(path)
            self.assertIn("last rendered", out)
            self.assertIn(str(path), out)


class RenderTests(unittest.TestCase):
    def _report(self):
        return {
            "health": "Health Index: 90.00",
            "plans": "No plans found",
            "board_drift": "check_project_sync: PASS",
            "spend": "Spend: $1.2300 total across 3 plan(s) ($0.4100/plan)",
            "memory": "Inbox: 2 unreviewed entries",
            "machinery": "Machinery: 3 OK, 0 WARN, 0 FAIL, 0 UNVERIFIED",
            "vault_doctor": "Vault doctor (live check, now):\n  [OK] vault-path fine",
            "vault_lint": "Vault lint: 0 error · 0 warn · 0 info (report: vault-lint-2026-07-10.md, rendered now)",
            "dream_expire": "Dreaming auto-expire: last cycle (run x, now) auto-expired 0 item(s) -- nothing to revert",
        }

    def test_render_terminal_contains_all_sections(self):
        text = c.render_terminal(self._report())
        for heading in (
            "Health", "Plans", "Board drift", "Spend", "Memory activity",
            "Machinery", "Vault doctor", "Vault lint", "Dreaming",
        ):
            self.assertIn(heading, text)
        for value in self._report().values():
            self.assertIn(value, text)

    def test_render_terminal_always_ends_with_rich_view_line(self):
        with tempfile.TemporaryDirectory() as td:
            html_path = Path(td) / "console.html"
            text = c.render_terminal(self._report(), html_path=html_path)
            self.assertIn("Rich view:", text)
            self.assertIn(str(html_path), text)

    def test_render_html_contains_all_sections(self):
        html = c.render_html_report(self._report(), repo_root=None)
        self.assertIn("<title>AgentM Console</title>", html)
        for heading in (
            "Health", "Plans", "Board drift", "Spend", "Memory activity",
            "Machinery", "Vault doctor", "Vault lint", "Dreaming",
        ):
            self.assertIn(f"<h2>{heading}</h2>", html)
        self.assertIn("Health Index: 90.00", html)

    def test_extract_body_strips_wrapper_and_duplicate_title(self):
        full = (
            "<!doctype html><html><head><title>t</title></head><body>"
            "<h1>AgentM Observability Console</h1><p>hi</p></body></html>"
        )
        body = c._extract_body(full)
        self.assertNotIn("<html>", body)
        self.assertNotIn("AgentM Observability Console", body)
        self.assertIn("<p>hi</p>", body)


class CliTests(unittest.TestCase):
    def test_main_terminal_mode_runs_clean(self):
        # No repo_root override, no vault likely resolvable in a bare test
        # env -- must still exit 0 with a printed report (graceful "n/a"
        # everywhere rather than a crash).
        rc = c.main([])
        self.assertEqual(rc, 0)

    def test_main_html_mode_writes_a_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "console.html"
            rc = c.main(["--html", "--output", str(out)])
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            content = out.read_text(encoding="utf-8")
            self.assertIn("<title>AgentM Console</title>", content)


if __name__ == "__main__":
    unittest.main()
