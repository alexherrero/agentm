#!/usr/bin/env python3
"""recall.py's `_iter_entry_paths` directory-exclusion set (L1/F4).

`_dream-staging/` used to be recall-visible: a bulk-review batch's proposal
files, each embedding a full copy of a real note's content, were
keyword-recall candidates until this test's fix closed the gap (dream.py
already excluded the directory from its own source walk; recall.py had no
matching entry). `_dream-staging/` is unconditionally excluded.

`_archive/` is excluded by default too, but — since auto-organization part
1 task 5 — independently reopenable via `include_archive=True`
(`--include-archive` on the CLI), mirroring `_inbox/`'s existing
`include_inbox` toggle exactly: an archived memory answers an explicit
archive search, never ordinary recall. `_shelf/` is never excluded at all
— the shelf is a browse convention, not a search boundary.

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

    def test_archive_subtree_reopens_with_include_archive(self):
        # Task 5: _archive/ is independently reopenable, mirroring _inbox/'s
        # existing include_inbox toggle exactly.
        self._write("projects/foo/_archive/old.md")
        paths = recall._iter_entry_paths(self.vault, include_archive=True)
        names = {p.name for p in paths}
        self.assertIn("old.md", names)

    def test_shelf_subtree_never_excluded(self):
        # Task 5: the shelf is a browse convention, not a search boundary —
        # no toggle needed, it's simply never in the exclusion set.
        self._write("personal/_shelf/old-plan.md")
        paths = recall._iter_entry_paths(self.vault)
        names = {p.name for p in paths}
        self.assertIn("old-plan.md", names)


class TestQueryEndToEndArchiveAndShelf(unittest.TestCase):
    """Task 5's own verification bar, exercised through the real
    recall.query() pipeline (mode="stub" -- deterministic, no network):
    a shelved artifact is found by ordinary search; an archived memory is
    not found by ordinary search but is found with include_archive=True."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel: str, body: str) -> None:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\nkind: note\nslug: {Path(rel).stem}\n---\n{body}\n", encoding="utf-8")

    def test_shelved_artifact_found_by_ordinary_search(self):
        self._write("personal/_shelf/old-plan.md", "widget subsystem retry logic notes")
        results = recall.query(vault=self.vault, query_text="widget subsystem", k=5)
        paths = {r["path"] for r in results}
        self.assertIn("personal/_shelf/old-plan.md", paths)

    def test_archived_memory_not_found_by_default_but_found_with_include_archive(self):
        self._write("personal/_archive/old-widget.md", "widget subsystem retry logic notes")
        default_results = recall.query(vault=self.vault, query_text="widget subsystem", k=5)
        self.assertNotIn(
            "personal/_archive/old-widget.md", {r["path"] for r in default_results}
        )

        reopened_results = recall.query(
            vault=self.vault, query_text="widget subsystem", k=5, include_archive=True,
        )
        self.assertIn(
            "personal/_archive/old-widget.md", {r["path"] for r in reopened_results}
        )


class TestInboxExclusion(unittest.TestCase):
    """`_inbox/` never leaks into ordinary recall.

    This used to test `_is_inbox_path`, a defense-in-depth backstop that
    `_vec_search`/`_vec_search_filtered` applied to their sqlite-vec result
    rows — those two queried the index by rowid and never walked via
    `_iter_entry_paths`, so they had no exclusion of their own until a
    retroactive /review found the gap. Both searches and the backstop went
    with the vector stack. The exclusion those tests were really protecting
    still exists, in `_iter_entry_paths`, which every surviving search walks
    through — so the same intent is pinned against that instead of deleted
    along with the function that used to need reminding."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel: str) -> None:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\nkind: note\n---\nbody\n", encoding="utf-8")

    def test_inbox_subtree_excluded_at_any_depth(self):
        self._write("personal/reference/live-note.md")
        self._write("personal/_inbox/candidate.md")
        self._write("personal/_inbox/ingested/typography/domain-reference/deep.md")
        names = {p.name for p in recall._iter_entry_paths(self.vault)}
        self.assertIn("live-note.md", names)
        self.assertNotIn("candidate.md", names)
        self.assertNotIn("deep.md", names)

    def test_inbox_subtree_reopens_with_include_inbox(self):
        self._write("personal/_inbox/candidate.md")
        names = {p.name for p in recall._iter_entry_paths(self.vault, include_inbox=True)}
        self.assertIn("candidate.md", names)

    def test_inbox_substring_in_a_filename_is_not_the_directory(self):
        self._write("personal/reference/inbox-notes.md")
        names = {p.name for p in recall._iter_entry_paths(self.vault)}
        self.assertIn("inbox-notes.md", names)


if __name__ == "__main__":
    unittest.main()
