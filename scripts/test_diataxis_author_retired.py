#!/usr/bin/env python3
"""Retire-invariant guard for the duplicate `diataxis-author` skill copy.

The four-mode `diataxis-author` skill was a vendored copy of a skill crickets
now owns canonically at seven-section (`crickets/src/wiki-maintenance/`). The
seven-section-convergence retire (part 2/4, ADR 0004 Amendment 2026-06-11)
removed agentm's copy and rewired every live call site to defer to crickets'
`wiki-maintenance` plugin with the ADR 0006 graceful-skip (suggest-then-skip
when crickets is absent — never hard-fail).

These tests pin that invariant so a later change can't silently re-introduce
the local copy or a dangling path dependency on it:

  1. The local skill directory is gone.
  2. No *live* surface (code / install scripts / skill bodies / adapters)
     references the deleted PATH. The bare skill *name* `diataxis-author`
     stays legal everywhere — crickets provides it, and detection still
     recommends it by name (see test_detect_project.py). Only the vanished
     local path `harness/skills/diataxis-author/...` is forbidden.
  3. The skill is categorized as crickets-shipped (graceful-skip if crickets
     is not paired), not harness-shipped, in both doctor surfaces. This is the
     documented crickets-absent contract: the harness assumes no local copy
     and degrades to suggest-then-skip rather than hard-failing.

`CHANGELOG.md` and `wiki/` are excluded from the path scan: they are
append-only historical / design records that legitimately describe the old
path as a record of what was (the retire design doc and the release note that
shipped the copy). Their own gate is `check-wiki.py`, not this guard.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

# The deleted local path. Built by join so this source file does not itself
# contain the literal needle (it would otherwise self-match the scan).
RETIRED_PATH = "harness/skills/" + "diataxis-author"

# Append-only historical / design records that legitimately retain the old
# path as a record of what was. Excluded from the live-surface scan.
EXCLUDED_DIRS = {".git", "wiki", "node_modules", "__pycache__"}
EXCLUDED_FILES = {"CHANGELOG.md"}
TEXT_SUFFIXES = {".md", ".py", ".sh", ".ps1", ".json", ".toml", ".yml", ".yaml"}


def _iter_live_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() == SELF:
            continue  # don't self-match on the needle in this file
        rel_parts = path.relative_to(ROOT).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            continue
        if path.name in EXCLUDED_FILES:
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        yield path


class TestDiataxisAuthorRetired(unittest.TestCase):
    def test_local_skill_dir_removed(self):
        """The vendored copy no longer exists in the harness tree."""
        self.assertFalse(
            (ROOT / "harness" / "skills" / "diataxis-author").exists(),
            "agentm's diataxis-author copy must be retired (disposition (b)); "
            "crickets' wiki-maintenance plugin is the single source.",
        )

    def test_no_dangling_path_in_live_surfaces(self):
        """No live surface references the deleted local path.

        The bare skill name `diataxis-author` is allowed (crickets provides
        it); only the deleted local PATH is forbidden.
        """
        offenders = []
        for path in _iter_live_text_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if RETIRED_PATH in line:
                    rel = path.relative_to(ROOT)
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "Dangling reference to the retired local path "
            f"`{RETIRED_PATH}/` in live surface(s) — rewire to crickets' "
            "wiki-maintenance plugin with graceful-skip:\n"
            + "\n".join(offenders),
        )

    def test_doctor_categorizes_diataxis_author_as_crickets(self):
        """Both doctor surfaces list diataxis-author as crickets-shipped.

        Pins the crickets-absent graceful-skip contract: the skill is grouped
        with the other crickets-shipped skills (graceful-skip if crickets is
        not paired), not with the harness-shipped compound skills.
        """
        name = "diataxis-author"
        for rel in (
            "harness/skills/doctor.md",
            "adapters/claude-code/skills/doctor/SKILL.md",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(name, text, f"{rel} should still mention {name}")
            # The harness-shipped compound list must no longer carry it.
            harness_list = "design, memory"
            self.assertIn(
                harness_list,
                text,
                f"{rel}: harness-shipped compound skills should be "
                f"`{harness_list}` (diataxis-author removed).",
            )
            self.assertNotIn(
                "design, " + name,
                text,
                f"{rel}: diataxis-author must not remain in the "
                "harness-shipped compound-skills list.",
            )


if __name__ == "__main__":
    unittest.main()
