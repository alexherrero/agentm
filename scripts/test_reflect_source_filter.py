#!/usr/bin/env python3
"""Unit tests for reflect.py's transcript source filter (R0.3 / agentmExperience#2).

Pre-fix, `_discover_transcripts` globs every `.jsonl` under `projects_root`
with no filtering — subagent transcripts and workflow journals get mined as
if they were operator conversation. Combined with the noisy bare
always/never HIGH pattern, this auto-saved 335 junk "never-*"/"always-*"
preference files into the live vault.

Covers two independent fixes:
  1. `_discover_transcripts` excludes `subagents/`, `wf_*`, and `journal.jsonl`.
  2. The bare always/never pattern no longer produces an unconditional HIGH
     auto-save from a machine-generated transcript (or from any transcript) —
     it's demoted to MEDIUM, so `route_candidates` sends it to `_inbox/`
     rather than the canonical preferences path.

Run: python3 scripts/test_reflect_source_filter.py
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


def _write_transcript(path: Path, user_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"type": "user", "message": {"role": "user", "content": user_text}})
    path.write_text(line + "\n", encoding="utf-8")


class TestDiscoverTranscripts(unittest.TestCase):
    """`_discover_transcripts` must exclude machine-generated transcript trees."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_excludes_subagents_path(self) -> None:
        operator = self.root / "proj" / "sess-1.jsonl"
        subagent = self.root / "proj" / "subagents" / "agent-abc.jsonl"
        _write_transcript(operator, "always use tabs")
        _write_transcript(subagent, "always use tabs")
        found = reflect._discover_transcripts(self.root)
        self.assertIn(operator.resolve(), found)
        self.assertNotIn(subagent.resolve(), found)

    def test_excludes_wf_star_path(self) -> None:
        workflow = self.root / "proj" / "subagents" / "workflows" / "wf_abc123" / "sess.jsonl"
        _write_transcript(workflow, "always use tabs")
        found = reflect._discover_transcripts(self.root)
        self.assertNotIn(workflow.resolve(), found)

    def test_excludes_journal_jsonl_filename(self) -> None:
        journal = self.root / "proj" / "journal.jsonl"
        _write_transcript(journal, "always use tabs")
        found = reflect._discover_transcripts(self.root)
        self.assertNotIn(journal.resolve(), found)

    def test_includes_ordinary_operator_transcript(self) -> None:
        operator = self.root / "proj" / "sess-1.jsonl"
        _write_transcript(operator, "hello")
        found = reflect._discover_transcripts(self.root)
        self.assertEqual(found, [operator.resolve()])


class TestBareAlwaysNeverDemotion(unittest.TestCase):
    """The bare always/never pattern must not auto-save at HIGH confidence."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_zero_high_confidence_from_bare_always_never(self) -> None:
        transcript = self.root / "sess.jsonl"
        _write_transcript(
            transcript,
            "the sidebar was never touched in that range, it always breaks the index",
        )
        result = reflect.mine_transcript(transcript)
        bare = [
            c for c in result["memory_candidates"]
            if c.rationale == reflect._BARE_ALWAYS_NEVER_RATIONALE
        ]
        self.assertTrue(bare, "expected the bare always/never pattern to fire")
        for c in bare:
            self.assertNotEqual(
                c.confidence, "HIGH",
                f"bare always/never candidate {c.slug!r} must not auto-save at HIGH",
            )

    def test_other_preference_patterns_stay_high(self) -> None:
        transcript = self.root / "sess.jsonl"
        _write_transcript(transcript, "I prefer tabs over spaces for indentation")
        result = reflect.mine_transcript(transcript)
        prefs = [c for c in result["memory_candidates"] if c.category == "preferences"]
        self.assertTrue(prefs, "expected the explicit preference pattern to fire")
        self.assertTrue(any(c.confidence == "HIGH" for c in prefs))


if __name__ == "__main__":
    unittest.main()
