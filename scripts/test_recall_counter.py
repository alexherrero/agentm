#!/usr/bin/env python3
"""Tests for harness/skills/memory/scripts/recall_counter.py (L1, ledger
ruling 6 -- the Morning Brief's retrieved-count needs a real per-recall
signal, privacy-shaped: query hashes + hit slugs + counts, never raw text).

Run directly:
    cd scripts && python3 -m unittest test_recall_counter
"""
from __future__ import annotations

import json
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

    def test_hits_omitted_by_default_is_byte_identical_to_pre_trace_shape(self):
        """recall-trace (Loose Ends Release 8): a caller not yet passing
        `hits` must produce exactly today's row -- no `hits` key at all,
        not even an empty list."""
        row = rc.record_recall("q", ["a", "b"], now=_NOW, history_path=self.history_path)
        self.assertNotIn("hits", row)
        on_disk = self.history_path.read_text(encoding="utf-8")
        self.assertNotIn('"hits"', on_disk)

    def test_hits_provided_lands_in_the_row_alongside_hit_slugs(self):
        hits = [
            {"slug": "a", "path": "personal/a.md", "sim": 0.81, "keyword": 12.3,
             "combined": 0.0163, "rank": 1, "lifecycle_tier": "volatile", "decay_score": 0.94},
            {"slug": "b", "path": "projects/agentm/b.md", "sim": 0.0, "keyword": 8.1,
             "combined": 0.0157, "rank": 2},
        ]
        row = rc.record_recall("q", ["a", "b"], hits=hits, now=_NOW, history_path=self.history_path)
        self.assertEqual(row["hit_slugs"], ["a", "b"])  # unchanged, back-compat
        self.assertEqual(row["hit_count"], 2)
        self.assertEqual(row["hits"], hits)
        # Round-trips through the JSONL write, not just the in-memory return.
        on_disk_row = json.loads(self.history_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(on_disk_row["hits"], hits)

    def test_hits_presence_does_not_affect_count_since(self):
        rc.record_recall("q", ["a"], hits=[{"slug": "a", "path": "a.md"}],
                          now=_NOW, history_path=self.history_path)
        s = rc.count_since(now=_NOW, lookback_seconds=86400, history_path=self.history_path)
        self.assertEqual(s, {"recall_count": 1, "hit_count": 1})


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
