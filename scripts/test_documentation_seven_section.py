#!/usr/bin/env python3
"""Convergence guard for `harness/documentation.md` (seven-section frame).

The seven-section-convergence (part 3/4, ADR 0004 Amendment 2026-06-11) rewrote
`harness/documentation.md` — the runtime documentation spec — from the four-mode
Diátaxis frame (tutorials / how-to / reference / explanation) to the seven-section
taxonomy crickets standardized: How-to · Reference · Architecture · Designs ·
Explanation · Decisions · Operational. Five are always present; Architecture is
gated on a per-repo `wiki/architecture.yml` manifest and Operational on non-public
visibility.

`harness/documentation.md` lives OUTSIDE `wiki/`, so `scripts/check-wiki.py` never
lints it, and `scripts/check-references.py` only scans the adapter globs — no
existing gate would catch four-mode language regressing back into this file. This
test is that gate: it pins the convergence so a later edit can't silently restore
the four-mode contract, drop a section, or break the crickets tooling pointer.

The negative assertions target the retired *contract* phrasings only. Historical
mentions of the old layout (e.g. the Migrating section describing what is being
migrated *from*, or the supersession note) legitimately keep the hyphenated
"four-mode" string — only the space-form contract phrasings are forbidden.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "harness" / "documentation.md"

# The seven ordered top-level sections (ADR 0004 Amendment 2026-06-11).
SEVEN_SECTIONS = (
    "how-to/",
    "reference/",
    "architecture/",
    "designs/",
    "explanation/",
    "decisions/",
    "operational/",
)

# Load-bearing positives the convergence must keep.
REQUIRED_SUBSTRINGS = (
    "seven-section",          # the frame is named
    "architecture.yml",       # the Architecture conditional gate
    "non-public visibility",  # the Operational conditional gate
    "wiki-maintenance",       # the crickets authoring-tooling pointer (task 2)
    "graceful-skip",          # the ADR 0006 crickets-absent dependency note (task 2)
    ".diataxis-conventions.md",  # preserved I/O property: per-repo override
    "Preview-before-write",   # preserved I/O property: per-write approval gate
)

# Retired four-mode *contract* phrasings — must not survive. (Hyphenated
# "four-mode" in historical prose is allowed; only these contract forms are not.)
FORBIDDEN_SUBSTRINGS = (
    "four modes",                          # space-form contract phrasing
    "all four modes",                      # old documenter full-pass sweep
    "Four modes is the Diátaxis contract", # the old five-mode-blocking Non-goal
    "Four subdirs",                        # old wiki/ intro
    "wiki/explanation/decisions",          # dangling pre-move ADR link path
)


class TestDocumentationSevenSection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")

    def test_all_seven_sections_named(self):
        missing = [s for s in SEVEN_SECTIONS if s not in self.text]
        self.assertEqual(
            missing,
            [],
            f"harness/documentation.md must name all seven sections; "
            f"missing: {missing}",
        )

    def test_required_substrings_present(self):
        missing = [s for s in REQUIRED_SUBSTRINGS if s.lower() not in self.text.lower()]
        self.assertEqual(
            missing,
            [],
            "harness/documentation.md is missing load-bearing seven-section / "
            f"crickets-pointer / I/O-property content: {missing}",
        )

    def test_no_four_mode_contract_language(self):
        offenders = []
        lowered = self.text.lower()
        for needle in FORBIDDEN_SUBSTRINGS:
            if needle.lower() in lowered:
                offenders.append(needle)
        self.assertEqual(
            offenders,
            [],
            "Retired four-mode contract language reappeared in "
            f"harness/documentation.md: {offenders}. The seven-section frame "
            "(ADR 0004 Amendment 2026-06-11) is the contract.",
        )


if __name__ == "__main__":
    unittest.main()
