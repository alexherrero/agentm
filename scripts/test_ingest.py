#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/ingest.py — `/memory ingest`,
capture part 2 (capture-article-ingestion plan)."""
from __future__ import annotations

import re
import shutil
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

    def test_local_file_ingest_writes_one_note_and_no_chunk_notes(self) -> None:
        # One outside source is one note (agentm-vault § Capture, amended
        # 2026-09-28): the index chunks a long note, and ingest writes none.
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        self.assertTrue(result.success)
        self.assertTrue(result.document.is_file())
        self.assertEqual(result.chunks, [])
        self.assertEqual(list(self.vault.rglob("*.md")), [result.document])
        self.assertEqual(list(self.vault.rglob("*-chunk-*")), [])

    def test_nonexistent_source_fails_explicitly(self) -> None:
        result = ingest.ingest(self.vault, "/no/such/file-or-url.md", topic="x")
        self.assertFalse(result.success)
        self.assertIn("not a URL and not a file", result.error)

    def test_raw_content_skips_fetch_and_preserves_provenance(self) -> None:
        # The ingest sweep (capture part 3) fetches once at staging time and
        # calls ingest() again at promotion time with the already-fetched
        # text -- raw_content must skip read_source()/fetch_url() entirely
        # and the caller-supplied source_url/source_fetched (the ORIGINAL
        # fetch's provenance) must land in the frontmatter unchanged, not
        # be re-derived from this call's own time.
        result = ingest.ingest(
            self.vault, "candidate-placeholder", topic="phone-test",
            raw_content="# A Phone Capture\n\nForwarded article text.",
            source_url="https://example.com/article",
            source_fetched="2026-07-18T04:00:00+00:00",
        )
        self.assertTrue(result.success)
        content = result.document.read_text(encoding="utf-8")
        self.assertIn("source_url: https://example.com/article", content)
        self.assertIn("source_fetched: 2026-07-18T04:00:00+00:00", content)

    def test_a_slug_taken_by_something_else_is_refused_untouched(self) -> None:
        # The destination is taken from a real write rather than rebuilt here:
        # a hand-rolled path went stale once filing v2 routed a memory type to
        # its class, and a collision staged there passed vacuously.
        probe_vault = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, probe_vault, True)
        probe = ingest.ingest(probe_vault, str(_MD_FIXTURE), topic="typography")
        self.assertTrue(probe.success, probe.error)
        colliding = self.vault / probe.document.relative_to(probe_vault)
        colliding.parent.mkdir(parents=True, exist_ok=True)
        colliding.write_text("pre-existing unrelated content\n", encoding="utf-8")

        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")

        self.assertFalse(result.success)
        self.assertIn("nothing written", result.error)
        self.assertEqual(colliding.read_text(encoding="utf-8"), "pre-existing unrelated content\n")
        self.assertEqual(list(self.vault.rglob("*.md")), [colliding])


class ReingestUpdatesInPlaceTests(unittest.TestCase):
    """The same page ingested again updates its note in place (the operator's
    ruling 6b): the path and links stay, the body is the new text."""

    URL = "https://example.com/article"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name)

    def _ingest(self, text, url=None):
        return ingest.ingest(self.vault, "candidate", topic="agents", raw_content=f"# The Article\n\n{text}",
                             source_url=url or self.URL, source_fetched="2026-09-28T00:00:00+00:00")

    def test_the_same_page_again_replaces_the_body_in_place(self) -> None:
        first = self._ingest("The first version.")
        path = first.document
        path.write_text(path.read_text(encoding="utf-8").replace("slug: ", "importance: 8\nslug: ", 1),
                        encoding="utf-8")
        again = self._ingest("The page as it reads now.")
        self.assertTrue(again.success, again.error)
        self.assertTrue(again.updated)
        self.assertEqual(again.document, path)
        self.assertEqual(list(self.vault.rglob("*.md")), [path])
        fm, body = _frontmatter_and_body(path)
        self.assertIn("The page as it reads now.", body)
        self.assertNotIn("The first version.", body)
        self.assertEqual(fm["importance"], "8", "a field the operator set stays")

    def test_the_same_page_unchanged_writes_nothing(self) -> None:
        first = self._ingest("Same text.")
        before = first.document.read_bytes()
        again = self._ingest("Same text.")
        self.assertTrue(again.success)
        self.assertFalse(again.updated)
        self.assertTrue(again.deduplicated)
        self.assertEqual(first.document.read_bytes(), before)

    def test_another_page_under_the_same_title_is_refused(self) -> None:
        first = self._ingest("One page.")
        other = self._ingest("Another page.", url="https://example.com/elsewhere")
        self.assertFalse(other.success)
        self.assertIn("nothing written", other.error)
        self.assertIn("One page.", first.document.read_text(encoding="utf-8"))


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

    def test_document_is_typed_as_a_reference(self) -> None:
        """Same intent this test always had — an ingested document is a
        reference — against the collapsed taxonomy. `domain-reference` is retired
        into `reference`, and a memory carries its value in `type`, not `kind`:
        `kind` is for records, which an ingested document is not."""
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        fm, _ = _frontmatter_and_body(result.document)
        self.assertEqual(fm["type"], "reference")
        self.assertNotIn("kind", fm)


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

    def test_no_note_carries_the_retired_group(self) -> None:
        # `group: memory` retired from the memory card with the card backfill
        # (agentm-vault § The card): the class directory says where it lives.
        result = ingest.ingest(self.vault, str(_MD_FIXTURE), topic="typography")
        fm_doc, _ = _frontmatter_and_body(result.document)
        self.assertNotIn("group", fm_doc)


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
        self.assertTrue(result.document.is_file())
        self.assertEqual(result.chunks, [])

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


class PreflightCollisionTests(unittest.TestCase):
    """The pre-flight exists so a slug collision refuses cleanly instead of
    writing part of the note family and rolling it back. It regressed to a
    no-op when filing v2 moved the destination out from under its hardcoded
    path formula, and stayed that way because nothing here asked. These are
    the tests that ask: both fail against the hardcoded version, which reaches
    `save_entry`'s own single-path error by way of the rollback."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        (self.vault / "memory").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _semantic(self) -> Path:
        return self.vault / "memory" / "semantic"

    def test_repeat_ingest_refuses_naming_every_colliding_slug(self) -> None:
        args = dict(topic="widgets", raw_content="A short body about widgets.")
        first = ingest.ingest(self.vault, "src", **args)
        self.assertTrue(first.success, first.error)
        before = sorted(p.name for p in self._semantic().glob("*.md"))

        second = ingest.ingest(self.vault, "src", **args)
        self.assertFalse(second.success)
        # The pre-flight's own message, not save_entry's: it reports the whole
        # colliding set at once, which is the point of checking before writing.
        self.assertIn("nothing written", second.error)
        self.assertIn(first.document.stem, second.error)
        self.assertEqual(sorted(p.name for p in self._semantic().glob("*.md")), before)


if __name__ == "__main__":
    unittest.main()
