#!/usr/bin/env python3
"""Unit coverage for harness/skills/memory/scripts/ideas_promote.py (R1.7).

Covers the promote/demote floor: a promoted idea appears in
personal/projects/<slug>/ and no longer in personal/_idea-incubator/<slug>/
(the "promoted idea appears in the main corpus, not _idea-incubator/"
invariant); collisions and missing entries raise cleanly.

Run directly:
    cd scripts && python3 -m unittest test_ideas_promote
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
import ideas_promote  # noqa: E402


class TestPromoteIdea(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        target = ideas_incubator.create_incubator_skeleton(
            "Promote Me", "A pitch worth promoting.", vault_path=self.vault, slug="promote-me",
        )
        self.slug = target.name
        # No Ideas.md — promote_idea must degrade the annotation gracefully.

    def tearDown(self):
        self._tmp.cleanup()

    def test_promote_moves_incubator_to_projects(self):
        result = ideas_promote.promote_idea(self.slug, vault_path=self.vault, mode="silent")
        self.assertTrue(result["promoted"])
        project_dir = self.vault / "personal" / "projects" / self.slug
        incubator_dir = self.vault / "personal" / "_idea-incubator" / self.slug
        self.assertTrue(project_dir.is_dir(), "promoted idea must land in personal/projects/")
        self.assertFalse(incubator_dir.exists(), "promoted idea must no longer live in _idea-incubator/")

    def test_promote_preserves_skeleton_files(self):
        ideas_promote.promote_idea(self.slug, vault_path=self.vault, mode="silent")
        project_dir = self.vault / "personal" / "projects" / self.slug
        self.assertTrue((project_dir / "_index.md").is_file())

    def test_promote_missing_incubator_entry_raises(self):
        with self.assertRaises(FileNotFoundError):
            ideas_promote.promote_idea("does-not-exist", vault_path=self.vault, mode="silent")

    def test_promote_existing_project_dir_raises(self):
        (self.vault / "personal" / "projects" / self.slug).mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            ideas_promote.promote_idea(self.slug, vault_path=self.vault, mode="silent")

    def test_promote_no_ideas_file_degrades_gracefully(self):
        result = ideas_promote.promote_idea(self.slug, vault_path=self.vault, mode="silent")
        self.assertEqual(result["ideas_annotation"], "no_ideas_file")


if __name__ == "__main__":
    unittest.main()
