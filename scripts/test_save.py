#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/save.py's frontmatter builder.

Tests live here in scripts/ so CI (`cd scripts && python3 -m unittest discover
-p 'test_*.py'`) runs them, same convention as test_vault_lint.py. We add the
skill scripts dir to sys.path to import the module under test.

Covers the `source_url`/`source_fetched` provenance fields (capture design's
provenance plumbing, `designs/friday/agentm-capture.md`, capture-front-door
plan task 1): both round-trip when present, both are omitted cleanly when
absent, and the locked field order (`FRONTMATTER_FIELD_ORDER`) places them
right after `slug`, before `fingerprint`.
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

import save  # noqa: E402


class FrontmatterFieldOrderTests(unittest.TestCase):
    def test_source_fields_positioned_after_slug_before_fingerprint(self) -> None:
        order = save.FRONTMATTER_FIELD_ORDER
        self.assertLess(order.index("slug"), order.index("source_url"))
        self.assertLess(order.index("source_url"), order.index("source_fetched"))
        self.assertLess(order.index("source_fetched"), order.index("fingerprint"))

    def test_source_fields_are_optional(self) -> None:
        self.assertIn("source_url", save._OPTIONAL_FIELDS)
        self.assertIn("source_fetched", save._OPTIONAL_FIELDS)
        self.assertNotIn("source_url", save.REQUIRED_FRONTMATTER_FIELDS)
        self.assertNotIn("source_fetched", save.REQUIRED_FRONTMATTER_FIELDS)


class BuildFrontmatterProvenanceTests(unittest.TestCase):
    def _build(self, **overrides):
        kwargs = dict(
            kind="domain-reference", group="personal", slug="test-slug",
            tags=[], always_load=False, supersedes=None,
        )
        kwargs.update(overrides)
        return save._build_frontmatter(**kwargs)

    def test_both_fields_present_round_trip(self) -> None:
        fm = self._build(source_url="https://example.com/article", source_fetched="2026-07-18")
        self.assertIn("source_url: https://example.com/article", fm)
        self.assertIn("source_fetched: 2026-07-18", fm)

    def test_both_fields_omitted_when_absent(self) -> None:
        fm = self._build()
        self.assertNotIn("source_url:", fm)
        self.assertNotIn("source_fetched:", fm)

    def test_one_field_present_other_absent(self) -> None:
        fm = self._build(source_url="https://example.com/x")
        self.assertIn("source_url: https://example.com/x", fm)
        self.assertNotIn("source_fetched:", fm)

    def test_field_order_in_emitted_yaml(self) -> None:
        fm = self._build(source_url="https://example.com/x", source_fetched="2026-07-18",
                         fingerprint="abc123")
        lines = fm.splitlines()
        slug_i = next(i for i, l in enumerate(lines) if l.startswith("slug:"))
        url_i = next(i for i, l in enumerate(lines) if l.startswith("source_url:"))
        fetched_i = next(i for i, l in enumerate(lines) if l.startswith("source_fetched:"))
        fp_i = next(i for i, l in enumerate(lines) if l.startswith("fingerprint:"))
        self.assertTrue(slug_i < url_i < fetched_i < fp_i)


class SaveEntryProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_entry_writes_provenance_fields(self) -> None:
        path = save.save_entry(
            self.vault, "domain-reference", "prov-test", "body text\n",
            source_url="https://example.com/article", source_fetched="2026-07-18",
        )
        content = path.read_text(encoding="utf-8")
        self.assertIn("source_url: https://example.com/article", content)
        self.assertIn("source_fetched: 2026-07-18", content)

    def test_save_entry_omits_provenance_fields_by_default(self) -> None:
        path = save.save_entry(self.vault, "domain-reference", "no-prov-test", "body text\n")
        content = path.read_text(encoding="utf-8")
        self.assertNotIn("source_url:", content)
        self.assertNotIn("source_fetched:", content)


if __name__ == "__main__":
    unittest.main()
