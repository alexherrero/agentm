#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/stem_links.py (task 193 step 3).

The unique-stem rule (LD-4): a generated link is the bare name when no other
note in the vault carries it, and the note's path with a label when one does,
because Obsidian resolves a bare name across the whole vault.

Run directly:

    python3 scripts/test_stem_links.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import stem_links  # noqa: E402


class StemIndexTests(unittest.TestCase):

    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="stem-links-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        (self.vault / ".obsidian").mkdir()
        self.root = self.vault / "agent"
        for rel in ("agent/memory/semantic/_index.md", "agent/memory/procedural/_index.md",
                    "agent/memory/semantic/editor-choice.md", "projects/x/Editor-Choice.md",
                    "agent/memory/semantic/lonely.md", "personal/ideas/an-idea.md",
                    "agent/memory/semantic/pic.md", "resources/pic.png",
                    ".trash/lonely.md", "agent/memory/semantic/.hidden.md"):
            p = self.vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n", encoding="utf-8")
        self.ix = stem_links.StemIndex.for_memory_root(self.root)

    def test_a_unique_name_stays_bare(self):
        self.assertEqual(self.ix.link("memory/semantic/lonely.md"), "[[lonely]]")

    def test_a_unique_name_under_another_label(self):
        self.assertEqual(self.ix.link("memory/semantic/lonely.md", "Lonely note"), "[[lonely|Lonely note]]")

    def test_a_shared_name_is_linked_by_path_with_a_label(self):
        self.assertEqual(self.ix.link("memory/semantic/_index.md"), "[[agent/memory/semantic/_index|_index]]")
        self.assertEqual(self.ix.link("memory/procedural/_index.md", "procedural"),
                         "[[agent/memory/procedural/_index|procedural]]")

    def test_names_are_compared_without_case(self):
        self.assertFalse(self.ix.unique("editor-choice"))
        self.assertEqual(self.ix.link("memory/semantic/editor-choice.md"),
                         "[[agent/memory/semantic/editor-choice|editor-choice]]")

    def test_names_are_compared_in_nfc(self):
        nfd = unicodedata.normalize("NFD", "café")
        (self.vault / "projects" / "x" / (nfd + ".md")).write_text("x\n", encoding="utf-8")
        (self.vault / "agent" / "memory" / "semantic" / "café.md").write_text("x\n", encoding="utf-8")
        ix = stem_links.StemIndex.for_memory_root(self.root)
        self.assertFalse(ix.unique("café"))

    def test_dot_folders_dot_files_and_attachments_carry_no_name(self):
        # `.trash/lonely.md` and `.hidden.md` are outside Obsidian's notes, and
        # `pic.png` is an attachment `[[pic]]` never resolves to.
        self.assertTrue(self.ix.unique("lonely"))
        self.assertTrue(self.ix.unique(".hidden"))
        self.assertEqual(self.ix.link("memory/semantic/pic.md"), "[[pic]]")

    def test_an_idea_card_resolves_from_the_vault_root(self):
        self.assertEqual(self.ix.vault_rel("personal/ideas/an-idea.md"), "personal/ideas/an-idea.md")
        self.assertEqual(self.ix.vault_rel("memory/semantic/lonely.md"), "agent/memory/semantic/lonely.md")

    def test_a_note_not_yet_on_disk_is_read_from_the_memory_root(self):
        self.assertEqual(self.ix.vault_rel("memory/mocs/tomorrow.md"), "agent/memory/mocs/tomorrow.md")
        self.assertEqual(self.ix.link("memory/mocs/tomorrow.md"), "[[tomorrow]]")

    def test_a_label_cannot_break_the_link(self):
        self.assertEqual(self.ix.link("memory/semantic/_index.md", "a | b [c]"),
                         "[[agent/memory/semantic/_index|a - b (c)]]")

    def test_a_flat_vault_is_both_roots(self):
        flat = Path(tempfile.mkdtemp(prefix="stem-links-flat-"))
        self.addCleanup(shutil.rmtree, flat, ignore_errors=True)
        for rel in ("memory/semantic/_index.md", "memory/episodic/_index.md"):
            (flat / rel).parent.mkdir(parents=True, exist_ok=True)
            (flat / rel).write_text("x\n", encoding="utf-8")
        ix = stem_links.StemIndex.for_memory_root(flat)
        self.assertEqual(ix.link("memory/episodic/_index.md", "episodic"), "[[memory/episodic/_index|episodic]]")


if __name__ == "__main__":
    unittest.main()
