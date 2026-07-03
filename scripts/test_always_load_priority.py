#!/usr/bin/env python3
"""Tests for always-load priority ordering + truncation visibility (R0.8 / voice#0).

Pre-fix, `session_start` sorted `_always-load/*.md` alphabetically and
`_apply_token_budget` stopped at the FIRST entry that overflowed the budget,
dropping the entire alphabetical tail. On the operator's real corpus this
dropped 19 of 37 entries — including `pii-guardrails-public-repo` and
`vault-memory-overrides-default` — because three large "voice" style-guide
files sorted early and consumed the whole 20k-token budget before essential
guardrails further down the alphabet ever got a chance.

Covers:
  1. A large entry that would fill the budget doesn't crowd out smaller,
     higher-priority entries that alphabetically sort after it.
  2. `priority: low` entries sort last (opt-out demotion, used by the heavy
     voice files) and `priority: high` sort first.
  3. The stdout truncation NOTE names the omitted slugs explicitly.

Pure-Python (no bash subprocess, no vec index required).

Run: python3 scripts/test_always_load_priority.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_RECALL_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_RECALL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RECALL_SCRIPTS))

import recall  # noqa: E402


def _write_entry(vault: Path, slug: str, body: str, *, priority: str | None = None) -> Path:
    """Write a minimal always-load entry, optionally with `priority:` frontmatter."""
    al_dir = vault / "personal" / "_always-load"
    al_dir.mkdir(parents=True, exist_ok=True)
    fm_lines = ["---", f"name: {slug}", f"description: test entry {slug}"]
    if priority:
        fm_lines.append(f"priority: {priority}")
    fm_lines.append("---")
    content = "\n".join(fm_lines) + "\n\n" + body
    path = al_dir / f"{slug}.md"
    path.write_text(content, encoding="utf-8")
    return path


class TestAlwaysLoadPriorityOrdering(unittest.TestCase):

    def test_small_high_priority_entries_survive_a_large_early_entry(self) -> None:
        """Entry `a-large` sorts first alphabetically and alone would fill the
        budget; `b-small` and `c-small` are marked high-priority. Both small
        entries must still load — the exact voice#0 failure mode: pre-fix,
        `a-large` alone consumed the whole budget and `b-small`/`c-small`
        never got a chance because they sort AFTER it alphabetically."""
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            # ~200 tokens (800 chars) — big enough to fill most of a small budget.
            _write_entry(vault, "a-large", "x" * 800)
            _write_entry(vault, "b-small", "y" * 40, priority="high")
            _write_entry(vault, "c-small", "z" * 40, priority="high")

            stdout = io.StringIO()
            stderr = io.StringIO()
            # Budget fits b-small + c-small (~10 tokens each) comfortably but
            # NOT a-large (~200 tokens) — old code (alphabetical + break-at-
            # first-overflow) would drop everything once a-large overflowed.
            recall.session_start(
                vault=vault, token_budget=40, stdout=stdout, stderr=stderr,
            )
            out = stdout.getvalue()
            self.assertIn("### b-small", out)
            self.assertIn("### c-small", out)
            self.assertNotIn("### a-large", out)
            # Named in the truncation NOTE as omitted, not silently dropped.
            self.assertIn("a-large", out)

    def test_low_priority_entry_sorts_last_even_when_alphabetically_first(self) -> None:
        """A `priority: low` entry that sorts first alphabetically must not
        crowd out a normal-priority entry that sorts after it."""
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            _write_entry(vault, "a-heavy-voice-file", "x" * 800, priority="low")
            _write_entry(vault, "z-essential-guardrail", "y" * 40)

            stdout = io.StringIO()
            stderr = io.StringIO()
            recall.session_start(
                vault=vault, token_budget=40, stdout=stdout, stderr=stderr,
            )
            out = stdout.getvalue()
            self.assertIn("### z-essential-guardrail", out)
            self.assertNotIn("### a-heavy-voice-file", out)
            self.assertIn("a-heavy-voice-file", out)  # named in the NOTE

    def test_priority_rank_mapping(self) -> None:
        self.assertEqual(recall._always_load_priority_rank({"priority": "high"}), 0)
        self.assertEqual(recall._always_load_priority_rank({}), 1)
        self.assertEqual(recall._always_load_priority_rank({"priority": "unknown"}), 1)
        self.assertEqual(recall._always_load_priority_rank({"priority": "low"}), 2)

    def test_truncation_note_names_omitted_slugs(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            _write_entry(vault, "kept-entry", "y" * 40)
            _write_entry(vault, "dropped-entry", "x" * 800)

            stdout = io.StringIO()
            stderr = io.StringIO()
            recall.session_start(
                vault=vault, token_budget=40, stdout=stdout, stderr=stderr,
            )
            out = stdout.getvalue()
            self.assertIn("recall truncated", out)
            self.assertIn("dropped-entry", out)


if __name__ == "__main__":
    unittest.main()
