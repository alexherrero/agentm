#!/usr/bin/env python3
"""evolve.py's write path routed through the storage seam (V5-14,
agentm-memory-index.md / agentm-memory-system.md). No dedicated evolve.py
unit test existed before this change — a real gap the seam-routing work
surfaced; this file covers the core round trips (in-place, renamed) so a
regression in the write-path change would be caught.

Run directly:
    cd scripts && python3 -m unittest test_evolve_seam_routing
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import evolve  # noqa: E402
import save  # noqa: E402


class TestEvolveInPlace(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_in_place_evolve_archives_old_and_writes_new(self):
        original = save.save_entry(self.vault, "reference", "my-note", "original body", group="personal")
        new_path, archive_path = evolve.evolve_entry(
            self.vault, original, "evolved body", "testing the seam-routed write path",
        )
        self.assertEqual(new_path, original)
        self.assertTrue(archive_path.is_file())
        new_content = new_path.read_text(encoding="utf-8")
        self.assertIn("evolved body", new_content)
        self.assertIn("status: active", new_content)
        archive_content = archive_path.read_text(encoding="utf-8")
        self.assertIn("original body", archive_content)
        self.assertIn("status: superseded", archive_content)

    def test_renamed_evolve_writes_new_slug_and_removes_old(self):
        original = save.save_entry(self.vault, "reference", "old-slug", "original body", group="personal")
        new_path, archive_path = evolve.evolve_entry(
            self.vault, original, "evolved body", "renaming", new_slug="new-slug",
        )
        self.assertTrue(new_path.is_file())
        self.assertEqual(new_path.stem, "new-slug")
        self.assertFalse(original.is_file(), "old path should be unlinked after a renamed evolve")
        self.assertTrue(archive_path.is_file())

    def test_evolved_entry_bytes_are_lf_only(self):
        """The seam-routed write must preserve the same LF-only, byte-exact
        guarantee atomic_write always gave — no accidental newline
        translation introduced by the DeviceLocalBackend indirection."""
        original = save.save_entry(self.vault, "reference", "my-note", "line one\nline two", group="personal")
        new_path, _ = evolve.evolve_entry(self.vault, original, "line a\nline b", "reason")
        raw = new_path.read_bytes()
        self.assertNotIn(b"\r\n", raw)


if __name__ == "__main__":
    unittest.main()
