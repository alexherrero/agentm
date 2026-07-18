#!/usr/bin/env python3
"""arc_registry.py — the arc-as-metadata registry (2026-07-18 convention).

Run directly:
    cd scripts && python3 -m unittest test_arc_registry
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

import arc_registry as ar  # noqa: E402


def _write(path: Path, arc: str | None, group: str = "decisions") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arc_line = f"arc: {arc}\n" if arc is not None else ""
    path.write_text(
        f"---\nkind: decision\nstatus: active\ncreated: 2026-07-01\n"
        f"updated: 2026-07-01\ntags: []\n{arc_line}group: {group}\nslug: {path.stem}\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )


class TestIsKebab(unittest.TestCase):
    def test_valid_kebab(self):
        self.assertTrue(ar.is_kebab("wave-a"))
        self.assertTrue(ar.is_kebab("v8"))

    def test_rejects_uppercase_and_underscore(self):
        self.assertFalse(ar.is_kebab("Wave-A"))
        self.assertFalse(ar.is_kebab("wave_a"))
        self.assertFalse(ar.is_kebab(""))


class TestIsKnown(unittest.TestCase):
    def test_known_arc(self):
        self.assertTrue(ar.is_known("architecture-governance"))
        self.assertTrue(ar.is_known("v8"))

    def test_unrecognized_arc(self):
        self.assertFalse(ar.is_known("not-a-real-arc"))

    def test_case_sensitive_no_normalization(self):
        # A differently-cased duplicate is a distinct, unrecognized value —
        # the registry doesn't normalize case (module docstring contract).
        self.assertFalse(ar.is_known("Wave-A"))
        self.assertTrue(ar.is_known("wave-a"))


class TestAudit(unittest.TestCase):
    def test_missing_vault_returns_empty(self):
        result = ar.audit("/nonexistent/path/xyz")
        self.assertEqual(result, {"by_arc": {}, "malformed": [], "unrecognized": [], "total_stamped": 0})

    def test_counts_known_arc(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "projects" / "agentm" / "decisions" / "a.md", "wave-a")
            _write(vault / "projects" / "agentm" / "decisions" / "b.md", "wave-a")
            _write(vault / "projects" / "agentm" / "decisions" / "c.md", "v8")
            result = ar.audit(vault)
            self.assertEqual(result["total_stamped"], 3)
            self.assertEqual(result["by_arc"], {"wave-a": 2, "v8": 1})
            self.assertEqual(result["malformed"], [])
            self.assertEqual(result["unrecognized"], [])

    def test_entry_with_no_arc_field_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "projects" / "agentm" / "decisions" / "a.md", None)
            result = ar.audit(vault)
            self.assertEqual(result["total_stamped"], 0)

    def test_unrecognized_arc_flagged_but_still_kebab(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "projects" / "agentm" / "decisions" / "a.md", "some-new-arc")
            result = ar.audit(vault)
            self.assertEqual(result["total_stamped"], 1)
            self.assertEqual(result["by_arc"], {"some-new-arc": 1})
            self.assertEqual(len(result["unrecognized"]), 1)
            self.assertEqual(result["unrecognized"][0][1], "some-new-arc")
            self.assertEqual(result["malformed"], [])

    def test_malformed_arc_not_counted_in_by_arc(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "projects" / "agentm" / "decisions" / "a.md", "Not_Kebab")
            result = ar.audit(vault)
            self.assertEqual(result["total_stamped"], 1)
            self.assertEqual(result["by_arc"], {})
            self.assertEqual(len(result["malformed"]), 1)
            self.assertEqual(result["malformed"][0][1], "Not_Kebab")
            self.assertEqual(result["unrecognized"], [])

    def test_archive_dir_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "projects" / "agentm" / "decisions" / "_archive" / "old.md", "wave-a")
            result = ar.audit(vault)
            self.assertEqual(result["total_stamped"], 0)

    def test_plan_archive_files_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            p = vault / "projects" / "agentm" / "_harness" / "PLAN.archive.20260718-foo.md"
            _write(p, "wave-a")
            result = ar.audit(vault)
            self.assertEqual(result["total_stamped"], 0)


if __name__ == "__main__":
    unittest.main()
