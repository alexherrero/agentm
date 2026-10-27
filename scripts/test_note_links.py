#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/note_links.py (task 193 step 6).

The link-valued keys' one parser (LD-5) reads every form the writers have
used. The cases match daemon/internal/fmlist/links_test.go's.

Run directly:

    python3 scripts/test_note_links.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import note_links as nl  # noqa: E402

L = nl.Link


class LinksTests(unittest.TestCase):

    def test_every_form(self):
        for value, want in (
            ("memory/semantic/x.md", [L("memory/semantic/x.md")]),
            ("x", [L("x")]),
            ('"[[x]]"', [L("x")]),
            ('"[[agent/memory/semantic/x|label]]"', [L("agent/memory/semantic/x")]),
            ('"[[x#heading]]"', [L("x")]),
            ('["[[a]]", "[[b|B]]"]', [L("a"), L("b")]),
            ("['memory/a.md', 'memory/b.md']", [L("memory/a.md"), L("memory/b.md")]),
            ("[a, b]", [L("a"), L("b")]),
            ("[[a]]", [L("a")]),       # unquoted: YAML's nested list, read as the link it meant
            ("[[[a]]]", [L("a")]),     # a flow list of one unquoted wikilink
            ("src-1 at v3", [L("src-1 at v3", source=True)]),
            ('"src-1 at v3"', [L("src-1 at v3", source=True)]),
            ("", []), ("   ", []), (None, []),
        ):
            self.assertEqual(nl.links(value), want, value)

    def test_values_a_yaml_parser_hands_back(self):
        # PyYAML reads an unquoted `[[a]]` as [["a"]], and a quoted list as strings.
        self.assertEqual(nl.links([["a"]]), [L("a")])
        self.assertEqual(nl.links(["[[a]]", "memory/b.md"]), [L("a"), L("memory/b.md")])

    def test_stem_memory_rel_and_first(self):
        wiki, bare = L("agent/memory/semantic/x"), L("memory/semantic/x.md")
        self.assertEqual((wiki.stem, bare.stem, L("x").stem), ("x", "x", "x"))
        self.assertEqual(wiki.memory_rel("agent"), "memory/semantic/x.md")
        self.assertEqual(bare.memory_rel("agent"), "memory/semantic/x.md")
        self.assertEqual(L("memory/semantic/x").memory_rel(""), "memory/semantic/x.md")
        self.assertEqual(nl.first('["src at v1", "[[y]]"]'), L("y"))
        self.assertIsNone(nl.first("src at v1"))
        self.assertIsNone(nl.first("[]"))


if __name__ == "__main__":
    unittest.main()
