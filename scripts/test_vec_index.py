#!/usr/bin/env python3
"""Unit coverage for harness/skills/memory/scripts/vec_index.py (R1.7).

vec_index.py lives in the memory skill's scripts/ dir (not top-level
scripts/); imported here via the same cross-dir sys.path pattern
test_memory_write_concurrency.py already uses.

Covers:
  - drain_queue: upsert processing, delete processing, and the R0 Task 2 /
    agentmExperience#3 stale-path re-validation (a queued upsert whose source
    file no longer exists converts to a delete rather than embedding a stale
    snapshot).
  - The freshness invariant: find_drifted_entries / full_sync report
    up-to-date / drifted / not-indexed correctly after a drain.

Skips the sqlite-vec-dependent assertions (SKIPPED, never silently dropped)
when the backend can't load extensions on this Python — same convention as
verify-vec-index.sh / verify-memory-roundtrip.sh.

Run directly:
    cd scripts && python3 -m unittest test_vec_index
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import vec_index  # noqa: E402


def _vec_backend_available(vault: Path) -> bool:
    conn = vec_index._open_index(vault)
    if conn is None:
        return False
    conn.close()
    return True


class TestDrainQueue(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "personal" / "reference").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_entry(self, rel: str, text: str) -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_drain_empty_queue_is_a_noop(self):
        stats = vec_index.drain_queue(self.vault, mode="stub")
        self.assertEqual(stats, {"processed": 0, "skipped": 0, "errors": 0, "remaining": 0})

    def test_drain_processes_upsert_when_backend_available(self):
        entry = self._seed_entry("personal/reference/note.md", "hello world content")
        vec_index.enqueue(self.vault, "personal/reference/note.md", "upsert", text="hello world")
        stats = vec_index.drain_queue(self.vault, mode="stub")
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        self.assertEqual(stats["processed"], 1)
        self.assertEqual(stats["errors"], 0)
        self.assertTrue(entry.is_file())  # drain never touches the source file

    def test_drain_delete_op_removes_index_row(self):
        self._seed_entry("personal/reference/note.md", "hello world content")
        vec_index.enqueue(self.vault, "personal/reference/note.md", "upsert", text="hello world")
        vec_index.drain_queue(self.vault, mode="stub")
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        vec_index.enqueue(self.vault, "personal/reference/note.md", "delete")
        stats = vec_index.drain_queue(self.vault, mode="stub")
        self.assertEqual(stats["processed"], 1)
        self.assertEqual(vec_index.index_size(self.vault), 0)

    def test_drain_stale_upsert_converts_to_delete(self):
        # agentmExperience#3 / R0 Task 2: a queued upsert whose source file
        # vanished by drain time must be treated as a delete, not embed a
        # stale snapshot. drain_queue's per-entry loop (where this
        # re-validation happens) only runs when the vec backend probe
        # succeeds — with no backend, everything short-circuits to
        # "skipped" before reaching this code path at all.
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        self._seed_entry("personal/reference/gone.md", "will be deleted before drain")
        vec_index.enqueue(self.vault, "personal/reference/gone.md", "upsert", text="will be deleted")
        (self.vault / "personal" / "reference" / "gone.md").unlink()
        stats = vec_index.drain_queue(self.vault, mode="stub")
        self.assertEqual(stats["errors"], 0)
        self.assertEqual(stats["processed"], 1)

    def test_drain_queue_file_removed_when_fully_processed(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        self._seed_entry("personal/reference/note.md", "content")
        vec_index.enqueue(self.vault, "personal/reference/note.md", "upsert", text="content")
        vec_index.drain_queue(self.vault, mode="stub")
        self.assertFalse(vec_index._queue_path(self.vault).exists())


class TestFreshnessInvariant(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "personal" / "reference").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_fresh_vault_reports_not_indexed(self):
        (self.vault / "personal" / "reference" / "a.md").write_text("content a", encoding="utf-8")
        inv = vec_index.find_drifted_entries(self.vault)
        self.assertEqual(inv["not_indexed"], ["personal/reference/a.md"])
        self.assertEqual(inv["up_to_date"], [])
        self.assertEqual(inv["drifted"], [])

    def test_full_sync_after_drain_reports_up_to_date(self):
        (self.vault / "personal" / "reference" / "a.md").write_text("content a", encoding="utf-8")
        vec_index.enqueue(self.vault, "personal/reference/a.md", "upsert", text="content a")
        vec_index.drain_queue(self.vault, mode="stub")
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        result = vec_index.full_sync(self.vault)
        self.assertEqual(result["up_to_date_count"], 1)
        self.assertEqual(result["drifted_count"], 0)
        self.assertEqual(result["not_indexed_count"], 0)

    def test_full_sync_missing_vault_gracefully_reports_empty(self):
        shutil.rmtree(self.vault)
        inv = vec_index.find_drifted_entries(self.vault)
        self.assertEqual(inv, {"drifted": [], "up_to_date": [], "not_indexed": []})


if __name__ == "__main__":
    unittest.main()
