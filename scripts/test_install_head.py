#!/usr/bin/env python3
"""The clone is the installation, so its git state is a deployment state.

Hooks and skills are symlinked into this clone, which makes whatever commit it
has checked out the machine-wide live configuration. Nothing said so. On
2026-09-06 a session's first minute ran `git checkout main` here and landed on
a ref 38 commits behind; the live config regressed two days, and a survey read
a stale design and published a false finding from it.

Two halves, both tested here. `refresh_local_main` levels the branch after
every release so a stray checkout is harmless, and `check_install_head` says
when the clone's HEAD is not the newest tag or its `main` has fallen behind.

Every fixture is a real git repository. The behaviour under test is entirely
about what git reports, and a mocked git would only prove the mock agrees with
itself.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import machinery_doctor as doctor_mod  # noqa: E402
import orchestration_phase as phase_mod  # noqa: E402


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, check=True)
    return r.stdout.strip()


def commit(repo: Path, message: str) -> str:
    (repo / "f.txt").write_text(message, encoding="utf-8")
    git(repo, "add", "f.txt")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def clone_pair(tmp: Path) -> "tuple[Path, Path]":
    """An origin and a clone of it, the shape the primary checkout has."""
    origin = tmp / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    commit(origin, "one")

    work = tmp / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True,
                   capture_output=True)
    return origin, work


class TheFastForward(unittest.TestCase):
    def test_it_levels_a_stale_local_main(self):
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            stale = git(work, "rev-parse", "refs/heads/main")
            commit(origin, "two")
            git(work, "fetch", "-q", "origin")
            # The primary is kept detached, which is why `main` goes stale
            # unnoticed: nothing is standing on it.
            git(work, "checkout", "-q", "--detach", "origin/main")
            self.assertEqual(git(work, "rev-parse", "refs/heads/main"), stale)

            out = phase_mod.refresh_local_main(work)

            self.assertEqual(out["status"], "fast-forwarded")
            self.assertEqual(git(work, "rev-parse", "refs/heads/main"),
                             git(work, "rev-parse", "refs/remotes/origin/main"))

    def test_a_checkout_of_main_is_then_harmless(self):
        # The whole point. Nothing here stops the checkout; it makes the
        # checkout land on the same tree the detached HEAD was on.
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            commit(origin, "two")
            git(work, "fetch", "-q", "origin")
            git(work, "checkout", "-q", "--detach", "origin/main")
            detached = git(work, "rev-parse", "HEAD")

            phase_mod.refresh_local_main(work)
            git(work, "checkout", "-q", "main")

            self.assertEqual(git(work, "rev-parse", "HEAD"), detached)

    def test_it_is_a_no_op_when_already_level(self):
        with tempfile.TemporaryDirectory() as td:
            _, work = clone_pair(Path(td))
            self.assertEqual(phase_mod.refresh_local_main(work)["status"],
                             "already-current")

    def test_it_refuses_to_discard_local_commits(self):
        # A `main` holding unpushed work is somebody's work, not staleness.
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            commit(origin, "remote-two")
            git(work, "fetch", "-q", "origin")
            git(work, "checkout", "-q", "main")
            mine = commit(work, "mine")
            git(work, "checkout", "-q", "--detach", "HEAD")

            out = phase_mod.refresh_local_main(work)

            self.assertEqual(out["status"], "diverged")
            self.assertEqual(git(work, "rev-parse", "refs/heads/main"), mine)

    def test_it_refuses_while_the_branch_is_checked_out(self):
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            commit(origin, "two")
            git(work, "fetch", "-q", "origin")
            git(work, "checkout", "-q", "main")

            self.assertEqual(phase_mod.refresh_local_main(work)["status"],
                             "checked-out")

    def test_a_worktree_holding_main_also_blocks_it(self):
        # The stranded-worktree state. Forcing the ref out from under a
        # worktree standing on it is not a refresh.
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            commit(origin, "two")
            git(work, "fetch", "-q", "origin")
            git(work, "checkout", "-q", "--detach", "origin/main")
            slot = Path(td) / "slot"
            git(work, "worktree", "add", "-q", str(slot), "main")

            self.assertEqual(phase_mod.refresh_local_main(work)["status"],
                             "checked-out")

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            stale = git(work, "rev-parse", "refs/heads/main")
            commit(origin, "two")
            git(work, "fetch", "-q", "origin")
            git(work, "checkout", "-q", "--detach", "origin/main")

            out = phase_mod.refresh_local_main(work, dry_run=True)

            self.assertEqual(out["status"], "dry-run")
            self.assertEqual(git(work, "rev-parse", "refs/heads/main"), stale)

    def test_a_repo_without_the_remote_branch_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "solo"
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "one")
            self.assertEqual(phase_mod.refresh_local_main(repo)["status"],
                             "no-remote-branch")


class TheDoctorsInstallRow(unittest.TestCase):
    def test_head_on_the_newest_tag_is_ok(self):
        with tempfile.TemporaryDirectory() as td:
            _, work = clone_pair(Path(td))
            git(work, "tag", "v1.0.0")
            check = doctor_mod.check_install_head(work)
        self.assertEqual(check.status, "OK")
        self.assertIn("v1.0.0", check.detail)

    def test_a_stale_local_main_warns_and_says_what_it_would_cost(self):
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            git(work, "tag", "v1.0.0")
            commit(origin, "two")
            git(origin, "tag", "v1.1.0")
            git(work, "fetch", "-q", "--tags", "origin")
            git(work, "checkout", "-q", "--detach", "origin/main")

            check = doctor_mod.check_install_head(work)

        self.assertEqual(check.status, "WARN")
        self.assertIn("behind origin/main", check.detail)
        self.assertIn("roll the live config back", check.detail)

    def test_being_ahead_of_the_tag_alone_is_not_a_warning(self):
        # Ordinary between releases. A row that cried wolf every day would be
        # read the same way the watchdog's JSON file was: not at all.
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            git(origin, "tag", "v1.0.0")          # the release
            commit(origin, "two")                 # merged since
            git(work, "fetch", "-q", "--tags", "origin")
            git(work, "merge", "-q", "--ff-only", "origin/main")

            check = doctor_mod.check_install_head(work)

        self.assertEqual(check.status, "OK")
        self.assertIn("ahead", check.detail)

    def test_no_tag_is_unverified_not_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            _, work = clone_pair(Path(td))
            check = doctor_mod.check_install_head(work)
        self.assertEqual(check.status, "UNVERIFIED")

    def test_a_non_repository_is_unverified(self):
        with tempfile.TemporaryDirectory() as td:
            check = doctor_mod.check_install_head(Path(td))
        self.assertEqual(check.status, "UNVERIFIED")

    def test_the_row_is_in_the_inventory(self):
        names = [c.name for c in doctor_mod.run_inventory(_REPO)]
        self.assertIn("install-head", names)


if __name__ == "__main__":
    unittest.main()
