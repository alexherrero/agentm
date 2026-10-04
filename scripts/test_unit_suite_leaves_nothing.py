#!/usr/bin/env python3
"""A run of the unit suite leaves nothing in the temporary directory.

`run_unit_suite.py` points every variable `engine_state_isolation` governs at
a fresh directory before each test. Until 2026-10-03 each of those was a
directory of its own in the temporary directory and none was removed: the
runner's docstring said a run's were small and the platform reclaimed its
temporary space. On one Mac the temporary directory came to hold 1,839,068
entries, 1,824,209 of them the runner's. Listing it took over two minutes,
and `pwsh`, which reads its home and its working directory as it starts, took
18 to 45 seconds to start with either in there.

The runner now makes one directory per run, keeps each rotation's in a child
of it, removes a child once the next rotation has moved the variables off it,
and removes the run's directory when the run stops. Each of those is checked
here by driving the runner over a small suite in a process of its own, the
way `test_engine_state_not_leaked.py` checks the rotation itself, with
`TMPDIR` pointed at a directory this test owns. Nothing here lists the
machine's own temporary directory, which is the one that had become too slow
to list.

The same is asked of `scripts/conftest.py`, pytest's way in. Its directories
are pytest's to remove: they sit under the one `pytest-of-<user>` entry, where
pytest keeps the last three runs and prunes the rest.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import engine_state_isolation  # noqa: E402 — names the variables a child must not inherit

# The suite the runner is driven over, as its own process. Every stage notes
# whether its directories were in place when it started, writes where a real
# test would, so there is something under each directory to remove, and
# records the directory it ran under and which of the earlier stages'
# directories were still there. The record goes to a file as the run goes,
# because one of the endings below never reaches the end of the script.
_PROBE = r'''
import io, json, os, sys, unittest
sys.path.insert(0, sys.argv[1])
out, ending = sys.argv[2], sys.argv[3]
import engine_state_isolation
import run_unit_suite

earlier = []

DIRECTORIES = ("AGENTM_STATE_DIR", "XDG_CACHE_HOME", "AGENTM_DEVICE_LOCAL_ROOT")

def record(stage):
    base = os.path.commonpath([os.environ[name] for name in engine_state_isolation.GOVERNED])
    in_place = all(os.path.isdir(os.environ[name]) for name in DIRECTORIES)
    for name in DIRECTORIES:
        nested = os.path.join(os.environ[name], "left-by-" + stage, "deeper")
        os.makedirs(nested, exist_ok=True)
        with open(os.path.join(nested, "a-file"), "w", encoding="utf-8") as fh:
            fh.write(stage)
    still_there = sorted({b for b in earlier if b != base and os.path.lexists(b)})
    earlier.append(base)
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"stage": stage, "base": base, "in_place": in_place,
                             "still_there": still_there}) + "\n")

class One(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        record("One.setUpClass")
    @classmethod
    def tearDownClass(cls):
        record("One.tearDownClass")
    def test_a(self):
        record("One.test_a")
    def test_b(self):
        record("One.test_b")
        self.fail("a failing test's directory is removed like any other")

class Two(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        record("Two.setUpClass")
    def test_c(self):
        record("Two.test_c")
        if ending == "interrupted":
            raise KeyboardInterrupt
        if ending == "killed":
            os._exit(9)
    def test_d(self):
        record("Two.test_d")

if ending == "unremovable":
    # Stands in for whatever a removal cannot get past: a directory a test
    # left unwritable, a file another process holds open on Windows.
    run_unit_suite.shutil.rmtree = lambda *args, **kwargs: None

load = unittest.defaultTestLoader.loadTestsFromTestCase
stream = io.StringIO()
try:
    unittest.TextTestRunner(resultclass=run_unit_suite._HermeticStateResult,
                            stream=stream).run(unittest.TestSuite([load(One), load(Two)]))
except KeyboardInterrupt:
    pass
sys.stdout.write(stream.getvalue())
'''

_WHOLE_RUN = ["One.setUpClass", "One.test_a", "One.test_b", "One.tearDownClass",
              "Two.setUpClass", "Two.test_c", "Two.test_d"]
_UP_TO_TEST_C = _WHOLE_RUN[:6]


def _child_env(tmp: str) -> dict:
    """This process's environment with the temporary directory moved to `tmp`.

    Every governed variable is dropped as well. Under the battery this test
    runs with all of them set, and a child that inherited them could record a
    directory its own runner never made."""
    env = {k: v for k, v in os.environ.items() if k not in engine_state_isolation.GOVERNED}
    for name in ("PYTEST_ADDOPTS", "PYTEST_DEBUG_TEMPROOT"):
        env.pop(name, None)
    # `tempfile` reads these three, in this order, on every platform.
    env.update(TMPDIR=tmp, TEMP=tmp, TMP=tmp, PYTHONIOENCODING="utf-8")
    return env


def _tree(directory: Path) -> dict:
    """Each entry directly under `directory`, with the names directly under it."""
    return {p.name: sorted(c.name for c in p.iterdir()) if p.is_dir() else None
            for p in sorted(directory.iterdir())}


class _Run:
    """One run of the probe suite: what it recorded and what it left behind."""

    def __init__(self, case: unittest.TestCase, ending: str) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as elsewhere:
            out = Path(elsewhere) / "stages.jsonl"
            r = subprocess.run(
                [sys.executable, "-c", _PROBE, str(_SCRIPTS), str(out), ending],
                capture_output=True, encoding="utf-8", errors="replace", timeout=120,
                cwd=str(_SCRIPTS), env=_child_env(tmp))
            self.returncode, self.stream, self.stderr = r.returncode, r.stdout, r.stderr
            self.rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] \
                if out.exists() else []
            self.left = _tree(Path(tmp))
            self.tmp = Path(os.path.realpath(tmp))
        self.stages = [row["stage"] for row in self.rows]
        # The listing above is only evidence if the run's directories were in
        # the directory it lists. A runner that put them somewhere else would
        # leave `tmp` empty and prove nothing.
        for row in self.rows:
            base = Path(os.path.realpath(row["base"]))
            case.assertEqual(
                base.parent.parent, self.tmp,
                f"{row['stage']} ran under {base}, which is not a child of one directory "
                f"in the temporary directory the run was given ({self.tmp})")
            case.assertTrue(
                base.parent.name.startswith("agentm-unit-"),
                f"the run's directory is no longer named agentm-unit-*: {base.parent.name}")


class ARunLeavesNothingBehind(unittest.TestCase):
    """The runner, driven over a two-class suite and watched from outside."""

    def test_a_finished_run_leaves_the_temporary_directory_empty(self):
        run = _Run(self, "finished")
        self.assertEqual(run.stages, _WHOLE_RUN, f"the probe suite did not run whole:\n{run.stderr}")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("FAILED (failures=1)", run.stream, "the probe's failing test did not fail")
        self.assertEqual(
            run.left, {},
            "a finished run of the unit suite left this in the temporary directory")

    def test_each_directory_goes_when_the_next_rotation_moves_off_it(self):
        # Removing them all at the end would leave the temporary directory
        # just as empty after a finished run, and a killed one would still
        # leave a directory for every test it had reached.
        run = _Run(self, "finished")
        self.assertEqual(run.stages, _WHOLE_RUN, f"the probe suite did not run whole:\n{run.stderr}")
        for row in run.rows:
            self.assertEqual(
                row["still_there"], [],
                f"when {row['stage']} ran, directories the variables had already left were still there")

    def test_the_isolation_is_what_it_was(self):
        # The rotation is `test_engine_state_not_leaked.py`'s to check. What
        # removal adds is two ways to break it.
        run = _Run(self, "finished")
        self.assertEqual(run.stages, _WHOLE_RUN, f"the probe suite did not run whole:\n{run.stderr}")
        by_stage = {row["stage"]: row["base"] for row in run.rows}
        # A child named as an earlier one was would hand a test the path
        # another test had kept.
        own = [by_stage[stage] for stage in
               ("One.setUpClass", "One.test_a", "One.test_b", "Two.test_c", "Two.test_d")]
        self.assertEqual(len(set(own)), len(own),
                         f"two of these ran under one directory: {own}")
        # And a directory removed too soon would be gone under a class's
        # fixtures, which run between two tests, under the directory of the
        # one before them.
        self.assertEqual(by_stage["One.tearDownClass"], by_stage["One.test_b"])
        self.assertEqual(by_stage["Two.setUpClass"], by_stage["One.test_b"])
        for row in run.rows:
            self.assertTrue(
                row["in_place"],
                f"{row['stage']} started with the directories its variables name already removed")

    def test_an_interrupted_run_leaves_it_empty_too(self):
        # Ctrl-C reaches a test as KeyboardInterrupt, which unittest lets
        # through, and the runner's `stopTestRun` is called on the way out.
        run = _Run(self, "interrupted")
        self.assertEqual(run.stages, _UP_TO_TEST_C, f"the probe suite did not stop where it was interrupted:\n{run.stderr}")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            run.left, {},
            "an interrupted run of the unit suite left this in the temporary directory")

    def test_a_killed_run_leaves_one_directory_holding_one_tests_state(self):
        # Nothing runs after a kill, so something is left. It is the run's
        # one directory with the test that was running in it, where the old
        # runner left a directory for every test the run had reached.
        run = _Run(self, "killed")
        self.assertEqual(run.stages, _UP_TO_TEST_C, f"the probe suite did not stop where it was killed:\n{run.stderr}")
        self.assertEqual(run.returncode, 9, run.stderr)
        running = Path(run.rows[-1]["base"])
        self.assertEqual(
            run.left, {running.parent.name: [running.name]},
            "a killed run should leave its one directory, holding the directory of the test that was running")

    def test_a_directory_that_cannot_be_removed_is_named(self):
        # Left in silence is how the temporary directory filled, so the runner
        # names what it could not remove, on a line of its own.
        run = _Run(self, "unremovable")
        self.assertEqual(run.stages, _WHOLE_RUN, f"the probe suite did not run whole:\n{run.stderr}")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(run.left), 1, f"expected the run's one directory to be left: {run.left}")
        (name,) = run.left
        self.assertRegex(
            run.stream, rf"(?m)^run_unit_suite: could not remove .*{re.escape(name)}\b",
            "the runner left its directory behind and did not say so")


class ThePytestPathIsPytestsToClean(unittest.TestCase):
    """`scripts/conftest.py` takes its directories from pytest's `tmp_path_factory`.

    pytest numbers a base directory per run under one `pytest-of-<user>` entry
    in the temporary directory, keeps the last three and removes the rest as
    each run ends, so this path never added an entry per test. What is held
    here is that it stays that way: a fixture that made its directories with
    `tempfile` would leave one per test, as the unittest runner did.
    """

    def test_a_pytest_run_adds_only_pytests_own_entry(self):
        if importlib.util.find_spec("pytest") is None:
            self.skipTest("pytest is not installed for this interpreter, and "
                          "conftest.py is reached through nothing else")
        # The probe sits under scripts/, where pytest finds scripts/conftest.py
        # the way it does for any suite here. Its name matches no discovery
        # pattern, and its directory goes when this test ends.
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory(dir=str(_SCRIPTS), prefix="pytest-leak-probe-") as td:
            out = Path(td) / "seen.jsonl"
            probe = Path(td) / "probe_conftest_leak.py"
            probe.write_text((
                "import json, os, sys\n"
                "sys.path.insert(0, %(scripts)r)\n"
                "import engine_state_isolation\n"
                "def _record():\n"
                "    values = [os.environ[name] for name in engine_state_isolation.GOVERNED]\n"
                "    with open(%(out)r, 'a', encoding='utf-8') as fh:\n"
                "        fh.write(json.dumps(os.path.commonpath(values)) + '\\n')\n"
                "def test_a():\n"
                "    _record()\n"
                "def test_b():\n"
                "    _record()\n"
            ) % {"scripts": str(_SCRIPTS), "out": str(out)}, encoding="utf-8")
            r = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "--rootdir", str(_SCRIPTS), str(probe)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=300,
                cwd=str(_SCRIPTS), env=_child_env(tmp))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("2 passed", r.stdout, "pytest did not run both probe tests:\n" + r.stdout)
            bases = [Path(os.path.realpath(json.loads(line)))
                     for line in out.read_text(encoding="utf-8").splitlines()]
            left = sorted(p.name for p in Path(tmp).iterdir())
            tmp_real = Path(os.path.realpath(tmp))
        self.assertEqual(len(left), 1, f"a pytest run left more than pytest's own entry: {left}")
        self.assertTrue(left[0].startswith("pytest-of-"),
                        f"a pytest run left something that is not pytest's own: {left}")
        self.assertEqual(len(bases), 2, "the probe suite did not run whole")
        for base in bases:
            self.assertIn(tmp_real / left[0], base.parents,
                          f"conftest.py made a test's directory outside pytest's own: {base}")


if __name__ == "__main__":
    unittest.main()
