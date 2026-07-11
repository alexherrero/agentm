#!/usr/bin/env python3
"""Unit tests for harness/skills/console/scripts/console.py (CONS-7,
Consolidation arc Wave 2 -- CONSOLIDATION-VERDICT.md Ruling 7).

Covers: repo/vault resolution, each section's graceful-degradation path
(injected fake subprocess runner -- never a real network/subprocess call),
the memory-activity helpers against a hermetic tmp-vault fixture (exercised
against the REAL heat_policy.py / watchlist_review.py sibling modules --
no fakes needed there, since those are pure-stdlib and already hermetic),
and the terminal/HTML renderers.

Run: python3 scripts/test_console.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

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
        out = c.section_health(Path("/fake"), runner=_fake_runner(returncode=0, stdout="Health Index: 88.00\n"))
        self.assertEqual(out, "Health Index: 88.00")

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


class RenderTests(unittest.TestCase):
    def _report(self):
        return {
            "health": "Health Index: 90.00",
            "plans": "No plans found",
            "board_drift": "check_project_sync: PASS",
            "spend": "Spend: $1.2300 total across 3 plan(s) ($0.4100/plan)",
            "memory": "Inbox: 2 unreviewed entries",
        }

    def test_render_terminal_contains_all_sections(self):
        text = c.render_terminal(self._report())
        for heading in ("Health", "Plans", "Board drift", "Spend", "Memory activity"):
            self.assertIn(heading, text)
        for value in self._report().values():
            self.assertIn(value, text)

    def test_render_html_contains_all_sections(self):
        html = c.render_html_report(self._report(), repo_root=None)
        self.assertIn("<title>AgentM Console</title>", html)
        for heading in ("Health", "Plans", "Board drift", "Spend", "Memory activity"):
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
