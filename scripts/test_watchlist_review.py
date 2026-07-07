#!/usr/bin/env python3
"""Unit tests for `watchlist_review.py` — the operator review CLI, now
generalized (AG Wave E experience plan, task 1) to scan BOTH
`personal/_skill-watchlist/` (adapt-don't-import, pre-existing) and
`personal/_watchlist/` (the new forward-learning findings) as one surface.

`watchlist_review.py` lives in `harness/skills/memory/scripts/` (same
cross-dir import pattern as the other memory-skill script tests).

Covers:
  - list_watchlist_entries merges entries from both roots
  - dismiss_entry archives into the entry's OWN root's _archive/, never
    cross-mixing a _watchlist/ entry into _skill-watchlist/_archive/ or
    vice versa
  - promote_entry / defer_entry work regardless of which root the entry
    came from (they operate purely on the given path)
  - _entry_path_from_slugs resolves an entry in either root
  - pre-existing skill-watchlist-only behavior is unchanged (regression
    coverage — this module had no prior test file)
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

import watchlist_review as wr  # noqa: E402


_ENTRY_TEMPLATE = (
    "---\n"
    "kind: {kind}\n"
    "status: pending-review\n"
    "evaluator_classification: {tier}\n"
    "---\n"
    "# {title}\n"
)


class _WatchlistReviewTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def _write_entry(self, root: Path, source_slug: str, pattern_slug: str, *, kind="idea", tier="HIGH") -> Path:
        entry_dir = self.vault / root / source_slug
        entry_dir.mkdir(parents=True, exist_ok=True)
        path = entry_dir / f"{pattern_slug}.md"
        path.write_text(_ENTRY_TEMPLATE.format(kind=kind, tier=tier, title=pattern_slug), encoding="utf-8")
        return path


class ListMergesBothRootsTests(_WatchlistReviewTestBase):
    def test_list_includes_entries_from_both_roots(self) -> None:
        self._write_entry(Path("personal/_skill-watchlist"), "skill-src", "cool-skill")
        self._write_entry(Path("personal/_watchlist"), "idea-src", "cool-idea")

        entries = wr.list_watchlist_entries(self.vault)
        pairs = {(e["source_slug"], e["pattern_slug"]) for e in entries}
        self.assertEqual(pairs, {("skill-src", "cool-skill"), ("idea-src", "cool-idea")})

    def test_list_excludes_archive_dirs_in_both_roots(self) -> None:
        self._write_entry(Path("personal/_skill-watchlist/_archive"), "skill-src", "old-skill")
        self._write_entry(Path("personal/_watchlist/_archive"), "idea-src", "old-idea")
        self._write_entry(Path("personal/_watchlist"), "idea-src", "fresh-idea")

        entries = wr.list_watchlist_entries(self.vault)
        pairs = {(e["source_slug"], e["pattern_slug"]) for e in entries}
        self.assertEqual(pairs, {("idea-src", "fresh-idea")})

    def test_empty_vault_returns_no_entries(self) -> None:
        self.assertEqual(wr.list_watchlist_entries(self.vault), [])


class DismissArchivesIntoOwnRootTests(_WatchlistReviewTestBase):
    def test_skill_watchlist_entry_archives_into_skill_watchlist_archive(self) -> None:
        path = self._write_entry(Path("personal/_skill-watchlist"), "skill-src", "cool-skill")
        result = wr.dismiss_entry(self.vault, path)
        self.assertEqual(result["action"], "dismissed")
        expected = self.vault / "personal" / "_skill-watchlist" / "_archive" / "skill-src" / "cool-skill.md"
        self.assertTrue(expected.exists())
        self.assertFalse((self.vault / "personal" / "_watchlist" / "_archive").exists())

    def test_general_watchlist_entry_archives_into_general_watchlist_archive(self) -> None:
        path = self._write_entry(Path("personal/_watchlist"), "idea-src", "cool-idea")
        result = wr.dismiss_entry(self.vault, path)
        self.assertEqual(result["action"], "dismissed")
        expected = self.vault / "personal" / "_watchlist" / "_archive" / "idea-src" / "cool-idea.md"
        self.assertTrue(expected.exists())
        self.assertFalse((self.vault / "personal" / "_skill-watchlist" / "_archive").exists())

    def test_dismissed_entry_no_longer_listed(self) -> None:
        path = self._write_entry(Path("personal/_watchlist"), "idea-src", "cool-idea")
        wr.dismiss_entry(self.vault, path)
        self.assertEqual(wr.list_watchlist_entries(self.vault), [])


class PromoteAndDeferWorkRegardlessOfRootTests(_WatchlistReviewTestBase):
    def test_promote_a_general_watchlist_entry(self) -> None:
        path = self._write_entry(Path("personal/_watchlist"), "idea-src", "cool-idea")
        result = wr.promote_entry(path)
        self.assertEqual(result["action"], "promoted")
        fm = wr._parse_frontmatter(path)
        self.assertEqual(fm["status"], "promoted")

    def test_defer_a_general_watchlist_entry(self) -> None:
        path = self._write_entry(Path("personal/_watchlist"), "idea-src", "cool-idea")
        result = wr.defer_entry(path, until_date="2026-08-01")
        self.assertEqual(result["action"], "deferred")
        fm = wr._parse_frontmatter(path)
        self.assertEqual(fm["deferred_until"], "2026-08-01")


class EntryPathFromSlugsResolvesEitherRootTests(_WatchlistReviewTestBase):
    def test_resolves_skill_watchlist_entry(self) -> None:
        path = self._write_entry(Path("personal/_skill-watchlist"), "skill-src", "cool-skill")
        resolved = wr._entry_path_from_slugs(self.vault, "skill-src", "cool-skill")
        self.assertEqual(resolved, path)

    def test_resolves_general_watchlist_entry(self) -> None:
        path = self._write_entry(Path("personal/_watchlist"), "idea-src", "cool-idea")
        resolved = wr._entry_path_from_slugs(self.vault, "idea-src", "cool-idea")
        self.assertEqual(resolved, path)

    def test_nonexistent_entry_falls_back_to_skill_watchlist_path(self) -> None:
        resolved = wr._entry_path_from_slugs(self.vault, "no-such-src", "no-such-slug")
        self.assertEqual(resolved, self.vault / "personal" / "_skill-watchlist" / "no-such-src" / "no-such-slug.md")


if __name__ == "__main__":
    unittest.main()
