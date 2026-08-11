#!/usr/bin/env python3
"""Tests for harness/skills/memory/scripts/recall_counter.py (L1, ledger
ruling 6 -- the Morning Brief's retrieved-count needs a real per-recall
signal, privacy-shaped: query hashes + hit slugs + counts, never raw text).

Run directly:
    cd scripts && python3 -m unittest test_recall_counter
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

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
            {"slug": "a", "path": "memory/a.md", "sim": 0.81, "keyword": 12.3,
             "combined": 0.0163, "rank": 1, "lifecycle_tier": "volatile", "decay_score": 0.94},
            {"slug": "b", "path": "desk/projects/agentm/b.md", "sim": 0.0, "keyword": 8.1,
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


# ---------------------------------------------------------------------------
# Retention (recall-ledger-retention)
#
# Every expected survivor below is a hand-computed date, never a cutoff
# re-derived from RETENTION_DAYS -- a test that recomputes the boundary with
# the implementation's own arithmetic only proves the two agree.
#
# Anchor: NOW = 2026-07-26T00:00Z, so a 90-day window cuts at 2026-04-27.
#   July 26 -26d-> June 30 -30d-> May 31 -31d-> Apr 30 -3d-> Apr 27.
# ---------------------------------------------------------------------------

_RETENTION_NOW = datetime(2026, 7, 26, 0, 0, 0, tzinfo=timezone.utc)


class _LedgerFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.history_path = Path(self._tmp.name) / "recall-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_rows(self, dates: "list[str]") -> None:
        """One ledger row per ISO date, in file order (oldest first)."""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(
            "".join(
                json.dumps({"ts": f"{d}T00:00:00+00:00", "query_hash": "h",
                            "hit_slugs": [], "hit_count": 0}, sort_keys=True) + "\n"
                for d in dates
            ),
            encoding="utf-8",
        )

    def _dates_on_disk(self) -> "list[str]":
        return [
            json.loads(line)["ts"][:10]
            for line in self.history_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class TestRetentionWindow(_LedgerFixture):
    def test_expired_rows_go_and_recent_rows_stay(self):
        self._write_rows(["2025-01-01", "2026-04-01", "2026-05-01", "2026-07-25"])
        out = rc.prune_history(now=_RETENTION_NOW, history_path=self.history_path)
        self.assertEqual(self._dates_on_disk(), ["2026-05-01", "2026-07-25"])
        self.assertEqual(out, {"kept": 2, "dropped": 2, "pruned": True})

    def test_boundary_is_the_hand_computed_2026_04_27(self):
        """One day either side of the cutoff, computed by hand above."""
        self._write_rows(["2026-04-26", "2026-04-28"])
        rc.prune_history(now=_RETENTION_NOW, history_path=self.history_path)
        self.assertEqual(self._dates_on_disk(), ["2026-04-28"])

    def test_surviving_rows_are_byte_identical(self):
        """The sweep re-emits the lines it kept, it does not re-serialize them
        -- otherwise the prune has quietly become a second writer that can
        disagree with record_recall.

        The survivor is deliberately written NON-canonically (unsorted keys,
        irregular spacing), because a row that already happens to be in
        `json.dumps(..., sort_keys=True)` form round-trips through a
        re-serializing prune unchanged and would prove nothing.
        """
        survivor = '{"hit_count":0,  "query_hash":"h",   "ts": "2026-07-25T00:00:00+00:00"}'
        expired = '{"ts": "2020-01-01T00:00:00+00:00", "hit_count": 0}'
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(f"{expired}\n{survivor}\n", encoding="utf-8")

        rc.prune_history(now=_RETENTION_NOW, history_path=self.history_path)

        self.assertEqual(self.history_path.read_text(encoding="utf-8"),
                          survivor + "\n")

    def test_unreadable_rows_are_dropped(self):
        """Torn-write debris carries no usable ts, no reader can interpret it,
        and keeping it means it accumulates with no way out."""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(
            "not json at all\n"
            '[1, 2, 3]\n'                                  # valid JSON, wrong shape
            '{"query_hash": "h"}\n'                        # object, no ts
            '{"ts": "not-a-timestamp"}\n'
            '{"ts": "2026-07-25T00:00:00+00:00"}\n',
            encoding="utf-8",
        )
        out = rc.prune_history(now=_RETENTION_NOW, history_path=self.history_path)
        self.assertEqual(self._dates_on_disk(), ["2026-07-25"])
        self.assertEqual(out["dropped"], 4)

    def test_naive_timestamp_is_read_as_utc(self):
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text('{"ts": "2026-07-25T00:00:00"}\n', encoding="utf-8")
        out = rc.prune_history(now=_RETENTION_NOW, history_path=self.history_path)
        self.assertEqual(out, {"kept": 1, "dropped": 0, "pruned": False})

    def test_nothing_to_drop_means_no_rewrite(self):
        self._write_rows(["2026-07-24", "2026-07-25"])
        before = self.history_path.read_bytes()
        out = rc.prune_history(now=_RETENTION_NOW, history_path=self.history_path)
        self.assertEqual(out, {"kept": 2, "dropped": 0, "pruned": False})
        self.assertEqual(self.history_path.read_bytes(), before)

    def test_missing_ledger_is_not_an_error(self):
        out = rc.prune_history(now=_RETENTION_NOW, history_path=self.history_path)
        self.assertEqual(out, {"kept": 0, "dropped": 0, "pruned": False})

    def test_pruned_ledger_still_reads_through_count_since(self):
        """Retention must not change row shape -- the existing readers keep
        working against a swept file."""
        self._write_rows(["2025-01-01", "2026-07-25"])
        rc.prune_history(now=_RETENTION_NOW, history_path=self.history_path)
        s = rc.count_since(now=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
                            lookback_seconds=86400, history_path=self.history_path)
        self.assertEqual(s["recall_count"], 1)


class TestPruneTrigger(_LedgerFixture):
    def test_fresh_ledger_does_not_trigger(self):
        self._write_rows(["2026-07-24", "2026-07-25"])
        self.assertFalse(rc._needs_prune(self.history_path, now=_RETENTION_NOW))

    def test_expired_oldest_row_triggers(self):
        self._write_rows(["2025-01-01", "2026-07-25"])
        self.assertTrue(rc._needs_prune(self.history_path, now=_RETENTION_NOW))

    def test_size_ceiling_triggers_even_when_every_row_is_fresh(self):
        self._write_rows(["2026-07-25"])
        self.assertFalse(rc._needs_prune(self.history_path, now=_RETENTION_NOW))
        self.assertTrue(rc._needs_prune(self.history_path, now=_RETENTION_NOW,
                                        max_bytes=8))

    def test_unreadable_head_row_triggers_and_self_heals_in_one_pass(self):
        """A ledger whose oldest row is garbage must not become one that can
        never age-prune."""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(
            'torn write\n{"ts": "2026-07-25T00:00:00+00:00"}\n', encoding="utf-8")
        self.assertTrue(rc._needs_prune(self.history_path, now=_RETENTION_NOW))
        rc.prune_history(now=_RETENTION_NOW, history_path=self.history_path)
        self.assertFalse(rc._needs_prune(self.history_path, now=_RETENTION_NOW))

    def test_missing_or_empty_ledger_does_not_trigger(self):
        self.assertFalse(rc._needs_prune(self.history_path, now=_RETENTION_NOW))
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text("", encoding="utf-8")
        self.assertFalse(rc._needs_prune(self.history_path, now=_RETENTION_NOW))


class TestRecordRecallSweeps(_LedgerFixture):
    def test_appending_sweeps_expired_rows(self):
        """End-to-end: enforcement rides the write path, so it cannot ship
        inert the way a scheduled sweep can."""
        self._write_rows(["2025-01-01", "2026-04-01"])
        rc.record_recall("q", ["fresh"], now=_RETENTION_NOW,
                          history_path=self.history_path)
        self.assertEqual(self._dates_on_disk(), ["2026-07-26"])

    def test_recorded_row_survives_the_sweep_it_triggers(self):
        self._write_rows(["2025-01-01"])
        rc.record_recall("q", ["fresh"], now=_RETENTION_NOW,
                          history_path=self.history_path)
        rows = [json.loads(line) for line
                in self.history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hit_slugs"], ["fresh"])

    def test_a_failing_sweep_never_breaks_recording(self):
        """Housekeeping must never raise into the recall pipeline."""
        self._write_rows(["2025-01-01"])
        original = rc.prune_history
        rc.prune_history = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            row = rc.record_recall("q", ["a"], now=_RETENTION_NOW,
                                    history_path=self.history_path)
        finally:
            rc.prune_history = original
        self.assertEqual(row["hit_slugs"], ["a"])
        self.assertIn("2026-07-26", self.history_path.read_text(encoding="utf-8"))


class TestLedgerPathOverride(unittest.TestCase):
    """`recall.py`'s prompt_submit() calls record_recall with no history_path,
    so anything exercising it writes the real ledger. Cosmetic while the file
    was append-only; a read-modify-write once retention landed."""

    def test_env_override_redirects_the_default_path(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "somewhere-else.jsonl"
            with mock.patch.dict(os.environ, {"AGENTM_RECALL_HISTORY": str(target)}):
                self.assertEqual(rc.default_history_path(), target)
                rc.record_recall("q", ["a"], now=_RETENTION_NOW)
            self.assertTrue(target.is_file())

    def test_without_the_override_the_cache_path_is_used(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTM_RECALL_HISTORY", None)
            self.assertEqual(
                rc.default_history_path(),
                Path.home() / ".cache" / "agentm" / "telemetry" / "recall-history.jsonl",
            )


if __name__ == "__main__":
    unittest.main()
