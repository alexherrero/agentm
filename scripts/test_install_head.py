#!/usr/bin/env python3
"""The clone is the installation, so its git state is a deployment state.

Hooks and skills are symlinked into this clone, which makes whatever commit it
has checked out the machine-wide live configuration. Nothing said so. On
2026-09-06 a session's first minute ran `git checkout main` here and landed on
a ref 38 commits behind; the live config regressed two days, and a survey read
a stale design and published a false finding from it.

Three parts, all tested here. `refresh_local_main` levels the branch after
every release so a stray checkout is harmless; `check_install_head` says when
the clone's HEAD is not the newest tag or its `main` has fallen behind; and
`check_install_binary` says when the Go half has not followed the Python half,
because the symlinked hooks go live on a fast-forward while the binary only
moves when somebody rebuilds it.

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


class TheLiveTreeVersusWhatHasMerged(unittest.TestCase):
    """The case the other two checks were standing in for, and could not see.

    On 2026-09-08 the primary clone sat on a feature branch cut before a fix
    merged. For twenty-three hours every scheduled job ran that older tree, and
    the nightly retrieval-gate job re-created a directory the plan that fixed it
    had verified gone hours earlier. The row said OK throughout: the branch was
    *ahead* of the last tag, which is ordinary between releases, and local
    `main` was level with the remote.
    """

    def test_a_branch_cut_before_a_merge_warns_and_counts_what_is_missing(self):
        # The reproduction, in miniature.
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            git(work, "tag", "v1.0.0")
            git(work, "checkout", "-q", "-b", "feature")
            commit(work, "my own work")          # ahead of the tag
            commit(origin, "somebody else's fix")  # merges to main meanwhile
            commit(origin, "and another")
            git(work, "fetch", "-q", "origin")

            check = doctor_mod.check_install_head(work)

        self.assertEqual(check.status, "WARN", check.detail)
        self.assertIn("missing 2 commit(s)", check.detail)
        self.assertIn("feature", check.detail)
        self.assertIn("scheduled job", check.detail)

    def test_the_tag_comparison_cannot_see_this_and_the_main_one_misnames_it(self):
        """Why the new comparison was needed, stated exactly.

        The tag check is blind here: the branch is *ahead* of the newest tag,
        which is the ordinary state between releases. The local-`main` check
        does fire once the fix merges — but it reports a branch nobody has
        checked out, while the tree actually executing every hook and scheduled
        job goes unmentioned. Right signal, wrong ref, and the operator reads
        "a branch I never use is stale" rather than "you are running old code".
        """
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            git(work, "tag", "v1.0.0")
            git(work, "checkout", "-q", "-b", "feature")
            commit(work, "my own work")
            commit(origin, "somebody else's fix")
            git(work, "fetch", "-q", "origin")

            # Blind: HEAD is ahead of the newest tag, never behind it.
            self.assertEqual(git(work, "rev-list", "--count", "HEAD..v1.0.0"), "0")

            check = doctor_mod.check_install_head(work)

        # The old note is there, and it names `main` — a ref that is not
        # checked out and is not what the jobs are running.
        self.assertIn("local `main`", check.detail)
        # The new one names the branch that is.
        self.assertIn("feature", check.detail)
        self.assertIn("older code", check.detail)

    def test_detached_at_origin_main_is_quiet(self):
        # The normal state of the primary clone. A commit is its own ancestor.
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            git(work, "tag", "v1.0.0")
            commit(origin, "two")
            git(work, "fetch", "-q", "origin")
            git(work, "checkout", "-q", "--detach", "origin/main")

            check = doctor_mod.check_install_head(work)

        self.assertNotIn("missing", check.detail)

    def test_being_ahead_of_origin_main_is_quiet(self):
        # Mid-release: the commit is made detached here and pushed after.
        with tempfile.TemporaryDirectory() as td:
            _, work = clone_pair(Path(td))
            git(work, "tag", "v1.0.0")
            git(work, "checkout", "-q", "--detach", "origin/main")
            commit(work, "the release commit, not yet pushed")

            check = doctor_mod.check_install_head(work)

        self.assertNotIn("missing", check.detail)
        self.assertEqual(check.status, "OK", check.detail)

    def test_a_branch_cut_from_current_main_is_quiet(self):
        # Ordinary feature work on an up-to-date tree is not a finding.
        with tempfile.TemporaryDirectory() as td:
            _, work = clone_pair(Path(td))
            git(work, "tag", "v1.0.0")
            git(work, "checkout", "-q", "-b", "feature")
            commit(work, "my own work")

            check = doctor_mod.check_install_head(work)

        self.assertNotIn("missing", check.detail)

    def test_a_detached_head_that_is_stale_says_this_checkout(self):
        # No branch name to print, and "HEAD" would be a useless one.
        with tempfile.TemporaryDirectory() as td:
            origin, work = clone_pair(Path(td))
            git(work, "tag", "v1.0.0")
            stale = git(work, "rev-parse", "HEAD")
            commit(origin, "two")
            git(work, "fetch", "-q", "origin")
            git(work, "checkout", "-q", "--detach", stale)

            check = doctor_mod.check_install_head(work)

        self.assertEqual(check.status, "WARN", check.detail)
        self.assertIn("this checkout", check.detail)
        self.assertNotIn("`HEAD`", check.detail)

    def test_no_remote_main_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "solo"
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "one")
            git(repo, "tag", "v1.0.0")

            check = doctor_mod.check_install_head(repo)

        self.assertNotIn("missing", check.detail)


class TheGoDurationParser(unittest.TestCase):
    """The daemon reports its uptime in Go's format, and the row reads it to
    tell a rebuild-without-restart from an ordinary one. A parser that sums
    the parts it recognises and ignores the rest reads `nonsense` as zero
    seconds, which the row would take for a daemon that started this instant."""

    def test_the_shapes_a_running_daemon_actually_prints(self):
        for text, want in (("20m15s", 1215.0), ("3h4m5s", 11045.0),
                           ("800ms", 0.8), ("45.2s", 45.2), ("0", 0.0)):
            with self.subTest(text=text):
                self.assertEqual(doctor_mod._parse_go_duration(text), want)

    def test_anything_that_is_not_a_duration_is_refused(self):
        for text in ("nonsense", "", "12x", "20m 15s", "m15s"):
            with self.subTest(text=text):
                self.assertIsNone(doctor_mod._parse_go_duration(text))


class TheResidentBinary(unittest.TestCase):
    """A commit touching both `daemon/` and `harness/` half-deploys: the
    symlinked Python goes live on the fast-forward, the binary does not.
    Every fixture is a real repository with a real file on disk, because the
    row's whole subject is what git and the filesystem report."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "daemon").mkdir()
        self.binary = self.tmp / "agentmd"
        self.binary.write_bytes(b"binary")

    def _commit_daemon(self, message: str) -> None:
        (self.repo / "daemon" / "main.go").write_text(message, encoding="utf-8")
        git(self.repo, "add", "daemon/main.go")
        git(self.repo, "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-q", "-m", message)

    def _touch(self, when: float) -> None:
        import os
        os.utime(self.binary, (when, when))

    def _commit_time(self) -> int:
        return int(git(self.repo, "log", "-1", "--format=%ct", "--", "daemon"))

    def test_a_binary_built_after_the_newest_daemon_commit_is_current(self):
        self._commit_daemon("one")
        built = self._commit_time() + 60
        self._touch(built)
        check = doctor_mod.check_install_binary(
            self.repo, binary=self.binary, now=built + 20, uptime_seconds=10)
        self.assertEqual(check.status, "OK", check.detail)

    def test_a_binary_older_than_the_daemon_source_warns(self):
        self._commit_daemon("one")
        self._touch(self._commit_time() - 3600)
        check = doctor_mod.check_install_binary(
            self.repo, binary=self.binary, now=self._commit_time() + 20,
            uptime_seconds=10)
        self.assertEqual(check.status, "WARN")
        self.assertIn("rebuild", check.detail)

    def test_a_commit_touching_only_the_python_half_does_not_warn(self):
        # The asymmetry itself: the symlinked half is live already, and the
        # binary is not owed a rebuild for it.
        self._commit_daemon("one")
        built = self._commit_time() + 60
        self._touch(built)
        (self.repo / "harness").mkdir()
        (self.repo / "harness" / "hook.sh").write_text("later", encoding="utf-8")
        git(self.repo, "add", "harness/hook.sh")
        git(self.repo, "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-q", "-m", "python only")
        check = doctor_mod.check_install_binary(
            self.repo, binary=self.binary, now=built + 20, uptime_seconds=10)
        self.assertEqual(check.status, "OK", check.detail)

    def test_a_rebuild_without_a_restart_warns(self):
        # `agentmd status` reports the old process as healthy in this state,
        # so the uptime is the only tell.
        self._commit_daemon("one")
        built = self._commit_time() + 60
        self._touch(built)
        check = doctor_mod.check_install_binary(
            self.repo, binary=self.binary, now=built + 10, uptime_seconds=100)
        self.assertEqual(check.status, "WARN")
        self.assertIn("without a restart", check.detail)

    def test_a_restart_after_the_build_is_current(self):
        self._commit_daemon("one")
        built = self._commit_time() + 60
        self._touch(built)
        check = doctor_mod.check_install_binary(
            self.repo, binary=self.binary, now=built + 100, uptime_seconds=50)
        self.assertEqual(check.status, "OK", check.detail)

    def test_a_daemon_that_cannot_be_asked_is_not_a_failure(self):
        # The daemon may simply be down; other rows own that.
        self._commit_daemon("one")
        self._touch(self._commit_time() + 60)
        check = doctor_mod.check_install_binary(self.repo, binary=self.binary)
        self.assertIn(check.status, ("OK", "WARN"))

    def test_no_installed_binary_is_unverified_rather_than_a_pass(self):
        self._commit_daemon("one")
        check = doctor_mod.check_install_binary(
            self.repo, binary=self.tmp / "absent", uptime_seconds=10)
        self.assertEqual(check.status, "UNVERIFIED")

    def test_a_tree_with_no_daemon_is_unverified(self):
        import shutil as _shutil
        _shutil.rmtree(self.repo / "daemon")
        check = doctor_mod.check_install_binary(
            self.repo, binary=self.binary, uptime_seconds=10)
        self.assertEqual(check.status, "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
