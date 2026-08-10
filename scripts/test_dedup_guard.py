#!/usr/bin/env python3
"""Unit coverage for the write-time dedup guard (PLAN-auto-org-dedup-and-lint,
task 2): dedup_guard.py + save_entry()'s guard hook + capture()'s inbox hook
+ ingest's rollback safety.

The save_entry half is gone: it resolved a fingerprint through the vector
index's `entry_meta` table, and that index was removed (see
wiki/designs/agentm-rescope-week1-experiment.md). What remains is the
capture/inbox half, a plain frontmatter scan over a small staging dir.

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


if __name__ == "__main__":
    unittest.main()
