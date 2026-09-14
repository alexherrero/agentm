#!/usr/bin/env python3
"""No test writes into the machine's real engine state.

On 2026-09-05 at 20:59 a suite ran without the hermetic guard and left ~200
directories under `~/.local/state/agentm/dream-runs/`, each recording a
`/var/folders/.../tmp...` vault path. `dreaming_scorecard.py` (retired in
agentm-vault plan 04) read the newest directory as "last night's run", so the
2026-09-05 edition reported on a
fixture — a diagnostic surface describing a test's tmpdir as the state of the
memory.

The guard exists on both runners now: `conftest.py` for pytest and
`run_unit_suite.py` for the unittest harness the battery invokes, both landed
2026-09-06 in #570 — a day *after* that leak. So this is not a hunt for an
unguarded suite; it is the assertion that the guard holds, which nothing made.

Two things are checked, because they fail differently. That both runners set
the variable is a property of the runners. That `engine_state_dir()` obeys it
is a property of the code every writer goes through — and the writers are what
actually reach the disk.

The recall ledger is checked the same way, because it sits outside the state
directory. `recall_counter.default_history_path()` answers
`$AGENTM_RECALL_HISTORY` and otherwise
`~/.cache/agentm/telemetry/recall-history.jsonl`, so rotating the state
directory never reached it. By 2026-09-13 the operator's ledger held 7,756
rows naming `zorbulax`, a fixture slug the recall suites use.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import site
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))
if str(_REPO / "scripts") not in sys.path:
    sys.path.append(str(_REPO / "scripts"))

import engine_state  # noqa: E402
import graph_snapshot  # noqa: E402 — the resolver every graph snapshot is written through
import harness_memory  # noqa: E402 — the resolver the repo registry writes through
import recall_counter  # noqa: E402 — the resolver the recall ledger writes through

# The real ones. Nothing in a test run may resolve here.
_MACHINE_STATE = Path.home() / ".local" / "state" / "agentm"
_MACHINE_LEDGER = Path.home() / ".cache" / "agentm" / "telemetry" / "recall-history.jsonl"


class TheGuardIsWiredOnBothRunners(unittest.TestCase):
    """A test reaches this repository through pytest or through the battery's
    own unittest runner, and only one of those reads conftest.py."""

    def test_conftest_sets_a_fresh_state_dir_per_test(self):
        text = (_REPO / "scripts" / "conftest.py").read_text(encoding="utf-8")
        self.assertIn("AGENTM_STATE_DIR", text)
        self.assertIn("autouse=True", text)

    def test_the_unittest_runner_sets_one_too(self):
        # The battery invokes run_unit_suite.py, which conftest.py never
        # reaches. This is the half that was missing when the leak happened.
        text = (_REPO / "scripts" / "run_unit_suite.py").read_text(encoding="utf-8")
        self.assertIn("AGENTM_STATE_DIR", text)
        self.assertIn("startTest", text)

    def test_both_redirect_the_recall_ledger_too(self):
        # The ledger is not in the state directory, so rotating that never
        # reached it. The statements that set it, since both docstrings name it.
        conftest = (_REPO / "scripts" / "conftest.py").read_text(encoding="utf-8")
        self.assertIn('monkeypatch.setenv("AGENTM_RECALL_HISTORY"', conftest)
        runner = (_REPO / "scripts" / "run_unit_suite.py").read_text(encoding="utf-8")
        self.assertIn('os.environ["AGENTM_RECALL_HISTORY"] =', runner)

    def test_the_battery_invokes_that_runner_and_not_bare_unittest(self):
        text = (_REPO / "scripts" / "check-all.sh").read_text(encoding="utf-8")
        self.assertIn("run_unit_suite.py", text)


class TheStateDirIsHonoured(unittest.TestCase):
    def test_engine_state_dir_follows_the_override(self):
        with tempfile.TemporaryDirectory() as td:
            previous = os.environ.get("AGENTM_STATE_DIR")
            os.environ["AGENTM_STATE_DIR"] = td
            try:
                resolved = Path(engine_state.engine_state_dir()).resolve()
            finally:
                if previous is None:
                    os.environ.pop("AGENTM_STATE_DIR", None)
                else:
                    os.environ["AGENTM_STATE_DIR"] = previous
        self.assertEqual(resolved, Path(td).resolve())

    def test_this_very_test_is_not_pointed_at_the_machine_state(self):
        """The guard, checking itself.

        Skipped, loudly, under a bare `python3 -m unittest test_<module>`.
        That invocation reaches neither guard — conftest.py is pytest's and
        `run_unit_suite.py` is the battery's — so a hand run of a single
        state-touching module still writes to the live directory. It is a real
        hole and it is not this task's: the residue it produces is caught
        unconditionally by TheMachineStateHoldsNoFixtures below, which is the
        check that actually protects the state.
        """
        resolved = Path(engine_state.engine_state_dir()).resolve()
        if resolved == _MACHINE_STATE.resolve():
            self.skipTest(
                "this invocation has no hermetic guard — a bare `python3 -m "
                "unittest` reaches neither conftest.py (pytest) nor "
                "run_unit_suite.py (the battery). Run it through one of those.")
        self.assertNotEqual(resolved, _MACHINE_STATE.resolve())


class TheMachineStateHoldsNoFixtures(unittest.TestCase):
    """The residue itself. A run directory naming a temporary vault path is a
    test's, and it should not be here."""

    TMP_VAULT = re.compile(r"/var/folders/|/tmp/")
    # The shape a real cycle mints: YYYYMMDD-HHMMSS-<8 hex>, no prefix.
    REAL_RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}$")

    def _run_dirs(self):
        d = _MACHINE_STATE / "dream-runs"
        if not d.is_dir():
            self.skipTest("no dream-runs on this machine")
        return sorted(p for p in d.iterdir() if p.is_dir())

    def test_no_run_directory_records_a_temporary_vault(self):
        offenders = []
        for d in self._run_dirs():
            for f in d.rglob("*"):
                if not f.is_file() or f.suffix not in (".json", ".md"):
                    continue
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if self.TMP_VAULT.search(text):
                    offenders.append(f"{d.name}/{f.name}")
                    break
        self.assertEqual(
            offenders, [],
            "run directories recording a temporary vault path — a suite wrote "
            "into the machine's engine state:\n  " + "\n  ".join(offenders[:20]))

    def test_every_run_directory_is_named_like_a_real_run(self):
        odd = [d.name for d in self._run_dirs() if not self.REAL_RUN_ID.match(d.name)]
        self.assertEqual(
            odd, [],
            "run directories that are not run ids — fixture names left behind "
            "by a suite:\n  " + "\n  ".join(odd[:20]))


class TheRunnersGuardActuallyRotates(unittest.TestCase):
    """The runner's mechanism, exercised rather than read.

    The three checks above read the two runner files for the right strings,
    which would go on passing if the guard were present and broken. This drives
    the result class through two tests and watches the variable move.
    """

    def test_each_test_gets_its_own_fresh_state_dir(self):
        probe = (
            "import os, sys, unittest\n"
            "sys.path.insert(0, %r)\n"
            "from run_unit_suite import _HermeticStateResult\n"
            "seen = []\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n"
            "        seen.append(os.environ['AGENTM_STATE_DIR'])\n"
            "    def test_b(self):\n"
            "        seen.append(os.environ['AGENTM_STATE_DIR'])\n"
            "suite = unittest.TestLoader().loadTestsFromTestCase(T)\n"
            "unittest.TextTestRunner(resultclass=_HermeticStateResult,\n"
            "                        stream=open(os.devnull, 'w')).run(suite)\n"
            "assert len(seen) == 2, seen\n"
            "assert seen[0] != seen[1], 'both tests shared one state dir: %%s' %% seen\n"
            "for d in seen:\n"
            "    assert %r not in d, 'a test resolved the machine state: ' + d\n"
            "print('ok')\n"
        ) % (str(_REPO / "scripts"), str(_MACHINE_STATE))

        r = subprocess.run([sys.executable, "-c", probe],
                           capture_output=True, text=True, timeout=120,
                           cwd=str(_REPO / "scripts"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)

    def test_each_test_gets_its_own_recall_ledger(self):
        # Read back through the resolver the ledger writes through. Nothing is
        # recorded, so a broken runner fails this without touching the ledger.
        probe = (
            "import os, sys, unittest\n"
            "sys.path.insert(0, %r)\n"
            "sys.path.insert(0, %r)\n"
            "from run_unit_suite import _HermeticStateResult\n"
            "import recall_counter\n"
            "seen = []\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n"
            "        seen.append(str(recall_counter.default_history_path()))\n"
            "    def test_b(self):\n"
            "        seen.append(str(recall_counter.default_history_path()))\n"
            "suite = unittest.TestLoader().loadTestsFromTestCase(T)\n"
            "unittest.TextTestRunner(resultclass=_HermeticStateResult,\n"
            "                        stream=open(os.devnull, 'w')).run(suite)\n"
            "assert len(seen) == 2, seen\n"
            "assert seen[0] != seen[1], 'both tests shared one ledger: %%s' %% seen\n"
            "for p in seen:\n"
            "    assert p != %r, 'a test resolved the machine ledger: ' + p\n"
            "print('ok')\n"
        ) % (str(_REPO / "scripts"), str(_REPO / "harness" / "skills" / "memory" / "scripts"),
             str(_MACHINE_LEDGER))
        # Without the battery's own setting, so the probe sees the runner alone.
        env = {k: v for k, v in os.environ.items() if k != "AGENTM_RECALL_HISTORY"}
        r = subprocess.run([sys.executable, "-c", probe],
                           capture_output=True, text=True, timeout=120,
                           cwd=str(_REPO / "scripts"), env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)


def _fingerprint(directory: Path) -> dict:
    """Every file under `directory`, with its modification time and a hash of its bytes."""
    return {
        f.relative_to(directory).as_posix():
            (f.stat().st_mtime_ns, hashlib.sha256(f.read_bytes()).hexdigest())
        for f in sorted(directory.rglob("*")) if f.is_file()
    }


def _hand_run_env(home: str) -> dict:
    """The environment a person runs a suite in, with the home directory moved to `home`."""
    env = dict(os.environ)
    # The battery's runner sets both for every test, this one included, and a
    # person's shell sets neither.
    for name in ("AGENTM_STATE_DIR", "AGENTM_RECALL_HISTORY"):
        env.pop(name, None)
    # `Path.home()` reads HOME on POSIX and USERPROFILE on Windows. Moving the
    # home also moves the user site directory, where a person's own packages
    # can live (PyYAML, on a machine that installed it with `pip install
    # --user`), so the child is pointed back at the real one rather than
    # failing an import that a hand run passes.
    env.update(HOME=home, USERPROFILE=home, PYTHONIOENCODING="utf-8",
               PYTHONUSERBASE=site.getuserbase())
    return env


def _seed_default_state(case: unittest.TestCase, env: dict, home: str) -> Path:
    """Seed a registry where the run's default state directory resolves.

    Asks the resolver the registry writes through, under exactly the
    environment the run gets. If the resolver ever stops following the home
    directory, the registry written here would land on the machine's real one,
    so this refuses before writing anything.
    """
    with mock.patch.dict(os.environ, env, clear=True):
        state = harness_memory.engine_state_dir()
    case.assertIn(
        Path(os.path.realpath(home)), Path(os.path.realpath(state)).parents,
        f"the default state directory no longer follows the home directory "
        f"({state}); this check cannot run without touching the machine's own")
    state.mkdir(parents=True)
    (state / "repos.json").write_text(json.dumps({"version": 1, "repos": [
        {"slug": "agentm", "root_path": "/srv/projects/agentm"},
        {"slug": "sherwood", "root_path": "/srv/projects/sherwood"},
    ]}, indent=2) + "\n", encoding="utf-8")
    return state


def _seed_default_ledger(case: unittest.TestCase, env: dict, home: str) -> Path:
    """Seed a recall ledger where the run's default one resolves; return its directory.

    The same guard as `_seed_default_state`, asked of the resolver the ledger
    writes through. `_hand_run_env` drops `$AGENTM_RECALL_HISTORY`; were a
    redirect still in place, the ledger would resolve outside `home` and this
    would refuse, rather than watch a directory the run never writes.
    """
    with mock.patch.dict(os.environ, env, clear=True):
        ledger = recall_counter.default_history_path()
    case.assertIn(
        Path(os.path.realpath(home)), Path(os.path.realpath(ledger)).parents,
        f"the default recall ledger no longer follows the home directory "
        f"({ledger}); this check cannot run without touching the machine's own")
    ledger.parent.mkdir(parents=True)
    ledger.write_text("".join(json.dumps(row) + "\n" for row in (
        {"ts": "2026-09-13T08:00:00+00:00", "query_hash": "feedfacecafebeef",
         "hit_slugs": ["a-real-note"], "hit_count": 1},
        {"ts": "2026-09-13T09:00:00+00:00", "query_hash": "deadbeefdeadbeef",
         "hit_slugs": [], "hit_count": 0},
    )), encoding="utf-8")
    return ledger.parent


def _run_by_hand(case: unittest.TestCase, suite: str, names: tuple, seed, place: str) -> None:
    """Run `suite` by hand, or only the tests `names` in it, under a home of its own.

    `seed(case, env, home)` puts a live file where the run's default resolves
    and returns the directory holding it, and every file in that directory must
    keep its mtime and SHA-256. With `names`, the run must also report exactly
    that many tests and a plain OK, so it cannot pass by running nothing or by
    skipping the tests that wrote.
    """
    what = " ".join(("a hand run of", suite) + names)
    with tempfile.TemporaryDirectory() as home:
        env = _hand_run_env(home)
        watched = seed(case, env, home)
        before = _fingerprint(watched)

        r = subprocess.run(
            [sys.executable, str(_REPO / "scripts" / suite), *names],
            capture_output=True, encoding="utf-8", errors="replace", timeout=300,
            cwd=str(_REPO / "scripts"), env=env)

        case.assertEqual(
            _fingerprint(watched), before,
            f"{what} wrote into {place}, which on a real machine is the live "
            f"one:\n{r.stderr[-3000:]}")
        case.assertEqual(r.returncode, 0, f"{what} failed:\n{r.stdout[-2000:]}{r.stderr[-3000:]}")
        if names:
            case.assertRegex(r.stderr, rf"(?m)^Ran {len(names)} tests? in ",
                             f"{what} did not run every named test")
            case.assertRegex(r.stderr, r"(?m)^OK$",
                             f"{what} skipped a named test, so the run proves less than it says")


class TheRegistryCLITestsRunByHand(unittest.TestCase):
    """A hand run of `test_harness_memory.py`, under a home of its own.

    The repo registry has lived at `engine_state_dir() / "repos.json"` since
    the memory-root trims (agentm-vault plan 05). The CLI tests went on
    redirecting the storage backend and never the state directory, so on
    2026-09-12 a hand run of that file registered two `/tmp/fixture-*` repos
    into the machine's registry, overwriting the real `sherwood` entry, and
    then unregistered the real `agentm`. The battery never saw it, because
    `run_unit_suite.py` gives every test its own state directory; the damage
    surfaced later as a `check-registry-hygiene` failure.

    This runs those tests the way a person does, with no `AGENTM_STATE_DIR`,
    but with the home directory pointed somewhere this test owns and a
    registry already in place where the default resolves. A leak rewrites
    that registry instead of the machine's, so the check can fail without
    doing the damage it looks for.
    """

    CLASS = "TestRepoRegistryCLI"
    # The two tests that write a registry. Named so the check cannot pass by
    # running nothing.
    WRITERS = ("test_register_then_list_via_cli", "test_unregister_via_cli")

    def test_leave_the_default_registry_as_they_found_it(self):
        with tempfile.TemporaryDirectory() as home:
            env = _hand_run_env(home)
            state = _seed_default_state(self, env, home)
            before = _fingerprint(state)

            r = subprocess.run(
                [sys.executable, str(_REPO / "scripts" / "test_harness_memory.py"), self.CLASS],
                capture_output=True, encoding="utf-8", errors="replace", timeout=300,
                cwd=str(_REPO / "scripts"), env=env)

            self.assertEqual(
                _fingerprint(state), before,
                f"a hand run of {self.CLASS} wrote into the default state directory, "
                f"which on a real machine holds the live registry:\n{r.stderr[-3000:]}")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for name in self.WRITERS:
                self.assertIn(name, r.stderr, "the run did not include a test that writes a registry")
            self.assertRegex(r.stderr, r"(?m)^OK$", "a test was skipped, so the run proves less than it says")


class TheStateWritingSuitesRunByHand(unittest.TestCase):
    """Hand runs of the other suites that wrote engine state, each under a home of its own.

    Running every suite by hand the way the class above runs one found thirteen
    more writing the machine's engine state directory (2026-09-13). Three wrote
    the repo registry: `test_project_config` added five throwaway entries, and
    the two conformance suites rewrote the file around a register-and-unregister.
    The rest wrote the `.heat.json` and `.lifecycle.json` sidecars, the
    auto-orchestration cooldown or the forward-learning watermarks, and four of
    them also failed on a hand run, because in one shared directory each test
    started from the last one's writes. The battery saw none of it: its runner
    gives every test a state directory of its own.

    Each suite runs by hand twice, with no `AGENTM_STATE_DIR`: whole, then only
    the tests named against it, each of which wrote the machine's state before
    the fix. Both runs must pass and leave the seeded default state directory
    exactly as it was. The second run is what keeps the check from passing by
    running nothing, since every named test has to run and none may be skipped.
    """

    WRITERS = {
        "test_project_config.py": (
            "TestRegisterIntegration.test_register_writes_block_and_registry",
            "TestRedetectIntegration.test_surface_then_apply_lifecycle",
        ),
        "test_storage_conformance.py": (
            "DeviceLocalConformance.test_routing_repo_registry",
            "RoutingConformanceReport.test_run_conformance_vault_includes_routing",
        ),
        "test_storage_conformance_negative.py": (
            "FaithfulDerivedConformance.test_routing_repo_registry",
        ),
        "test_auto_orchestration.py": (
            "TestState.test_save_load_round_trip",
        ),
        "test_orchestration_phase.py": (
            "TestPostWork.test_reflects_marks_and_records_fire",
            "TestPostRelease.test_runs_index_then_discover_and_records",
        ),
        "test_forward_learning.py": (
            "DryRunFixtureSourceSetTests.test_watermark_advances_after_scan",
            "CrossScanDedupTests.test_rescan_does_not_rewrite_already_seen_candidate",
        ),
        "test_memory_heat_policy.py": (
            "TestRecordHit.test_first_hit_creates_sidecar",
            "TestRecallHitIntegration.test_prompt_submit_records_hits",
        ),
        "test_memory_lifecycle.py": (
            "TestAccessDrivenReset.test_genuine_recall_access_resets_volatile_clock",
            "TestSteppedDecayScore.test_genuine_recall_access_resets_the_stepped_clock_too",
        ),
        "test_recall_daemon_fast_path.py": (
            "PromptSubmitIntegrationTests.test_the_daemon_answer_is_used_and_the_walk_never_runs",
        ),
        "test_recall_machine_prompt_skip.py": (
            "AHumanPromptQuotingAMarkerIsStillServed.test_a_marker_quoted_mid_sentence_is_served",
        ),
        "test_recall_stream_admission.py": (
            "TransparencyTests.test_a_matching_entry_is_injected_when_the_budget_is_sufficient",
        ),
        "test_recall_token_budget.py": (
            "TestPromptSubmitTokenBudget.test_truncation_marker_in_prompt_submit",
        ),
        "test_recall_trace.py": (
            "TestPackedCaptureAlignment.test_hits_carry_the_same_evidence_query_already_computed",
        ),
    }

    def test_each_leaves_the_default_state_directory_as_it_found_it(self):
        for suite, names in self.WRITERS.items():
            with self.subTest(suite=suite):
                _run_by_hand(self, suite, (), _seed_default_state, "the default state directory")
                _run_by_hand(self, suite, names, _seed_default_state, "the default state directory")


class TheLedgerWritingSuitesRunByHand(unittest.TestCase):
    """Hand runs of the suites that wrote the recall ledger, each under a home of its own.

    The ledger is not engine state. `recall_counter.default_history_path()`
    answers `$AGENTM_RECALL_HISTORY` and otherwise
    `~/.cache/agentm/telemetry/recall-history.jsonl`, whatever `XDG_CACHE_HOME`
    says. So the hand runs that found the suites above also found four writing
    the operator's ledger through `prompt_submit()` (2026-09-13), three of them
    with `isolate_module(globals())` already in place. The battery wrote it as
    well, because its runner rotated only the state directory. By then the
    ledger held 7,756 rows naming `zorbulax`, a fixture slug, and because
    `record_recall` prunes the file in place, a run could drop real rows too.

    Each suite runs by hand twice, as above: whole, then only the tests named
    against it, each of which wrote the ledger before the fix. A ledger is
    seeded where the default resolves, and every file in its directory must
    keep its mtime and SHA-256.
    """

    WRITERS = {
        "test_recall_daemon_fast_path.py": (
            "HookCutoverInjectionTests.test_the_daemons_note_is_surfaced_on_the_transparency_line",
            "PromptSubmitIntegrationTests.test_the_daemon_answer_is_used_and_the_walk_never_runs",
        ),
        "test_recall_machine_prompt_skip.py": (
            "AHumanPromptQuotingAMarkerIsStillServed.test_a_marker_quoted_mid_sentence_is_served",
            "AnEmptyPromptKeepsItsExistingBehavior.test_an_empty_prompt_still_reaches_the_engine",
        ),
        "test_recall_stream_admission.py": (
            "TransparencyTests.test_a_blocked_search_is_labelled_unsearched",
        ),
        "test_recall_temporal.py": (
            "TransparencyLineTests.test_a_resolved_bound_is_named_on_the_transparency_line",
            "TransparencyLineTests.test_no_match_leaves_the_transparency_line_unchanged",
        ),
    }

    def test_each_leaves_the_default_ledger_as_it_found_it(self):
        for suite, names in self.WRITERS.items():
            with self.subTest(suite=suite):
                _run_by_hand(self, suite, (), _seed_default_ledger, "the default recall ledger's directory")
                _run_by_hand(self, suite, names, _seed_default_ledger, "the default recall ledger's directory")


def _seed_snapshot_root(case: unittest.TestCase, env: dict, home: str) -> Path:
    """Seed a snapshot where the run's default graph snapshot root resolves.

    Asks the resolver every snapshot is written through, under exactly the
    environment the run gets. If that root ever stops following the home
    directory, the snapshot written here would land among the machine's own,
    so this refuses before writing anything. The seeded directory stands in
    for the real vault's, which sits among the fixtures on a machine that has
    run these suites.
    """
    with mock.patch.dict(os.environ, env, clear=True):
        root = graph_snapshot._local_index_root()
    case.assertIn(
        Path(os.path.realpath(home)), Path(os.path.realpath(root)).parents,
        f"the default snapshot root no longer follows the home directory "
        f"({root}); this check cannot run without touching the machine's own")
    seeded = root / "Agent-00000000" / "graph-snapshot.db"
    seeded.parent.mkdir(parents=True)
    seeded.write_bytes(b"stands in for the real vault's snapshot\n")
    return root


class TheGraphSnapshotSuitesRunByHand(unittest.TestCase):
    """Hand runs of the suites that rebuild a graph snapshot, each under a home of its own.

    `graph_snapshot.py` keeps one SQLite snapshot per vault in
    `~/.agentm/memory/_meta/<vault name>-<hash>/`. Until 2026-09-13 it fixed
    that root when the module was imported, with no override, so every fixture
    vault these suites linted, dreamed over or rebuilt got a directory of its
    own there, beside the real vault's: 77,431 of them on one machine, 2.4 GB in
    all. The battery wrote them as surely as a hand run did, because its runner
    redirects only the engine state directory.

    The root now follows `AGENTM_DEVICE_LOCAL_ROOT`, read on every call, and
    `engine_state_isolation` governs that variable. Each suite runs by hand
    twice, with neither `AGENTM_STATE_DIR` nor `AGENTM_DEVICE_LOCAL_ROOT` set:
    whole, then only the tests named against it, each of which wrote a snapshot
    before the fix. Both runs must pass and leave the seeded snapshot root
    exactly as it was. The second must run every named test and skip none, so
    the check cannot pass by running nothing.
    """

    WRITERS = {
        "test_a2_index_invariant.py": (
            "TestA2IndexInvariant.test_rebuild_leaves_no_sqlite_file_in_the_vault",
        ),
        "test_dream.py": (
            "FullPassFixtureTests.test_no_note_changes",
            "CliTests.test_main_smoke_run",
        ),
        "test_dream_job.py": (
            "SameShapeAsManualRunTests.test_job_invoked_command_matches_manual_run_shape",
        ),
        "test_dream_retired_lanes.py": (
            "RetiredLanesTests.test_a_full_cycle_leaves_the_binarys_lanes_alone",
        ),
        "test_dream_storage_rules.py": (
            "HaltTests.test_the_same_corpus_does_propose_when_the_rules_parse",
            "HashWatchTests.test_the_digest_announces_a_changed_hash",
        ),
        "test_graph_snapshot.py": (
            "TestGraphSnapshot.test_round_trip_incoming_and_orphans",
            # Below the file's `unittest.main()` guard until 2026-09-13, where
            # a hand run never reached it.
            "TestNestedLayoutSiblingSpace.test_a_sibling_root_space_note_indexes_and_never_crashes_the_rebuild",
        ),
        "test_lifecycle_transitions.py": (
            "TheCycle.test_the_cycle_never_moves_the_axis",
        ),
        "test_lint.py": (
            "GraphSnapshotCrossCheckTests.test_second_scan_after_the_rebuild_finds_no_drift",
            "SeededRotFixtureTests.test_orphan_reported",
        ),
    }

    def _run_by_hand(self, suite: str, names: tuple = ()) -> None:
        what = " ".join(("a hand run of", suite) + names)
        with tempfile.TemporaryDirectory() as home:
            env = _hand_run_env(home)
            # A person's shell does not move the device-local root either, and
            # one inherited from an outer run would let this pass vacuously.
            env.pop("AGENTM_DEVICE_LOCAL_ROOT", None)
            root = _seed_snapshot_root(self, env, home)
            before = _fingerprint(root)

            r = subprocess.run(
                [sys.executable, str(_REPO / "scripts" / suite), *names],
                capture_output=True, encoding="utf-8", errors="replace", timeout=300,
                cwd=str(_REPO / "scripts"), env=env)

            after = _fingerprint(root)
            changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            self.assertEqual(
                after, before,
                f"{what} wrote into the default graph snapshot root, which on a real "
                f"machine holds the real vault's snapshot: {changed}\n{r.stderr[-2000:]}")
            self.assertEqual(r.returncode, 0, f"{what} failed:\n{r.stdout[-2000:]}{r.stderr[-3000:]}")
            if names:
                self.assertRegex(r.stderr, rf"(?m)^Ran {len(names)} tests? in ",
                                 f"{what} did not run every named test")
                self.assertRegex(r.stderr, r"(?m)^OK$",
                                 f"{what} skipped a named test, so the run proves less than it says")

    def test_each_leaves_the_default_snapshot_root_as_it_found_it(self):
        for suite, names in self.WRITERS.items():
            with self.subTest(suite=suite):
                self._run_by_hand(suite)
                self._run_by_hand(suite, names)


if __name__ == "__main__":
    unittest.main()
