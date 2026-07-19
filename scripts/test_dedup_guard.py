#!/usr/bin/env python3
"""Unit coverage for the write-time dedup guard (PLAN-auto-org-dedup-and-lint,
task 2): dedup_guard.py + save_entry()'s guard hook + capture()'s inbox hook
+ ingest's rollback safety.

The save_entry half needs the sqlite-vec backend for its entry_meta lookup
and skips gracefully on the macOS system Python; the capture/inbox half is
a plain frontmatter scan and never skips.

Run directly:
    cd scripts && python3 -m unittest test_dedup_guard
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

import capture  # noqa: E402
import dedup_guard  # noqa: E402
import fingerprint as fp_mod  # noqa: E402
import save  # noqa: E402
import vec_index  # noqa: E402


def _vec_backend_available(vault: Path) -> bool:
    conn = vec_index._open_index(vault)
    if conn is None:
        return False
    conn.close()
    return True


def _index_entry(vault: Path, target: Path) -> str:
    """Upsert `target` into the vec index (stub vector) so entry_meta holds
    its fingerprint — simulating a drained entry. Returns the rel path."""
    rel = str(target.relative_to(vault)).replace("\\", "/")
    vec_index.upsert_entry(vault, rel, [0.0] * vec_index.EMBEDDING_DIM)
    return rel


class TestSaveEntryGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_exact_duplicate_reinforces_no_new_file(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        original = save.save_entry(self.vault, "reference", "the-original", "identical body text")
        _index_entry(self.vault, original)

        info: dict = {}
        result = save.save_entry(
            self.vault, "reference", "a-different-slug", "identical  body   TEXT",
            dedup_info=info,
        )

        self.assertEqual(result, original)  # the existing note's path, not a new one
        self.assertTrue(info["deduplicated"])
        self.assertFalse((self.vault / "personal" / "reference" / "a-different-slug.md").exists())
        content = original.read_text(encoding="utf-8")
        self.assertIn("occurrences: 2", content)

    def test_second_reinforce_increments_again(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        original = save.save_entry(self.vault, "reference", "the-original", "body text")
        _index_entry(self.vault, original)
        save.save_entry(self.vault, "reference", "dup-1", "body text")
        save.save_entry(self.vault, "reference", "dup-2", "body text")
        self.assertIn("occurrences: 3", original.read_text(encoding="utf-8"))

    def test_genuinely_new_note_writes_normally(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        original = save.save_entry(self.vault, "reference", "the-original", "some body")
        _index_entry(self.vault, original)

        info: dict = {}
        result = save.save_entry(self.vault, "reference", "brand-new", "entirely different content", dedup_info=info)
        self.assertFalse(info["deduplicated"])
        self.assertTrue(result.is_file())
        self.assertNotEqual(result, original)

    def test_near_duplicate_but_distinct_still_writes(self):
        # The plan's verification asks for this case asserted explicitly
        # either way. The shipped behavior: the guard is EXACT-only (the
        # Locked design call routes fuzzy merges through a model verdict,
        # and a write-time near-match reinforce would discard the arriving
        # note's real differences without one) -- so a near-duplicate-but-
        # distinct body IS written, and the weekly cluster pass owns it.
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        original = save.save_entry(
            self.vault, "reference", "the-original",
            "we chose sqlite for the index because it is embedded",
        )
        _index_entry(self.vault, original)

        info: dict = {}
        result = save.save_entry(
            self.vault, "reference", "near-dup",
            "we chose sqlite for the index because it is embeddable",  # one word differs
            dedup_info=info,
        )
        self.assertFalse(info["deduplicated"])
        self.assertTrue(result.is_file())
        self.assertNotEqual(result, original)

    def test_caller_supplied_fingerprint_never_triggers_guard(self):
        # A semantic (diagnostics join-key) fingerprint colliding with an
        # existing entry's is that workflow's own affair -- the guard only
        # acts on auto-computed content hashes.
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        first = save.save_entry(
            self.vault, "failure-incident", "incident-a", "trace one", fingerprint="join-key-1",
        )
        _index_entry(self.vault, first)

        info: dict = {}
        second = save.save_entry(
            self.vault, "failure-incident", "incident-b", "trace two", fingerprint="join-key-1",
            dedup_info=info,
        )
        self.assertFalse(info["deduplicated"])
        self.assertTrue(second.is_file())
        self.assertNotEqual(first, second)

    def test_supersedes_write_never_triggers_guard(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        original = save.save_entry(self.vault, "reference", "the-original", "shared body")
        _index_entry(self.vault, original)

        info: dict = {}
        result = save.save_entry(
            self.vault, "reference", "successor", "shared body",
            supersedes="personal/reference/the-original.md", dedup_info=info,
        )
        self.assertFalse(info["deduplicated"])
        self.assertTrue(result.is_file())

    def test_stale_index_row_never_reinforces_changed_content(self):
        # The index lags drain: a row whose live file has since changed
        # must not cause a reinforce (live re-verification).
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        original = save.save_entry(self.vault, "reference", "the-original", "old body")
        _index_entry(self.vault, original)  # entry_meta now holds old body's fp
        old_fp = fp_mod.compute_fingerprint("old body")
        # The live file changes after indexing.
        original.write_text(
            original.read_text(encoding="utf-8").replace("old body", "totally new body"),
            encoding="utf-8",
        )
        self.assertIsNone(dedup_guard.find_vault_duplicate(self.vault, old_fp))

    def test_guard_passes_through_when_backend_unavailable(self):
        # No backend -> find_vault_duplicate returns None -> write proceeds.
        # Runs everywhere; on backend-capable machines it still passes
        # because the vault index is empty for this fresh fixture.
        info: dict = {}
        result = save.save_entry(self.vault, "reference", "plain-note", "plain body", dedup_info=info)
        self.assertTrue(result.is_file())
        self.assertFalse(info["deduplicated"])


class TestCaptureInboxGuard(unittest.TestCase):
    """The capture/inbox half -- frontmatter scan, no sqlite-vec, never skips."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_identical_capture_reinforces_instead_of_suffixing(self):
        r1 = capture.capture(self.vault, "the same thought", slug="thought")
        r2 = capture.capture(self.vault, "the  same   THOUGHT")  # formatting variant, no slug
        self.assertTrue(r1.success and r2.success)
        self.assertTrue(r2.deduplicated)
        self.assertEqual(r2.path, r1.path)
        inbox_files = list((self.vault / "personal" / "_inbox").glob("*.md"))
        self.assertEqual(len(inbox_files), 1)
        self.assertIn("occurrences: 2", r1.path.read_text(encoding="utf-8"))

    def test_distinct_content_same_slug_still_suffixes(self):
        r1 = capture.capture(self.vault, "first idea", slug="idea")
        r2 = capture.capture(self.vault, "second, different idea", slug="idea")
        self.assertTrue(r1.success and r2.success)
        self.assertFalse(r2.deduplicated)
        self.assertEqual({r1.slug, r2.slug}, {"idea", "idea-1"})

    def test_capture_writes_fingerprint_frontmatter(self):
        r = capture.capture(self.vault, "some captured content", slug="cap")
        content = r.path.read_text(encoding="utf-8")
        self.assertIn(f"fingerprint: {fp_mod.compute_fingerprint('some captured content')}", content)


class TestGuardStatusAndCurationFilters(unittest.TestCase):
    """Review-caught defects: matching must be status- and curation-aware.
    A dead note (expired/deleted/superseded) or a curated _always-load rule
    is never a reinforce target."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _flip_status(self, path: Path, new_status: str) -> None:
        path.write_text(
            path.read_text(encoding="utf-8").replace("status: active", f"status: {new_status}", 1),
            encoding="utf-8",
        )

    def test_recapture_of_expired_inbox_candidate_writes_fresh(self):
        r1 = capture.capture(self.vault, "a thought worth keeping", slug="thought")
        # Triage archives in place: the candidate becomes a tombstone.
        r1.path.write_text(
            r1.path.read_text(encoding="utf-8").replace("status: inbox", "status: expired", 1),
            encoding="utf-8",
        )
        r2 = capture.capture(self.vault, "a thought worth keeping")
        self.assertTrue(r2.success)
        self.assertFalse(r2.deduplicated)  # NOT swallowed into the tombstone
        self.assertNotEqual(r2.path, r1.path)
        # The re-capture is a live inbox candidate again, eligible for triage.
        self.assertIn("status: inbox", r2.path.read_text(encoding="utf-8"))

    def test_deleted_note_is_never_reinforced(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        original = save.save_entry(self.vault, "reference", "the-original", "important body")
        _index_entry(self.vault, original)
        self._flip_status(original, "deleted")

        info: dict = {}
        result = save.save_entry(self.vault, "reference", "fresh-save", "important body", dedup_info=info)
        self.assertFalse(info["deduplicated"])
        self.assertTrue(result.is_file())
        self.assertNotEqual(result, original)

    def test_superseded_note_is_never_reinforced(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        original = save.save_entry(self.vault, "reference", "the-original", "chained body")
        _index_entry(self.vault, original)
        self._flip_status(original, "superseded")
        info: dict = {}
        result = save.save_entry(self.vault, "reference", "fresh-save", "chained body", dedup_info=info)
        self.assertFalse(info["deduplicated"])
        self.assertTrue(result.is_file())

    def test_always_load_note_is_never_a_match_target(self):
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        curated = save.save_entry(
            self.vault, "convention", "standing-rule", "the standing rule body", always_load=True,
        )
        _index_entry(self.vault, curated)
        info: dict = {}
        result = save.save_entry(self.vault, "reference", "coincidence", "the standing rule body", dedup_info=info)
        self.assertFalse(info["deduplicated"])
        self.assertTrue(result.is_file())
        self.assertNotIn("occurrences:", curated.read_text(encoding="utf-8"))

    def test_capture_with_new_source_url_refuses_reinforce(self):
        # A link resend deduping into a plain-text candidate would silently
        # discard source_url -- the ingest sweep's trigger. It writes fresh.
        r1 = capture.capture(self.vault, "https://example.com/article and a note")
        r2 = capture.capture(
            self.vault, "https://example.com/article and a note",
            source_url="https://example.com/article",
        )
        self.assertTrue(r2.success)
        self.assertFalse(r2.deduplicated)
        self.assertNotEqual(r2.path, r1.path)
        self.assertIn("source_url:", r2.path.read_text(encoding="utf-8"))

    def test_capture_resend_without_new_metadata_still_reinforces(self):
        r1 = capture.capture(self.vault, "plain thought", source_url="https://example.com/a")
        r2 = capture.capture(self.vault, "plain thought", source_url="https://example.com/a")
        self.assertTrue(r2.deduplicated)
        self.assertEqual(r2.path, r1.path)


class TestIngestDocDedupShortCircuit(unittest.TestCase):
    """Review-caught defect: a doc-note dedup must not write an orphaned
    chunk family whose backlinks point at a document that never
    materialized."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")

    def tearDown(self):
        self._tmp.cleanup()

    def test_reingest_same_article_different_topic_writes_nothing(self):
        import ingest

        fixture = _HERE / "fixtures" / "ingest" / "sample-article.md"
        article_text = fixture.read_text(encoding="utf-8")

        first = ingest.ingest(self.vault, str(fixture), topic="alpha", raw_content=article_text)
        self.assertTrue(first.success)
        # Index the first run's doc note (as a drain would).
        _index_entry(self.vault, first.document)
        files_before = sorted((self.vault / "personal").rglob("*.md"))

        second = ingest.ingest(self.vault, str(fixture), topic="beta", raw_content=article_text)

        self.assertTrue(second.success)
        self.assertTrue(second.deduplicated)
        self.assertEqual(second.document, first.document)  # the EXISTING doc
        self.assertEqual(second.chunks, [])
        files_after = sorted((self.vault / "personal").rglob("*.md"))
        self.assertEqual(files_before, files_after)  # zero new files, no orphan family
        self.assertIn("occurrences: 2", first.document.read_text(encoding="utf-8"))


class TestIngestRollbackSafety(unittest.TestCase):
    """A failed ingest whose doc note was a dedup reinforce must never
    unlink the pre-existing note it reinforced."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_failed_ingest_never_deletes_the_reinforced_note(self):
        # A CHUNK save that dedups into a pre-existing note, followed by a
        # later chunk failure: the rollback must unlink only what this run
        # actually created, never the reinforced pre-existing note. (The
        # doc-dedup case short-circuits before chunks now, so the chunk
        # path is where dedup_info's rollback protection earns its keep.)
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        import unittest.mock
        import ingest

        fixture = _HERE / "fixtures" / "ingest" / "sample-article.md"
        article_text = fixture.read_text(encoding="utf-8")
        title, text = ingest.extract_title_and_text(article_text)
        chunks = ingest.chunk_text(text)
        self.assertGreater(len(chunks), 2)

        # Plant a note whose body EXACTLY matches what chunk 0 will be
        # (chunk text + reading-order nav footer), so that chunk's save
        # dedups into it.
        title_slug = ingest._slugify(title)
        doc_slug = f"other-{title_slug}"
        chunk0_body = f"{chunks[0]}\n\n---\n\nFrom [[{doc_slug}]] · [[{doc_slug}-chunk-1]] (next)"
        existing = save.save_entry(self.vault, "domain-reference", "already-here", chunk0_body)
        _index_entry(self.vault, existing)

        # doc (1) writes fresh, chunk 0 (2) dedups into the plant, then a
        # later chunk raises -> rollback.
        real_save_entry = ingest.save_entry
        calls = {"n": 0}

        def failing_save_entry(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise ValueError("injected mid-sequence failure")
            return real_save_entry(*args, **kwargs)

        with unittest.mock.patch.object(ingest, "save_entry", side_effect=failing_save_entry):
            result = ingest.ingest(self.vault, str(fixture), topic="other", raw_content=article_text)

        self.assertFalse(result.success)
        # The reinforced pre-existing note survives the rollback...
        self.assertTrue(existing.is_file())
        self.assertIn("occurrences: 2", existing.read_text(encoding="utf-8"))
        # ...while the doc note this run DID create was rolled back.
        doc_target = self.vault / "personal" / ingest._INGEST_KIND / f"{doc_slug}.md"
        self.assertFalse(doc_target.exists())


if __name__ == "__main__":
    unittest.main()
