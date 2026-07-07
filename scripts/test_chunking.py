#!/usr/bin/env python3
"""Tests for chunking.py — V6-10 chunk boundaries (PLAN-wave-e-v6-index task 6)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import chunking  # noqa: E402


class TestChunkText(unittest.TestCase):

    def test_short_body_is_one_chunk(self):
        body = "A short entry, well under the chunk size."
        chunks = chunking.chunk_text(body)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], body)

    def test_empty_body_returns_single_empty_chunk(self):
        self.assertEqual(chunking.chunk_text(""), [""])

    def test_long_body_splits_into_multiple_chunks(self):
        paragraphs = [f"Paragraph {i} with enough words to add real length to this test case." for i in range(20)]
        body = "\n\n".join(paragraphs)
        chunks = chunking.chunk_text(body, chunk_chars=200, overlap_chars=20)
        self.assertGreater(len(chunks), 1)

    def test_no_content_lost_across_chunks(self):
        # Every paragraph's distinctive marker word must appear somewhere
        # in the chunked output — chunking must never silently drop text.
        paragraphs = [f"UNIQUEMARKER{i} filler filler filler filler filler." for i in range(15)]
        body = "\n\n".join(paragraphs)
        chunks = chunking.chunk_text(body, chunk_chars=150, overlap_chars=20)
        joined = " ".join(chunks)
        for i in range(15):
            self.assertIn(f"UNIQUEMARKER{i}", joined)

    def test_oversized_single_paragraph_is_hard_split_not_dropped(self):
        huge_paragraph = "word " * 500  # one giant paragraph, no blank lines
        chunks = chunking.chunk_text(huge_paragraph, chunk_chars=200, overlap_chars=20)
        self.assertGreater(len(chunks), 1)
        self.assertIn("word", "".join(chunks))

    def test_overlap_repeats_trailing_context_in_next_chunk(self):
        paragraphs = [f"Section{i} " + ("x" * 180) for i in range(5)]
        body = "\n\n".join(paragraphs)
        chunks = chunking.chunk_text(body, chunk_chars=200, overlap_chars=50)
        # Some suffix of chunk[i] should reappear as a prefix of chunk[i+1].
        for i in range(len(chunks) - 1):
            tail = chunks[i][-50:]
            self.assertIn(tail[:20], chunks[i + 1])


if __name__ == "__main__":
    unittest.main()
