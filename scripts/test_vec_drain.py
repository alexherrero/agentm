#!/usr/bin/env python3
"""Unit tests for vec_index.py's drain_queue (R0.2 / agentmExperience#0, #3).

Pre-fix, nothing in the production code path ever called `drain_queue` — the
only caller was verify-memory-roundtrip.sh's CI stub-mode smoke test. This
proves drain_queue itself does what agentmExperience#0/#3 need:

  1. Draining a queue of live entries populates entry_meta with indexed_at
     set (the "drain actually indexes" smoke test — done at the vec_index.py
     layer since driving it through the idle hook end-to-end needs a real
     sentence-transformers install; the hook-wiring itself is asserted by
     grepping the hook source in test_memory_reflect_idle_hook.py's sibling
     assertions, not re-executed here).
  2. A queued upsert whose source file has since been deleted is converted
     to a delete rather than embedding a stale snapshot (agentmExperience#3).

Uses --mode stub throughout (deterministic hash-based vectors — no network,
no model download). Requires the sqlite-vec extension to be loadable; skips
gracefully (matching vec_index.py's own contract) if it isn't.

Run: python3 scripts/test_vec_drain.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_SCRIPTS = _REPO_ROOT / "harness" / "skills" / "memory" / "scripts"
if str(_MEMORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SCRIPTS))

import vec_index  # noqa: E402

_ENTRY_BODY = (
    "---\nkind: preferences\nslug: {slug}\ntags: []\n---\n\nBody for {slug}.\n"
)


def _sqlite_vec_available(vault: Path) -> bool:
    conn = vec_index._open_index(vault)
    if conn is None:
        return False
    conn.close()
    return True


class TestVecDrain(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        (self.vault / "personal" / "preferences").mkdir(parents=True)
        if not _sqlite_vec_available(self.vault):
            self.skipTest("sqlite-vec extension not loadable on this Python")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_entry(self, slug: str) -> Path:
        p = self.vault / "personal" / "preferences" / f"{slug}.md"
        p.write_text(_ENTRY_BODY.format(slug=slug), encoding="utf-8")
        return p

    def test_drain_populates_entry_meta_with_indexed_at(self) -> None:
        n = 3
        rel_paths = []
        for i in range(n):
            slug = f"pref-{i}"
            self._write_entry(slug)
            rel = f"personal/preferences/{slug}.md"
            rel_paths.append(rel)
            vec_index.enqueue(self.vault, rel, "upsert", text=f"seed text {i}")

        queue_lines = vec_index._queue_path(self.vault).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(queue_lines), n)

        stats = vec_index.drain_queue(self.vault, mode="stub")
        self.assertEqual(stats["processed"], n)
        self.assertEqual(stats["errors"], 0)
        self.assertEqual(stats["remaining"], 0)
        self.assertEqual(vec_index.index_size(self.vault), n)

        conn = vec_index._open_index(self.vault)
        try:
            for rel in rel_paths:
                row = conn.execute(
                    "SELECT indexed_at FROM entry_meta WHERE path = ?", (rel,)
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertGreater(row[0], 0)
        finally:
            conn.close()

        # Fully-drained queue file is removed.
        self.assertFalse(vec_index._queue_path(self.vault).exists())

    def test_dead_path_upsert_converts_to_delete(self) -> None:
        rel = "personal/preferences/ghost.md"
        # Enqueue an upsert for a path with no backing file (the file was
        # deleted/renamed after enqueue, or the queue entry is simply stale).
        vec_index.enqueue(self.vault, rel, "upsert", text="stale snapshot text")

        stats = vec_index.drain_queue(self.vault, mode="stub")
        self.assertEqual(stats["processed"], 1)
        self.assertEqual(stats["errors"], 0)
        self.assertEqual(stats["remaining"], 0)
        # Nothing was embedded from the stale snapshot.
        self.assertEqual(vec_index.index_size(self.vault), 0)

    def test_dead_path_upsert_removes_stale_existing_row(self) -> None:
        slug = "will-be-deleted"
        path = self._write_entry(slug)
        rel = f"personal/preferences/{slug}.md"
        vec_index.enqueue(self.vault, rel, "upsert", text="initial")
        stats = vec_index.drain_queue(self.vault, mode="stub")
        self.assertEqual(stats["processed"], 1)
        self.assertEqual(vec_index.index_size(self.vault), 1)

        # File removed after the first drain; a second upsert for the same
        # path (e.g. a leftover queue entry) must clear the stale row rather
        # than re-embedding the old snapshot.
        path.unlink()
        vec_index.enqueue(self.vault, rel, "upsert", text="stale snapshot text")
        stats2 = vec_index.drain_queue(self.vault, mode="stub")
        self.assertEqual(stats2["processed"], 1)
        self.assertEqual(vec_index.index_size(self.vault), 0)

    def test_live_upsert_reembeds_from_file_not_stale_queued_text(self) -> None:
        """The queued `text` snapshot must be ignored; drain re-extracts from
        the live file. Verified by spying on embed_text's call argument
        rather than decoding the vec0 blob (sqlite-vec's internal on-disk
        float format, not the JSON this module passes in at insert time)."""
        slug = "reembed-check"
        path = self._write_entry(slug)
        rel = f"personal/preferences/{slug}.md"
        vec_index.enqueue(self.vault, rel, "upsert", text="THIS TEXT MUST NOT BE USED")

        live_text = vec_index._extract_embed_text_from_file(path)
        calls = []
        real_embed_text = vec_index.embed_text

        def _spy(text, *, mode=None):
            calls.append(text)
            return real_embed_text(text, mode=mode)

        vec_index.embed_text = _spy
        try:
            stats = vec_index.drain_queue(self.vault, mode="stub")
        finally:
            vec_index.embed_text = real_embed_text

        self.assertEqual(stats["processed"], 1)
        self.assertEqual(calls, [live_text])
        self.assertNotIn("THIS TEXT MUST NOT BE USED", calls)


if __name__ == "__main__":
    unittest.main()
