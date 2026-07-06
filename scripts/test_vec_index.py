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


class TestV6_11ExtractMeta(unittest.TestCase):
    """_extract_meta_from_file — pure frontmatter parsing, no sqlite-vec
    dependency, so these never skip (agentm-memory-index.md)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel: str, frontmatter: str, body: str = "body") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
        return p

    def test_extracts_kind_status_created_tags_group(self):
        p = self._write(
            "note.md",
            "kind: reference\nstatus: active\ncreated: 2026-07-01\n"
            "tags: [a, b]\ngroup: personal/reference\nslug: my-note\n",
        )
        meta = vec_index._extract_meta_from_file(p)
        self.assertEqual(meta["kind"], "reference")
        self.assertEqual(meta["status"], "active")
        self.assertEqual(meta["created"], "2026-07-01")
        self.assertEqual(meta["group_name"], "personal/reference")
        self.assertEqual(meta["slug"], "my-note")
        self.assertEqual(vec_index.json.loads(meta["tags"]), ["a", "b"])

    def test_project_derived_from_projects_group_path(self):
        p = self._write("note.md", "group: projects/agentm/decisions\n")
        meta = vec_index._extract_meta_from_file(p)
        self.assertEqual(meta["project"], "agentm")

    def test_project_none_for_a_non_project_group(self):
        p = self._write("note.md", "group: personal/reference\n")
        meta = vec_index._extract_meta_from_file(p)
        self.assertIsNone(meta["project"])

    def test_slug_falls_back_to_filename_stem_without_frontmatter_slug(self):
        p = self._write("fallback-name.md", "kind: reference\n")
        meta = vec_index._extract_meta_from_file(p)
        self.assertEqual(meta["slug"], "fallback-name")

    def test_fingerprint_absent_by_default(self):
        p = self._write("note.md", "kind: reference\n")
        meta = vec_index._extract_meta_from_file(p)
        self.assertIsNone(meta["fingerprint"])

    def test_fingerprint_read_when_present(self):
        p = self._write("note.md", "kind: reference\nfingerprint: abc123\n")
        meta = vec_index._extract_meta_from_file(p)
        self.assertEqual(meta["fingerprint"], "abc123")

    def test_unreadable_file_returns_all_none_never_raises(self):
        meta = vec_index._extract_meta_from_file(self.root / "nonexistent.md")
        self.assertTrue(all(v is None for v in meta.values()))

    def test_no_frontmatter_returns_all_none(self):
        p = self.root / "plain.md"
        p.write_text("just a body, no frontmatter", encoding="utf-8")
        meta = vec_index._extract_meta_from_file(p)
        self.assertTrue(all(v is None for v in meta.values()))


class TestV6_11Migration(unittest.TestCase):
    """The additive entry_meta migration + index creation — plain sqlite3,
    no sqlite-vec extension needed, so these never skip either."""

    def test_migrate_adds_all_eight_columns_idempotently(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE entry_meta (rowid INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, "
            "updated_at TEXT NOT NULL, indexed_at INTEGER NOT NULL DEFAULT 0)"
        )
        for column in vec_index._V6_11_COLUMNS:
            self.assertFalse(vec_index._has_column(conn, "entry_meta", column))

        first = vec_index._migrate_v6_11(conn)
        self.assertTrue(first)
        for column in vec_index._V6_11_COLUMNS:
            self.assertTrue(vec_index._has_column(conn, "entry_meta", column))

        # Idempotent: a second call on an already-migrated table is a no-op.
        second = vec_index._migrate_v6_11(conn)
        self.assertFalse(second)
        conn.close()

    def test_ensure_indexes_is_idempotent(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE entry_meta (rowid INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, "
            "updated_at TEXT NOT NULL, indexed_at INTEGER NOT NULL DEFAULT 0)"
        )
        vec_index._migrate_v6_11(conn)
        vec_index._ensure_v6_11_indexes(conn)
        vec_index._ensure_v6_11_indexes(conn)  # second call must not raise
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        names = {r[0] for r in rows}
        for column in vec_index._V6_11_INDEXED_COLUMNS:
            self.assertIn(f"idx_entry_meta_{column}", names)
        conn.close()


class TestV6_11UpsertPopulatesMetadata(unittest.TestCase):
    """upsert_entry populates the new columns from the source file's
    frontmatter. Needs the real sqlite-vec backend — skips gracefully."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "projects" / "agentm" / "decisions").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_upsert_populates_v6_11_columns(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        rel = "projects/agentm/decisions/note.md"
        (self.vault / rel).write_text(
            "---\nkind: decision\nstatus: active\ncreated: 2026-07-06\n"
            "tags: [architecture]\ngroup: projects/agentm/decisions\nslug: note\n"
            "---\nbody",
            encoding="utf-8",
        )
        embedding = [0.0] * vec_index.EMBEDDING_DIM
        self.assertTrue(vec_index.upsert_entry(self.vault, rel, embedding))

        conn = vec_index._open_index(self.vault)
        row = conn.execute(
            "SELECT kind, status, project, group_name, tags FROM entry_meta WHERE path = ?",
            (rel,),
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("decision", "active", "agentm", "projects/agentm/decisions",
                               vec_index.json.dumps(["architecture"])))


if __name__ == "__main__":
    unittest.main()
