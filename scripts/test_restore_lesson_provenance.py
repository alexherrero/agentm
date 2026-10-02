#!/usr/bin/env python3
"""restore_lesson_provenance: a lesson gets back the sources a recheck took off
it, and names them in `released:` (task 182, the operator's ruling of
2026-10-01). The emptied lesson that started this is the second case."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "migrate"))
import restore_lesson_provenance as rlp  # noqa: E402

ORIGINAL = """---
title: CI green closes work
kind: crystallized
status: active
lifecycle: pinned
tags: [wake-on-ci-pattern]
consolidated_from:
  - "[[coordinated-release-order]]"
  - "[[state-on-disk]]"
  - "[[projects/agentm/tasks/157-x/tracker|157-x]]"
slug: ci-green-closes-work
---

Nothing records completion before the suite is green.

## What taught it

- [[coordinated-release-order]] — card, 2026-05-19
- [[state-on-disk]] — card, 2026-05-19
- [[projects/agentm/tasks/157-x/tracker|157-x]] — outcome, 2026-09-17
"""


class RestoreLessonProvenance(unittest.TestCase):
    def test_the_removed_sources_come_back_and_are_named_released(self):
        current = ORIGINAL.replace('  - "[[state-on-disk]]"\n', "").replace(
            "- [[state-on-disk]] — card, 2026-05-19\n", "")
        after, removed = rlp.restore(current, ORIGINAL)
        self.assertEqual(removed, ["state-on-disk"])
        self.assertIn('consolidated_from:\n  - "[[coordinated-release-order]]"\n  - "[[state-on-disk]]"\n'
                      '  - "[[projects/agentm/tasks/157-x/tracker|157-x]]"\n', after)
        self.assertIn("- [[state-on-disk]] — card, 2026-05-19\n", after)
        self.assertIn('\nreleased: ["[[state-on-disk]]"]\n', after)
        # `released` sits in the read block after the sources, before the machine block.
        self.assertLess(after.index("released:"), after.index("slug:"))
        self.assertGreater(after.index("released:"), after.index("consolidated_from:"))
        self.assertIn("Nothing records completion before the suite is green.", after)

    def test_a_lesson_the_recheck_emptied_gets_its_whole_list_back(self):
        # ci-green-closes-work's shape: every source a card, every card released.
        cards_only = ORIGINAL.replace('  - "[[projects/agentm/tasks/157-x/tracker|157-x]]"\n', "").replace(
            "- [[projects/agentm/tasks/157-x/tracker|157-x]] — outcome, 2026-09-17\n", "")
        emptied = cards_only.replace(
            'consolidated_from:\n  - "[[coordinated-release-order]]"\n  - "[[state-on-disk]]"\n',
            "consolidated_from:\n").split("## What taught it")[0] + "## What taught it\n\n"
        after, removed = rlp.restore(emptied, cards_only)
        self.assertEqual(removed, ["coordinated-release-order", "state-on-disk"])
        self.assertIn('consolidated_from:\n  - "[[coordinated-release-order]]"\n  - "[[state-on-disk]]"\n', after)
        self.assertIn("- [[coordinated-release-order]] — card, 2026-05-19\n"
                      "- [[state-on-disk]] — card, 2026-05-19\n", after)
        self.assertIn('released: ["[[coordinated-release-order]]", "[[state-on-disk]]"]', after)

    def test_nothing_removed_leaves_the_lesson_alone(self):
        after, removed = rlp.restore(ORIGINAL, ORIGINAL)
        self.assertEqual((after, removed), (ORIGINAL, []))

    def test_an_existing_released_list_is_kept_and_extended(self):
        current = ORIGINAL.replace('  - "[[state-on-disk]]"\n', "").replace(
            "slug: ci-green-closes-work\n",
            'released: ["[[old-release]]"]\nslug: ci-green-closes-work\n')
        after, removed = rlp.restore(current, ORIGINAL)
        self.assertIn('released: ["[[old-release]]", "[[state-on-disk]]"]', after)


if __name__ == "__main__":
    unittest.main()
