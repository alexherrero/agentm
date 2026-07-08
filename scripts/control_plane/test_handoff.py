#!/usr/bin/env python3
"""Tests for scripts/control_plane/handoff.py (PLAN-autonomy-control-plane
task 4). Real-bridge tests (against the actual crickets sibling checkout)
are isolated into their own classes with a setUpClass skip guard -- CI runs
on a clean runner that doesn't clone crickets as a sibling of agentm, so
these must degrade to "skipped", never "failed", the same way crickets'
own real-bridge tests already do."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import handoff as hf  # noqa: E402
import dispatch as dp  # noqa: E402


@dataclass(frozen=True)
class _FakeResult:
    """Mirrors dispatch.DispatchResult's label-relevant fields without
    requiring a real dispatch."""
    name: str
    tier: str
    model_id: str
    effort: str


class DispatchResultLabelTests(unittest.TestCase):
    def test_label_shape_matches_schema_keys(self):
        result = _FakeResult(name="p-1", tier="T1-Execute", model_id="claude-sonnet-5", effort="medium")
        label = hf.dispatch_result_label(result)
        self.assertEqual(set(label.keys()), {"tier", "model_id", "effort"})
        self.assertEqual(label["tier"], "T1-Execute")
        self.assertEqual(label["model_id"], "claude-sonnet-5")
        self.assertEqual(label["effort"], "medium")


class LoadHandoffPackModuleGracefulSkipTests(unittest.TestCase):
    def setUp(self):
        hf._reset_cache_for_tests()

    def tearDown(self):
        hf._reset_cache_for_tests()

    def test_unresolvable_crickets_yields_none(self):
        with tempfile.TemporaryDirectory() as td:
            empty_home = Path(td) / "empty_home"
            empty_home.mkdir()
            with mock.patch.object(Path, "home", return_value=empty_home):
                with mock.patch.dict(os.environ, {"CRICKETS_SCRIPTS_DIR": ""}):
                    self.assertIsNone(hf.load_handoff_pack_module())


class _RealCricketsHandoffBridgeTestCase(unittest.TestCase):
    """Shared skip guard for every test class below that needs the real
    crickets handoff_pack.py sibling checkout."""

    @classmethod
    def setUpClass(cls):
        hf._reset_cache_for_tests()
        if hf.load_handoff_pack_module() is None:
            raise unittest.SkipTest("crickets sibling checkout unavailable -- real-bridge test skipped")

    @classmethod
    def tearDownClass(cls):
        hf._reset_cache_for_tests()


class LoadHandoffPackModuleRealBridgeTests(_RealCricketsHandoffBridgeTestCase):
    def test_resolves_real_sibling_checkout(self):
        module = hf.load_handoff_pack_module()
        self.assertIsNotNone(module)
        self.assertTrue(hasattr(module, "build_handoff_pack"))
        self.assertTrue(hasattr(module, "label_matches_schema"))


class DispatchResultToHandoffEntryTests(_RealCricketsHandoffBridgeTestCase):
    def test_real_bridge_produces_a_handoff_entry_with_matching_label(self):
        result = _FakeResult(name="myplan-1", tier="T1-Execute", model_id="claude-sonnet-5", effort="medium")
        entry = hf.dispatch_result_to_handoff_entry(result, prompt_text="do the thing")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.title, "myplan-1")
        self.assertEqual(entry.prompt_text, "do the thing")
        self.assertEqual(entry.label(), hf.dispatch_result_label(result))


class DispatchResultToHandoffEntryGracefulSkipTests(unittest.TestCase):
    def setUp(self):
        hf._reset_cache_for_tests()

    def tearDown(self):
        hf._reset_cache_for_tests()

    def test_unresolvable_crickets_yields_none(self):
        with tempfile.TemporaryDirectory() as td:
            empty_home = Path(td) / "empty_home"
            empty_home.mkdir()
            with mock.patch.object(Path, "home", return_value=empty_home):
                with mock.patch.dict(os.environ, {"CRICKETS_SCRIPTS_DIR": ""}):
                    entry = hf.dispatch_result_to_handoff_entry(
                        _FakeResult(name="p-1", tier="T1-Execute", model_id="claude-sonnet-5", effort="medium"),
                    )
        self.assertIsNone(entry)


class BuildFleetHandoffPackTests(_RealCricketsHandoffBridgeTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_real_pack_written_and_downstream_reader_parses_without_special_casing(self):
        # A downstream reader is anything that loads prompts.json and checks
        # each entry's "label" against the schema crickets ships -- the
        # SAME validator crickets uses for its own fixtures, not a bespoke
        # parser this test invents.
        results = [
            _FakeResult(name="myplan-1", tier="T1-Execute", model_id="claude-sonnet-5", effort="medium"),
            _FakeResult(name="myplan-2", tier="T3-Architect", model_id="claude-opus-4-8", effort="max"),
        ]
        dest = self.tmp / "handoff"
        manifest = hf.build_fleet_handoff_pack(results, {"summary.md": "# fleet run\n"}, dest)
        self.assertIsNotNone(manifest)

        prompts_json = json.loads((dest / "prompts.json").read_text(encoding="utf-8"))
        handoff_module = hf.load_handoff_pack_module()
        self.assertEqual(len(prompts_json["prompts"]), 2)
        for entry in prompts_json["prompts"]:
            self.assertTrue(handoff_module.label_matches_schema(entry["label"]))
        self.assertIn("summary.md", prompts_json["snapshotted_files"])
        self.assertTrue((dest / "PROMPTS.md").is_file())


class BuildFleetHandoffPackGracefulSkipTests(unittest.TestCase):
    def setUp(self):
        hf._reset_cache_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        hf._reset_cache_for_tests()
        self._tmp.cleanup()

    def test_unresolvable_crickets_yields_none_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            empty_home = Path(td) / "empty_home"
            empty_home.mkdir()
            with mock.patch.object(Path, "home", return_value=empty_home):
                with mock.patch.dict(os.environ, {"CRICKETS_SCRIPTS_DIR": ""}):
                    result = hf.build_fleet_handoff_pack([], {}, self.tmp / "handoff")
        self.assertIsNone(result)


class DispatchIntegrationTests(_RealCricketsHandoffBridgeTestCase):
    """Proves a real dispatch.DispatchResult (not the test's _FakeResult
    stand-in) round-trips through the same label path."""

    def setUp(self):
        dp._reset_cache_for_tests()

    def tearDown(self):
        dp._reset_cache_for_tests()

    def test_real_dispatch_result_label_matches_schema(self):
        def fake_runner(cmd, **kwargs):
            class _P:
                returncode = 0
                stdout = ""
                stderr = ""
            return _P()

        with tempfile.TemporaryDirectory() as td:
            item = dp.WorkItem(plan="p", task="1", prompt="x", cwd=td,
                                declared={"model": "claude-sonnet-5", "effort": "medium", "tier": "T1-Execute"})
            result = dp.dispatch(item, runner=fake_runner)
        label = hf.dispatch_result_label(result)
        handoff_module = hf.load_handoff_pack_module()
        self.assertTrue(handoff_module.label_matches_schema(label))


if __name__ == "__main__":
    unittest.main()
