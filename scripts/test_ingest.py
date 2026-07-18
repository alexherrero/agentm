#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/ingest.py — `/memory ingest`,
capture part 2 (capture-article-ingestion plan)."""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import ingest  # noqa: E402

_FIXTURES = _HERE / "fixtures" / "ingest"
_MD_FIXTURE = _FIXTURES / "sample-article.md"
_HTML_FIXTURE = _FIXTURES / "sample-article.html"


def _frontmatter_and_body(path: Path) -> "tuple[dict, str]":
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n\n(.*)$", raw, re.DOTALL)
    assert m, f"no frontmatter block found in {path}"
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, m.group(2)


class IngestBasicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_local_file_ingest_produces_expected_note_count(self) -> None:
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        self.assertTrue(result.success)
        expected_chunks = len(ingest.chunk_text(_MD_FIXTURE.read_text(encoding="utf-8")))
        self.assertEqual(len(result.chunks), expected_chunks)
        self.assertTrue(result.document.is_file())
        self.assertTrue(all(c.is_file() for c in result.chunks))

    def test_nonexistent_source_fails_explicitly(self) -> None:
        result = ingest.ingest(self.vault, "/no/such/file-or-url.md", topic="x")
        self.assertFalse(result.success)
        self.assertIn("not a URL and not a file", result.error)

    def test_mid_sequence_collision_leaves_no_orphaned_notes(self) -> None:
        # Pre-create a target slug this ingest would try to write (as if a
        # prior, unrelated write already landed there) -- a retroactive
        # /review found a version with no pre-flight check and no rollback
        # would still write the document note and any earlier chunks before
        # discovering the collision, orphaning them while reporting failure.
        original = _MD_FIXTURE.read_text(encoding="utf-8")
        expected_chunks = len(ingest.chunk_text(original))
        self.assertGreater(expected_chunks, 1, "fixture must produce >1 chunk to exercise mid-sequence failure")
        doc_slug = f"typography-{ingest._slugify(_MD_FIXTURE.read_text(encoding='utf-8').splitlines()[0])}"
        colliding = self.vault / "personal" / ingest._INGEST_KIND / f"{doc_slug}-chunk-1.md"
        colliding.parent.mkdir(parents=True, exist_ok=True)
        colliding.write_text("pre-existing unrelated content\n", encoding="utf-8")

        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")

        self.assertFalse(result.success)
        doc_path = self.vault / "personal" / ingest._INGEST_KIND / f"{doc_slug}.md"
        chunk0_path = self.vault / "personal" / ingest._INGEST_KIND / f"{doc_slug}-chunk-0.md"
        self.assertFalse(doc_path.exists(), "document note must not be orphaned on a chunk collision")
        self.assertFalse(chunk0_path.exists(), "earlier chunk notes must not be orphaned on a later collision")
        # The pre-existing unrelated file must survive untouched.
        self.assertEqual(colliding.read_text(encoding="utf-8"), "pre-existing unrelated content\n")


class FullDocumentNoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_exactly_one_document_note_byte_for_byte(self) -> None:
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        self.assertTrue(result.success)
        _, body = _frontmatter_and_body(result.document)
        original = _MD_FIXTURE.read_text(encoding="utf-8")
        self.assertEqual(body.rstrip("\n") + "\n", original.rstrip("\n") + "\n")

    def test_document_kind_is_domain_reference(self) -> None:
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        fm, _ = _frontmatter_and_body(result.document)
        self.assertEqual(fm["kind"], "domain-reference")


class ChunkNoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_chunk_count_matches_chunk_text(self) -> None:
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        original = _MD_FIXTURE.read_text(encoding="utf-8")
        self.assertEqual(len(result.chunks), len(ingest.chunk_text(original)))
        self.assertGreater(len(result.chunks), 1, "fixture must be long enough to force multiple chunks")

    def test_every_chunk_links_back_to_document(self) -> None:
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        doc_slug = result.document.stem
        for c in result.chunks:
            _, body = _frontmatter_and_body(c)
            self.assertIn(f"[[{doc_slug}]]", body)

    def test_reading_order_chain_no_cycle(self) -> None:
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        slugs = [c.stem for c in result.chunks]
        bodies = {c.stem: _frontmatter_and_body(c)[1] for c in result.chunks}

        # First chunk: no "(previous)" link.
        self.assertNotIn("(previous)", bodies[slugs[0]])
        # Last chunk: no "(next)" link -- otherwise it'd point back toward
        # the start and the chain would be a cycle, not a path.
        self.assertNotIn("(next)", bodies[slugs[-1]])
        # Every middle chunk links to both its immediate neighbors.
        for i in range(1, len(slugs) - 1):
            self.assertIn(f"[[{slugs[i - 1]}]]", bodies[slugs[i]])
            self.assertIn(f"[[{slugs[i + 1]}]]", bodies[slugs[i]])
        # Unbroken chain: chunk i always links forward to chunk i+1.
        for i in range(len(slugs) - 1):
            self.assertIn(f"[[{slugs[i + 1]}]]", bodies[slugs[i]])


class TopicSuggestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_omitted_topic_suggests_without_writing(self) -> None:
        result = ingest.ingest(self.vault, str(_MD_FIXTURE))
        self.assertFalse(result.success)
        self.assertTrue(result.needs_confirmation)
        self.assertEqual(result.suggested_topic, "the-quiet-discipline-of-paragraph-breaks")
        self.assertEqual(list(self.vault.rglob("*.md")), [])

    def test_provided_topic_skips_suggestion(self) -> None:
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        self.assertTrue(result.success)
        self.assertFalse(result.needs_confirmation)
        self.assertIsNone(result.suggested_topic)


class GroupCorrectnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_every_note_carries_group_personal(self) -> None:
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        fm_doc, _ = _frontmatter_and_body(result.document)
        self.assertEqual(fm_doc["group"], "personal")
        for c in result.chunks:
            fm_chunk, _ = _frontmatter_and_body(c)
            self.assertEqual(fm_chunk["group"], "personal")


class HtmlExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_title_extracted_from_html(self) -> None:
        title, _ = ingest.extract_title_and_text(_HTML_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(title, "Overlap-Aware Chunking, Briefly")

    def test_script_and_style_content_excluded(self) -> None:
        _, text = ingest.extract_title_and_text(_HTML_FIXTURE.read_text(encoding="utf-8"))
        self.assertNotIn("tracking pixel", text)
        self.assertNotIn("font-family", text)

    def test_html_fixture_ingests_end_to_end(self) -> None:
        result = ingest.ingest(self.vault, str(_HTML_FIXTURE), topic="chunking")
        self.assertTrue(result.success)
        self.assertEqual(result.title, "Overlap-Aware Chunking, Briefly")
        self.assertGreaterEqual(len(result.chunks), 1)

    def test_html_fragment_without_document_wrapper_is_stripped(self) -> None:
        # A retroactive /review found the sniff only recognized full-document
        # HTML (<html>/<body>/<title> near the top) -- a fragment with real
        # markup but no document wrapper fell through to the plain-text path
        # unmodified, leaving literal tags in the saved note.
        fragment = (
            '<article><h1>Real Article Title</h1>'
            '<p>Some <b>bold</b> text with a <a href="#">link</a>.</p></article>'
        )
        title, text = ingest.extract_title_and_text(fragment)
        self.assertEqual(title, "Real Article Title")
        self.assertNotIn("<", text)
        self.assertIn("bold", text)

    def test_angle_bracket_placeholder_is_not_misdetected_as_html(self) -> None:
        # This vault's own docs use <placeholder> conventions (e.g.
        # "<url-or-file>") in plain-text/markdown -- these have no matching
        # close tag and must not trip the fragment-HTML sniff.
        plain = "Usage: python3 ingest.py <url-or-file> [--topic <slug>] [--vault-path <path>]"
        self.assertFalse(ingest._looks_like_html(plain))


if __name__ == "__main__":
    unittest.main()
