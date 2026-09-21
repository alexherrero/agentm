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
import episodic_trace  # noqa: E402  — reads the recall ledger
import recall_counter  # noqa: E402  — writes the recall ledger


class TheContextManager(unittest.TestCase):
    def test_it_points_the_governed_variables_at_a_fresh_directory(self):
        with esi.isolated_engine_state() as state:
            self.assertEqual(os.environ["AGENTM_STATE_DIR"], str(state))
            self.assertTrue(os.environ["XDG_CACHE_HOME"].endswith("cache"))
            self.assertNotEqual(Path(state), Path.home() / ".local" / "state" / "agentm")
            # One place per governed variable, none shared, each inside this
            # block's own directory. The recall ledger is a file the helper
            # names and does not create; the directories it does create.
            places = [Path(os.environ[name]) for name in esi.GOVERNED]
            self.assertEqual(len(set(places)), len(esi.GOVERNED), places)
            for place in places:
                self.assertIn(Path(state).parent, place.parents, place)
            for name in ("AGENTM_STATE_DIR", "XDG_CACHE_HOME", "AGENTM_DEVICE_LOCAL_ROOT"):
                self.assertTrue(Path(os.environ[name]).is_dir(), name)
            first = state
        with esi.isolated_engine_state() as second:
            self.assertNotEqual(first, second, "each block gets its own directory")

    def test_the_recall_ledger_resolves_inside_the_block(self):
        # Through the resolvers rather than the variable, so a rename on either
        # side cannot leave the helper setting something nothing reads.
        machine_ledger = Path.home() / ".cache" / "agentm" / "telemetry" / "recall-history.jsonl"
        with esi.isolated_engine_state() as state:
            written = recall_counter.default_history_path()
            read = episodic_trace.default_history_path()
        self.assertIn(state.parent, written.parents)
        self.assertNotEqual(written, machine_ledger)
        self.assertEqual(read, written, "the trace reads a different ledger from the one recall writes")

    def test_it_moves_the_graph_snapshot_root_with_the_device_local_root(self):
        # A test that lints, dreams or rebuilds a snapshot writes under this
        # root, which is the operator's `~/.agentm/memory/_meta` by default.
        import graph_snapshot

        with esi.isolated_engine_state():
            root = graph_snapshot._local_index_root()
            self.assertEqual(root, Path(os.environ["AGENTM_DEVICE_LOCAL_ROOT"]) / "_meta")
            self.assertNotEqual(root, Path.home() / ".agentm" / "memory" / "_meta")

    def test_it_moves_the_runner_state_root_with_the_cache_root(self):
        # The runner's per-job markers, watchdog records and last cycle's
        # account sit under `~/.cache/agentm/runner`, which the live runner
        # writes on every cycle. Until 2026-09-14 that root was fixed at import
        # and made by every reader. Through the resolver, so the runner cannot
        # drift back to a root this helper no longer moves.
        from runner import state as runner_state

        with esi.isolated_engine_state():
            root = runner_state.default_state_root()
            self.assertEqual(root, Path(os.environ["XDG_CACHE_HOME"]) / "agentm" / "runner")
            self.assertNotEqual(root, Path.home() / ".cache" / "agentm" / "runner")
            self.assertEqual(runner_state.cycle_summary_path(), root / "last-cycle.json")
            self.assertEqual(runner_state.read_marker("never-run"), {})
            self.assertFalse(root.exists(), "a read made the runner's state root")

    def test_it_restores_a_variable_that_was_set(self):
        os.environ["AGENTM_STATE_DIR"] = "/outer/state"
        self.addCleanup(os.environ.pop, "AGENTM_STATE_DIR", None)
        with esi.isolated_engine_state():
            self.assertNotEqual(os.environ["AGENTM_STATE_DIR"], "/outer/state")
        self.assertEqual(os.environ["AGENTM_STATE_DIR"], "/outer/state",
                         "a battery that sets the variable from outside gets it back")

    def test_it_removes_a_variable_that_was_not_set(self):
        for name in esi.GOVERNED:
            os.environ.pop(name, None)
        with esi.isolated_engine_state():
            for name in esi.GOVERNED:
                self.assertIn(name, os.environ)
        for name in esi.GOVERNED:
            self.assertNotIn(name, os.environ)


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

    # Four suites left the list with their subjects in agentm-vault plan 04:
    # the crystallize staging and its console section, the opinion supplement,
    # and the sampled audit's console surface. The needs-review suite joined
    # it, because the map now reads the dream cycle's findings from here. The
    # incubator lint left with its subject in agentm-vault plan 13, when
    # `Ideas.md` became a generated file.
    NAMED = (
        "test_backfill_reference_bodies", "test_calendar_promotion", "test_console",
        "test_correction", "test_dream_storage_rules",
        "test_lint", "test_notes_link_discovery",
        "test_orchestration_briefing", "test_orchestration_idle",
        "test_repair_excerpts", "test_retro_mining_cleanup", "test_needs_review",
        "test_skill_modules_file_loadable", "test_storage_rules", "test_vault_lint",
        # Derived by running every state-mentioning suite against a scratch
        # state directory and watching what it wrote or read, rather than by
        # reading imports. The breaker suite is the sharpest case: its trip
        # record lives here and ignores the vault path it is handed, so one
        # test's trip was the next test's starting state — five failures on a
        # hand run while the battery's own per-test rotation kept it green.
        # The health suite appends to `health/history.jsonl`, which the
        # scorecard reads. The last two carried a by-hand copy of this helper.
        "test_enrichment_breaker", "test_health_score",
        "health/test_session_brief",
        # The repo registry moved into engine state with the memory-root trims
        # (agentm-vault plan 05), and `TestRepoRegistryCLI` went on redirecting
        # the storage backend alone, so a hand run of this suite rewrote the
        # machine's registry on 2026-09-12. test_engine_state_not_leaked runs
        # that class by hand to prove it stays off the machine.
        "test_harness_memory",
        # Running every suite by hand the same way found thirteen more writing
        # the machine's engine state (2026-09-13). Three wrote the registry:
        # `test_project_config` added five throwaway entries, and the two
        # conformance suites rewrote it around a register-and-unregister. The
        # rest wrote the heat and lifecycle sidecars, the auto-orchestration
        # cooldown or the forward-learning watermarks, and four of those failed
        # on a hand run because each test started from the last one's writes.
        # test_engine_state_not_leaked runs every one of them by hand.
        "test_project_config", "test_storage_conformance",
        "test_storage_conformance_negative", "test_auto_orchestration",
        "test_orchestration_phase", "test_forward_learning",
        "test_memory_heat_policy", "test_memory_lifecycle",
        "test_recall_daemon_fast_path", "test_recall_machine_prompt_skip",
        "test_recall_stream_admission", "test_recall_token_budget",
        "test_recall_trace",
        # The recall ledger joined the governed variables when the same hand
        # runs found four suites appending to the operator's real ledger
        # (2026-09-13). Three are named above; this one writes the ledger and
        # nothing else. test_engine_state_not_leaked runs all four by hand.
        "test_recall_temporal",
        # The graph snapshot's root follows the device-local root, which this
        # helper governs since 2026-09-13. Before that the root was fixed at
        # import, and eight suites rebuilt a snapshot per fixture vault into
        # the operator's `~/.agentm/memory/_meta`, from the battery and hand
        # runs alike: 77,431 directories on one machine. Two of the eight,
        # `test_dream_storage_rules` and `test_lint`, are named above.
        # test_engine_state_not_leaked runs all eight by hand.
        "test_a2_index_invariant", "test_dream", "test_dream_job",
        "test_dream_retired_lanes", "test_graph_snapshot",
        "test_lifecycle_transitions",
        # The runner's state root reads `XDG_CACHE_HOME` on every call since
        # 2026-09-14; before that it was fixed at import and made by every
        # reader, and four suites left `~/.cache/agentm/runner` under a
        # throwaway home by asking after markers. The doctor's and the brief's
        # suites joined the helper then; the console's and the morning note's
        # already carried it. test_engine_state_not_leaked runs all four by hand.
        "test_machinery_doctor", "test_session_brief_runner", "test_morning_note",
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
