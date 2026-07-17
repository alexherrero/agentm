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

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class TestResolveIdeasPathDefault(unittest.TestCase):
    """Pins the same derivation as ideas_surface.py: Ideas.md defaults to
    the PARENT of the resolved vault path, not vault_path() itself (which
    resolves to the `Agent/` subfolder) — and never a cached
    `~/Obsidian/Ideas.md` literal."""

    def setUp(self):
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("IDEAS_SURFACE_PATH", "MEMORY_VAULT_PATH")
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_default_derives_from_passed_vault_parent(self):
        vault = Path("/fake/Obsidian/Agent")
        result = ideas_promote._resolve_ideas_path(None, vault=vault)
        self.assertEqual(result, Path("/fake/Obsidian/Ideas.md"))

    def test_default_falls_back_to_resolve_vault_root_when_vault_omitted(self):
        with mock.patch.object(ideas_promote, "_resolve_vault_root", return_value=Path("/fake/Obsidian/Agent")):
            result = ideas_promote._resolve_ideas_path(None)
        self.assertEqual(result, Path("/fake/Obsidian/Ideas.md"))

    def test_explicit_arg_wins_over_vault_derivation(self):
        result = ideas_promote._resolve_ideas_path(
            "/explicit/Ideas.md", vault=Path("/fake/Obsidian/Agent")
        )
        self.assertEqual(result, Path("/explicit/Ideas.md"))

    def test_unresolvable_vault_raises_instead_of_falling_back(self):
        with mock.patch.object(ideas_promote, "_resolve_vault_root", return_value=None):
            with self.assertRaises(FileNotFoundError):
                ideas_promote._resolve_ideas_path(None)


class TestPromoteAnnotatesDerivedIdeasPath(unittest.TestCase):
    """End-to-end: promote_idea must annotate the Ideas.md sitting at the
    real Obsidian vault root (sibling of `Agent/`), not inside the
    MemoryVault itself — the exact layout the stale hardcoded default got
    wrong."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "Obsidian" / "Agent"
        self.vault.mkdir(parents=True)
        target = ideas_incubator.create_incubator_skeleton(
            "Promote Me", "A pitch worth promoting.", vault_path=self.vault, slug="promote-me",
        )
        self.slug = target.name
        self.ideas_path = self.vault.parent / "Ideas.md"
        self.ideas_path.write_text(
            "## 2026-07-01: Promote Me\n"
            "A pitch worth promoting.\n"
            f"See deep research: [[MemoryVault/personal/_idea-incubator/{self.slug}/_index.md]]\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_promote_annotates_ideas_md_at_derived_default_path(self):
        result = ideas_promote.promote_idea(self.slug, vault_path=self.vault, mode="silent")
        self.assertEqual(result["ideas_annotation"], "written")
        content = self.ideas_path.read_text(encoding="utf-8")
        self.assertIn("→ promoted", content)


if __name__ == "__main__":
    unittest.main()
