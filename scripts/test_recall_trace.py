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
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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

        # This class is about the in-process engine's own hit-packing
        # (module docstring: "the capture half of recall-trace"), so the
        # daemon fast path prompt_submit() tries first has to be forced to
        # decline — otherwise it answers from whatever real vault `agentmd`
        # resolves rather than this test's tempdir fixture, and `captured`
        # never fills. Before the hybrid-retrieval cutover, an installed
        # daemon that rejected `-mode`/`-question` as unknown flags made the
        # decline happen by accident; a daemon that recognizes them no
        # longer obliges, so the fixture says so directly.
        with mock.patch.object(recall_counter, "record_recall", _fake_record), \
             mock.patch.object(recall.subprocess, "run", side_effect=FileNotFoundError()):
            recall.prompt_submit(
                vault=vault,
                prompt=token,
                budget_ms=5000,
                token_budget=token_budget,
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
            _write_entry(vault, "memory/dup.md", f"{token} first copy " + "w" * 100)
            _write_entry(vault, "memory/sub/dup.md", f"{token} second copy " + "w" * 100)

            captured = self._run_and_capture(vault, token, token_budget=0)

            self.assertIsNotNone(captured.get("hits"))
            self.assertEqual(len(captured["hits"]), 2)
            paths = {h["path"] for h in captured["hits"]}
            self.assertEqual(paths, {"memory/dup.md", "memory/sub/dup.md"})
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
                _write_entry(vault, f"memory/big-{i}.md", f"{token} " + "w" * 500 + f" {i}")

            captured = self._run_and_capture(vault, token, token_budget=150)

            self.assertEqual(captured["hit_slugs"], [h["slug"] for h in captured["hits"]])
            self.assertLess(len(captured["hits"]), 3, "budget=150 should truncate at least one of three ~500-char bodies")

    def test_hits_carry_the_same_evidence_query_already_computed(self):
        """No re-scoring: sim/keyword/combined on a captured hit must match
        what query() itself would report for that entry."""
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            token = "zorptackle"
            _write_entry(vault, "memory/solo.md", f"{token} " + "content " * 20)

            captured = self._run_and_capture(vault, token, token_budget=0)
            direct = recall.query(vault=vault, query_text=token, k=5)

            self.assertEqual(len(captured["hits"]), 1)
            hit = captured["hits"][0]
            match = next(r for r in direct if r["slug"] == "solo")
            self.assertEqual(hit["sim"], match["sim"])
            self.assertEqual(hit["keyword"], match["keyword"])
            self.assertEqual(hit["combined"], match["combined"])


class TestTraceReader(unittest.TestCase):
    """`memory-recall trace <slug>` -- the read half of recall-trace."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.history_path = Path(self._tmp.name) / "recall-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, slug: str, n: int = 3) -> str:
        out = io.StringIO()
        exit_code = recall.trace(slug=slug, n=n, history_path=self.history_path, stdout=out)
        self.assertEqual(exit_code, 0, "trace() must always return 0 -- a reader, not a hook")
        return out.getvalue()

    def test_no_ledger_file_at_all(self):
        out = self._run("anything")
        self.assertIn("no recall ledger found", out)

    def test_non_object_json_line_does_not_crash_trace(self):
        """A ledger line can be syntactically valid JSON without being the
        row shape every write produces (e.g. a bare list). Must degrade,
        never raise -- caught by adversarial review against the first
        shipped cut of trace(), which only guarded JSONDecodeError."""
        self.history_path.write_text("[1, 2, 3]\n", encoding="utf-8")
        out = self._run("anything")  # must not raise
        self.assertIn("never recalled", out)

    def test_hits_entry_that_is_not_a_dict_does_not_crash_trace(self):
        recall_counter.record_recall("q", ["s"], history_path=self.history_path)
        # Hand-corrupt the row's `hits` to a shape record_recall would never
        # itself write, to prove the reader survives it regardless.
        row = json.loads(self.history_path.read_text(encoding="utf-8"))
        row["hits"] = ["not-a-dict"]
        self.history_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        out = self._run("s")  # must not raise
        self.assertIn("recalled before trace capture landed", out)

    def test_undecodable_bytes_in_ledger_does_not_crash_trace(self):
        self.history_path.write_bytes(b"\xff\xfe not valid utf-8\n")
        out = self._run("anything")  # must not raise
        self.assertIn("unreadable", out)

    def test_slug_never_recalled(self):
        recall_counter.record_recall("q", ["some-other-slug"], history_path=self.history_path)
        out = self._run("never-seen")
        self.assertIn("never recalled", out)

    def test_slug_recalled_before_trace_capture_landed(self):
        """hit_slugs contains the slug but the row carries no `hits` key at
        all -- a pre-recall-trace event, not "never recalled"."""
        recall_counter.record_recall("q", ["old-slug"], history_path=self.history_path)  # hits=None
        out = self._run("old-slug")
        self.assertNotIn("never recalled", out)
        self.assertIn("recalled before trace capture landed", out)

    def test_normal_case_shows_score_breakdown_at_fixed_precision(self):
        hit = {"slug": "s", "path": "memory/s.md", "sim": 0.812345, "keyword": 12.345,
               "combined": 0.0163456, "rank": 1, "lifecycle_tier": "volatile"}
        recall_counter.record_recall("q", ["s"], hits=[hit], history_path=self.history_path)
        out = self._run("s")
        self.assertIn("path: memory/s.md", out)
        self.assertIn("sim=0.81", out)
        self.assertIn("keyword=12.3", out)
        self.assertIn("combined=0.0163", out)
        self.assertIn("rank=1", out)
        self.assertIn("tier: volatile", out)
        # Never the raw float repr (would print a long tail for a BM25 score).
        self.assertNotIn("12.345", out)

    def test_most_recent_first_and_dash_n_limits_count(self):
        for i in range(5):
            recall_counter.record_recall(
                f"q{i}", ["s"],
                hits=[{"slug": "s", "path": "s.md", "rank": 1}],
                now=datetime(2026, 7, 1, 12, i, 0, tzinfo=timezone.utc),
                history_path=self.history_path,
            )
        out_default = self._run("s")  # default n=3
        self.assertEqual(out_default.count("### s @"), 3)
        # The newest (minute 4) must appear before the oldest shown (minute 2).
        self.assertLess(out_default.index("12:04:00"), out_default.index("12:02:00"))
        self.assertIn("2 more recall event(s)", out_default)

        out_one = self._run("s", n=1)
        self.assertEqual(out_one.count("### s @"), 1)
        self.assertIn("12:04:00", out_one)

    def test_duplicate_slug_within_one_event_shows_both(self):
        """A single recall event where two distinct-path entries share a
        slug (task 2's exact scenario) must surface both, not just one."""
        hits = [
            {"slug": "dup", "path": "memory/dup.md", "rank": 1},
            {"slug": "dup", "path": "memory/sub/dup.md", "rank": 2},
        ]
        recall_counter.record_recall("q", ["dup", "dup"], hits=hits, history_path=self.history_path)
        out = self._run("dup")
        self.assertIn("memory/dup.md", out)
        self.assertIn("memory/sub/dup.md", out)
        self.assertEqual(out.count("### dup @"), 2)


if __name__ == "__main__":
    unittest.main()
