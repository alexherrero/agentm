#!/usr/bin/env python3
"""Tests for recall-trace (Loose Ends Release 8) -- the per-hit evidence
capture in recall.py's prompt_submit() and the `memory-recall trace` CLI
reader.

Two concerns, one file (two views of the same feature):
  - Capture: prompt_submit() packs (slug, hit) pairs through
    _apply_token_budget so recall_counter.record_recall() receives
    duplicate-slug-safe evidence -- proves the bug the design session found
    (filtering `results` by membership in the post-truncation slug list
    mis-identifies entries when two results share a slug) cannot recur.
  - Reader: `memory-recall trace <slug>` reads recall-history.jsonl back
    out, most-recent-first, degrading honestly on the "nothing to show"
    cases.

Run: python3 scripts/test_recall_trace.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_RECALL_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_RECALL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RECALL_SCRIPTS))

import recall  # noqa: E402
import recall_counter  # noqa: E402


def _write_entry(vault: Path, rel: str, body: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\nname: {p.stem}\nkind: feedback\ntags: [test]\n---\n\n{body}",
        encoding="utf-8",
    )


class TestPackedCaptureAlignment(unittest.TestCase):
    """prompt_submit()'s zip-pack of (slug, hit) pairs through
    _apply_token_budget -- the capture half of recall-trace."""

    def _run_and_capture(self, vault: Path, token: str, token_budget: int) -> dict:
        captured = {}

        def _fake_record(query_text, hit_slugs, *, hits=None, **kw):
            captured["hit_slugs"] = hit_slugs
            captured["hits"] = hits
            return {}

        with mock.patch.object(recall_counter, "record_recall", _fake_record):
            recall.prompt_submit(
                vault=vault,
                prompt=token,
                budget_ms=5000,
                token_budget=token_budget,
                mode="stub",
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )
        return captured

    def test_duplicate_slug_entries_keep_their_own_path_in_hits(self):
        """Two entries sharing a slug (different directories) must not
        cross-contaminate each other's evidence."""
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            token = "zorptackle"
            _write_entry(vault, "personal/dup.md", f"{token} first copy " + "w" * 100)
            _write_entry(vault, "personal/sub/dup.md", f"{token} second copy " + "w" * 100)

            captured = self._run_and_capture(vault, token, token_budget=0)

            self.assertIsNotNone(captured.get("hits"))
            self.assertEqual(len(captured["hits"]), 2)
            paths = {h["path"] for h in captured["hits"]}
            self.assertEqual(paths, {"personal/dup.md", "personal/sub/dup.md"})
            for h in captured["hits"]:
                self.assertEqual(h["slug"], "dup")
            ranks = sorted(h["rank"] for h in captured["hits"])
            self.assertEqual(ranks, [1, 2])

    def test_budget_truncated_hits_match_kept_blocks_exactly(self):
        """When _apply_token_budget drops the tail, `hits` must drop the
        identical entries -- not merely the identical count."""
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            token = "zorptackle"
            for i in range(3):
                _write_entry(vault, f"personal/big-{i}.md", f"{token} " + "w" * 500 + f" {i}")

            captured = self._run_and_capture(vault, token, token_budget=150)

            self.assertEqual(captured["hit_slugs"], [h["slug"] for h in captured["hits"]])
            self.assertLess(len(captured["hits"]), 3, "budget=150 should truncate at least one of three ~500-char bodies")

    def test_hits_carry_the_same_evidence_query_already_computed(self):
        """No re-scoring: sim/keyword/combined on a captured hit must match
        what query() itself would report for that entry."""
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            token = "zorptackle"
            _write_entry(vault, "personal/solo.md", f"{token} " + "content " * 20)

            captured = self._run_and_capture(vault, token, token_budget=0)
            direct = recall.query(vault=vault, query_text=token, k=5, mode="stub")

            self.assertEqual(len(captured["hits"]), 1)
            hit = captured["hits"][0]
            match = next(r for r in direct if r["slug"] == "solo")
            self.assertEqual(hit["sim"], match["sim"])
            self.assertEqual(hit["keyword"], match["keyword"])
            self.assertEqual(hit["combined"], match["combined"])


if __name__ == "__main__":
    unittest.main()
