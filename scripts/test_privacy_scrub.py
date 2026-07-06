#!/usr/bin/env python3
"""Unit tests for privacy_scrub.py — the mandatory failure-incident scrub
(agentm-memory-index.md, AG Wave B leader 3/5).

Run directly:
    cd scripts && python3 -m unittest test_privacy_scrub
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import privacy_scrub  # noqa: E402


class TestScrubPii(unittest.TestCase):
    def test_email_redacted(self):
        out = privacy_scrub.scrub_pii("contact alex@example.com for details")
        self.assertNotIn("alex@example.com", out)
        self.assertIn("[REDACTED-EMAIL]", out)

    def test_mac_personal_path_redacted(self):
        out = privacy_scrub.scrub_pii("traceback at /Users/alex/project/file.py line 12")
        self.assertNotIn("/Users/alex", out)
        self.assertIn("[REDACTED-PATH]", out)

    def test_windows_personal_path_redacted(self):
        out = privacy_scrub.scrub_pii(r"file C:\Users\alex\project\file.py")
        self.assertNotIn(r"C:\Users\alex", out)
        self.assertIn("[REDACTED-PATH]", out)

    def test_openai_key_shape_redacted(self):
        out = privacy_scrub.scrub_pii("key=sk-" + "a" * 40)
        self.assertNotIn("sk-" + "a" * 40, out)
        self.assertIn("[REDACTED-API-KEY]", out)

    def test_github_pat_shape_redacted(self):
        out = privacy_scrub.scrub_pii("token ghp_" + "b" * 36)
        self.assertIn("[REDACTED-API-KEY]", out)

    def test_aws_key_shape_redacted(self):
        out = privacy_scrub.scrub_pii("AKIAABCDEFGHIJKLMNOP")
        self.assertIn("[REDACTED-API-KEY]", out)

    def test_phone_number_redacted(self):
        out = privacy_scrub.scrub_pii("call (555) 123-4567 now")
        self.assertIn("[REDACTED-PHONE]", out)

    def test_clean_text_passes_through_unchanged(self):
        clean = "the runner's due-decision loop is idempotent"
        self.assertEqual(privacy_scrub.scrub_pii(clean), clean)

    def test_empty_string_never_raises(self):
        self.assertEqual(privacy_scrub.scrub_pii(""), "")

    def test_multiple_findings_all_redacted(self):
        out = privacy_scrub.scrub_pii("alex@example.com at /Users/alex/repo")
        self.assertNotIn("@", out)
        self.assertNotIn("/Users/alex", out)


if __name__ == "__main__":
    unittest.main()
