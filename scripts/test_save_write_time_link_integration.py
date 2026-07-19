#!/usr/bin/env python3
"""End-to-end coverage for the write-time linker's full pipeline (plan
PLAN-auto-org-write-time-linking, task 3): save.py's post-save hook flags
the queue record (`link=True`), and `vec_index.drain_queue()` applies the
link at drain time via write_time_linker.apply(), reusing the embedding
drain already computes for the ordinary upsert.

Two things this file exists to prove that no other test file proves:
  1. `save_entry()` itself never calls `embed.embed_text` — the
     regression this whole design (deferring to drain) exists to avoid.
     Verified by patching `embed.embed_text` to raise if called at all,
     during a plain `save_entry()` call with no drain involved.
  2. The full pipeline — save, then drain (mode="stub" so no real model
     load) — actually results in a linked note, using drain's own
     `mode="stub"` embedding path (patched to a controlled vector so the
     neighbor relationship is deterministic).

Run directly:
    cd scripts && python3 -m unittest test_save_write_time_link_integration
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import embed  # noqa: E402
import save  # noqa: E402
import vec_index  # noqa: E402


def _vec_backend_available(vault: Path) -> bool:
    conn = vec_index._open_index(vault)
    if conn is None:
        return False
    conn.close()
    return True


def _unit_vector(hot_index: int, sign: float = 1.0) -> list:
    v = [0.0] * vec_index.EMBEDDING_DIM
    v[hot_index] = sign
    return v


class TestSaveNeverEmbedsSynchronously(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_entry_never_calls_embed_text(self):
        with patch.object(embed, "embed_text", side_effect=AssertionError(
            "save_entry must never call embed_text synchronously -- "
            "that's exactly the regression deferring to drain exists to avoid"
        )):
            target = save.save_entry(self.vault, "reference", "quick-note", "some body text")
        self.assertTrue(target.is_file())

    def test_save_entry_enqueues_with_link_flag(self):
        save.save_entry(self.vault, "reference", "quick-note", "some body text")
        queue_path = vec_index._queue_path(self.vault)
        records = [
            __import__("json").loads(line)
            for line in queue_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["link"])
        self.assertEqual(records[0]["path"], "personal/reference/quick-note.md")


class TestFullPipelineViaDrain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "personal" / "reference").mkdir(parents=True)
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")

    def tearDown(self):
        self._tmp.cleanup()

    def _seed(self, slug: str, embedding: list) -> None:
        rel = f"personal/reference/{slug}.md"
        path = self.vault / rel
        path.write_text(f"---\nslug: {slug}\n---\nseed body\n", encoding="utf-8")
        vec_index.upsert_entry(self.vault, rel, embedding)

    def test_save_then_drain_links_the_new_note(self):
        self._seed("near", _unit_vector(0, 0.99))

        target = save.save_entry(self.vault, "reference", "new-note", "some new body")

        # No embed call has happened yet -- drain hasn't run. The note is
        # provably unlinked at this point.
        self.assertNotIn("Related:", target.read_text(encoding="utf-8"))

        with patch("vec_index.embed_text", return_value=_unit_vector(0)):
            stats = vec_index.drain_queue(self.vault, mode="stub")

        self.assertEqual(stats["processed"], 1)
        content = target.read_text(encoding="utf-8")
        self.assertIn("**Related:**", content)
        self.assertIn("[[near]]", content)

    def test_drain_upsert_stats_unaffected_by_a_failing_linker(self):
        self._seed("near", _unit_vector(0, 0.99))
        save.save_entry(self.vault, "reference", "new-note", "some new body")

        with patch("vec_index.embed_text", return_value=_unit_vector(0)):
            with patch("write_time_linker.apply", side_effect=RuntimeError("boom")):
                stats = vec_index.drain_queue(self.vault, mode="stub")

        # The upsert itself succeeded; a linker exception must not turn a
        # successful embed+index into a drain error.
        self.assertEqual(stats["processed"], 1)
        self.assertEqual(stats["errors"], 0)


if __name__ == "__main__":
    unittest.main()
