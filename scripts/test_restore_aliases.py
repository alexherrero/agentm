#!/usr/bin/env python3
"""restore_aliases: a note gets back the aliases a rewrite dropped, after the
ones it carries now (task 182 step 5)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "migrate"))
import restore_aliases as ra  # noqa: E402

CARD = """---
title: Consolidation loop is dreaming
type: reference
status: active
aliases: [ConsolidateAgent, sleep cycles]
slug: the-always-on-agent
enriched_by: enrich/1
---

A ConsolidateAgent runs on a timer.
"""


class RestoreAliases(unittest.TestCase):
    def test_lost_aliases_come_back_after_the_current_ones(self):
        after = ra.with_aliases(CARD, ["who else synthesizes memories on a timer", "sleep cycles"])
        self.assertEqual(ra.note_aliases(after),
                         ["ConsolidateAgent", "sleep cycles", "who else synthesizes memories on a timer"])
        self.assertTrue(after.endswith("A ConsolidateAgent runs on a timer.\n"))
        # aliases is a machine field: it stays in the machine block.
        self.assertLess(after.index("slug:"), after.index("aliases:"))

    def test_a_note_with_no_aliases_gains_the_list(self):
        bare = CARD.replace("aliases: [ConsolidateAgent, sleep cycles]\n", "")
        after = ra.with_aliases(bare, ["EnterWorktree", "isolation.mode: worktree-per-plan"])
        self.assertEqual(ra.note_aliases(after), ["EnterWorktree", "isolation.mode: worktree-per-plan"])

    def test_a_block_list_is_read(self):
        block = CARD.replace("aliases: [ConsolidateAgent, sleep cycles]\n",
                             "aliases:\n  - ConsolidateAgent\n  - \"sleep cycles\"\n")
        self.assertEqual(ra.note_aliases(block), ["ConsolidateAgent", "sleep cycles"])


    # Task 182 release review: a quoted alias holding a comma is one alias, and
    # a note this reader cannot split is left alone rather than stopping the run.
    def test_a_quoted_alias_with_a_comma_is_one_alias(self):
        text = '---\ntitle: x\naliases: ["when the gate fails, who is told", plain]\n---\nbody\n'
        self.assertEqual(ra.note_aliases(text), ["when the gate fails, who is told", "plain"])
        after = ra.with_aliases(text, ["another, with a comma"])
        self.assertEqual(ra.note_aliases(after),
                         ["when the gate fails, who is told", "plain", "another, with a comma"])

    def test_a_crlf_note_is_left_alone(self):
        text = '---\r\ntitle: x\r\n---\r\nbody\r\n'
        self.assertEqual(ra.with_aliases(text, ["lost alias"]), text)


if __name__ == "__main__":
    unittest.main()
