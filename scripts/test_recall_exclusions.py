#!/usr/bin/env python3
"""recall.py's `_iter_entry_paths` directory-exclusion set (L1/F4).

`_dream-staging/` used to be recall-visible: a bulk-review batch's proposal
files, each embedding a full copy of a real note's content, were
keyword-recall candidates until this test's fix closed the gap (dream.py
already excluded the directory from its own source walk; recall.py had no
matching entry). `_archive/` is covered too since both live in the same
exclusion set.

Run directly:
    cd scripts && python3 -m unittest test_recall_exclusions
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

import recall  # noqa: E402


class TestIterEntryPathsExclusions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel: str) -> None:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\nkind: note\n---\nbody\n", encoding="utf-8")

    def test_dream_staging_subtree_excluded(self):
        self._write("personal/reference/live-note.md")
        self._write("_dream-staging/inbox-20260712-abc/123-proposal.md")
        paths = recall._iter_entry_paths(self.vault)
        names = {p.name for p in paths}
        self.assertIn("live-note.md", names)
        self.assertNotIn("123-proposal.md", names)

    def test_archive_subtree_still_excluded(self):
        self._write("personal/reference/live-note.md")
        self._write("projects/foo/_archive/old.md")
        paths = recall._iter_entry_paths(self.vault)
        names = {p.name for p in paths}
        self.assertIn("live-note.md", names)
        self.assertNotIn("old.md", names)


class TestIsInboxPath(unittest.TestCase):
    """`_is_inbox_path` is the defense-in-depth backstop `_vec_search`/
    `_vec_search_filtered` apply to their sqlite-vec result rows -- unlike
    `_bm25_search`/`_grep_search`, those two query the index directly by
    rowid and never walk via `_iter_entry_paths`, so they had no `_inbox`
    exclusion of their own until a retroactive /review (capture-phone-
    ingest-sweep plan) found the gap. Nothing indexes `_inbox` content
    today, so this is a backstop against a future writer changing that,
    not a currently-reachable leak on its own — but it must be correct."""

    def test_inbox_path_at_any_depth_is_excluded(self):
        self.assertTrue(recall._is_inbox_path("personal/_inbox/candidate.md"))
        self.assertTrue(recall._is_inbox_path("personal/_inbox/ingested/typography/domain-reference/x.md"))

    def test_ordinary_path_is_not_excluded(self):
        self.assertFalse(recall._is_inbox_path("personal/domain-reference/typography.md"))
        self.assertFalse(recall._is_inbox_path("personal/reference/inbox-notes.md"))  # "inbox" substring, not the dir


if __name__ == "__main__":
    unittest.main()
