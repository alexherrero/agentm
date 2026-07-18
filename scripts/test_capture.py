#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/capture.py — the staging-only
front door for personal/_inbox/ (capture-front-door plan task 2)."""
from __future__ import annotations

import concurrent.futures
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import capture as cap  # noqa: E402

_NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


class CaptureBasicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_capture_kind_writes_note(self) -> None:
        result = cap.capture(self.vault, "a thought worth keeping", kind="capture", now=_NOW)
        self.assertTrue(result.success)
        self.assertTrue(result.path.is_file())
        content = result.path.read_text(encoding="utf-8")
        self.assertIn("kind: capture", content)
        self.assertIn("status: inbox", content)
        self.assertIn("a thought worth keeping", content)

    def test_idea_kind_writes_note(self) -> None:
        result = cap.capture(self.vault, "an idea worth keeping", kind="idea", now=_NOW)
        self.assertTrue(result.success)
        content = result.path.read_text(encoding="utf-8")
        self.assertIn("kind: idea", content)

    def test_unknown_kind_fails_explicitly(self) -> None:
        result = cap.capture(self.vault, "x", kind="bogus", now=_NOW)
        self.assertFalse(result.success)
        self.assertIsNone(result.path)
        self.assertIn("unknown kind", result.error)

    def test_empty_content_fails_explicitly(self) -> None:
        result = cap.capture(self.vault, "   ", now=_NOW)
        self.assertFalse(result.success)
        self.assertIn("non-empty", result.error)

    def test_nonexistent_vault_fails_explicitly(self) -> None:
        result = cap.capture(self.vault / "does-not-exist", "x", now=_NOW)
        self.assertFalse(result.success)
        self.assertIn("does not exist", result.error)

    def test_writes_to_inbox_not_permanent_memory(self) -> None:
        result = cap.capture(self.vault, "x", now=_NOW)
        self.assertTrue(result.success)
        self.assertIn("personal/_inbox", result.path.as_posix())

    def test_optional_fields_written_when_provided(self) -> None:
        result = cap.capture(
            self.vault, "x", source="cli", surface="desktop", tags=["a", "b"],
            instructions="add to my ideas ledger", source_url="https://example.com/article",
            now=_NOW,
        )
        content = result.path.read_text(encoding="utf-8")
        self.assertIn("source: cli", content)
        self.assertIn("surface: desktop", content)
        self.assertIn("tags: [a, b]", content)
        self.assertIn("source_url: https://example.com/article", content)
        self.assertIn("instructions:", content)
        self.assertIn("add to my ideas ledger", content)

    def test_optional_fields_omitted_when_absent(self) -> None:
        result = cap.capture(self.vault, "x", now=_NOW)
        content = result.path.read_text(encoding="utf-8")
        self.assertNotIn("source:", content)
        self.assertNotIn("surface:", content)
        self.assertNotIn("tags:", content)
        self.assertNotIn("source_url:", content)
        self.assertNotIn("instructions:", content)

    def test_write_failure_returns_explicit_error_not_silent(self) -> None:
        with mock.patch("capture.atomic_write", side_effect=OSError("disk full")):
            result = cap.capture(self.vault, "x", now=_NOW)
        self.assertFalse(result.success)
        self.assertIn("disk full", result.error)


class SlugCollisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_explicit_slug_collision_appends_suffix(self) -> None:
        r1 = cap.capture(self.vault, "first", slug="my-slug", now=_NOW)
        r2 = cap.capture(self.vault, "second", slug="my-slug", now=_NOW)
        self.assertTrue(r1.success)
        self.assertTrue(r2.success)
        self.assertNotEqual(r1.path, r2.path)
        self.assertEqual(r2.slug, "my-slug-1")
        # Both files survive with their own distinct content.
        self.assertIn("first", r1.path.read_text(encoding="utf-8"))
        self.assertIn("second", r2.path.read_text(encoding="utf-8"))

    def test_three_way_collision_increments(self) -> None:
        r1 = cap.capture(self.vault, "a", slug="dup", now=_NOW)
        r2 = cap.capture(self.vault, "b", slug="dup", now=_NOW)
        r3 = cap.capture(self.vault, "c", slug="dup", now=_NOW)
        self.assertEqual({r1.slug, r2.slug, r3.slug}, {"dup", "dup-1", "dup-2"})


class ConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_concurrent_writes_do_not_corrupt_files(self) -> None:
        # Distinct slugs, concurrent writers -- each atomic_write call is
        # independent (temp-in-same-dir + fsync + rename), so no shared
        # mutex is needed for non-colliding targets. Confirms no torn writes.
        def _write(i):
            return cap.capture(self.vault, f"content-{i}", slug=f"slug-{i}", now=_NOW)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(_write, range(20)))

        self.assertTrue(all(r.success for r in results))
        for i, r in enumerate(results):
            content = r.path.read_text(encoding="utf-8")
            self.assertIn(f"content-{i}", content)
            self.assertTrue(content.startswith("---\n"))
            self.assertIn("\n---\n", content[3:])


if __name__ == "__main__":
    unittest.main()
