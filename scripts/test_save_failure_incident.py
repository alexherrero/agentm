#!/usr/bin/env python3
"""save.py's `failure-incident` mandatory scrub gate (agentm-memory-index.md,
AG Wave B leader 3/5): a failure-incident write is scrubbed before it lands;
any other kind is unaffected; the scrub can't be silently skipped.

Run directly:
    cd scripts && python3 -m unittest test_save_failure_incident
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

import save  # noqa: E402
import vec_index  # noqa: E402


class TestFailureIncidentFingerprint(unittest.TestCase):
    """wave-c-diagnostics task 4: save_entry(fingerprint=...) is the first real
    writer for V6-11's entry_meta.fingerprint column (agentm-memory-index.md)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_fingerprint_lands_in_frontmatter(self):
        target = save.save_entry(
            self.vault, "failure-incident", "crash-report",
            "ValueError in classify", group="personal", fingerprint="abc123",
        )
        self.assertIn("fingerprint: abc123", target.read_text(encoding="utf-8"))

    def test_fingerprint_round_trips_through_extract_meta(self):
        target = save.save_entry(
            self.vault, "failure-incident", "crash-report",
            "ValueError in classify", group="personal", fingerprint="abc123",
        )
        meta = vec_index._extract_meta_from_file(target)
        self.assertEqual(meta["fingerprint"], "abc123")

    def test_fingerprint_auto_computed_when_not_provided(self):
        # Contract extended by auto-org part 3 task 1: an omitted
        # fingerprint no longer stays absent -- save_entry auto-computes a
        # content hash. The ORIGINAL intent this test keeps checking: the
        # caller-supplied value is optional, and what lands without one is
        # never garbage -- now specifically the deterministic content hash
        # (scrub runs BEFORE the hash, so the fingerprint reflects the
        # scrubbed body actually written, not the raw PII-bearing input).
        import fingerprint as fp_mod
        from privacy_scrub import scrub_pii
        target = save.save_entry(
            self.vault, "failure-incident", "crash-report",
            "ValueError in classify", group="personal",
        )
        content = target.read_text(encoding="utf-8")
        expected = fp_mod.compute_fingerprint(scrub_pii("ValueError in classify"))
        self.assertIn(f"fingerprint: {expected}", content)


class TestFailureIncidentScrub(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_failure_incident_write_is_scrubbed(self):
        # "alexherrero" is the allowlisted public GitHub handle
        # (check-no-pii.sh) — keeps this fixture out of the PII pre-push
        # scan while still exercising the real /Users/<name>/ path shape.
        target = save.save_entry(
            self.vault, "failure-incident", "crash-report",
            "traceback: alex@example.com hit /Users/alexherrero/project/file.py",
            group="personal",
        )
        content = target.read_text(encoding="utf-8")
        self.assertNotIn("alex@example.com", content)
        self.assertNotIn("/Users/alexherrero/project", content)
        self.assertIn("[REDACTED-EMAIL]", content)
        self.assertIn("[REDACTED-PATH]", content)

    def test_non_failure_incident_kind_is_unaffected(self):
        target = save.save_entry(
            self.vault, "reference", "a-note",
            "contact alex@example.com if this ever happens",
            group="personal",
        )
        content = target.read_text(encoding="utf-8")
        self.assertIn("alex@example.com", content)

    def test_scrub_cannot_be_silently_skipped(self):
        # Force the sibling import to fail (sys.modules[name] = None makes
        # any subsequent `import name` raise ImportError) and confirm the
        # write refuses loudly rather than landing unscrubbed.
        sys.modules["privacy_scrub"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises(RuntimeError):
                save.save_entry(
                    self.vault, "failure-incident", "should-not-land",
                    "alex@example.com", group="personal",
                )
            self.assertFalse(
                (self.vault / "personal" / "failure-incident" / "should-not-land.md").exists()
            )
        finally:
            del sys.modules["privacy_scrub"]


if __name__ == "__main__":
    unittest.main()
