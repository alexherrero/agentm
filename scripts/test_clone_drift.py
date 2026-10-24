#!/usr/bin/env python3
"""How far the primary clone is behind origin/main (#815), read locally.

`clone_drift` is the one reading the runner's cycle record, the doctor and the
morning note share. The repositories here are scratch clones with the machine's
global git config shut out, because a scratch repo otherwise inherits its
`hooksPath`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import clone_drift  # noqa: E402

_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(cwd: Path, name: str) -> None:
    (cwd / name).write_text(name, encoding="utf-8")
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", name)


def clone_pair(root: Path) -> tuple:
    """A bare origin, the clone under test, and a second clone that merges."""
    seed, origin, work, other = (root / n for n in ("seed", "origin", "work", "other"))
    for d in (seed, work, other):
        d.mkdir()
    git(seed, "init", "-q", "-b", "main")
    commit(seed, "a.md")
    git(root, "clone", "-q", "--bare", str(seed), str(origin))
    git(work, "clone", "-q", str(origin), ".")
    git(other, "clone", "-q", str(origin), ".")
    return work, other


class ReadingTheClone(unittest.TestCase):

    def test_behind_by_two_names_the_newest_missing(self):
        with tempfile.TemporaryDirectory() as td:
            work, other = clone_pair(Path(td))
            commit(other, "b.md")
            commit(other, "the fix.md")
            git(other, "push", "-q", "origin", "main")
            git(work, "fetch", "-q", "origin")
            d = clone_drift.read(work)
        self.assertEqual((d.behind, d.ahead, d.error), (2, 0, None))
        self.assertEqual(d.newest_missing_subject, "the fix.md")
        s = clone_drift.sentence(d)
        self.assertTrue(s.startswith("the primary clone is 2 commits behind main; the newest it lacks is "), s)
        self.assertEqual(d.as_dict()["behind"], 2)

    def test_level_says_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            work, _ = clone_pair(Path(td))
            d = clone_drift.read(work)
        self.assertEqual((d.behind, d.ahead), (0, 0))
        self.assertIsNone(clone_drift.sentence(d))
        self.assertIsNone(clone_drift.note(d))

    def test_a_branch_ahead_is_a_note_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            work, _ = clone_pair(Path(td))
            git(work, "checkout", "-q", "-b", "feature")
            commit(work, "mine.md")
            d = clone_drift.read(work)
        self.assertIsNone(d.error)
        self.assertIsNone(clone_drift.sentence(d))
        self.assertEqual(clone_drift.note(d), "the primary clone is on branch `feature` and 1 commit ahead of main")

    def test_no_clone_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            d = clone_drift.read(Path(td))
        self.assertIsNotNone(d.error)
        self.assertIsNone(d.behind)


if __name__ == "__main__":
    unittest.main()
