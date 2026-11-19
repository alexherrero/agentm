#!/usr/bin/env python3
"""Retire-invariant guard for the idea incubator and its researcher sub-agent.

The operator retired both on 2026-11-03: an idea is a card in the vault's
`personal/ideas/` now, `Ideas.md` is generated over those cards every night,
and the `_idea-incubator/` folder no longer exists. These tests pin that:

  1. The research-skeleton script, its tests and the sub-agent spec are gone.
  2. No live surface (code, install and integrity lists, doctor, contracts)
     names the deleted script or the sub-agent. `CHANGELOG.md` and `wiki/`
     are excluded: they record what was, and `check-wiki.py` gates them.
  3. Doctor's required sub-agent set is `adapt-evaluator` alone.

The scan reads `git ls-files`, so a gitignored `.claude/worktrees/` checkout
is never picked up.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RETIRED_PATHS = (
    "harness/skills/memory/scripts/ideas_incubator.py",
    "scripts/test_ideas_incubator.py",
    "harness/agents/memory-idea-researcher.md",
)
NEEDLES = ("memory-idea-researcher", "ideas_incubator")
# Places allowed to say the names: history, docs, the retirement notes in the
# memory skill and doctor, the pinned doctor tests, and this guard.
EXCLUDED = (
    "CHANGELOG.md",
    "wiki/",
    "harness/skills/memory/SKILL.md",
    "harness/skills/doctor.md",
    "scripts/test_devloop_slim_retired.py",
    "scripts/test_idea_incubator_retired.py",
)


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


class IdeaIncubatorRetired(unittest.TestCase):
    def test_retired_files_are_gone(self):
        for rel in RETIRED_PATHS:
            self.assertFalse((ROOT / rel).exists(), f"{rel} should be deleted")

    def test_no_live_surface_names_them(self):
        hits = []
        for rel in _tracked():
            if rel.startswith(EXCLUDED) or not (ROOT / rel).is_file():
                continue
            try:
                text = (ROOT / rel).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            hits += [f"{rel}: {n}" for n in NEEDLES if n in text]
        self.assertEqual(hits, [], "live surfaces still name the retired incubator")

    def test_doctor_requires_adapt_evaluator_alone(self):
        for rel in ("harness/skills/doctor.md", "adapters/claude-code/skills/doctor/SKILL.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("1/1 required (adapt-evaluator)", text, rel)


if __name__ == "__main__":
    unittest.main()
