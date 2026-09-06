#!/usr/bin/env python3
"""test_engine_state_isolation.py — the shared isolation helper (PLAN-source-and-hygiene, task 3)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_SKILL)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine_state_isolation as esi  # noqa: E402
import engine_state  # noqa: E402  — the resolver the wrapper exists to redirect


class TheContextManager(unittest.TestCase):
    def test_it_points_the_governed_variables_at_a_fresh_directory(self):
        with esi.isolated_engine_state() as state:
            self.assertEqual(os.environ["AGENTM_STATE_DIR"], str(state))
            self.assertTrue(os.environ["XDG_CACHE_HOME"].endswith("cache"))
            self.assertNotEqual(Path(state), Path.home() / ".local" / "state" / "agentm")
            first = state
        with esi.isolated_engine_state() as second:
            self.assertNotEqual(first, second, "each block gets its own directory")

    def test_it_restores_a_variable_that_was_set(self):
        os.environ["AGENTM_STATE_DIR"] = "/outer/state"
        self.addCleanup(os.environ.pop, "AGENTM_STATE_DIR", None)
        with esi.isolated_engine_state():
            self.assertNotEqual(os.environ["AGENTM_STATE_DIR"], "/outer/state")
        self.assertEqual(os.environ["AGENTM_STATE_DIR"], "/outer/state",
                         "a battery that sets the variable from outside gets it back")

    def test_it_removes_a_variable_that_was_not_set(self):
        os.environ.pop("AGENTM_STATE_DIR", None)
        os.environ.pop("XDG_CACHE_HOME", None)
        with esi.isolated_engine_state():
            self.assertIn("AGENTM_STATE_DIR", os.environ)
        self.assertNotIn("AGENTM_STATE_DIR", os.environ)
        self.assertNotIn("XDG_CACHE_HOME", os.environ)


class ThePerTestHelper(unittest.TestCase):
    def test_it_returns_the_state_dir_and_restores_on_cleanup(self):
        outer = os.environ.get("AGENTM_STATE_DIR")

        class Probe(unittest.TestCase):
            seen = None

            def setUp(inner):
                inner.state = esi.isolate_engine_state(inner)

            def runTest(inner):
                Probe.seen = os.environ["AGENTM_STATE_DIR"]
                inner.assertEqual(Probe.seen, str(inner.state))

        result = Probe().run()
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertIsNotNone(Probe.seen)
        self.assertEqual(os.environ.get("AGENTM_STATE_DIR"), outer)

    def test_root_reuses_a_directory_the_test_already_owns(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            class Probe(unittest.TestCase):
                def runTest(inner):
                    state = esi.isolate_engine_state(inner, root=tmp)
                    inner.assertEqual(state, Path(tmp) / "state")

            self.assertTrue(Probe().run().wasSuccessful())


class TheModuleWrapper(unittest.TestCase):
    def test_every_test_in_the_module_gets_its_own_directory(self):
        # Two tests in one class that would see each other's state without
        # the wrapper: the first writes a file, the second reads for it.
        class Leaky(unittest.TestCase):
            def test_a_writes(self):
                d = engine_state.engine_state_dir()
                d.mkdir(parents=True, exist_ok=True)
                (d / "left-behind").write_text("x", encoding="utf-8")

            def test_b_should_not_see_it(self):
                d = engine_state.engine_state_dir()
                assert not (d / "left-behind").exists(), "state leaked between tests"

        namespace = {"__name__": "probe_module", "Leaky": Leaky}
        Leaky.__module__ = "probe_module"
        self.assertEqual(esi.isolate_module(namespace), ["Leaky"])

        suite = unittest.TestLoader().loadTestsFromTestCase(Leaky)
        result = unittest.TestResult()
        suite.run(result)
        self.assertTrue(result.wasSuccessful(),
                        [str(e) for e in result.errors + result.failures])

    def test_it_skips_a_testcase_the_module_did_not_define(self):
        class Elsewhere(unittest.TestCase):
            def runTest(self):
                pass

        Elsewhere.__module__ = "some.other.module"
        self.assertEqual(esi.isolate_module({"__name__": "mine", "Elsewhere": Elsewhere}), [])

    def test_it_is_idempotent(self):
        class Once(unittest.TestCase):
            def runTest(self):
                pass

        Once.__module__ = "mine"
        namespace = {"__name__": "mine", "Once": Once}
        esi.isolate_module(namespace)
        first = Once.run
        esi.isolate_module(namespace)
        self.assertIs(Once.run, first, "a second pass must not stack another wrapper")


class TheNamedSuitesAreGoverned(unittest.TestCase):
    """The suites the plan named must actually carry the helper. A list in a
    plan is a claim; this is the check that it stayed true."""

    NAMED = (
        "test_backfill_reference_bodies", "test_calendar_promotion", "test_console",
        "test_console_crystallize_section", "test_correction", "test_dream_storage_rules",
        "test_inbox_triage", "test_incubator_lint", "test_lint", "test_notes_link_discovery",
        "test_opinion_supplement", "test_orchestration_briefing", "test_orchestration_idle",
        "test_repair_excerpts", "test_retro_mining_cleanup", "test_sampled_audit_surface",
        "test_skill_modules_file_loadable", "test_storage_rules", "test_vault_lint",
    )

    def test_each_named_suite_calls_the_helper(self):
        missing = []
        for name in self.NAMED:
            path = _HERE / f"{name}.py"
            if not path.exists():
                missing.append(f"{name} (file is gone)")
                continue
            text = path.read_text(encoding="utf-8")
            if "isolate_module(globals())" not in text and "isolate_engine_state(" not in text:
                missing.append(name)
        self.assertEqual(missing, [], "these suites read engine state without governing it")


if __name__ == "__main__":
    unittest.main()
