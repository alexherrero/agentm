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
import harness_memory  # noqa: E402 — the resolver the repo registry writes through

# The real one. Nothing in a test run may resolve here.
_MACHINE_STATE = Path.home() / ".local" / "state" / "agentm"


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
    env.pop("AGENTM_STATE_DIR", None)
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

    def _run_by_hand(self, suite: str, names: tuple = ()) -> None:
        what = " ".join(("a hand run of", suite) + names)
        with tempfile.TemporaryDirectory() as home:
            env = _hand_run_env(home)
            state = _seed_default_state(self, env, home)
            before = _fingerprint(state)

            r = subprocess.run(
                [sys.executable, str(_REPO / "scripts" / suite), *names],
                capture_output=True, encoding="utf-8", errors="replace", timeout=300,
                cwd=str(_REPO / "scripts"), env=env)

            self.assertEqual(
                _fingerprint(state), before,
                f"{what} wrote into the default state directory, which on a real "
                f"machine is the live one:\n{r.stderr[-3000:]}")
            self.assertEqual(r.returncode, 0, f"{what} failed:\n{r.stdout[-2000:]}{r.stderr[-3000:]}")
            if names:
                self.assertRegex(r.stderr, rf"(?m)^Ran {len(names)} tests? in ",
                                 f"{what} did not run every named test")
                self.assertRegex(r.stderr, r"(?m)^OK$",
                                 f"{what} skipped a named test, so the run proves less than it says")

    def test_each_leaves_the_default_state_directory_as_it_found_it(self):
        for suite, names in self.WRITERS.items():
            with self.subTest(suite=suite):
                self._run_by_hand(suite)
                self._run_by_hand(suite, names)


if __name__ == "__main__":
    unittest.main()
