#!/usr/bin/env python3
"""Tests for scripts/check-no-harness-paths.py (agentm-vault plan 15).

The gate must catch the literal `_harness` anywhere in scanned code, comments
included, and never an identifier that merely contains it; must honour the
inline and file-scope `harness-deprecation:` markers and the allowed places;
must leave test files and the repo-local `.harness/` alone; must read only
tracked files in a git work tree; and must print an inventory with a count.
"""
from __future__ import annotations

import importlib.util
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("check_no_harness_paths", _HERE / "check-no-harness-paths.py")
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore[union-attr]


def _write(tmp: str, rel: str, content: str) -> None:
    p = Path(tmp) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _main(tmp: str, *extra: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = _mod.main(["--root", tmp, *extra])
    return rc, out.getvalue(), err.getvalue()


def _run(tmp: str, rel: str, content: str, *extra: str) -> tuple[int, str, str]:
    _write(tmp, rel, content)
    return _main(tmp, *extra)


class TestHits(unittest.TestCase):
    def test_a_composed_path_in_code_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = _run(tmp, "scripts/x.py", 'state = project / "_harness" / "PLAN.md"\n')
            self.assertEqual(rc, 1)
            self.assertIn("scripts/x.py:1", err)

    def test_a_comment_naming_the_directory_fails(self):
        """The retirement is of the name: a comment that points at `_harness/`
        teaches the next reader a place that no longer exists."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = _run(tmp, "harness/hooks/x.sh", "# reads <vault>/projects/<slug>/_harness/\n")
            self.assertEqual(rc, 1)
            self.assertIn("harness/hooks/x.sh:1", err)

    def test_a_go_constant_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = _run(tmp, "daemon/internal/x.go", '\tdir := filepath.Join(p, "_harness")\n')
            self.assertEqual(rc, 1)
            self.assertIn("daemon/internal/x.go:1", err)

    def test_a_skill_doc_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "adapters/claude-code/skills/doctor/SKILL.md",
                            "state files [OK] vault-resident — <vault>/projects/<slug>/_harness/\n")
            self.assertEqual(rc, 1)

    def test_an_identifier_containing_the_word_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = _run(tmp, "scripts/x.py",
                              "root = resolve_harness_root()\n"
                              "_harness_dir = 1\n"
                              "import test_harness_memory\n")
            self.assertEqual(rc, 0, out)

    def test_the_repo_local_dot_harness_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "scripts/x.sh", 'bash "$ROOT/.harness/verify.sh"\n')
            self.assertEqual(rc, 0)


class TestAllowed(unittest.TestCase):
    def test_an_inline_marker_allows_the_line_and_only_that_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = _run(
                tmp, "scripts/health/eval.py",
                '    ("projects/agentm/_harness/designs/", "projects/agentm/designs/"),  '
                "# harness-deprecation: the frozen gold set's old spelling\n"
                'bad = "_harness"\n',
            )
            self.assertEqual(rc, 1)
            self.assertIn("scripts/health/eval.py:2", err)
            self.assertNotIn("scripts/health/eval.py:1", err)

    def test_a_file_marker_in_the_opening_allows_the_whole_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = ('"""A finished migration.\n\nharness-deprecation: file — it records the '
                    'layout of its day.\n"""\n' + 'SRC = "_harness"\n' * 5)
            rc, out, _ = _run(tmp, "scripts/migrate/old.py", body)
            self.assertEqual(rc, 0, out)

    def test_a_file_marker_buried_in_the_body_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = "x = 1\n" * 50 + "# harness-deprecation: file — too late\n" + 'y = "_harness"\n'
            rc, _, _ = _run(tmp, "scripts/late.py", body)
            self.assertEqual(rc, 1)

    def test_documentation_and_frozen_records_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rel in ("wiki/designs/x.md", "CHANGELOG.md",
                        "scripts/health/fixtures/gold.json",
                        "scripts/health/results/week1/a.json"):
                _write(tmp, rel, "projects/agentm/_harness/PLAN.md\n")
            rc, out, _ = _main(tmp)
            self.assertEqual(rc, 0, out)

    def test_test_files_are_not_scanned(self):
        """A test may build the old layout on purpose, to prove it is refused."""
        with tempfile.TemporaryDirectory() as tmp:
            for rel in ("scripts/test_x.py", "daemon/internal/x_test.go",
                        "daemon/internal/testdata/v.json"):
                _write(tmp, rel, '(root / "_harness").mkdir()\n')
            rc, out, _ = _main(tmp)
            self.assertEqual(rc, 0, out)

    def test_an_unscanned_suffix_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = _run(tmp, "assets/x.png", "_harness\n")
            self.assertEqual(rc, 0)


class TestTrackedOnly(unittest.TestCase):
    def test_in_a_work_tree_only_tracked_files_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True)
            except (OSError, subprocess.CalledProcessError):
                self.skipTest("git unavailable")
            _write(tmp, "scripts/tracked.py", 'a = "_harness"\n')
            _write(tmp, "scripts/untracked.py", 'b = "_harness"\n')
            subprocess.run(["git", "-C", tmp, "add", "scripts/tracked.py"], check=True)
            rc, _, err = _main(tmp)
            self.assertEqual(rc, 1)
            self.assertIn("scripts/tracked.py:1", err)
            self.assertNotIn("untracked.py", err)


class TestInventory(unittest.TestCase):
    def test_inventory_lists_every_hit_with_a_count_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "scripts/a.py", 'a = "_harness"\nb = "_harness"  # harness-deprecation: why\n')
            _write(tmp, "wiki/x.md", "_harness/\n")
            rc, out, _ = _main(tmp, "--inventory")
            self.assertEqual(rc, 0)
            self.assertIn("scripts/a.py:1", out)
            self.assertIn("scripts/a.py:2", out)
            self.assertIn("(allowed: marker)", out)
            self.assertIn("(allowed: documentation)", out)
            self.assertIn("1 literal(s) to retire in 1 file(s); 2 allowed in 2 file(s)", out)

    def test_a_clean_tree_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = _run(tmp, "scripts/a.py", "x = 1\n")
            self.assertEqual(rc, 0)
            self.assertIn("clean", out)

    def test_a_missing_root_is_a_setup_error(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = _mod.main(["--root", "/nonexistent/check-no-harness-paths"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
