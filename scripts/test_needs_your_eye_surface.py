#!/usr/bin/env python3
"""Task-5 verification (PLAN-auto-org-dedup-and-lint): the needs-your-eye
surface — one underlying list (`_meta/needs-your-eye.json`), three surfaces
(console section, digest, morning-brief count). A fixture ambiguous pair
appears on all three after one cycle; resolving it clears all three on the
next.

Run directly:
    cd scripts && python3 -m unittest test_needs_your_eye_surface
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
_CONSOLE_SCRIPTS = _HERE.parent / "harness" / "skills" / "console" / "scripts"
_HEALTH_SCRIPTS = _HERE / "health"
for d in (_SKILL_SCRIPTS, _CONSOLE_SCRIPTS, _HEALTH_SCRIPTS):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import console  # noqa: E402
import inbox_triage as it  # noqa: E402
import session_brief  # noqa: E402

_INBOX_TEMPLATE = (
    "---\n"
    "kind: idea\n"
    "status: inbox\n"
    "slug: {slug}\n"
    "mining_confidence: LOW\n"
    "mining_rationale: \"test fixture\"\n"
    "mining_occurrences: 1\n"
    "---\n\n"
    "{body}\n"
)


class NeedsYourEyeThreeSurfacesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "personal" / "_inbox").mkdir(parents=True)

    def _write_inbox(self, slug: str, body: str) -> Path:
        path = self.vault / "personal" / "_inbox" / f"{slug}.md"
        path.write_text(_INBOX_TEMPLATE.format(slug=slug, body=body), encoding="utf-8")
        return path

    def test_ambiguous_pair_on_all_three_surfaces_then_cleared(self):
        # An ambiguous (fuzzy-similar, fingerprint-distinct) pair.
        self._write_inbox("a", "The quick brown fox jumps over the lazy dog today.")
        b = self._write_inbox("b", "The quick brown fox jumps over the lazy dog today!")

        digest = it.run_inbox_triage(self.vault, now=time.time())

        # Surface 1 — the digest.
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("Needs your eye", digest_text)
        self.assertIn("a.md", digest_text)

        # Surface 2 — the console section.
        section = console.section_needs_your_eye(self.vault)
        self.assertIn("1 ambiguous dedup/merge candidate(s)", section)
        self.assertIn("a.md, b.md", section)

        # Surface 3 — the morning-brief count.
        self.assertEqual(session_brief.count_needs_your_eye(self.vault), 1)

        # Operator resolves it (edits one note apart -- simulated action).
        b.write_text(
            b.read_text(encoding="utf-8").replace(
                "The quick brown fox jumps over the lazy dog today!",
                "A totally rewritten, unrelated observation.",
            ),
            encoding="utf-8",
        )
        digest2 = it.run_inbox_triage(self.vault, now=time.time())

        # All three surfaces clear on the next cycle.
        self.assertNotIn("Needs your eye", digest2.digest_path.read_text(encoding="utf-8"))
        self.assertIn("nothing", console.section_needs_your_eye(self.vault))
        self.assertEqual(session_brief.count_needs_your_eye(self.vault), 0)

    def test_console_section_honest_dark_before_first_cycle(self):
        self.assertIn("dark", console.section_needs_your_eye(self.vault))
        self.assertEqual(session_brief.count_needs_your_eye(self.vault), 0)

    def test_brief_line_carries_the_count(self):
        from datetime import datetime, timezone

        # Minimal digest note so build_brief has a headline to anchor on.
        briefs = self.vault / "_briefs"
        briefs.mkdir(parents=True)
        (briefs / "20260719-digest-daily.md").write_text(
            "# Daily digest — all quiet\n\nNothing notable.\n", encoding="utf-8"
        )
        self._write_inbox("a", "The quick brown fox jumps over the lazy dog today.")
        self._write_inbox("b", "The quick brown fox jumps over the lazy dog today!")
        it.run_inbox_triage(self.vault, now=time.time())

        brief = session_brief.build_brief(
            vault=self.vault,
            now=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
            park_dir=Path(self._tmp.name) / "no-park-dir",
            history_path=Path(self._tmp.name) / "no-history.jsonl",
        )
        self.assertIsNotNone(brief)
        self.assertIn("1 dedup candidate needs your eye", brief["line"])
        # The count is part of the anti-fatigue signature, so a change
        # re-surfaces the line.
        self.assertIn("|1", brief["signature"])


if __name__ == "__main__":
    unittest.main()
