#!/usr/bin/env python3
"""Unit coverage for write_time_linker.py (plan
PLAN-auto-org-write-time-linking, task 3).

`apply()` is the drain-time half of the write-time linker: given an
already-computed embedding (drain's own upsert flow computes it; this
module never embeds anything itself), it queries task 1's nearest-neighbor
index and patches the target file's "Related" line.

Uses hand-crafted unit/opposite vectors seeded directly into the vec-index
(same technique as test_vec_index.py::TestNearest) — no embed.py call
anywhere in this file, so nothing here can trigger a model load or network
request.

Requires the real sqlite-vec backend (nearest() is a no-op without it) —
skips gracefully on macOS system Python, same convention as test_vec_index.py.

Run directly:
    cd scripts && python3 -m unittest test_write_time_linker
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

import vec_index  # noqa: E402
import write_time_linker  # noqa: E402


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


class TestWriteTimeLinkerApply(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "personal" / "reference").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _seed(self, slug: str, embedding: list) -> None:
        rel = f"personal/reference/{slug}.md"
        path = self.vault / rel
        path.write_text(f"---\nslug: {slug}\n---\nseed body\n", encoding="utf-8")
        vec_index.upsert_entry(self.vault, rel, embedding)

    def _write_target(self, rel: str, body: str = "just-saved body") -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nslug: new-note\n---\n{body}\n", encoding="utf-8")
        return path

    def test_related_links_added_for_neighbors_above_floor(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        self._seed("near", _unit_vector(0, 0.99))  # similarity ~0.99 -- clears the floor
        self._seed("far", _unit_vector(0, -1.0))   # similarity 0.0 -- well below the floor
        target = self._write_target("personal/reference/new-note.md")

        slugs = write_time_linker.apply(self.vault, "personal/reference/new-note.md", _unit_vector(0))

        self.assertEqual(slugs, ["near"])
        content = target.read_text(encoding="utf-8")
        self.assertIn("**Related:**", content)
        self.assertIn("[[near]]", content)
        self.assertNotIn("[[far]]", content)

    def test_never_more_than_the_cap(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        for i in range(5):
            self._seed(f"near-{i}", _unit_vector(0, 0.99 - i * 0.001))
        self._write_target("personal/reference/new-note.md")

        slugs = write_time_linker.apply(self.vault, "personal/reference/new-note.md", _unit_vector(0))
        self.assertEqual(len(slugs), write_time_linker.MAX_RELATED_LINKS)

    def test_no_qualifying_neighbors_returns_empty_and_touches_nothing(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        target = self._write_target("personal/reference/lonely-note.md")
        original = target.read_text(encoding="utf-8")

        slugs = write_time_linker.apply(self.vault, "personal/reference/lonely-note.md", _unit_vector(0))

        self.assertEqual(slugs, [])
        self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_missing_target_file_returns_empty_without_raising(self):
        # Simulates the note being deleted between save and drain.
        slugs = write_time_linker.apply(self.vault, "personal/reference/gone.md", _unit_vector(0))
        self.assertEqual(slugs, [])

    def test_never_adds_a_self_link(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        rel = "personal/reference/self-check.md"
        self._write_target(rel)
        vec_index.upsert_entry(self.vault, rel, _unit_vector(0))  # index already has THIS path

        slugs = write_time_linker.apply(self.vault, rel, _unit_vector(0))
        self.assertNotIn("self-check", slugs)

    def test_apply_nudges_the_graph_snapshot(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        import graph_snapshot
        self._seed("near", _unit_vector(0, 0.99))
        self._write_target("personal/reference/new-note.md")

        write_time_linker.apply(self.vault, "personal/reference/new-note.md", _unit_vector(0))

        # The freshly-applied Related line is now a real outgoing edge in
        # the persisted graph snapshot, without a separate full rebuild.
        outgoing = graph_snapshot.outgoing(self.vault, "personal/reference/new-note.md")
        self.assertEqual([e.target for e in outgoing], ["near"])


if __name__ == "__main__":
    unittest.main()
