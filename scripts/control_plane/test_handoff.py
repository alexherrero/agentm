#!/usr/bin/env python3
"""Tests for scripts/control_plane/handoff.py (PLAN-autonomy-control-plane
task 4)."""
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


class LoadHandoffPackModuleTests(unittest.TestCase):
    def setUp(self):
        hf._reset_cache_for_tests()

    def tearDown(self):
        hf._reset_cache_for_tests()

    def test_resolves_real_sibling_checkout(self):
        module = hf.load_handoff_pack_module()
        self.assertIsNotNone(module)
        self.assertTrue(hasattr(module, "build_handoff_pack"))
        self.assertTrue(hasattr(module, "label_matches_schema"))

    def test_unresolvable_crickets_yields_none(self):
        with tempfile.TemporaryDirectory() as td:
            empty_home = Path(td) / "empty_home"
            empty_home.mkdir()
            with mock.patch.object(Path, "home", return_value=empty_home):
                with mock.patch.dict(os.environ, {"CRICKETS_SCRIPTS_DIR": ""}):
                    self.assertIsNone(hf.load_handoff_pack_module())


class DispatchResultToHandoffEntryTests(unittest.TestCase):
    def setUp(self):
        hf._reset_cache_for_tests()

    def tearDown(self):
        hf._reset_cache_for_tests()

    def test_real_bridge_produces_a_handoff_entry_with_matching_label(self):
        result = _FakeResult(name="myplan-1", tier="T1-Execute", model_id="claude-sonnet-5", effort="medium")
        entry = hf.dispatch_result_to_handoff_entry(result, prompt_text="do the thing")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.title, "myplan-1")
        self.assertEqual(entry.prompt_text, "do the thing")
        self.assertEqual(entry.label(), hf.dispatch_result_label(result))

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


class BuildFleetHandoffPackTests(unittest.TestCase):
    def setUp(self):
        hf._reset_cache_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        hf._reset_cache_for_tests()
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

    def test_unresolvable_crickets_yields_none_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            empty_home = Path(td) / "empty_home"
            empty_home.mkdir()
            with mock.patch.object(Path, "home", return_value=empty_home):
                with mock.patch.dict(os.environ, {"CRICKETS_SCRIPTS_DIR": ""}):
                    result = hf.build_fleet_handoff_pack([], {}, self.tmp / "handoff")
        self.assertIsNone(result)


class DispatchIntegrationTests(unittest.TestCase):
    """Proves a real dispatch.DispatchResult (not the test's _FakeResult
    stand-in) round-trips through the same label path."""

    def setUp(self):
        dp._reset_cache_for_tests()
        hf._reset_cache_for_tests()

    def tearDown(self):
        dp._reset_cache_for_tests()
        hf._reset_cache_for_tests()

    def test_real_dispatch_result_label_matches_schema(self):
        def fake_runner(cmd, **kwargs):
            class _P:
                returncode = 0
                stdout = ""
                stderr = ""
            return _P()

        with tempfile.TemporaryDirectory() as td:
            item2 = dp.WorkItem(plan="p", task="1", prompt="x", cwd=td,
                                 declared={"model": "claude-sonnet-5", "effort": "medium", "tier": "T1-Execute"})
            result = dp.dispatch(item2, runner=fake_runner)
        label = hf.dispatch_result_label(result)
        handoff_module = hf.load_handoff_pack_module()
        self.assertTrue(handoff_module.label_matches_schema(label))


if __name__ == "__main__":
    unittest.main()
