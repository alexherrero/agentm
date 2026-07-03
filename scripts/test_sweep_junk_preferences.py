#!/usr/bin/env python3
"""Unit tests for sweep_junk_preferences.py (R0.3 / agentmExperience#2 junk sweep).

Run: python3 scripts/test_sweep_junk_preferences.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_SCRIPTS = _REPO_ROOT / "harness" / "skills" / "memory" / "scripts"
if str(_MEMORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SCRIPTS))

import sweep_junk_preferences as sjp  # noqa: E402

_JUNK_BODY = (
    "---\nkind: preferences\nstatus: active\ncreated: 2026-06-30\nupdated: 2026-06-30\n"
    "tags: []\ngroup: personal\nslug: never-touched-in-the-e505bd3\nalways_load: false\n---\n\n"
    "User stated: ...was never touched in the e505bd3..HEAD range...\n"
)

_REAL_BODY = (
    "---\nkind: preferences\nstatus: active\ncreated: 2026-06-30\nupdated: 2026-06-30\n"
    "tags: []\ngroup: personal\nslug: never-commit-directly-to-main\nalways_load: false\n---\n\n"
    "Manually written: never commit directly to main without a PR.\n"
)


class TestSweepJunkPreferences(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.prefs = self.vault / "personal" / "preferences"
        self.prefs.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, name: str, body: str) -> Path:
        p = self.prefs / name
        p.write_text(body, encoding="utf-8")
        return p

    def test_identifies_junk_by_full_signature(self) -> None:
        junk = self._write("never-touched-in-the-e505bd3.md", _JUNK_BODY)
        real = self._write("never-commit-directly-to-main.md", _REAL_BODY)
        found = sjp.find_junk_preferences(self.vault)
        self.assertEqual(found, [junk])
        self.assertNotIn(real, found)

    def test_wrong_directory_not_flagged(self) -> None:
        other_dir = self.vault / "personal" / "_inbox"
        other_dir.mkdir(parents=True)
        stray = other_dir / "never-something.md"
        stray.write_text(_JUNK_BODY, encoding="utf-8")
        self.assertEqual(sjp.find_junk_preferences(self.vault), [])

    def test_dry_run_does_not_move_files(self) -> None:
        junk = self._write("never-touched-in-the-e505bd3.md", _JUNK_BODY)
        out = io.StringIO()
        n = sjp.sweep(self.vault, apply=False, stdout=out)
        self.assertEqual(n, 1)
        self.assertTrue(junk.exists())
        self.assertIn("would archive", out.getvalue())

    def test_apply_moves_file_to_group_archive(self) -> None:
        junk = self._write("never-touched-in-the-e505bd3.md", _JUNK_BODY)
        n = sjp.sweep(self.vault, apply=True, stdout=io.StringIO())
        self.assertEqual(n, 1)
        self.assertFalse(junk.exists())
        dest = self.vault / "personal" / "_archive" / "preferences" / "never-touched-in-the-e505bd3.md"
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_text(encoding="utf-8"), _JUNK_BODY)

    def test_apply_preserves_group_personal_private(self) -> None:
        pp_prefs = self.vault / "personal-private" / "preferences"
        pp_prefs.mkdir(parents=True)
        junk = pp_prefs / "always-something.md"
        junk.write_text(_JUNK_BODY, encoding="utf-8")
        sjp.sweep(self.vault, apply=True, stdout=io.StringIO())
        dest = self.vault / "personal-private" / "_archive" / "preferences" / "always-something.md"
        self.assertTrue(dest.exists())

    def test_rerun_after_apply_finds_nothing_left(self) -> None:
        """Idempotency: a second sweep after --apply must not re-flag archived
        files (regression guard — archived junk's parent dir is also named
        `preferences`, so a naive check re-matches and nests _archive/_archive/)."""
        self._write("never-touched-in-the-e505bd3.md", _JUNK_BODY)
        sjp.sweep(self.vault, apply=True, stdout=io.StringIO())
        second_pass = sjp.find_junk_preferences(self.vault)
        self.assertEqual(second_pass, [])

    def test_collision_gets_numeric_suffix(self) -> None:
        junk = self._write("never-touched-in-the-e505bd3.md", _JUNK_BODY)
        archive_dir = self.vault / "personal" / "_archive" / "preferences"
        archive_dir.mkdir(parents=True)
        (archive_dir / "never-touched-in-the-e505bd3.md").write_text("pre-existing", encoding="utf-8")
        sjp.sweep(self.vault, apply=True, stdout=io.StringIO())
        self.assertFalse(junk.exists())
        self.assertTrue((archive_dir / "never-touched-in-the-e505bd3-2.md").exists())
        self.assertEqual(
            (archive_dir / "never-touched-in-the-e505bd3.md").read_text(encoding="utf-8"),
            "pre-existing",
        )


if __name__ == "__main__":
    unittest.main()
