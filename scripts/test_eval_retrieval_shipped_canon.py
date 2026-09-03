#!/usr/bin/env python3
"""The shipped retrieval eval compares paths in a canonical form after
filing-v2 part 3: a gold note pinned at its pre-migration path and the same
note served from its class directory are one note (basenames preserved), so
both fold onto `Agent/memory/<basename>`. Everything outside the migrated
populations and the six classes compares exactly, as before — a hit under
`Projects/` or `Agent/desk/` is not loosened, and neither is the canary.

Run: python3 scripts/test_eval_retrieval_shipped_canon.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for p in (_HERE, _HERE / "health"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import eval_retrieval_shipped as ev  # noqa: E402


class CanonicalComparison(unittest.TestCase):
    def test_a_migrated_pin_matches_its_class_path(self):
        self.assertEqual(ev._canon("Agent/memory/2026/08/eval-canary.md"),
                         ev._canon("Agent/memory/semantic/eval-canary.md"))
        self.assertEqual(ev._canon("Agent/memory/_inbox/workflow-bash-13.md"),
                         ev._canon("Agent/memory/procedural/workflow-bash-13.md"))
        self.assertEqual(ev._canon("Agent/memory/preferences/x.md"), "Agent/memory/x.md")

    def test_a_lane_supplement_folds_too(self):
        self.assertEqual(ev._canon("Agent/memory/_opinions/good/never-self.md"),
                         ev._canon("Agent/memory/crystallized/good/never-self.md"))

    def test_paths_outside_the_migration_compare_exactly(self):
        for path in ("Projects/agentm/_harness/PLAN.md", "Agent/desk/tasks/x/notes.md",
                     "Agent/memory/trusted-sources.md", "Agent/diagnostics/health/latest-scorecard.md"):
            self.assertEqual(ev._canon(path), path)

    def test_different_notes_stay_different(self):
        self.assertNotEqual(ev._canon("Agent/memory/semantic/a.md"), ev._canon("Agent/memory/semantic/b.md"))
        self.assertNotEqual(ev._canon("Agent/memory/2026/08/a.md"), ev._canon("Agent/memory/2026/08/b.md"))

    def test_the_canary_pin_survives_the_move(self):
        self.assertEqual(ev._canon(ev.CANARY_PATH), "Agent/memory/eval-canary.md")


if __name__ == "__main__":
    unittest.main()
