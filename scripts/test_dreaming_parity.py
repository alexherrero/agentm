#!/usr/bin/env python3
"""The parity recording is the Python layer's word (filing v2 part 6, task 4):
if a producer changes, this fails until the fixture is re-recorded on
purpose. The Go side asserts the same file from its own tests."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE / "health") not in sys.path:
    sys.path.insert(0, str(_HERE / "health"))

import record_dreaming_parity as rec  # noqa: E402


class TheRecording(unittest.TestCase):
    def test_the_python_layer_still_produces_the_recording(self):
        expected = json.loads((rec.FIXTURE / "expected.json").read_text(encoding="utf-8"))
        self.assertEqual(rec.record(), expected,
                         "the Python producers drifted from scripts/fixtures/dreaming-parity/expected.json — "
                         "re-record deliberately (record_dreaming_parity.py --write) and re-audit the Go port")

    def test_the_recording_covers_every_ported_job(self):
        expected = json.loads((rec.FIXTURE / "expected.json").read_text(encoding="utf-8"))
        self.assertEqual([r for r, _ in expected["lifecycle"]["demoted"]], ["memory/semantic/silent-a.md"])
        self.assertEqual([r for r, _ in expected["lifecycle"]["revived"]], ["memory/semantic/dormant-back.md"])
        self.assertEqual([r for r, _ in expected["lifecycle"]["archive_candidates"]], ["memory/semantic/dormant-cold.md"])
        self.assertEqual(len(expected["copies"]), 1)
        self.assertEqual(expected["copies"][0]["canonical"], "memory/procedural/copy-canon.md")
        self.assertEqual(expected["copies"][0]["copies"], ["memory/procedural/copy-1.md", "memory/procedural/copy-2.md"])
        self.assertEqual(list(expected["promote"]), ["shared-target"])
        self.assertEqual(expected["promote"]["shared-target"]["sources"],
                         ["memory/episodic/e1.md", "memory/episodic/e2.md", "memory/episodic/e3.md"])


if __name__ == "__main__":
    unittest.main()
