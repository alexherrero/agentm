#!/usr/bin/env python3
"""Junk-slug regression corpus (R0.3 / agentmExperience#2).

A small fixture of known-junk excerpts pulled from the actual polluted
cohort in the vault (mid-sentence "always"/"never" usage — discussion, not
an operator directive). Asserts none of them produce an unconditional HIGH
auto-save through `mine_transcript` any more; each still routes to `_inbox/`
via the tri-modal MEDIUM path so a real signal isn't silently dropped.

Run: python3 scripts/test_junk_slug_corpus.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_SCRIPTS = _REPO_ROOT / "harness" / "skills" / "memory" / "scripts"
if str(_MEMORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SCRIPTS))

import reflect  # noqa: E402

# Fixture excerpts modeled on real junk found in the vault's
# personal/preferences/ — mid-sentence "always"/"never" usage mined as if it
# were an operator preference statement. None of these are the operator
# stating a preference; they're quoted prose / discussion / git-history
# narration that happens to contain the word.
_JUNK_EXCERPTS = [
    "_Sidebar.md was never touched in the e505bd3..HEAD range, so the two indices were left inconsistent.",
    "raw data and specific trade-offs imply importance. Never use descriptive adjectives or filler.",
    "the queue has been stuck for days, and never had token budget freed up to drain it.",
    "the seam (lines 712-739); this never blocks the write and needs a follow-up.",
    "the resolver falls back to the active plan** (never pre-persisted before this fix).",
]


class TestJunkSlugCorpus(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _mine(self, text: str) -> dict:
        transcript = self.root / "sess.jsonl"
        line = json.dumps({"type": "user", "message": {"role": "user", "content": text}})
        transcript.write_text(line + "\n", encoding="utf-8")
        return reflect.mine_transcript(transcript)

    def test_junk_corpus_never_auto_saves_high(self) -> None:
        for excerpt in _JUNK_EXCERPTS:
            with self.subTest(excerpt=excerpt[:40]):
                result = self._mine(excerpt)
                bare = [
                    c for c in result["memory_candidates"]
                    if c.rationale == reflect._BARE_ALWAYS_NEVER_RATIONALE
                ]
                self.assertTrue(bare, "fixture should still trigger the bare pattern")
                for c in bare:
                    self.assertNotEqual(c.confidence, "HIGH")


if __name__ == "__main__":
    unittest.main()
