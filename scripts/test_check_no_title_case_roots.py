#!/usr/bin/env python3
"""Tests for scripts/check-no-title-case-roots.py (agentm-vault plan 08).

The gate must catch a literal that names a root space by its retired Title
Case spelling, in the path form anywhere and in the bare quoted form in code,
must skip comment lines, must honour the `root-casing:` marker and the
allowed places, and must print an inventory with a count.
"""
from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("check_no_title_case_roots", _HERE / "check-no-title-case-roots.py")
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore[union-attr]

TITLE = "Pro" + "jects"  # spelled apart so this file never carries the literal itself
AGENT = "Ag" + "ent"


def _run(tmp: str, rel: str, content: str, *extra: str) -> tuple[int, str, str]:
    p = Path(tmp) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = _mod.main(["--root", tmp, *extra])
    return rc, out.getvalue(), err.getvalue()


class TestHits(unittest.TestCase):
    def test_a_path_form_in_code_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = _run(tmp, "scripts/x.py", f'rel = "{AGENT}/memory/a.md"\n')
            self.assertEqual(rc, 1)
            self.assertIn("scripts/x.py:1 [path]", err)

    def test_a_path_form_in_a_skill_doc_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = _run(tmp, "harness/skills/memory/SKILL.md", f"Write it under `{TITLE}/agentm/_watchlist/`.\n")
            self.assertEqual(rc, 1)
            self.assertIn("[path]", err)

    def test_a_bare_quoted_name_in_code_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = _run(tmp, "daemon/internal/x.go", f'\tspace := "{TITLE}"\n')
            self.assertEqual(rc, 1)
            self.assertIn("[bare-name]", err)

    def test_a_bare_quoted_name_in_prose_passes(self):
        """A word in a document is not a directory a module joins onto a root."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "harness/skills/memory/SKILL.md", f'The "{TITLE}" board column.\n')
            self.assertEqual(rc, 0)

    def test_a_root_closing_a_path_fails_and_the_operators_home_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = _run(tmp, "scripts/x.py", f'"projects": "../{TITLE}",\n')
            self.assertEqual(rc, 1)
            self.assertIn("[path]", err)
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "scripts/y.sh", f'R_VAULT="$R_ROOT/{AGENT}"\n')
            self.assertEqual(rc, 1)
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "harness/telemetry.md", f"All default roots (~/Antigravity, ~/{TITLE}):\n")
            self.assertEqual(rc, 0)

    def test_a_longer_identifier_is_not_a_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "scripts/x.py", f'x = "{AGENT}Memory/notes"  # {AGENT}M is a product\n')
            self.assertEqual(rc, 0)

    def test_the_lowercase_spelling_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = _run(tmp, "scripts/x.py", 'rel = "agent/memory/a.md"\nspace = "projects"\n')
            self.assertEqual(rc, 0)
            self.assertIn("clean", out)


class TestAllowed(unittest.TestCase):
    def test_a_comment_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "scripts/x.py", f"# the old {AGENT}/memory/ layout\n")
            self.assertEqual(rc, 0)
            rc, _, _ = _run(tmp, "daemon/x.go", f"// the old {AGENT}/memory/ layout\n")
            self.assertEqual(rc, 0)

    def test_the_marker_allows_a_line_and_the_inventory_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "daemon/x.go", f'\tadd("{TITLE}", maps) // root-casing: the map heading, not a path\n')
            self.assertEqual(rc, 0)
            rc, out, _ = _run(tmp, "daemon/x.go",
                              f'\tadd("{TITLE}", maps) // root-casing: the map heading, not a path\n', "--inventory")
            self.assertEqual(rc, 0)
            self.assertIn("(allowed: marker)", out)
            self.assertIn("0 literal(s) to repoint", out)

    def test_documentation_and_frozen_records_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rel in ("wiki/how-to/x.md", "CHANGELOG.md", "scripts/health/fixtures/gold/gold.json",
                        "scripts/health/results/run.json"):
                rc, _, _ = _run(tmp, rel, f'{{"path": "{AGENT}/memory/a.md"}}\n')
                self.assertEqual(rc, 0, rel)

    def test_a_retired_state_tree_is_no_longer_allowed(self):
        # agentm-vault plan 15: the repo holds no per-project state tree any
        # more, so a file under one is scanned like any other.
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = _run(tmp, "Projects/agentm/_harness/PLAN.md", f'{{"path": "{AGENT}/memory/a.md"}}\n')
            self.assertEqual(rc, 1)
            self.assertIn("_harness/PLAN.md", err)

    def test_a_finished_migration_keeps_its_spelling(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "scripts/migrate/maps_and_root_notes.py", f'MOVES = (("Calendar/x.md", "{AGENT}/y.md"),)\n')
            self.assertEqual(rc, 0)

    def test_a_new_migration_is_not_allowed_by_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "scripts/migrate/root_casing.py", f'x = "{AGENT}/memory"\n')
            self.assertEqual(rc, 1)


class TestInventory(unittest.TestCase):
    def test_the_inventory_lists_every_hit_with_a_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "scripts").mkdir()
            (Path(tmp) / "scripts" / "a.py").write_text(f'a = "{AGENT}/memory"\nb = "Calendar/2026"\n', encoding="utf-8")
            rc, out, _ = _run(tmp, "scripts/b.sh", f'ls "$VAULT/{TITLE}/"\n', "--inventory")
            self.assertEqual(rc, 0)
            self.assertIn("scripts/a.py:1 [path]", out)
            self.assertIn("scripts/a.py:2 [path]", out)
            self.assertIn("scripts/b.sh:1 [path]", out)
            self.assertIn("3 literal(s) to repoint in 2 file(s); 0 allowed", out)

    def test_the_real_repo_is_clean(self):
        """The repoint has been applied: the tree the gate ships in passes it."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = _mod.main([])
        self.assertEqual(rc, 0, err.getvalue())


if __name__ == "__main__":
    unittest.main()
