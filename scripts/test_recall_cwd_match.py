#!/usr/bin/env python3
"""A project's `recall_cwd_match:` keeps its notes to sessions in its folders.

The other direction from `recall_excludes` (test_recall_exclusion.py), and the
same doors: the real hook scripts with a real payload, `recall.py query`, deep
search, the in-process engine and the rule itself. Each door is checked from
inside the folder, from outside it, and for an unmarked project, because a wall
that also hid everyone else's notes would pass a test that only looked for the
marked one.

The fixture is test_recall_exclusion's vault (where `clean` excludes `port`)
with one more project, `secret`, whose notes are served only in folders that
contain `/secret-mono/`.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import test_recall_exclusion as base  # noqa: E402
from test_recall_exclusion import _SCRIPTS, PROMPT, recall, recall_exclusion  # noqa: E402

SECRET_NOTE = "projects/secret/research/zoning-floodplain-secret.md"
SECRET_CARD = "agent/memory/semantic/zoning-floodplain-secret-card.md"
RULE = "slug: secret\ncode_paths:\n  - {code}\nrecall_cwd_match:\n  - /secret-mono/\n"


class _Secret(base._Hooks):
    def setUp(self):
        super().setUp()
        self.inside = self.root / "code" / "secret-mono"
        (self.inside / "web").mkdir(parents=True)
        self.write_secret(RULE.format(code=self.inside))
        self.write(SECRET_NOTE, f"---\nkind: reference\ntitle: t\n---\n{base.BODY} secret-folder\n")
        self.write(SECRET_CARD, f"---\nkind: reference\ntitle: t\nproject: secret\n---\n{base.BODY} secret-stamped\n")
        base._fake_agentmd(self.bin, [
            {"path": SECRET_NOTE, "score": 9.5},
            {"path": SECRET_CARD, "score": 9.2, "project": "secret"},
            {"path": base.PORT_NOTE, "score": 9.0},
            {"path": base.KEPT_NOTE, "score": 5.0},
        ])

    def write_secret(self, text):
        self.write("projects/secret/project.yaml", text)


class TheRule(_Secret):
    def test_inside_the_folder_nothing_is_walled(self):
        for directory in (self.inside, self.inside / "web", Path(str(self.inside).upper())):
            with self.subTest(directory=directory):
                self.assertEqual(self.exclusion(directory), recall_exclusion.NONE)

    def test_outside_it_the_project_is_walled(self):
        for directory in (self.other, self.root, self.vault / "projects" / "secret",
                          self.root / "code" / "secret-mono-old"):
            with self.subTest(directory=directory):
                walls = self.exclusion(directory)
                self.assertEqual(walls.slugs, ("secret",))
                self.assertTrue(walls.hides(SECRET_NOTE))
                self.assertTrue(walls.hides("memory/a.md", "secret"))
                self.assertFalse(walls.hides(base.PORT_NOTE))

    def test_a_linked_worktree_elsewhere_is_inside(self):
        slot = self.root / "slots" / "w1"
        slot.mkdir(parents=True)
        (slot / ".git").write_text(f"gitdir: {self.inside}/.git/worktrees/w1\n", encoding="utf-8")
        self.assertEqual(self.exclusion(slot), recall_exclusion.NONE)

    def test_a_session_nobody_can_place_is_outside(self):
        for directory in (None, "", "  "):
            with self.subTest(directory=directory):
                self.assertIn("secret", recall_exclusion.for_session(directory, self.memory).slugs)

    def test_it_adds_to_a_projects_own_rule(self):
        walls = self.exclusion(self.code)
        self.assertEqual(walls.slugs, ("port", "secret"))
        self.assertEqual(walls.walled, ("secret",))

    def test_a_rule_that_cannot_be_read_walls_the_project_everywhere_and_stops_nothing_else(self):
        for rule in ("recall_cwd_match: /secret-mono/\n", "recall_cwd_match:\n  - /\n",
                     "recall_cwd_match:\n  path: /secret-mono/\n"):
            with self.subTest(rule=rule):
                self.write_secret(f"slug: secret\n{rule}")
                for directory in (self.inside, self.other):
                    walls = self.exclusion(directory)
                    self.assertIsNone(walls.problem)
                    self.assertEqual(walls.slugs, ("secret",))

    def test_an_unmarked_vault_sees_no_change(self):
        self.write_secret("slug: secret\n")
        self.assertEqual(self.exclusion(self.other), recall_exclusion.NONE)


class WhatReviewFound(_Secret):
    """Each of these served the project's notes outside its folder until fixed."""

    def test_a_comment_after_the_slug_still_walls_the_project(self):
        self.write_secret(f"slug: secret  # a client's work\ncode_paths:\n  - {self.inside}\n"
                          "recall_cwd_match:\n  - /secret-mono/\n")
        for walls in (self.exclusion(self.other), recall_exclusion.for_session(None, self.memory)):
            self.assertTrue(walls.hides(SECRET_NOTE), walls)
            self.assertTrue(walls.hides("memory/a.md", "secret"), walls)
        r = self.prompt(self.other)
        self.assertNotIn("secret-folder", r.stdout)
        self.assertNotIn("secret-stamped", r.stdout)

    def test_a_path_that_leaves_the_folder_is_outside(self):
        self.assertIn("secret", self.exclusion(f"{self.inside}/../other").slugs)

    def test_a_projects_folder_that_will_not_open_is_a_problem(self):
        completed = self.vault / "projects" / "completed"
        completed.mkdir(exist_ok=True)
        completed.chmod(0)
        self.addCleanup(completed.chmod, 0o755)
        try:
            next(completed.iterdir(), None)
            self.skipTest("this user opens every folder")
        except PermissionError:
            pass
        for directory in (self.code, self.other):
            with self.subTest(directory=directory):
                self.assertTrue(self.exclusion(directory).problem)


class ThePromptHook(_Secret):
    def test_inside_the_folder_the_notes_are_served(self):
        r = self.prompt(self.inside)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("secret-folder", r.stdout)
        self.assertIn("secret-stamped", r.stdout)

    def test_outside_it_they_are_not_and_nothing_names_them(self):
        for directory in (self.other, self.vault / "projects" / "secret"):
            with self.subTest(directory=directory):
                r = self.prompt(directory)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("secret-folder", r.stdout)
                self.assertNotIn("secret-stamped", r.stdout)
                self.assertIn("port-folder", r.stdout)
                self.assertIn("shared-memory", r.stdout)
                self.assertNotIn("secret", r.stderr)

    def test_the_payloads_folder_decides_not_the_process_folder(self):
        """Antigravity runs the hook from the workspace it puts in the payload;
        a host that runs it elsewhere must not lend it the process's folder."""
        r = self.run_hook(base.PROMPT_HOOK, self.inside, {"prompt": PROMPT, "cwd": str(self.other)})
        self.assertNotIn("secret-folder", r.stdout)
        r = self.run_hook(base.PROMPT_HOOK, self.other, {"prompt": PROMPT, "cwd": str(self.inside)})
        self.assertIn("secret-folder", r.stdout)


class TheSessionStartHook(_Secret):
    def setUp(self):
        super().setUp()
        self.write("standards/user-preferences.md", "---\ntitle: p\n---\nstanding preference\n")
        self.write("agent/memory/_always-load/secret-pin.md",
                   "---\ntitle: pin\nproject: secret\n---\npinned secret detail\n")

    def test_inside_the_folder_the_pinned_entry_loads(self):
        r = self.start(self.inside)
        self.assertIn("pinned secret detail", r.stdout)

    def test_outside_it_the_pinned_entry_does_not(self):
        r = self.start(self.other)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("standing preference", r.stdout)
        self.assertNotIn("pinned secret detail", r.stdout)


class TheSearchASessionRuns(_Secret):
    def query(self, directory):
        return subprocess.run([sys.executable, str(_SCRIPTS / "recall.py"), "query",
                               "zoning floodplain", "-k", "10"],
                              cwd=directory, env=self.env(), capture_output=True, text=True, timeout=60)

    def test_memory_search(self):
        inside, outside = self.query(self.inside), self.query(self.other)
        self.assertEqual(outside.returncode, 0, outside.stderr)
        self.assertIn("projects/secret", inside.stdout)
        self.assertNotIn("projects/secret", outside.stdout)
        self.assertNotIn("secret-card", outside.stdout)
        self.assertIn("projects/port", outside.stdout)


class DeepSearch(_Secret):
    def setUp(self):
        super().setUp()
        self.write("agent/archive/memory/semantic/secret-old.md",
                   "---\ntitle: old\nproject: secret\n---\nzoning floodplain\n")
        self.write("agent/archive/memory/semantic/kept-old.md", "---\ntitle: kept\n---\nzoning floodplain\n")

    def deep(self, directory):
        r = subprocess.run([sys.executable, str(_SCRIPTS / "deep_search.py"), "zoning floodplain",
                            "--vault", str(self.memory), "--json"],
                           cwd=directory, env=self.env(), capture_output=True, text=True, timeout=60)
        return [h["rel"] for h in json.loads(r.stdout)["archived"]]

    def test_inside_and_outside(self):
        self.assertEqual(len(self.deep(self.inside)), 2)
        self.assertEqual(self.deep(self.other), ["archive/memory/semantic/kept-old.md"])


class TheInProcessEngine(_Secret):
    def test_the_engine_and_its_daemon_fallback_hold_the_rule(self):
        outside = self.exclusion(self.other)
        hits = {h["path"] for h in recall.query(vault=self.memory, query_text="zoning floodplain", k=10,
                                                exclusion=outside, stderr=io.StringIO())}
        self.assertFalse([p for p in hits if "secret" in p], hits)
        self.assertTrue([p for p in hits if "projects/port/" in p], hits)
        inside = {h["path"] for h in recall.query(vault=self.memory, query_text="zoning floodplain", k=10,
                                                  exclusion=self.exclusion(self.inside), stderr=io.StringIO())}
        self.assertTrue([p for p in inside if "secret" in p], inside)


if __name__ == "__main__":
    unittest.main()
