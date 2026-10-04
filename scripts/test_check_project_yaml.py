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

    def test_former_names_are_a_list_of_repositories_for_a_one_repository_project(self):
        # Task 186: a renamed repository's old names, which the entity builder
        # folds into its page. They need one repository to belong to.
        _project(self.vault, "alpha", GOOD.format(slug="alpha") + "former_names:\n  - owner/old-alpha\n")
        self.assertEqual(self.scan(), [])
        _project(self.vault, "beta", GOOD.format(slug="beta") + "former_names: owner/old-beta\n")
        _project(self.vault, "gamma", "slug: gamma\ntitle: G\nstatus: active\nrepositories: [o/g1, o/g2]\n"
                 "code_paths: []\nformer_names: [o/old, not-a-repo]\n")
        text = "\n".join(self.scan())
        for want in ("projects/beta/project.yaml: `former_names` must be a list",
                     "former name `not-a-repo` is not `owner/repo`",
                     "projects/gamma/project.yaml: `former_names` needs exactly one repository"):
            self.assertIn(want, text)

    def test_a_bare_issue_floor_is_a_positive_whole_number_for_a_project_with_a_repository(self):
        # Task 187: below the floor a bare `#NN` in the project's notes is not
        # read as one of its issues. The entity extractor reads it through
        # projectbind, which ignores a value it cannot read, so the gate is
        # what says so.
        _project(self.vault, "alpha", GOOD.format(slug="alpha") + "bare_issue_floor: 48\n")
        self.assertEqual(self.scan(), [])
        _project(self.vault, "beta", GOOD.format(slug="beta") + "bare_issue_floor: forty-eight\n")
        _project(self.vault, "gamma", GOOD.format(slug="gamma") + "bare_issue_floor: 0\n")
        _project(self.vault, "delta", GOOD.format(slug="delta") + "bare_issue_floor: 4.5\n")
        _project(self.vault, "epsilon", GOOD.format(slug="epsilon") + "bare_issue_floor: true\n")
        _project(self.vault, "zeta", "slug: zeta\ntitle: Z\nstatus: active\nrepositories: []\n"
                 "code_paths: []\nbare_issue_floor: 48\n")
        text = "\n".join(self.scan())
        for slug in ("beta", "gamma", "delta", "epsilon"):
            self.assertIn(f"projects/{slug}/project.yaml: `bare_issue_floor` must be a positive whole number", text)
        self.assertIn("projects/zeta/project.yaml: `bare_issue_floor` needs a repository to belong to", text)
        self.assertNotIn("projects/alpha/", text)

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
