#!/usr/bin/env python3
"""Tests for console.py's section_crystallize_candidates (agentm-experience-
and-dreaming.md § Crystallization's phase-close trigger, call 6). Scoped to
just this function — console.py otherwise has no dedicated test file; this
is not a retrofit of its other sections' coverage.

Run: python3 scripts/test_console_crystallize_section.py
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

import console  # noqa: E402


class TestSectionCrystallizeCandidates(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_vault_resolved(self) -> None:
        self.assertIn("n/a", console.section_crystallize_candidates(None))

    def test_no_staging_dir_yet(self) -> None:
        out = console.section_crystallize_candidates(self.vault)
        self.assertIn("none staged", out)

    def test_empty_staging_dir(self) -> None:
        (self.vault / "_crystallize-staging").mkdir()
        out = console.section_crystallize_candidates(self.vault)
        self.assertIn("none staged", out)

    def test_counts_candidates(self) -> None:
        staging = self.vault / "_crystallize-staging"
        staging.mkdir()
        (staging / "post-work-a.json").write_text("{}", encoding="utf-8")
        (staging / "post-release-b.json").write_text("{}", encoding="utf-8")
        (staging / "not-a-candidate.txt").write_text("x", encoding="utf-8")
        out = console.section_crystallize_candidates(self.vault)
        self.assertIn("2 session(s) staged", out)

    def test_wired_into_gather_report(self) -> None:
        report = console.gather_report(repo_root=None, vault=self.vault)
        self.assertIn("crystallize_candidates", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
