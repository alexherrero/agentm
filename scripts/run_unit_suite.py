#!/usr/bin/env python3
"""The unit suite, with every variable `engine_state_isolation` governs rotated per test.

`scripts/conftest.py` gives every pytest-run test its own engine state; this
runner is the same guard for the unittest harness the battery and CI actually
invoke. One place, not a setUp edit in every state-touching TestCase: the
result object points the governed variables at a fresh temporary directory
before each test starts, so no test can read the machine's real
`~/.local/state/agentm` or another test's leftovers (filing-v2 part 2a moved
machine state there, which made this guard load-bearing).

Which variables move is `engine_state_isolation.GOVERNED`'s to say, and the
runner takes the list from there rather than naming any itself. It once named
two, the engine state directory and the recall ledger, while the helper
governed four, so a suite that never asked for isolation still reached the
operator's own directories from inside the battery: anything that lints,
dreams or rebuilds a graph snapshot writes under the device-local root,
`~/.agentm/memory/_meta` by default, and vault locks and the dream revert log
follow `XDG_CACHE_HOME` into the real `~/.cache`, where one run of the unit
suite left 155 lock directories on 2026-09-14. A variable added to `GOVERNED`
is covered here from then on.

The rotation starts before the first test rather than at it, because a
class's `setUpClass` runs before `startTest` is called for any of its tests,
and the first class's would otherwise run under whatever the shell had.

A run leaves nothing in the temporary directory. It makes one directory
there, gives each rotation a numbered child of it, removes a child as soon as
the next rotation has moved the variables off it, and removes the directory
itself when the run stops. Until 2026-10-03 each rotation made a directory of
its own in the temporary directory and none was ever removed, on the
reasoning that a run's are small and the platform reclaims its temporary
space. macOS did not: one machine's temporary directory held 1,839,068
entries that day, 1,824,209 of them left by this runner, and whatever listed
it stalled. `pwsh` took 18 to 45 seconds to start with its home or working
directory in there, and 0.3 seconds anywhere else. A run that is killed
outright leaves its one directory behind, holding one test's state, where it
used to leave a directory for every test it had reached.

Behaviorally identical to `python -m unittest discover -p 'test_*.py'` in
every other respect — same discovery, same exit code, same output stream.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import engine_state_isolation


class _HermeticStateResult(unittest.TextTestResult):
    """Rotates the governed variables before each test and removes what they left.

    The run's directory is made on the first rotation and removed in
    `stopTestRun`, which `TextTestRunner.run` reaches however the run ends
    short of the process being killed: a failing test, an error in a fixture
    and a Ctrl-C all pass through it.

    A class's `setUpClass` runs under the directory of the test before it, or
    under the one made before the run for the first class, and that directory
    goes when the class's first test starts. The variables have moved off it
    by then, so nothing that asks a resolver can still reach it; a fixture
    that wants a directory for the whole class makes its own.
    """

    _run_dir: "Path | None" = None   # this run's one entry in the temporary directory
    _test_dir: "Path | None" = None  # the child the governed variables sit under now
    _rotations = 0

    def _rotate(self) -> None:
        """Point every governed variable under a fresh child of the run's directory.

        The children are numbered, so a path never names a second directory
        within a run: one a test kept after its own rotation stays gone. The
        child the variables just left is removed only once they have moved,
        so there is no moment when they name a directory that is not there.
        """
        if self._run_dir is None:
            self._run_dir = Path(tempfile.mkdtemp(prefix="agentm-unit-"))
        left = self._test_dir
        self._rotations += 1
        self._test_dir = self._run_dir / str(self._rotations)
        engine_state_isolation.redirect(self._test_dir)
        if left is not None:
            # Whatever cannot go now, a file a child process still holds open
            # on Windows say, goes with the run's directory at the end.
            shutil.rmtree(left, ignore_errors=True)

    def startTestRun(self):  # noqa: N802 (unittest API)
        self._rotate()
        super().startTestRun()

    def startTest(self, test):  # noqa: N802 (unittest API)
        self._rotate()
        super().startTest(test)

    def stopTestRun(self):  # noqa: N802 (unittest API)
        super().stopTestRun()
        run_dir, self._run_dir, self._test_dir = self._run_dir, None, None
        if run_dir is None:
            return
        # The governed variables still name the last test's directory. They
        # are left there and not handed back to the shell's values: anything
        # that writes after the run would otherwise write the machine's own.
        shutil.rmtree(run_dir, ignore_errors=True)
        if os.path.lexists(run_dir):
            # Said aloud, because a directory left behind in silence is how
            # the temporary directory came to hold 1.8 million of them.
            if self.dots:
                self.stream.writeln()  # the line of progress dots is still open
            self.stream.writeln(f"run_unit_suite: could not remove {run_dir}; it is safe to delete")


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.dirname(os.path.abspath(__file__)) or ".",
                            pattern="test_*.py")
    runner = unittest.TextTestRunner(resultclass=_HermeticStateResult, verbosity=1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
