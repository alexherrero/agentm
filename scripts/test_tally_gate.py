#!/usr/bin/env python3
"""The tally gate at the Python write door (agentm-vault, landing group 02).

The retired miner wrote one note per tool per session whose whole body was a
count. 292 were purged as manifest A. The miner resolves against the session's
working directory, so a worktree from an older base still runs it — the gate
sits at the door, not in the writer.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import filing_engine  # noqa: E402

TEMPLATE = ("The `Bash` tool was invoked 2592 times during this session. "
            "If this represents a repeatable workflow, capture the sequence + when to use it.")


class TheTallyGate(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="tally-gate-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)

    def test_the_template_is_refused_at_the_door(self):
        with self.assertRaises(filing_engine.RefusedTally) as caught:
            filing_engine.decide(self.vault, title="Bash 2592", body=TEMPLATE, slug="bash-2592")
        self.assertIn("tool-tally template", str(caught.exception))

    def test_a_refused_tally_writes_nothing(self):
        before = sorted(self.vault.rglob("*"))
        with self.assertRaises(filing_engine.RefusedTally):
            filing_engine.decide(self.vault, title="Bash 2592", body=TEMPLATE, slug="bash-2592")
        self.assertEqual(sorted(self.vault.rglob("*")), before)

    def test_the_gate_recognises_every_tool_and_count(self):
        for body in ("The `Read` tool was invoked 3 times during this session.",
                     "The `mcp__foo__bar` tool was invoked 118 times during this session.",
                     "prose. The `Edit` tool was invoked 7 times during this session. more prose."):
            self.assertTrue(filing_engine.TALLY_TEMPLATE_RE.search(body), body)

    def test_the_title_is_read_too(self):
        self.assertTrue(filing_engine.TALLY_TEMPLATE_RE.search(
            "The `Bash` tool was invoked 12 times during this session.\nsome body"))

    def test_a_real_note_still_lands(self):
        # a door that guesses costs a real capture: the gate is the template, not
        # any note that counts something.
        for body in ("Run the Bash tool sparingly; it was slow in this session.",
                     "The daemon was invoked 12 times during this session by the runner.",
                     "Batch tool calls: three independent reads go in one message.",
                     "The `Bash` tool was invoked during this session."):
            self.assertIsNone(filing_engine.TALLY_TEMPLATE_RE.search(body), body)

    def test_the_gate_matches_the_purged_population(self):
        # the same sentence the purge lane's manifest-A predicate recognises
        import purge
        self.assertTrue(purge._TALLY_RE.search(TEMPLATE))
        self.assertTrue(filing_engine.TALLY_TEMPLATE_RE.search(TEMPLATE))


if __name__ == "__main__":
    unittest.main()
