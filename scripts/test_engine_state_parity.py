#!/usr/bin/env python3
"""The two engine-state resolvers agree, in both branches.

`harness_memory.engine_state_dir()` (the scripts/ side) and
`engine_state.engine_state_dir()` (the memory-skill family's vendored copy)
must answer identically — override honored, default identical — or machine
state splits across two directories and half of it silently stops being
committed. This is the vendored-parity pattern: the copy exists for import
reachability, and this test is what keeps it a copy.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for p in (str(_HERE), str(_HERE.parent / "harness" / "skills" / "memory" / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import engine_state  # noqa: E402
import harness_memory  # noqa: E402

sys.path.insert(0, str(_HERE.parent / "harness" / "skills" / "console" / "scripts"))
sys.path.insert(0, str(_HERE / "health"))
import console as _console  # noqa: E402
import session_brief as _session_brief  # noqa: E402

_ALL_RESOLVERS = (
    engine_state.engine_state_dir,
    harness_memory.engine_state_dir,
    _console._engine_state_dir,
    _session_brief._engine_state_dir,
)


class EngineStateParity(unittest.TestCase):
    def test_override_branch_agrees(self):
        os.environ["AGENTM_STATE_DIR"] = "/tmp/parity-check-state"
        try:
            # Compare as Paths, not strings — Windows renders the same
            # directory with backslashes, and the contract is "one directory".
            answers = {Path(fn()) for fn in _ALL_RESOLVERS}
            self.assertEqual(answers, {Path("/tmp/parity-check-state")},
                             "every vendored resolver honors the override identically")
        finally:
            del os.environ["AGENTM_STATE_DIR"]

    def test_default_branch_agrees(self):
        saved = os.environ.pop("AGENTM_STATE_DIR", None)
        try:
            answers = {Path(fn()) for fn in _ALL_RESOLVERS}
            self.assertEqual(len(answers), 1,
                             f"the vendored resolvers disagree: {answers}")
            self.assertEqual(answers.pop().parts[-3:], (".local", "state", "agentm"))
        finally:
            if saved is not None:
                os.environ["AGENTM_STATE_DIR"] = saved


if __name__ == "__main__":
    unittest.main()
