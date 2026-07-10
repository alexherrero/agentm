#!/usr/bin/env python3
"""kind_registry.py — V6-15's kind-taxonomy registry + read-only audit.

Run directly:
    cd scripts && python3 -m unittest test_kind_registry
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

import kind_registry as kr  # noqa: E402


def _write_note(path: Path, kind: str, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nkind: {kind}\nstatus: active\ncreated: 2026-07-10\n"
        f"updated: 2026-07-10\ntags: []\ngroup: test\nslug: {path.stem}\n---\n\n"
        f"body{extra}\n",
        encoding="utf-8",
    )


class TestKnownKinds(unittest.TestCase):
    def test_reserved_kinds_are_known(self):
        for kind in ("failure-incident", "session-cost", "crystallized"):
            self.assertTrue(kr.is_known(kind), kind)

    def test_unknown_kind_is_not_known(self):
        self.assertFalse(kr.is_known("totally-invented-kind"))

    def test_near_duplicates_both_kept_distinct(self):
        # The registry deliberately does not collapse near-synonyms.
        self.assertTrue(kr.is_known("convention"))
        self.assertTrue(kr.is_known("conventions"))

    def test_is_known_is_case_sensitive(self):
        self.assertFalse(kr.is_known("Fix"))
        self.assertTrue(kr.is_known("fix"))


class TestIsKebab(unittest.TestCase):
    def test_valid_kebab(self):
        self.assertTrue(kr.is_kebab("domain-reference"))

    def test_parenthetical_suffix_is_not_kebab(self):
        self.assertFalse(kr.is_kebab("handoff-artifact (verdict memo)"))

    def test_pipe_separated_is_not_kebab(self):
        self.assertFalse(kr.is_kebab("bundle | skill | command"))


class TestAudit(unittest.TestCase):
    def test_missing_vault_returns_empty_report(self):
        result = kr.audit("/nonexistent/path/xyz")
        self.assertEqual(result, {
            "by_kind": {}, "malformed": [], "unrecognized": [], "total_files": 0,
        })

    def test_audit_never_writes_to_the_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "personal" / "a.md"
            _write_note(note, "fix")
            before = note.read_text(encoding="utf-8")
            kr.audit(vault)
            after = note.read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_audit_counts_known_kinds_by_frequency(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "a.md", "fix")
            _write_note(vault / "personal" / "b.md", "fix")
            _write_note(vault / "projects" / "p" / "c.md", "idea")
            result = kr.audit(vault)
            self.assertEqual(result["by_kind"], {"fix": 2, "idea": 1})
            self.assertEqual(result["total_files"], 3)
            self.assertEqual(result["malformed"], [])
            self.assertEqual(result["unrecognized"], [])

    def test_audit_flags_unrecognized_valid_kebab_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "a.md", "made-up-kind")
            result = kr.audit(vault)
            self.assertEqual(len(result["unrecognized"]), 1)
            self.assertEqual(result["unrecognized"][0][1], "made-up-kind")
            # by_kind counts every valid-kebab kind regardless of recognition
            # status; "unrecognized" is additive metadata, not a filter.
            self.assertEqual(result["by_kind"], {"made-up-kind": 1})

    def test_audit_flags_malformed_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "personal" / "a.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(
                "---\nkind: handoff-artifact (verdict memo)\nstatus: active\n"
                "created: 2026-07-10\nupdated: 2026-07-10\ntags: []\n"
                "group: test\nslug: a\n---\n\nbody\n",
                encoding="utf-8",
            )
            result = kr.audit(vault)
            self.assertEqual(len(result["malformed"]), 1)
            self.assertEqual(result["malformed"][0][1], "handoff-artifact (verdict memo)")
            self.assertEqual(result["by_kind"], {})

    def test_audit_excludes_archive_dir_and_plan_archive_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "_archive" / "old.md", "fix")
            archived_plan = vault / "personal" / "PLAN.archive.20260101-x.md"
            _write_note(archived_plan, "workflow")
            result = kr.audit(vault)
            self.assertEqual(result["by_kind"], {})
            self.assertEqual(result["total_files"], 0)


if __name__ == "__main__":
    unittest.main()
