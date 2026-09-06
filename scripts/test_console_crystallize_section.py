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
        console._engine_state_dir().joinpath("crystallize-staging").mkdir(parents=True)
        out = console.section_crystallize_candidates(self.vault)
        self.assertIn("none staged", out)

    def test_counts_candidates(self) -> None:
        staging = console._engine_state_dir() / "crystallize-staging"
        staging.mkdir()
        (staging / "post-work-a.json").write_text("{}", encoding="utf-8")
        (staging / "post-release-b.json").write_text("{}", encoding="utf-8")
        (staging / "not-a-candidate.txt").write_text("x", encoding="utf-8")
        out = console.section_crystallize_candidates(self.vault)
        self.assertIn("2 session(s) staged", out)

    def test_wired_into_gather_report(self) -> None:
        report = console.gather_report(repo_root=None, vault=self.vault)
        self.assertIn("crystallize_candidates", report)


# Every test in this module gets its own engine state dir. Without it a hand
# run shares one directory across the file and reads what the last test left
# (PLAN-source-and-hygiene, task 3); the battery's runner hid that from CI.
# The path insert is here rather than assumed: a hand run reaches this module
# as `scripts.<name>`, which puts the repo root on the path and not `scripts/`.
import os.path as _osp  # noqa: E402
import sys as _sys  # noqa: E402

if _osp.dirname(_osp.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _osp.dirname(_osp.abspath(__file__)))
from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main(verbosity=2)
