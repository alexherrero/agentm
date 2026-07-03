#!/usr/bin/env python3
"""Unit coverage for harness/skills/memory/scripts/index_skills.py (R1.7).

Basic coverage: index_one_skill / index_skills write a SKILL.md's summary to
<vault>/personal-skills/<repo>/<skill-name>.md, skip an unchanged entry on
re-index, and error cleanly on a SKILL.md missing a valid `name:` field.

Run directly:
    cd scripts && python3 -m unittest test_index_skills
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import index_skills  # noqa: E402

_SKILL_MD = """---
name: my-cool-skill
description: Does something useful.
version: 1.0.0
---

# my-cool-skill

A skill that does something useful.
"""

_SKILL_MD_NO_NAME = """---
description: Missing a name field.
---

# nameless
"""


class TestIndexOneSkill(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.repo = self.root / "some-repo"
        (self.repo / ".claude" / "skills" / "my-cool-skill").mkdir(parents=True)
        self.skill_md = self.repo / ".claude" / "skills" / "my-cool-skill" / "SKILL.md"
        self.skill_md.write_text(_SKILL_MD, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_summary_to_personal_skills(self):
        result = index_skills.index_one_skill(self.skill_md, vault=self.vault, repo_name="some-repo")
        self.assertEqual(result["action"], "written")
        target = self.vault / "personal-skills" / "some-repo" / "my-cool-skill.md"
        self.assertTrue(target.is_file())
        self.assertIn("my-cool-skill", target.read_text(encoding="utf-8"))

    def test_reindex_unchanged_skill_is_skipped(self):
        index_skills.index_one_skill(self.skill_md, vault=self.vault, repo_name="some-repo")
        result = index_skills.index_one_skill(self.skill_md, vault=self.vault, repo_name="some-repo")
        self.assertEqual(result["action"], "skipped")

    def test_missing_name_field_errors_cleanly(self):
        bad_md = self.repo / "SKILL_bad.md"
        bad_md.write_text(_SKILL_MD_NO_NAME, encoding="utf-8")
        result = index_skills.index_one_skill(bad_md, vault=self.vault, repo_name="some-repo")
        self.assertEqual(result["action"], "error")
        self.assertIn("name", result["reason"])


class TestIndexSkills(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_index_skills_raises_on_missing_vault(self):
        with self.assertRaises(FileNotFoundError):
            index_skills.index_skills([self.root], vault=self.vault / "nonexistent")

    def test_index_skills_empty_paths_returns_zeroed_summary(self):
        summary = index_skills.index_skills([], vault=self.vault)
        self.assertEqual(summary, {"written": 0, "skipped": 0, "errors": 0, "results": []})

    def test_index_skills_over_a_tree_finds_and_writes(self):
        # discover_skill_md_files walks exactly one level deep
        # (<root>/<skill-name>/SKILL.md) — the search root passed to
        # index_skills() must be the skills/ dir itself, not its repo parent.
        skills_root = self.root / "repo" / ".claude" / "skills"
        (skills_root / "my-cool-skill").mkdir(parents=True)
        (skills_root / "my-cool-skill" / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
        summary = index_skills.index_skills([skills_root], vault=self.vault, repo_name="repo")
        self.assertEqual(summary["written"], 1)
        self.assertEqual(summary["errors"], 0)


if __name__ == "__main__":
    unittest.main()
