#!/usr/bin/env python3
"""`/memory search --deep` (agentm-vault plan 11, task 8).

The question is "where did that note go?", and the answer has three places:
the archive, the deletion manifests, and the vault's git history. The
verification bar the plan set is the third one — *the deep search names the
commit a deleted note lives in* — so that is tested against a real repository
with a real deletion, not against a stubbed `git`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import deep_search as ds  # noqa: E402


def _git(cwd, *args):
    env = dict(os.environ,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True, env=env).stdout


class _Vault(unittest.TestCase):
    """A vault with a memory root under it, which is the shipped layout: the
    manifests' paths are memory-root-relative and git's are vault-relative, and
    the two bases not being the same is the mistake this module exists around.
    """

    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="deep-search-"))
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        self.vault = self.top / "vault"
        self.root = self.vault / "agent"
        (self.root / "memory" / "semantic").mkdir(parents=True)
        _git(self.top, "init", "-q", str(self.vault))

    def _note(self, rel: str, body: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\ntitle: {Path(rel).stem}\n---\n\n{body}\n", encoding="utf-8")
        return p

    def _manifest(self, stamp: str, rows: list) -> Path:
        d = self.root / ds.PURGE_DIR / stamp
        d.mkdir(parents=True, exist_ok=True)
        p = d / "manifest.json"
        p.write_text(json.dumps({
            "written": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T03:11:00Z",
            "run_id": "r1", "by": "the night's lifecycle job",
            "count": len(rows), "rows": rows}), encoding="utf-8")
        return p

    def _commit(self, message: str):
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", message)


class TheArchive(_Vault):
    def test_an_archived_note_is_found_and_named(self):
        self._note("archive/memory/semantic/old-widget.md",
                   "the widget subsystem retry logic")
        rep = ds.search(self.root, self.vault, "widget retry")
        self.assertEqual([h["rel"] for h in rep["archived"]],
                         ["archive/memory/semantic/old-widget.md"])
        self.assertEqual(rep["archived"][0]["title"], "old-widget")
        # The excerpt comes from the body. Drawn from the frontmatter it would
        # print `title: old-widget` under a hit for "widget" — the note being
        # labelled, not the note saying anything.
        self.assertEqual(rep["archived"][0]["excerpt"],
                         "the widget subsystem retry logic")

    def test_a_live_note_is_not_an_answer_to_this_question(self):
        """Deep search is about what is *not* live. A live note is what ordinary
        search is for, and returning it here would make the two commands say the
        same thing in different words."""
        self._note("memory/semantic/live-widget.md", "the widget subsystem")
        rep = ds.search(self.root, self.vault, "widget")
        self.assertEqual(rep["archived"], [])
        self.assertEqual(rep["deleted"], [])

    def test_every_word_has_to_match(self):
        """"Where did it go" is not a relevance question. A note that carries
        some of the words is not a weaker answer, it is a different note."""
        self._note("archive/memory/semantic/a.md", "widgets and sprockets")
        self.assertEqual(len(ds.archive_hits(self.root, "widgets sprockets")), 1)
        self.assertEqual(ds.archive_hits(self.root, "widgets flanges"), [])


class TheManifests(_Vault):
    def test_a_deleted_note_is_named_with_what_the_manifest_recorded(self):
        self._manifest("20260901T031100Z", [
            {"rel": "memory/semantic/gone-widget.md", "title": "the widget rule",
             "lifecycle": "archived", "since": "2021-01-01", "days": 2600.0,
             "sha256": "abc123"},
        ])
        rep = ds.search(self.root, self.vault, "widget rule")
        self.assertEqual(len(rep["deleted"]), 1)
        row = rep["deleted"][0]
        self.assertEqual(row["rel"], "memory/semantic/gone-widget.md")
        self.assertEqual(row["days_silent"], 2600.0)
        self.assertEqual(row["sha256"], "abc123")
        self.assertIn("20260901T031100Z", row["manifest"])

    def test_the_newest_manifest_is_read_first(self):
        for stamp in ("20260101T000000Z", "20260901T000000Z"):
            self._manifest(stamp, [
                {"rel": f"memory/semantic/widget-{stamp[:4]}.md",
                 "title": "widget", "days": 2600.0, "sha256": ""},
            ])
        rep = ds.search(self.root, self.vault, "widget")
        self.assertIn("20260901T000000Z", rep["deleted"][0]["manifest"])


class TheGitHistory(_Vault):
    """The plan's own bar: the deep search names the commit a deleted note
    lives in."""

    def _delete_a_committed_note(self) -> str:
        rel = "memory/semantic/gone-widget.md"
        self._note(rel, "the widget rule: never back-merge")
        self._commit("add the widget rule")
        (self.root / rel).unlink()
        self._commit("the night forgot it")
        self._manifest("20260901T031100Z", [
            {"rel": rel, "title": "the widget rule", "days": 2600.0, "sha256": ""},
        ])
        return rel

    def test_it_names_the_commit_and_the_show_that_recovers_the_file(self):
        self._delete_a_committed_note()
        rep = ds.search(self.root, self.vault, "widget rule")
        git = rep["deleted"][0]["git"]
        self.assertTrue(git["available"], git.get("why"))
        self.assertEqual(len(git["deleted_in"]), 40)
        self.assertEqual(git["subject"], "the night forgot it")

        # And the command it prints actually prints the note. A sha and a path
        # a reader has to assemble themselves is a recipe for printing nothing
        # and concluding the note is gone.
        out = subprocess.run(git["show"], shell=True, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("never back-merge", out.stdout)

    def test_the_manifests_base_is_translated_before_git_is_asked(self):
        """A manifest's path is memory-root-relative; git wants it from the
        vault root. Asking git the wrong one answers "no commit deletes that
        path" for a note git is holding — a false negative that reads exactly
        like a true one."""
        rel = self._delete_a_committed_note()
        self.assertEqual(ds._vault_rel(self.root, self.vault, rel),
                         f"agent/{rel}")
        # The wrong base finds nothing, which is what makes the right one a
        # claim rather than a coincidence.
        self.assertFalse(ds.commit_for(self.vault, rel)["available"])
        self.assertTrue(ds.commit_for(self.vault, f"agent/{rel}")["available"])

    def test_a_vault_that_is_not_a_repository_says_so(self):
        plain = self.top / "plain"
        (plain / "memory").mkdir(parents=True)
        got = ds.commit_for(plain, "memory/semantic/a.md")
        self.assertFalse(got["available"])
        self.assertIn("git repository", got["why"])


class TheReport(_Vault):
    def test_nothing_anywhere_is_a_real_answer(self):
        rep = ds.search(self.root, self.vault, "nothing like this")
        text = ds.render(rep)
        self.assertIn("never under that name here", text)

    def test_the_rendering_names_the_place_each_hit_came_from(self):
        self._note("archive/memory/semantic/old-widget.md", "the widget rule")
        self._manifest("20260901T031100Z", [
            {"rel": "memory/semantic/gone-widget.md", "title": "the widget rule",
             "days": 2600.0, "sha256": ""}])
        text = ds.render(ds.search(self.root, self.vault, "widget rule"))
        self.assertIn("In the archive (1)", text)
        self.assertIn("Deleted (1)", text)
        self.assertIn("2600.0 silent day(s)", text)


if __name__ == "__main__":
    unittest.main()
