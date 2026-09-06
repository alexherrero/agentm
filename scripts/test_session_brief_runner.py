#!/usr/bin/env python3
"""test_session_brief_runner.py — the session brief carries the runner's refusals
(filing-v2 remainders task 1).

A refused job manifest used to stop every scheduled job on the machine with a
traceback in a launchd log as the only trace. The runner now leaves its last
cycle's account at `~/.cache/agentm/runner/last-cycle.json`, and the brief the
operator sees at session start names what was refused.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE / "health") not in sys.path:
    sys.path.insert(0, str(_HERE / "health"))

import session_brief  # noqa: E402

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


class BriefCarriesRefusals(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "diagnostics" / "digests").mkdir(parents=True)
        self.cycle = self.root / "last-cycle.json"

    def _digest(self):
        (self.vault / "diagnostics" / "digests" / "20260905-digest-daily.md").write_text(
            "# Daily digest — all quiet\n\nNothing notable.\n", encoding="utf-8")

    def _brief(self):
        return session_brief.build_brief(
            vault=self.vault, now=NOW,
            park_dir=self.root / "no-park", history_path=self.root / "no-history.jsonl",
            runner_cycle_path=self.cycle,
        )

    def test_a_refusal_rides_the_fresh_line_and_its_signature(self):
        self._digest()
        clean = self._brief()
        self.assertIsNotNone(clean)
        self.assertNotIn("refused", clean["line"])
        self.cycle.write_text(json.dumps({"at": 1.0, "loaded": 3, "refused": [
            {"file": "dreaming.yaml", "reason": "invalid YAML"}], "outcomes": []}), encoding="utf-8")
        brief = self._brief()
        self.assertIn("⚠ runner refused 1 manifest: dreaming.yaml", brief["line"])
        self.assertIn("every other job still runs", brief["line"])
        self.assertNotEqual(brief["signature"], clean["signature"], "a refusal is a change the anti-fatigue guard must not suppress")

    def test_a_refusal_speaks_even_when_the_ladder_never_ran(self):
        self.assertIsNone(self._brief(), "honest-quiet without a digest, a ledger or a refusal")
        self.cycle.write_text(json.dumps({"at": 1.0, "loaded": 0, "refused": [
            {"file": "a.yaml", "reason": "x"}, {"file": "b.yaml", "reason": "y"}], "outcomes": []}), encoding="utf-8")
        brief = self._brief()
        self.assertIsNotNone(brief)
        self.assertIn("refused 2 manifests: a.yaml, b.yaml", brief["line"])

    def test_an_unreadable_or_absent_summary_is_no_refusal(self):
        self._digest()
        self.cycle.write_text("{not json", encoding="utf-8")
        self.assertNotIn("refused", self._brief()["line"])
        self.assertEqual(session_brief.runner_refusals(self.root / "missing.json"), [])


if __name__ == "__main__":
    unittest.main()
