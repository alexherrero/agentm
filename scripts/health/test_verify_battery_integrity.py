#!/usr/bin/env python3
"""Tests for verify-battery-integrity.py (AA5 C7 — verification honesty axis)."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "verify_battery_integrity", _HERE / "verify-battery-integrity.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify_battery_integrity"] = mod
    spec.loader.exec_module(mod)
    return mod


vbi = _load()


class TestDeclaredSuiteLabels(unittest.TestCase):
    def test_parses_real_run_fast_tier(self):
        labels = vbi.declared_suite_labels()
        self.assertIn("verify-efficiency", labels)
        self.assertIn("verify-battery-integrity", labels)
        self.assertEqual(len(labels), len(set(labels)), "labels should be unique")


class TestDeclaredGateScriptStems(unittest.TestCase):
    def test_parses_real_check_all(self):
        stems = vbi.declared_gate_script_stems()
        self.assertIn("check-references", stems)
        self.assertIn("verify-v4", stems)
        self.assertIn("check-vendored-parity", stems)


class TestChecksAllPassOnThisRepo(unittest.TestCase):
    def test_scorecard_determinism(self):
        ok, detail = vbi.check_scorecard_determinism()
        self.assertTrue(ok, detail)

    def test_no_skipped_suites(self):
        ok, detail = vbi.check_no_skipped_suites()
        self.assertTrue(ok, detail)

    def test_gate_results_parse_and_agree(self):
        ok, detail = vbi.check_gate_results_parse_and_agree()
        self.assertTrue(ok, detail)

    def test_battery_lists_agree(self):
        ok, detail = vbi.check_battery_lists_agree()
        self.assertTrue(ok, detail)


class TestNoSkippedSuitesDetectsDrop(unittest.TestCase):
    def test_empty_declared_labels_fails_closed(self):
        original = vbi.declared_suite_labels
        vbi.declared_suite_labels = lambda: []
        try:
            ok, detail = vbi.check_no_skipped_suites()
            self.assertFalse(ok)
            self.assertIn("zero", detail)
        finally:
            vbi.declared_suite_labels = original


class TestBatteryListsAgreeDetectsDivergence(unittest.TestCase):
    """CONS-1: a fast-tier suite that falls out of both check-all.sh's gates
    AND the FAST_TIER_ONLY allowlist must be caught, not silently accepted."""

    def test_unlisted_suite_fails_closed(self):
        original = vbi.declared_suite_labels
        vbi.declared_suite_labels = lambda: ["verify-v4", "a-suite-nobody-gates-or-allowlists"]
        try:
            ok, detail = vbi.check_battery_lists_agree()
            self.assertFalse(ok)
            self.assertIn("a-suite-nobody-gates-or-allowlists", detail)
        finally:
            vbi.declared_suite_labels = original

    def test_fast_tier_only_allowlist_entries_dont_trip_it(self):
        original = vbi.declared_suite_labels
        vbi.declared_suite_labels = lambda: sorted(vbi.FAST_TIER_ONLY)
        try:
            ok, detail = vbi.check_battery_lists_agree()
            self.assertTrue(ok, detail)
        finally:
            vbi.declared_suite_labels = original


class TestMainEmitsJsonl(unittest.TestCase):
    def test_emits_four_records_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.jsonl"
            rc = vbi.main(["--jsonl-out", str(out)])
            self.assertEqual(rc, 0)
            lines = out.read_text().splitlines()
            self.assertEqual(len(lines), 4)
            for line in lines:
                record = json.loads(line)
                self.assertEqual(record["axis"], "verification honesty")
                self.assertIs(record["pass"], True)
                self.assertEqual(record["weight"], 5.0)


if __name__ == "__main__":
    unittest.main()
