#!/usr/bin/env python3
"""`scripts/migrate/projects_merge_2b.sh` — apply semantics on a scratch tree.

Pins what the live 2b apply relied on and what the pre-tag review found
untested: the dry run is the default and writes nothing; a directory arriving
on a directory merges per entry with the source winning a same-name file and
destination-only files surviving; a type clash (a file arriving on a
directory, or the reverse) is refused with both copies left in place rather
than `rm -rf`'d; and a flat vault — the memory root at the top of its own
Obsidian vault — has nothing to merge upward and is refused before anything
moves into whatever `Projects/` sits beside it.

POSIX-gated: the script is bash, and Windows CI's bash is a WSL stub.

Run: python3 scripts/test_projects_merge_migration.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if sys.platform == "win32":
    raise unittest.SkipTest("bash migration script — POSIX only")

_HERE = Path(__file__).resolve().parent
SCRIPT = _HERE / "migrate" / "projects_merge_2b.sh"


def _tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    return out


def _nested(tmp: Path) -> tuple:
    """A nested vault mid-merge: desk still holds `a/` and `b/`, the root
    shell already holds the operator's index, a stale copy of one `b` file,
    and one root-only file."""
    vault_root = tmp / "Vault"
    (vault_root / ".obsidian").mkdir(parents=True)
    mem = vault_root / "Agent"
    (mem / "desk" / "projects" / "a").mkdir(parents=True)
    (mem / "desk" / "projects" / "a" / "x.md").write_text("a/x from desk\n", encoding="utf-8")
    (mem / "desk" / "projects" / "b" / "nested").mkdir(parents=True)
    (mem / "desk" / "projects" / "b" / "nested" / "y.md").write_text("b/nested/y from desk\n", encoding="utf-8")
    (mem / "desk" / "labelling").mkdir(parents=True)
    (mem / "desk" / "labelling" / "l.md").write_text("label\n", encoding="utf-8")
    (vault_root / "Projects" / "b" / "nested").mkdir(parents=True)
    (vault_root / "Projects" / "index.md").write_text("operator index\n", encoding="utf-8")
    (vault_root / "Projects" / "b" / "nested" / "y.md").write_text("b/nested/y stale root copy\n", encoding="utf-8")
    (vault_root / "Projects" / "b" / "nested" / "z.md").write_text("root-only z\n", encoding="utf-8")
    return vault_root, mem


def _run(mem: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, MEMORY_VAULT_PATH=str(mem))
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)


class DryRunIsTheDefault(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            vault_root, mem = _nested(Path(td))
            before = _tree(vault_root)
            r = _run(mem)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("would:", r.stdout)
            self.assertEqual(_tree(vault_root), before)


class ApplyMergesWithTheSourceWinning(unittest.TestCase):
    def test_move_merge_and_labelling(self):
        with tempfile.TemporaryDirectory() as td:
            vault_root, mem = _nested(Path(td))
            r = _run(mem, "--apply")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            after = _tree(vault_root)
            self.assertEqual(after["Projects/a/x.md"], "a/x from desk\n")
            # A same-name file: the moving copy is the last production write.
            self.assertEqual(after["Projects/b/nested/y.md"], "b/nested/y from desk\n")
            # A destination-only file inside a merged directory survives.
            self.assertEqual(after["Projects/b/nested/z.md"], "root-only z\n")
            self.assertEqual(after["Projects/index.md"], "operator index\n")
            self.assertEqual(after["Projects/agentm/labelling/l.md"], "label\n")
            self.assertFalse((mem / "desk" / "projects").exists())
            self.assertFalse((mem / "desk" / "labelling").exists())


class TypeClashIsRefused(unittest.TestCase):
    def _clash(self, tmp: Path) -> tuple:
        vault_root, mem = _nested(tmp)
        (mem / "desk" / "projects" / "badname").write_text("a stray file\n", encoding="utf-8")
        precious = vault_root / "Projects" / "badname" / "nested" / "precious.md"
        precious.parent.mkdir(parents=True)
        precious.write_text("keep me\n", encoding="utf-8")
        return vault_root, mem, precious

    def test_a_file_never_replaces_a_directory(self):
        with tempfile.TemporaryDirectory() as td:
            vault_root, mem, precious = self._clash(Path(td))
            r = _run(mem, "--apply")
            self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
            self.assertIn("type clash", r.stderr)
            self.assertEqual(precious.read_text(encoding="utf-8"), "keep me\n")
            self.assertTrue((mem / "desk" / "projects" / "badname").is_file())

    def test_the_dry_run_reports_the_clash_before_any_apply(self):
        with tempfile.TemporaryDirectory() as td:
            vault_root, mem, precious = self._clash(Path(td))
            before = _tree(vault_root)
            r = _run(mem)
            self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
            self.assertIn("type clash", r.stderr)
            self.assertEqual(_tree(vault_root), before)


class FlatVaultHasNothingToMerge(unittest.TestCase):
    def test_refuses_when_the_memory_root_is_the_vault_itself(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            vault = home / "Vault"
            (vault / ".obsidian").mkdir(parents=True)
            (vault / "desk" / "projects" / "a").mkdir(parents=True)
            (home / "Projects").mkdir()  # the operator's own, beside the vault
            r = _run(vault, "--apply")
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
            self.assertIn("not nested", r.stderr)
            self.assertTrue((vault / "desk" / "projects" / "a").is_dir())
            self.assertEqual(list((home / "Projects").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
