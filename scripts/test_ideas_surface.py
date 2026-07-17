#!/usr/bin/env python3
"""Unit coverage for harness/skills/memory/scripts/ideas_surface.py (R1.7).

Basic coverage: append_idea_to_surface writes a section to the ideas file
(mode='silent' — always approves the permeable-boundary check), returns None
when the boundary denies (mode='auto'), and raises on empty title/summary.

Run directly:
    cd scripts && python3 -m unittest test_ideas_surface
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

import ideas_surface  # noqa: E402


class TestAppendIdeaToSurface(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ideas_path = Path(self._tmp.name) / "Ideas.md"

    def tearDown(self):
        self._tmp.cleanup()

    def test_silent_mode_writes_a_section(self):
        result = ideas_surface.append_idea_to_surface(
            "A Cool Idea", "A one-sentence pitch.",
            ideas_path=self.ideas_path, mode="silent",
        )
        self.assertEqual(result, self.ideas_path)
        self.assertTrue(self.ideas_path.is_file())
        content = self.ideas_path.read_text(encoding="utf-8")
        self.assertIn("A Cool Idea", content)
        self.assertIn("A one-sentence pitch.", content)

    def test_auto_mode_denies_and_returns_none(self):
        result = ideas_surface.append_idea_to_surface(
            "A Cool Idea", "A one-sentence pitch.",
            ideas_path=self.ideas_path, mode="auto",
        )
        self.assertIsNone(result)
        self.assertFalse(self.ideas_path.exists())

    def test_incubator_slug_used_verbatim_in_wikilink(self):
        ideas_surface.append_idea_to_surface(
            "A Cool Idea", "A one-sentence pitch.",
            incubator_slug="explicit-slug", ideas_path=self.ideas_path, mode="silent",
        )
        content = self.ideas_path.read_text(encoding="utf-8")
        self.assertIn("explicit-slug", content)

    def test_empty_title_raises(self):
        with self.assertRaises(ValueError):
            ideas_surface.append_idea_to_surface("  ", "a summary", ideas_path=self.ideas_path, mode="silent")


class TestResolveIdeasPathDefault(unittest.TestCase):
    """Pins the default Ideas.md derivation: the real Obsidian vault root is
    the PARENT of the resolved MemoryVault path, not vault_path() itself
    (which resolves to the `Agent/` subfolder) — and never a cached
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

    def test_default_derives_from_vault_parent(self):
        fake_vault = Path("/fake/Obsidian/Agent")
        with mock.patch.object(ideas_surface, "_resolve_vault_root", return_value=fake_vault):
            result = ideas_surface._resolve_ideas_path(None)
        self.assertEqual(result, Path("/fake/Obsidian/Ideas.md"))

    def test_explicit_arg_wins_over_vault_derivation(self):
        with mock.patch.object(ideas_surface, "_resolve_vault_root", return_value=Path("/fake/Obsidian/Agent")):
            result = ideas_surface._resolve_ideas_path("/explicit/Ideas.md")
        self.assertEqual(result, Path("/explicit/Ideas.md"))

    def test_env_override_wins_over_vault_derivation(self):
        os.environ["IDEAS_SURFACE_PATH"] = "/env/Ideas.md"
        with mock.patch.object(ideas_surface, "_resolve_vault_root", return_value=Path("/fake/Obsidian/Agent")):
            result = ideas_surface._resolve_ideas_path(None)
        self.assertEqual(result, Path("/env/Ideas.md"))

    def test_unresolvable_vault_raises_instead_of_falling_back(self):
        with mock.patch.object(ideas_surface, "_resolve_vault_root", return_value=None):
            with self.assertRaises(FileNotFoundError):
                ideas_surface._resolve_ideas_path(None)

    def test_append_idea_writes_to_derived_default_path(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "Obsidian" / "Agent"
            vault.mkdir(parents=True)
            with mock.patch.object(ideas_surface, "_resolve_vault_root", return_value=vault):
                result = ideas_surface.append_idea_to_surface(
                    "A Cool Idea", "A one-sentence pitch.", mode="silent",
                )
            expected = vault.parent / "Ideas.md"
            self.assertEqual(result, expected)
            self.assertTrue(expected.is_file())


if __name__ == "__main__":
    unittest.main()
