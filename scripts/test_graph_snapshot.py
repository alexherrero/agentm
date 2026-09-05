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
        (self.vault / "memory" / "reference").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel: str, body: str) -> None:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_round_trip_incoming_and_orphans(self):
        self._write("memory/reference/note-a.md", "---\nslug: note-a\n---\nno links here")
        self._write("memory/reference/note-b.md", "---\nslug: note-b\n---\nsee [[note-a]] for context")
        self._write("memory/reference/note-c.md", "---\nslug: note-c\n---\nnothing points here, points nowhere")

        stats = graph_snapshot.rebuild(self.vault)
        self.assertEqual(stats.files_touched, 3)

        # Reload via a fresh call (no in-process cache to coast on).
        incoming = graph_snapshot.incoming(self.vault, "memory/reference/note-a.md")
        self.assertEqual(incoming, ["memory/reference/note-b.md"])

        orphaned = graph_snapshot.orphans(self.vault)
        self.assertEqual(orphaned, ["memory/reference/note-c.md"])
        # note-a has an incoming edge, note-b has an outgoing edge -- neither is an orphan.
        self.assertNotIn("memory/reference/note-a.md", orphaned)
        self.assertNotIn("memory/reference/note-b.md", orphaned)

    def test_touched_paths_reports_only_newly_extracted_files(self):
        # The "arrived or changed since the last cycle" signal task 4's
        # weekly sweep needs, sourced from this rebuild rather than a
        # second staleness tracker.
        self._write("memory/reference/note-a.md", "---\nslug: note-a\n---\nbody a")
        self._write("memory/reference/note-b.md", "---\nslug: note-b\n---\nbody b")
        first = graph_snapshot.rebuild(self.vault)
        self.assertEqual(sorted(first.touched_paths), [
            "memory/reference/note-a.md", "memory/reference/note-b.md",
        ])

        # Nothing changed -- a second rebuild touches nothing.
        second = graph_snapshot.rebuild(self.vault)
        self.assertEqual(second.touched_paths, [])

        # Only the newly-added file shows up in touched_paths.
        self._write("memory/reference/note-c.md", "---\nslug: note-c\n---\nbody c")
        third = graph_snapshot.rebuild(self.vault)
        self.assertEqual(third.touched_paths, ["memory/reference/note-c.md"])

    def test_outgoing_returns_stored_edges(self):
        self._write("memory/reference/note-a.md", "---\nslug: note-a\n---\nbody")
        self._write("memory/reference/note-b.md", "---\nslug: note-b\n---\nsee [[note-a]]")
        graph_snapshot.rebuild(self.vault)

        edges = graph_snapshot.outgoing(self.vault, "memory/reference/note-b.md")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].source_path, "memory/reference/note-b.md")
        self.assertEqual(edges[0].target, "note-a")

    def test_targeted_incremental_touches_only_given_paths(self):
        self._write("memory/reference/note-a.md", "---\nslug: note-a\n---\nbody a")
        self._write("memory/reference/note-b.md", "---\nslug: note-b\n---\nbody b")
        self._write("memory/reference/note-c.md", "---\nslug: note-c\n---\nbody c")
        graph_snapshot.rebuild(self.vault)  # initial full population

        self._write("memory/reference/note-d.md", "---\nslug: note-d\n---\nsee [[note-a]]")
        stats = graph_snapshot.rebuild(self.vault, paths=["memory/reference/note-d.md"])
        self.assertEqual(stats.files_touched, 1)
        self.assertEqual(stats.edges_written, 1)

        # note-d is now visible to a query without any other file being re-touched.
        incoming = graph_snapshot.incoming(self.vault, "memory/reference/note-a.md")
        self.assertIn("memory/reference/note-d.md", incoming)

    def test_incremental_rebuild_touches_fewer_files_than_a_full_rebuild(self):
        for i in range(5):
            self._write(f"memory/reference/note-{i}.md", f"---\nslug: note-{i}\n---\nbody {i}")
        graph_snapshot.rebuild(self.vault)  # initial full population, 5 files

        self._write("memory/reference/note-new.md", "---\nslug: note-new\n---\nsee [[note-0]]")
        incremental_stats = graph_snapshot.rebuild(self.vault, paths=["memory/reference/note-new.md"])

        # A from-scratch full rebuild over the same 6-file corpus must touch
        # every file -- the coarse contrast the plan's verification criteria
        # asks for (incremental touches 1; a full rebuild from nothing touches 6).
        (graph_snapshot._snapshot_path(self.vault)).unlink()
        full_stats = graph_snapshot.rebuild(self.vault)

        self.assertEqual(incremental_stats.files_touched, 1)
        self.assertEqual(full_stats.files_touched, 6)
        self.assertLess(incremental_stats.files_touched, full_stats.files_touched)

    def test_full_rebuild_is_a_noop_on_unchanged_files(self):
        self._write("memory/reference/note-a.md", "---\nslug: note-a\n---\nbody")
        graph_snapshot.rebuild(self.vault)
        stats = graph_snapshot.rebuild(self.vault)  # nothing changed since last rebuild
        self.assertEqual(stats.files_touched, 0)

    def test_full_rebuild_drops_deleted_files(self):
        self._write("memory/reference/note-a.md", "---\nslug: note-a\n---\nbody")
        self._write("memory/reference/note-b.md", "---\nslug: note-b\n---\nsee [[note-a]]")
        graph_snapshot.rebuild(self.vault)

        (self.vault / "memory" / "reference" / "note-b.md").unlink()
        stats = graph_snapshot.rebuild(self.vault)
        self.assertEqual(stats.nodes_removed, 1)

        incoming = graph_snapshot.incoming(self.vault, "memory/reference/note-a.md")
        self.assertEqual(incoming, [])

    def test_targeted_rebuild_of_a_deleted_path_removes_it(self):
        self._write("memory/reference/note-a.md", "---\nslug: note-a\n---\nbody")
        graph_snapshot.rebuild(self.vault)
        (self.vault / "memory" / "reference" / "note-a.md").unlink()

        stats = graph_snapshot.rebuild(self.vault, paths=["memory/reference/note-a.md"])
        self.assertEqual(stats.nodes_removed, 1)
        self.assertEqual(graph_snapshot.outgoing(self.vault, "memory/reference/note-a.md"), [])

    def test_inbox_and_archive_excluded_from_full_walk(self):
        (self.vault / "memory" / "_inbox").mkdir(parents=True)
        self._write("memory/_inbox/staged.md", "---\nslug: staged\n---\nbody")
        (self.vault / "memory" / "_archive").mkdir(parents=True)
        self._write("memory/_archive/old.md", "---\nslug: old\n---\nbody")
        self._write("memory/reference/real.md", "---\nslug: real\n---\nbody")

        stats = graph_snapshot.rebuild(self.vault)
        self.assertEqual(stats.files_touched, 1)  # only real.md


if __name__ == "__main__":
    unittest.main()


class TestNestedLayoutSiblingSpace(unittest.TestCase):
    """Filing-v2 2b: the vault-root `Projects/` space is a sibling of a nested
    memory root (`.obsidian/` at the vault root, none at the memory root).
    The walk keys its notes relative to the vault root; the rebuild must
    join them back the same way — joining onto the memory root crashed the
    nightly cycle in the lint stage on the live vault (2026-09-05)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "vault"
        (self.root / ".obsidian").mkdir(parents=True)
        self.vault = self.root / "Agent"
        (self.vault / "memory" / "reference").mkdir(parents=True)
        (self.root / "Projects" / "_global" / "style").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_sibling_root_space_note_indexes_and_never_crashes_the_rebuild(self):
        (self.vault / "memory" / "reference" / "note-a.md").write_text("---\nslug: note-a\n---\nhome\n", encoding="utf-8")
        (self.root / "Projects" / "_global" / "style" / "howto.md").write_text(
            "---\nslug: howto\n---\nsee [[note-a]] from the projects space\n", encoding="utf-8")
        stats = graph_snapshot.rebuild(self.vault)
        self.assertEqual(stats.files_touched, 2)
        self.assertIn("Projects/_global/style/howto.md", stats.touched_paths)
        self.assertEqual(graph_snapshot.incoming(self.vault, "memory/reference/note-a.md"), ["Projects/_global/style/howto.md"])
        # A second rebuild is a no-op, and the targeted path resolves too.
        self.assertEqual(graph_snapshot.rebuild(self.vault).files_touched, 0)
        targeted = graph_snapshot.rebuild(self.vault, paths=["Projects/_global/style/howto.md"])
        self.assertEqual(targeted.files_touched, 1)
        self.assertEqual(targeted.nodes_removed, 0)
