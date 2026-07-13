#!/usr/bin/env python3
"""Tests for harness/skills/memory/scripts/recall_counter.py (L1, ledger
ruling 6 -- the Morning Brief's retrieved-count needs a real per-recall
signal, privacy-shaped: query hashes + hit slugs + counts, never raw text).

Run directly:
    cd scripts && python3 -m unittest test_recall_counter
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import recall_counter as rc  # noqa: E402

_NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


class TestRecordRecall(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.history_path = Path(self._tmp.name) / "recall-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_never_stores_raw_query_text(self):
        secret = "my sensitive prompt about a private matter"
        row = rc.record_recall(secret, ["some-slug"], now=_NOW, history_path=self.history_path)
        on_disk = self.history_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, on_disk)
        self.assertNotIn("query_text", row)
        self.assertIn("query_hash", row)
        self.assertNotEqual(row["query_hash"], secret)

    def test_hash_is_deterministic_for_the_same_query(self):
        r1 = rc.record_recall("what did we decide about X", [], now=_NOW, history_path=self.history_path)
        r2 = rc.record_recall("what did we decide about X", [], now=_NOW, history_path=self.history_path)
        self.assertEqual(r1["query_hash"], r2["query_hash"])

    def test_records_hit_slugs_and_count(self):
        row = rc.record_recall("q", ["a", "b", "c"], now=_NOW, history_path=self.history_path)
        self.assertEqual(row["hit_slugs"], ["a", "b", "c"])
        self.assertEqual(row["hit_count"], 3)

    def test_appends_multiple_events(self):
        rc.record_recall("q1", ["a"], now=_NOW, history_path=self.history_path)
        rc.record_recall("q2", ["b", "c"], now=_NOW, history_path=self.history_path)
        lines = self.history_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)


class TestCountSince(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.history_path = Path(self._tmp.name) / "recall-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_history_yields_zero(self):
        s = rc.count_since(now=_NOW, lookback_seconds=86400, history_path=self.history_path)
        self.assertEqual(s, {"recall_count": 0, "hit_count": 0})

    def test_sums_within_lookback_only(self):
        old = _NOW.replace(day=1)
        rc.record_recall("old query", ["x", "y"], now=old, history_path=self.history_path)
        rc.record_recall("new query", ["z"], now=_NOW, history_path=self.history_path)
        s = rc.count_since(now=_NOW, lookback_seconds=86400, history_path=self.history_path)
        self.assertEqual(s["recall_count"], 1)
        self.assertEqual(s["hit_count"], 1)

    def test_malformed_line_is_skipped_not_raised(self):
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text("not json\n", encoding="utf-8")
        s = rc.count_since(now=_NOW, lookback_seconds=86400, history_path=self.history_path)
        self.assertEqual(s, {"recall_count": 0, "hit_count": 0})


if __name__ == "__main__":
    unittest.main()
