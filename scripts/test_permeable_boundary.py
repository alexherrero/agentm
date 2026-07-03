#!/usr/bin/env python3
"""Unit coverage for harness/skills/memory/scripts/permeable_boundary.py (R1.7).

Basic round-trip: confirm_write_outside_memoryvault's three modes
(silent=always-approve, auto=always-deny, interactive) and the non-TTY-stdin
safety fallback (interactive + non-TTY stdin → deny, never a hang).

Run directly:
    cd scripts && python3 -m unittest test_permeable_boundary
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import permeable_boundary  # noqa: E402


class TestConfirmWriteOutsideMemoryVault(unittest.TestCase):
    def test_silent_mode_always_approves(self):
        approved = permeable_boundary.confirm_write_outside_memoryvault(
            "/tmp/somewhere/Ideas.md", "preview content", "test rationale", mode="silent",
        )
        self.assertTrue(approved)

    def test_auto_mode_always_denies(self):
        approved = permeable_boundary.confirm_write_outside_memoryvault(
            "/tmp/somewhere/Ideas.md", "preview content", "test rationale", mode="auto",
        )
        self.assertFalse(approved)

    def test_interactive_non_tty_stdin_denies_never_hangs(self):
        # A plain io.StringIO is never a TTY — this is the hook-safety path:
        # interactive mode requested but no human is present to answer.
        approved = permeable_boundary.confirm_write_outside_memoryvault(
            "/tmp/somewhere/Ideas.md", "preview content", "test rationale",
            mode="interactive", stdin=io.StringIO(""), stdout=io.StringIO(),
        )
        self.assertFalse(approved)

    def test_never_raises_on_empty_content_preview(self):
        approved = permeable_boundary.confirm_write_outside_memoryvault(
            "/tmp/somewhere/Ideas.md", "", "test rationale", mode="silent",
        )
        self.assertTrue(approved)


if __name__ == "__main__":
    unittest.main()
