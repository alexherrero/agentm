#!/usr/bin/env python3
"""Tests for check-slop.py (PLAN-r3-voice-mechanism task 2, agentm-side delegator)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("check_slop_agentm", _SCRIPTS / "check-slop.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_slop_agentm"] = mod
    spec.loader.exec_module(mod)
    return mod


slop = _load()


class TestSiblingResolution(unittest.TestCase):
    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "scripts" / "check-slop.py"
            fake.parent.mkdir()
            fake.write_text("# fake\n")
            old = os.environ.get("CRICKETS_REPO_ROOT")
            os.environ["CRICKETS_REPO_ROOT"] = tmp
            try:
                found = slop.find_crickets_check_slop()
            finally:
                if old is None:
                    os.environ.pop("CRICKETS_REPO_ROOT", None)
                else:
                    os.environ["CRICKETS_REPO_ROOT"] = old
            self.assertEqual(found, fake)

    def test_sibling_checkout_found_on_this_machine(self):
        # This repo's documented layout is ~/Antigravity/{agentm,crickets} siblings.
        found = slop.find_crickets_check_slop()
        if found is None:
            self.skipTest("crickets sibling checkout not present in this environment")
        self.assertTrue(found.is_file())


class TestGracefulSkip(unittest.TestCase):
    def test_missing_sibling_exits_zero_and_emits_dark_record(self):
        # Force the not-found path directly (machine-independent — a real
        # sibling checkout on this machine would otherwise still resolve via
        # the default candidate even with CRICKETS_REPO_ROOT pointed elsewhere).
        original = slop.find_crickets_check_slop
        slop.find_crickets_check_slop = lambda: None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "out.jsonl"
                rc = slop.main(["--report", "--jsonl-out", str(out)])
                self.assertEqual(rc, 0)
                record = json.loads(out.read_text().splitlines()[0])
                self.assertIsNone(record["pass"])
                self.assertEqual(record["axis"], "docs+voice health")
        finally:
            slop.find_crickets_check_slop = original


class TestDelegation(unittest.TestCase):
    def test_delegates_to_crickets_and_scans_agentm_wiki(self):
        found = slop.find_crickets_check_slop()
        if found is None:
            self.skipTest("crickets sibling checkout not present in this environment")
        rc = slop.main(["--report", "wiki"])
        self.assertEqual(rc, 0)  # --report always forces 0


if __name__ == "__main__":
    unittest.main()
