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

Behaviorally identical to `python -m unittest discover -p 'test_*.py'` in
every other respect — same discovery, same exit code, same output stream.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import engine_state_isolation


def _fresh_engine_state() -> None:
    """Point every governed variable under a temporary directory of its own.

    The directories are not removed, as the runner has never removed them; a
    run's are small and the platform reclaims its temporary space."""
    engine_state_isolation.redirect(Path(tempfile.mkdtemp(prefix="agentm-unit-")))


class _HermeticStateResult(unittest.TextTestResult):
    def startTestRun(self):  # noqa: N802 (unittest API)
        _fresh_engine_state()
        super().startTestRun()

    def startTest(self, test):  # noqa: N802 (unittest API)
        _fresh_engine_state()
        super().startTest(test)


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.dirname(os.path.abspath(__file__)) or ".",
                            pattern="test_*.py")
    runner = unittest.TextTestRunner(resultclass=_HermeticStateResult, verbosity=1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
