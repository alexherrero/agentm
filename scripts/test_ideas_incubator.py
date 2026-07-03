#!/usr/bin/env python3
"""Unit coverage for harness/skills/memory/scripts/ideas_incubator.py (R1.7).

Basic coverage: create_incubator_skeleton creates the 4-file skeleton at
<vault>/personal/_idea-incubator/<slug>/, slug collisions get a numeric
suffix, and empty title/summary raise ValueError.

Run directly:
    cd scripts && python3 -m unittest test_ideas_incubator
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

import ideas_incubator  # noqa: E402


class TestCreateIncubatorSkeleton(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_four_skeleton_files(self):
        target = ideas_incubator.create_incubator_skeleton(
            "A cool idea", "A one-sentence pitch for the idea.",
            vault_path=self.vault,
        )
        self.assertTrue(target.is_dir())
        self.assertEqual(target.parent, self.vault / "personal" / "_idea-incubator")
        for name in ("_index.md", "research-pending.md", "related-memoryvault.md", "related-obsidian.md"):
            self.assertTrue((target / name).is_file(), f"missing {name}")

    def test_index_md_carries_title_and_summary(self):
        target = ideas_incubator.create_incubator_skeleton(
            "A cool idea", "A one-sentence pitch for the idea.",
            vault_path=self.vault,
        )
        content = (target / "_index.md").read_text(encoding="utf-8")
        self.assertIn("A cool idea", content)
        self.assertIn("A one-sentence pitch for the idea.", content)

    def test_slug_collision_gets_numeric_suffix(self):
        first = ideas_incubator.create_incubator_skeleton(
            "Same Title", "First pitch.", vault_path=self.vault, slug="same-title",
        )
        second = ideas_incubator.create_incubator_skeleton(
            "Same Title", "Second pitch.", vault_path=self.vault, slug="same-title",
        )
        self.assertNotEqual(first, second)
        self.assertTrue(second.name.startswith("same-title"))

    def test_empty_title_raises(self):
        with self.assertRaises(ValueError):
            ideas_incubator.create_incubator_skeleton("  ", "a summary", vault_path=self.vault)

    def test_empty_summary_raises(self):
        with self.assertRaises(ValueError):
            ideas_incubator.create_incubator_skeleton("a title", "   ", vault_path=self.vault)

    def test_missing_vault_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            ideas_incubator.create_incubator_skeleton(
                "a title", "a summary", vault_path=self.vault / "nonexistent",
            )


if __name__ == "__main__":
    unittest.main()
