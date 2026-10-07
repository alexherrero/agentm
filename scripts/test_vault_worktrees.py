#!/usr/bin/env python3
"""The doctor and the morning note name a git worktree in the vault the same day.

On 2026-10-02 a worktree made inside the vault left `extensions.worktreeConfig`
in its repository's config, and the daemon stopped committing at its next
restart a day later (#859). The vault-worktree-guard hook refuses the tool calls
that make one. This is the detection for the routes no hook reaches. Each of
the three signs is seeded in a throwaway vault and must turn the doctor's
`vault-worktrees` row FAIL and put a line in the morning note's "What needs
you". Removing the sign must clear both. The empty `.claude/worktrees/` the live
vault carries from 2026-08-16 must not count.

Run: python3 scripts/test_vault_worktrees.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import machinery_doctor as doctor_mod  # noqa: E402
import morning_note  # noqa: E402
import vault_worktrees  # noqa: E402


class _Vault(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self._tmp.name))
        empty = self.root / "gitconfig"
        empty.write_text("", encoding="utf-8")
        self._env = dict(os.environ)
        os.environ.update({"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"})
        for key in ("GIT_DIR", "GIT_WORK_TREE"):
            os.environ.pop(key, None)
        # The live layout: the git directory outside the vault, behind a pointer file.
        self.vault = self.root / "vault"
        (self.vault / "agent").mkdir(parents=True)
        (self.vault / "agent" / "note.md").write_text("# note\n", encoding="utf-8")
        (self.root / "vault-git").mkdir()
        self.git("init", "-q", f"--separate-git-dir={self.root / 'vault-git' / 'vault.git'}", str(self.vault),
                 cwd=self.root)
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()

    def git(self, *args: str, cwd: Path = None) -> None:
        subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                       cwd=str(cwd or self.vault), check=True, capture_output=True)

    def row(self):
        return doctor_mod.check_vault_worktrees(vault=self.vault)

    def needs(self) -> list:
        night = morning_note.Night(now=time.time(), start=time.time())
        night.worktrees = vault_worktrees.describe(vault_worktrees.inspect(self.vault))
        return morning_note.needs_you(night)

    def assertNamed(self, *fragments: str) -> None:
        row = self.row()
        self.assertEqual(row.status, "FAIL", row.detail)
        needs = self.needs()
        self.assertTrue(needs and needs[0].startswith("- **A git worktree is in the vault**"), needs)
        for fragment in fragments:
            self.assertIn(fragment, row.detail)
            self.assertIn(fragment, needs[0])

    def assertClear(self) -> None:
        row = self.row()
        self.assertEqual(row.status, "OK", row.detail)
        self.assertEqual(self.needs(), [])


class EachSignIsNamedAndClears(_Vault):

    def test_a_clean_vault_is_ok_and_the_empty_folder_left_at_its_root_is_not_a_sign(self):
        self.assertClear()
        (self.vault / ".claude" / "worktrees").mkdir(parents=True)
        self.assertClear()

    def test_a_registered_worktree(self):
        outside = self.root / "elsewhere"
        self.git("worktree", "add", "-q", "--detach", str(outside))
        self.assertNamed(f"`{outside}`")
        self.git("worktree", "remove", "--force", str(outside))
        self.assertClear()

    def test_a_registered_worktree_whose_directory_is_gone_is_prunable(self):
        outside = self.root / "gone"
        self.git("worktree", "add", "-q", "--detach", str(outside))
        shutil.rmtree(outside)
        self.assertNamed("prunable")
        self.assertIn("git worktree prune", self.row().detail)
        self.git("worktree", "prune")
        self.assertClear()

    def test_a_non_empty_claude_worktrees_folder_anywhere_under_the_vault(self):
        # The 2026-10-02 shape: a project folder's own `.claude/worktrees/`.
        folder = self.vault / "projects" / "pixelton" / ".claude" / "worktrees"
        (folder / "laughing-bassi-06d858").mkdir(parents=True)
        self.assertNamed("projects/pixelton/.claude/worktrees/")
        shutil.rmtree(folder / "laughing-bassi-06d858")
        self.assertClear()

    def test_an_extensions_key_in_the_repository_config(self):
        self.git("config", "extensions.worktreeConfig", "true")
        self.assertNamed("extensions.worktreeconfig=true", "#859")
        self.git("config", "--unset", "extensions.worktreeConfig")
        self.assertClear()


class WhenItCannotLook(unittest.TestCase):

    def test_a_vault_that_is_not_a_repository_is_unverified_not_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = doctor_mod.check_vault_worktrees(vault=Path(tmp))
        self.assertEqual(row.status, "UNVERIFIED", row.detail)

    def test_no_vault_configured_is_unverified(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(os.environ, {"HOME": tmp, "AGENTM_INSTALL_PREFIX": tmp}):
            os.environ.pop("MEMORY_ROOT", None)
            os.environ.pop("MEMORY_VAULT_PATH", None)
            row = doctor_mod.check_vault_worktrees()
        self.assertEqual(row.status, "UNVERIFIED", row.detail)

    def test_the_row_is_part_of_the_inventory(self):
        import inspect as pyinspect
        self.assertIn("check_vault_worktrees()", pyinspect.getsource(doctor_mod.run_inventory))


if __name__ == "__main__":
    unittest.main()
