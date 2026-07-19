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
import unittest.mock
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


class TestMergeRelatedSlugs(unittest.TestCase):
    """merge_related_slugs() is a pure function -- no sqlite-vec/vault
    needed, so these never skip."""

    def test_creates_a_related_line_when_absent(self):
        updated = write_time_linker.merge_related_slugs("body text\n", ["a", "b"])
        self.assertEqual(updated, "body text\n\n**Related:** [[a]], [[b]]\n")

    def test_empty_new_slugs_is_a_noop(self):
        self.assertIsNone(write_time_linker.merge_related_slugs("body text\n", []))

    def test_merges_new_slugs_into_existing_line(self):
        content = "body\n\n**Related:** [[a]]\n"
        updated = write_time_linker.merge_related_slugs(content, ["b"])
        self.assertEqual(updated, "body\n\n**Related:** [[a]], [[b]]\n")

    def test_already_present_slug_is_a_noop(self):
        content = "body\n\n**Related:** [[a]], [[b]]\n"
        self.assertIsNone(write_time_linker.merge_related_slugs(content, ["a"]))

    def test_merge_caps_at_max_related_links(self):
        content = "body\n\n**Related:** [[a]], [[b]], [[c]]\n"
        updated = write_time_linker.merge_related_slugs(content, ["d"])
        self.assertIsNone(updated)  # already at the cap -- "d" can't fit

    def test_merge_partial_fit_under_the_cap(self):
        content = "body\n\n**Related:** [[a]], [[b]]\n"
        updated = write_time_linker.merge_related_slugs(content, ["c", "d"])
        # Only "c" fits under MAX_RELATED_LINKS=3; "d" is dropped, not just deferred.
        self.assertEqual(updated, "body\n\n**Related:** [[a]], [[b]], [[c]]\n")

    def test_related_line_inside_a_fenced_code_block_is_ignored(self):
        # Review-caught defect: a note showing markdown syntax as a worked
        # example must not have that example line treated as the real
        # Related line -- the fenced content stays untouched, and a real
        # line gets appended after it instead.
        content = (
            "intro\n\n"
            "```\n"
            "**Related:** [[example-note]]\n"
            "```\n\n"
            "real body\n"
        )
        updated = write_time_linker.merge_related_slugs(content, ["new-neighbor"])
        self.assertIn("**Related:** [[example-note]]\n```", updated)  # fence untouched
        self.assertTrue(updated.rstrip("\n").endswith("**Related:** [[new-neighbor]]"))

    def test_duplicate_related_lines_merges_into_the_last_one(self):
        # Review-caught defect: corrupted/manually-edited state with two
        # Related-shaped lines must not silently drop the second one or
        # produce three lines -- the last (most-recently-appended, by this
        # system's own append-only convention) is the one merged into.
        content = "body\n\n**Related:** [[old-a]]\n\nmore text\n\n**Related:** [[old-b]]\n"
        updated = write_time_linker.merge_related_slugs(content, ["new-c"])
        related_lines = [l for l in updated.splitlines() if l.startswith("**Related:**")]
        self.assertEqual(related_lines, ["**Related:** [[old-a]]", "**Related:** [[old-b]], [[new-c]]"])


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

    def test_apply_is_idempotent_no_duplicate_related_line(self):
        # Reviewer-caught defect: drain_queue's own contract is "idempotent
        # -- re-running drain on a stable queue produces the same final
        # state." A queue record that gets reprocessed (drain interrupted
        # between a record's upsert and the queue file's rewrite) must not
        # append a second Related line to an already-linked note.
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        self._seed("near", _unit_vector(0, 0.99))
        target = self._write_target("personal/reference/new-note.md")

        first = write_time_linker.apply(self.vault, "personal/reference/new-note.md", _unit_vector(0))
        second = write_time_linker.apply(self.vault, "personal/reference/new-note.md", _unit_vector(0))

        self.assertEqual(first, ["near"])
        self.assertEqual(second, [])  # idempotent no-op, not a second append
        content = target.read_text(encoding="utf-8")
        self.assertEqual(content.count("**Related:**"), 1)

    def test_apply_reads_the_file_fresh_inside_the_lock(self):
        # Reviewer-caught defect: an earlier version read the target file
        # BEFORE acquiring vault_mutex, then wrote that stale snapshot back
        # inside the lock -- a TOCTOU window where a concurrent writer to
        # the same note (another save, an /memory evolve) landing between
        # the read and the lock would be silently clobbered. This proves
        # the fix: content written by a "concurrent" writer that lands
        # during vault_mutex's own critical section (simulated by patching
        # vault_mutex to write before yielding) survives in the final file.
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        import contextlib
        self._seed("near", _unit_vector(0, 0.99))
        target = self._write_target("personal/reference/new-note.md")

        @contextlib.contextmanager
        def _mutex_that_races(vault):
            target.write_text(
                target.read_text(encoding="utf-8").rstrip("\n") + "\nCONCURRENT-WRITE-MARKER\n",
                encoding="utf-8",
            )
            yield

        with unittest.mock.patch("write_time_linker.vault_mutex", side_effect=_mutex_that_races):
            write_time_linker.apply(self.vault, "personal/reference/new-note.md", _unit_vector(0))

        content = target.read_text(encoding="utf-8")
        self.assertIn("CONCURRENT-WRITE-MARKER", content)
        self.assertIn("[[near]]", content)


if __name__ == "__main__":
    unittest.main()
