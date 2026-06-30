#!/usr/bin/env python3
"""Convergence guard for `harness/documentation.md` (six-section frame).

The documentation convention is the **six-section taxonomy** crickets
standardized: How-to · Reference · Architecture · Designs · Explanation ·
Operational. Four are always present; Architecture is gated on a per-repo
`wiki/architecture.yml` manifest and Operational on non-public visibility.
Decision records live in each living design's `## Amendment log` (under
`designs/`) — the ADR model, and the standalone `decisions/` section it
needed, are retired (2026-06-30, machine-wide).

`harness/documentation.md` lives OUTSIDE `wiki/`, so `scripts/check-wiki.py`
never lints it, and `scripts/check-references.py` only scans the adapter globs
— no existing gate would catch a regression. This test is that gate: it pins
the six-section frame so a later edit can't silently restore the four-mode
contract, resurrect the retired `decisions/` section, drop a section, or break
the crickets tooling pointer.

The negative assertions target the retired *contract* phrasings only.
Historical mentions of an old layout (e.g. the Migrating section describing
what is being migrated *from*) legitimately keep the hyphenated "four-mode"
string — only the space-form contract phrasings are forbidden.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "harness" / "documentation.md"

# The six ordered top-level sections.
SIX_SECTIONS = (
    "how-to/",
    "reference/",
    "architecture/",
    "designs/",
    "explanation/",
    "operational/",
)

# Load-bearing positives the convention must keep.
REQUIRED_SUBSTRINGS = (
    "six-section",            # the frame is named
    "architecture.yml",       # the Architecture conditional gate
    "non-public visibility",  # the Operational conditional gate
    "wiki-maintenance",       # the crickets authoring-tooling pointer
    "graceful-skip",          # the crickets-absent dependency note
    ".diataxis-conventions.md",  # preserved I/O property: per-repo override
    "Preview-before-write",   # preserved I/O property: per-write approval gate
    "Amendment log",          # decisions now live in the governing design's amendment log
)

# Retired *contract* phrasings — must not survive. (Hyphenated "four-mode" in
# historical prose is allowed; only these contract forms are not.)
FORBIDDEN_SUBSTRINGS = (
    "four modes",                          # space-form contract phrasing
    "all four modes",                      # old documenter full-pass sweep
    "Four subdirs",                        # old wiki/ intro
    "seven-section",                       # the retired seven-section frame
    "seven sections",                      # space-form of the same
    "decisions/",                          # the retired standalone Decisions section/folder
    "wiki/explanation/decisions",          # dangling pre-move ADR link path
)


class TestDocumentationSixSection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")

    def test_all_six_sections_named(self):
        missing = [s for s in SIX_SECTIONS if s not in self.text]
        self.assertEqual(
            missing,
            [],
            f"harness/documentation.md must name all six sections; "
            f"missing: {missing}",
        )

    def test_required_substrings_present(self):
        missing = [s for s in REQUIRED_SUBSTRINGS if s.lower() not in self.text.lower()]
        self.assertEqual(
            missing,
            [],
            "harness/documentation.md is missing load-bearing six-section / "
            f"crickets-pointer / I/O-property content: {missing}",
        )

    def test_no_retired_contract_language(self):
        offenders = []
        lowered = self.text.lower()
        for needle in FORBIDDEN_SUBSTRINGS:
            if needle.lower() in lowered:
                offenders.append(needle)
        self.assertEqual(
            offenders,
            [],
            "Retired four-mode / seven-section / Decisions contract language "
            f"reappeared in harness/documentation.md: {offenders}. The "
            "six-section frame is the contract; decisions live in living-design "
            "amendment logs.",
        )


if __name__ == "__main__":
    unittest.main()
