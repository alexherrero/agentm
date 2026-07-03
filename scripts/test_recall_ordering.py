#!/usr/bin/env python3
"""Recall prefix-stability: identical queries return results in a stable
order (R1.7). recall.query()'s merge sort key is
`(-combined, -sim, path)` (recall.py:802) — path is the deterministic
tie-breaker, so entries with tied combined scores always come back in the
same relative order, every run, at a stable content SHA. Without this,
repeated identical queries could non-deterministically reorder tied hits,
which is exactly the kind of instability a cache or a stable-recall
assumption elsewhere would silently break on.

Run directly:
    cd scripts && python3 -m unittest test_recall_ordering
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

import recall  # noqa: E402


class TestRecallPrefixStability(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "personal" / "reference").mkdir(parents=True)
        # Three entries with IDENTICAL body text -> identical keyword AND
        # (stub-mode) vector scores -> a genuine 3-way tie on combined score,
        # so any non-deterministic ordering would show up as flaky failures.
        for name in ("charlie", "alpha", "bravo"):
            (self.vault / "personal" / "reference" / f"{name}.md").write_text(
                "shared identical content for the tie-break test", encoding="utf-8",
            )

    def tearDown(self):
        self._tmp.cleanup()

    def test_repeated_identical_queries_return_the_same_order(self):
        first = recall.query(vault=self.vault, query_text="shared identical content", k=10, mode="stub")
        second = recall.query(vault=self.vault, query_text="shared identical content", k=10, mode="stub")
        self.assertEqual([r["path"] for r in first], [r["path"] for r in second])

    def test_tied_scores_break_by_path_alphabetically(self):
        results = recall.query(vault=self.vault, query_text="shared identical content", k=10, mode="stub")
        paths = [r["path"] for r in results]
        self.assertEqual(paths, sorted(paths))


if __name__ == "__main__":
    unittest.main()
