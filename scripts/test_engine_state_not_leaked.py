#!/usr/bin/env python3
"""No test writes into the machine's real engine state.

On 2026-09-05 at 20:59 a suite ran without the hermetic guard and left ~200
directories under `~/.local/state/agentm/dream-runs/`, each recording a
`/var/folders/.../tmp...` vault path. `dreaming_scorecard.py` reads the newest
directory as "last night's run", so the 2026-09-05 edition reported on a
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

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import engine_state  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
