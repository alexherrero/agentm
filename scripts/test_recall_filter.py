#!/usr/bin/env python3
"""The V6-11 hybrid `--filter` path in recall.py (agentm-memory-index.md,
AG Wave B leader 3/5): parse_filter, _entry_matches_filter (the grep-fallback
predicate), and the CLI's --filter wiring. The SQL-joined vec half
(_vec_search_filtered) needs the real sqlite-vec backend and is covered by
a graceful-skip integration test, same convention as test_vec_index.py.

Run directly:
    cd scripts && python3 -m unittest test_recall_filter
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


class TestParseFilter(unittest.TestCase):
    def test_empty_expression_is_no_criteria(self):
        self.assertEqual(recall.parse_filter(None), {})
        self.assertEqual(recall.parse_filter(""), {})
        self.assertEqual(recall.parse_filter("   "), {})

    def test_single_clause(self):
        self.assertEqual(recall.parse_filter("tag=security"), {"tag": "security"})

    def test_and_joined_clauses(self):
        criteria = recall.parse_filter("tag=security AND project=sherwood")
        self.assertEqual(criteria, {"tag": "security", "project": "sherwood"})

    def test_case_insensitive_and(self):
        criteria = recall.parse_filter("kind=reference and status=active")
        self.assertEqual(criteria, {"kind": "reference", "status": "active"})

    def test_quoted_values_are_unquoted(self):
        self.assertEqual(recall.parse_filter('project="sherwood"'), {"project": "sherwood"})

    def test_malformed_clause_raises(self):
        with self.assertRaises(recall.FilterError):
            recall.parse_filter("not-a-clause")

    def test_unknown_key_raises(self):
        with self.assertRaises(recall.FilterError):
            recall.parse_filter("nonexistent=foo")


class TestDeriveProject(unittest.TestCase):
    def test_projects_group_yields_slug(self):
        self.assertEqual(recall._derive_project("projects/agentm/decisions"), "agentm")

    def test_non_project_group_yields_none(self):
        self.assertIsNone(recall._derive_project("personal/reference"))

    def test_empty_group_yields_none(self):
        self.assertIsNone(recall._derive_project(""))


class TestEntryMatchesFilter(unittest.TestCase):
    def test_tag_match(self):
        fm = {"tags": "[security, architecture]"}
        self.assertTrue(recall._entry_matches_filter(fm, {"tag": "security"}))
        self.assertFalse(recall._entry_matches_filter(fm, {"tag": "nonexistent"}))

    def test_kind_and_status_match(self):
        fm = {"kind": "reference", "status": "active"}
        self.assertTrue(recall._entry_matches_filter(fm, {"kind": "reference", "status": "active"}))
        self.assertFalse(recall._entry_matches_filter(fm, {"kind": "reference", "status": "superseded"}))

    def test_project_derived_from_group(self):
        fm = {"group": "projects/sherwood/decisions"}
        self.assertTrue(recall._entry_matches_filter(fm, {"project": "sherwood"}))
        self.assertFalse(recall._entry_matches_filter(fm, {"project": "agentm"}))

    def test_multiple_criteria_all_must_match(self):
        fm = {"kind": "reference", "group": "projects/agentm/decisions", "tags": "[security]"}
        self.assertTrue(recall._entry_matches_filter(
            fm, {"kind": "reference", "project": "agentm", "tag": "security"}))
        self.assertFalse(recall._entry_matches_filter(
            fm, {"kind": "reference", "project": "agentm", "tag": "nonexistent"}))


class TestGrepSearchWithFilter(unittest.TestCase):
    """_grep_search's filter_criteria param — no sqlite-vec dependency."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "projects" / "agentm" / "decisions").mkdir(parents=True)
        (self.vault / "personal" / "reference").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel: str, frontmatter: str, body: str = "widget content") -> None:
        # write_bytes (not write_text) — avoids write_text's universal-newline
        # translation (LF -> CRLF on Windows; write_text's `newline=` kwarg
        # needs Python 3.10+, so bytes-mode is also the portable fix).
        # recall.py reads via backend.read(), bytes-mode with no translation
        # (this codebase's LF-only convention, matching atomic_write's own
        # discipline) — a CRLF fixture would make `_parse_frontmatter`'s
        # `"---\n"` boundary check silently miss the frontmatter block.
        p = self.vault / rel
        p.write_bytes(f"---\n{frontmatter}\n---\n{body}".encode("utf-8"))

    def test_filter_narrows_grep_results_by_project(self):
        self._write(
            "projects/agentm/decisions/a.md",
            "kind: reference\nstatus: active\ntags: []\ngroup: projects/agentm/decisions\n",
        )
        self._write(
            "personal/reference/b.md",
            "kind: reference\nstatus: active\ntags: []\ngroup: personal/reference\n",
        )
        tokens = recall._tokenize("widget content")
        unfiltered = recall._grep_search(self.vault, tokens)
        self.assertEqual(len(unfiltered), 2)

        filtered = recall._grep_search(
            self.vault, tokens, filter_criteria={"project": "agentm"},
        )
        self.assertEqual(list(filtered.keys()), ["projects/agentm/decisions/a.md"])

    def test_filter_by_tag(self):
        self._write(
            "projects/agentm/decisions/tagged.md",
            "kind: reference\nstatus: active\ntags: [security]\ngroup: projects/agentm/decisions\n",
        )
        self._write(
            "personal/reference/untagged.md",
            "kind: reference\nstatus: active\ntags: []\ngroup: personal/reference\n",
        )
        tokens = recall._tokenize("widget content")
        filtered = recall._grep_search(self.vault, tokens, filter_criteria={"tag": "security"})
        self.assertEqual(list(filtered.keys()), ["projects/agentm/decisions/tagged.md"])


class TestQueryFilterIntegration(unittest.TestCase):
    """query()'s filter_expr param, end-to-end (grep-only path — no
    embedding/vec dependency needed since mode='stub' plus no vault index
    means vec_results is {} and grep alone drives the result)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "projects" / "agentm" / "decisions").mkdir(parents=True)
        (self.vault / "personal" / "reference").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_query_with_filter_expr_narrows_results(self):
        (self.vault / "projects" / "agentm" / "decisions" / "match.md").write_bytes(
            ("---\nkind: reference\nstatus: active\ntags: []\n"
             "group: projects/agentm/decisions\n---\nwidget content here").encode("utf-8")
        )
        (self.vault / "personal" / "reference" / "nomatch.md").write_bytes(
            ("---\nkind: reference\nstatus: active\ntags: []\n"
             "group: personal/reference\n---\nwidget content here too").encode("utf-8")
        )
        results = recall.query(
            vault=self.vault, query_text="widget content", filter_expr="project=agentm",
            mode="stub",
        )
        paths = [r["path"] for r in results]
        self.assertEqual(paths, ["projects/agentm/decisions/match.md"])

    def test_query_raises_filter_error_for_malformed_expression(self):
        with self.assertRaises(recall.FilterError):
            recall.query(vault=self.vault, query_text="widget", filter_expr="garbage", mode="stub")


if __name__ == "__main__":
    unittest.main()
