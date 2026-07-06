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
