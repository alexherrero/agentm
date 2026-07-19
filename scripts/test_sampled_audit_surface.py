#!/usr/bin/env python3
"""Task-9 verification (PLAN-auto-org-dedup-and-lint): the sampled higher-
tier audit's console surface. `_meta/sampled-audit-latest.json` is the one
underlying pointer (overwritten every cycle by
`dream.run_dream_and_auto_apply()`); `console.section_sampled_audit()` is
the one console reader.

Run directly:
    cd scripts && python3 -m unittest test_sampled_audit_surface
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CONSOLE_SCRIPTS = _HERE.parent / "harness" / "skills" / "console" / "scripts"
if str(_CONSOLE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_SCRIPTS))

import console  # noqa: E402


class SampledAuditConsoleSurfaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "_meta").mkdir(parents=True)

    def _write_pointer(self, payload: dict) -> None:
        (self.vault / "_meta" / "sampled-audit-latest.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_honest_dark_before_first_cycle(self):
        self.assertIn("dark", console.section_sampled_audit(self.vault))

    def test_zero_sampled_reports_nothing_not_an_error(self):
        self._write_pointer({
            "run_id": "run-1", "sampled_count": 0, "agree_count": 0,
            "disagree_count": 0, "disagreement_rate": None, "narrowed": False,
        })
        section = console.section_sampled_audit(self.vault)
        self.assertIn("nothing sampled", section)
        self.assertIn("unavailable", section)

    def test_console_is_flagged_when_the_bands_narrow(self):
        # The plan's own verification text: "confirms the ambiguous bands
        # actually narrow and the console is flagged."
        self._write_pointer({
            "run_id": "run-2", "sampled_count": 10, "agree_count": 5,
            "disagree_count": 5, "disagreement_rate": 0.5, "narrowed": True,
        })
        section = console.section_sampled_audit(self.vault)
        self.assertIn("10 applied link/merge(s) reviewed", section)
        self.assertIn("5 disagreement(s)", section)
        self.assertIn("50.0%", section)
        self.assertIn("narrowed", section)
        self.assertIn("⚠", section)

    def test_no_flag_when_disagreement_stays_low(self):
        self._write_pointer({
            "run_id": "run-3", "sampled_count": 10, "agree_count": 9,
            "disagree_count": 1, "disagreement_rate": 0.1, "narrowed": False,
        })
        section = console.section_sampled_audit(self.vault)
        self.assertNotIn("narrowed", section)
        self.assertNotIn("⚠", section)


if __name__ == "__main__":
    unittest.main()
