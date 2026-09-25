#!/usr/bin/env python3
"""Tests for scripts/check-project-yaml.py — every project root carries a
`project.yaml` in its one schema (task 176, step 5).

Run: python3 scripts/test_check_project_yaml.py
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("check_project_yaml", _HERE / "check-project-yaml.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

GOOD = ("slug: {slug}\ntitle: Alpha\nstatus: active\nrepositories:\n  - owner/alpha\n"
        "code_paths:\n  - ~/code/alpha\n")


def _project(vault: Path, slug: str, text: "str | None") -> None:
    d = vault / "projects" / slug
    d.mkdir(parents=True)
    if text is not None:
        (d / "project.yaml").write_text(text, encoding="utf-8")


class TheGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name)

    def scan(self) -> list:
        return gate.scan(self.vault, yaml)[0]

    def test_a_clean_projects_space_passes(self):
        _project(self.vault, "alpha", GOOD.format(slug="alpha"))
        _project(self.vault, "beta", GOOD.format(slug="beta") + "board:\n  owner: o\n  number: 5\n"
                 "sensitivity: personal-financial\n")
        self.assertEqual(self.scan(), [])
        self.assertEqual(gate.main(["--vault", str(self.vault)]), 0)

    def test_a_slug_that_names_another_directory_fails(self):
        _project(self.vault, "alpha", GOOD.format(slug="gamma"))
        findings = self.scan()
        self.assertEqual(len(findings), 1)
        self.assertIn("does not match its directory `alpha`", findings[0])
        self.assertEqual(gate.main(["--vault", str(self.vault)]), 1)

    def test_a_project_without_one_fails(self):
        _project(self.vault, "alpha", None)
        self.assertIn("projects/alpha/project.yaml: missing", self.scan()[0])

    def test_finished_and_hidden_folders_are_not_projects(self):
        for name in ("completed", "_archive", ".obsidian"):
            (self.vault / "projects" / name / "x").mkdir(parents=True)
        self.assertEqual(self.scan(), [])

    def test_the_shape_of_every_key_is_held(self):
        _project(self.vault, "alpha",
                 "slug: alpha\ntitle: ''\nstatus: executing\nrepositories: [not-a-repo]\n"
                 "code_paths: [/opt/code]\nboard: {owner: o, number: 0}\n"
                 "sensitivity: Personal Financial\nowner: me\n")
        text = "\n".join(self.scan())
        for want in ("`title` must be", "status `executing`", "`not-a-repo` is not `owner/repo`",
                     "is not home-relative", "`board` must be", "`sensitivity` must be",
                     "unknown key `owner`"):
            self.assertIn(want, text)

    def test_the_template_is_held_to_the_schema_without_a_slug(self):
        t = self.vault / "standards" / "templates"
        t.mkdir(parents=True)
        (t / "project.yaml").write_text("title: <name>\nstatus: active\nrepositories: []\ncode_paths: []\n",
                                        encoding="utf-8")
        (self.vault / "projects").mkdir()
        self.assertEqual(self.scan(), [])
        (t / "project.yaml").write_text("title: <name>\nstatus: active\n", encoding="utf-8")
        self.assertIn("missing required key `repositories`", "\n".join(self.scan()))

    def test_no_vault_skips_cleanly(self):
        self.assertEqual(gate.main(["--vault", str(self.vault / "nowhere")]), 0)

    def test_the_self_test_passes(self):
        self.assertEqual(gate.main(["--self-test"]), 0)


if __name__ == "__main__":
    unittest.main()
