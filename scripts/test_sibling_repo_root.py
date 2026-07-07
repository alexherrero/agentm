#!/usr/bin/env python3
"""Tests for sibling_repo_root.py -- the worktree-aware sibling-checkout
root resolver shared by check-slop.py and model_effort_routing_refresh.py.

stdlib only -- no pytest.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import sibling_repo_root

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent


class TestSiblingLayoutRoot(unittest.TestCase):
    def test_resolves_from_main_checkout(self):
        root = sibling_repo_root.sibling_layout_root(_REPO_ROOT)
        self.assertIsNotNone(root)
        self.assertTrue((root / "agentm").is_dir() or (root / "crickets").is_dir())

    def test_resolves_the_same_root_from_every_worktree(self):
        """The regression this module exists to fix: a `.claude/worktrees/<slug>`
        checkout must resolve to the SAME sibling-layout root as the main
        checkout, not to `.claude/worktrees/` itself."""
        proc = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        worktree_paths = [
            line.split(" ", 1)[1]
            for line in proc.stdout.splitlines()
            if line.startswith("worktree ")
        ]
        main_root = sibling_repo_root.sibling_layout_root(_REPO_ROOT)
        for wt in worktree_paths:
            wt_path = Path(wt)
            if not wt_path.is_dir():
                continue
            self.assertEqual(
                sibling_repo_root.sibling_layout_root(wt_path),
                main_root,
                f"worktree {wt_path} resolved a different sibling-layout root",
            )

    def test_returns_none_outside_a_git_repo(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(sibling_repo_root.sibling_layout_root(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
