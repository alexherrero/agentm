#!/usr/bin/env python3
"""Unit coverage for harness/skills/memory/scripts/graph_snapshot.py
(plan PLAN-auto-org-write-time-linking, task 2).

Covers:
  - Round-trip: write (rebuild), reload (fresh connection per call — no
    process-local cache to bypass), query incoming()/orphans().
  - Incremental rebuild (paths=[...]) touches only the given file(s);
    contrasted against a from-scratch full rebuild over the same corpus,
    which must touch every file — the coarse "fewer files touched" evidence
    the plan's verification criteria asks for.
  - outgoing() returns the stored edges for one source path.
  - A full rebuild drops a node whose source file was deleted.

No sqlite-vec dependency here (plain sqlite3, always available) — these
never skip.

Run directly:
    cd scripts && python3 -m unittest test_graph_snapshot
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

import graph_snapshot  # noqa: E402


class TestGraphSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "personal" / "reference").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel: str, body: str) -> None:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_round_trip_incoming_and_orphans(self):
        self._write("personal/reference/note-a.md", "---\nslug: note-a\n---\nno links here")
        self._write("personal/reference/note-b.md", "---\nslug: note-b\n---\nsee [[note-a]] for context")
        self._write("personal/reference/note-c.md", "---\nslug: note-c\n---\nnothing points here, points nowhere")

        stats = graph_snapshot.rebuild(self.vault)
        self.assertEqual(stats.files_touched, 3)

        # Reload via a fresh call (no in-process cache to coast on).
        incoming = graph_snapshot.incoming(self.vault, "personal/reference/note-a.md")
        self.assertEqual(incoming, ["personal/reference/note-b.md"])

        orphaned = graph_snapshot.orphans(self.vault)
        self.assertEqual(orphaned, ["personal/reference/note-c.md"])
        # note-a has an incoming edge, note-b has an outgoing edge -- neither is an orphan.
        self.assertNotIn("personal/reference/note-a.md", orphaned)
        self.assertNotIn("personal/reference/note-b.md", orphaned)

    def test_outgoing_returns_stored_edges(self):
        self._write("personal/reference/note-a.md", "---\nslug: note-a\n---\nbody")
        self._write("personal/reference/note-b.md", "---\nslug: note-b\n---\nsee [[note-a]]")
        graph_snapshot.rebuild(self.vault)

        edges = graph_snapshot.outgoing(self.vault, "personal/reference/note-b.md")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].source_path, "personal/reference/note-b.md")
        self.assertEqual(edges[0].target, "note-a")

    def test_targeted_incremental_touches_only_given_paths(self):
        self._write("personal/reference/note-a.md", "---\nslug: note-a\n---\nbody a")
        self._write("personal/reference/note-b.md", "---\nslug: note-b\n---\nbody b")
        self._write("personal/reference/note-c.md", "---\nslug: note-c\n---\nbody c")
        graph_snapshot.rebuild(self.vault)  # initial full population

        self._write("personal/reference/note-d.md", "---\nslug: note-d\n---\nsee [[note-a]]")
        stats = graph_snapshot.rebuild(self.vault, paths=["personal/reference/note-d.md"])
        self.assertEqual(stats.files_touched, 1)
        self.assertEqual(stats.edges_written, 1)

        # note-d is now visible to a query without any other file being re-touched.
        incoming = graph_snapshot.incoming(self.vault, "personal/reference/note-a.md")
        self.assertIn("personal/reference/note-d.md", incoming)

    def test_incremental_rebuild_touches_fewer_files_than_a_full_rebuild(self):
        for i in range(5):
            self._write(f"personal/reference/note-{i}.md", f"---\nslug: note-{i}\n---\nbody {i}")
        graph_snapshot.rebuild(self.vault)  # initial full population, 5 files

        self._write("personal/reference/note-new.md", "---\nslug: note-new\n---\nsee [[note-0]]")
        incremental_stats = graph_snapshot.rebuild(self.vault, paths=["personal/reference/note-new.md"])

        # A from-scratch full rebuild over the same 6-file corpus must touch
        # every file -- the coarse contrast the plan's verification criteria
        # asks for (incremental touches 1; a full rebuild from nothing touches 6).
        (graph_snapshot._snapshot_path(self.vault)).unlink()
        full_stats = graph_snapshot.rebuild(self.vault)

        self.assertEqual(incremental_stats.files_touched, 1)
        self.assertEqual(full_stats.files_touched, 6)
        self.assertLess(incremental_stats.files_touched, full_stats.files_touched)

    def test_full_rebuild_is_a_noop_on_unchanged_files(self):
        self._write("personal/reference/note-a.md", "---\nslug: note-a\n---\nbody")
        graph_snapshot.rebuild(self.vault)
        stats = graph_snapshot.rebuild(self.vault)  # nothing changed since last rebuild
        self.assertEqual(stats.files_touched, 0)

    def test_full_rebuild_drops_deleted_files(self):
        self._write("personal/reference/note-a.md", "---\nslug: note-a\n---\nbody")
        self._write("personal/reference/note-b.md", "---\nslug: note-b\n---\nsee [[note-a]]")
        graph_snapshot.rebuild(self.vault)

        (self.vault / "personal" / "reference" / "note-b.md").unlink()
        stats = graph_snapshot.rebuild(self.vault)
        self.assertEqual(stats.nodes_removed, 1)

        incoming = graph_snapshot.incoming(self.vault, "personal/reference/note-a.md")
        self.assertEqual(incoming, [])

    def test_targeted_rebuild_of_a_deleted_path_removes_it(self):
        self._write("personal/reference/note-a.md", "---\nslug: note-a\n---\nbody")
        graph_snapshot.rebuild(self.vault)
        (self.vault / "personal" / "reference" / "note-a.md").unlink()

        stats = graph_snapshot.rebuild(self.vault, paths=["personal/reference/note-a.md"])
        self.assertEqual(stats.nodes_removed, 1)
        self.assertEqual(graph_snapshot.outgoing(self.vault, "personal/reference/note-a.md"), [])

    def test_inbox_and_archive_excluded_from_full_walk(self):
        (self.vault / "personal" / "_inbox").mkdir(parents=True)
        self._write("personal/_inbox/staged.md", "---\nslug: staged\n---\nbody")
        (self.vault / "personal" / "_archive").mkdir(parents=True)
        self._write("personal/_archive/old.md", "---\nslug: old\n---\nbody")
        self._write("personal/reference/real.md", "---\nslug: real\n---\nbody")

        stats = graph_snapshot.rebuild(self.vault)
        self.assertEqual(stats.files_touched, 1)  # only real.md


if __name__ == "__main__":
    unittest.main()
