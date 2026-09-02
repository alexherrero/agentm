#!/usr/bin/env python3
"""The unit suite, with per-test hermetic engine state.

`scripts/conftest.py` gives every pytest-run test its own fresh
`$AGENTM_STATE_DIR`; this runner is the same guard for the unittest harness
the battery and CI actually invoke. One place, not a setUp edit in every
state-touching TestCase: the result object rotates a fresh temporary state
directory before each test starts, so no test can read the machine's real
`~/.local/state/agentm` or another test's leftovers (filing-v2 part 2a moved
machine state there, which made this guard load-bearing).

Behaviorally identical to `python -m unittest discover -p 'test_*.py'` in
every other respect — same discovery, same exit code, same output stream.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest


class _HermeticStateResult(unittest.TextTestResult):
    def startTest(self, test):  # noqa: N802 (unittest API)
        os.environ["AGENTM_STATE_DIR"] = tempfile.mkdtemp(prefix="agentm-unit-state-")
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
