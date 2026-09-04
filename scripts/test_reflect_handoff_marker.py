#!/usr/bin/env python3
"""The handoff marker (miner-provenance, ruling 1): a handoff prompt the agent
wrote and the operator pasted is not the operator speaking, whatever the host
stamps on the paste. The fixture is the shape of the real message that
produced five false "User stated" preferences — a long human-origin paste
full of standing rules — with and without the marker.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import reflect  # noqa: E402

HANDOFF = (
    "# Session prompt — build the filing contract\n\n"
    "You are picking up the memory-ingestion / filing-contract arc in `agentm`, at part 5 of 6. "
    "Parts 1–4 are built. Run `/work` against the plan and work the task list autonomously, in sequence. "
    "Safety-gate each task; stop and ask only when a task is genuinely unrecoverable, ambiguous, "
    "scope-drifting, or needs a decision the design never settled. Recoverable actions — push, PR, merge — "
    "proceed announced.\n\n"
    "Facts to honour:\n"
    "* Staleness is computed from input hashes, never judged by a model.\n"
    "* Shell-script test stubs do not run on Windows. Part 4's did, and three batch tests failed on "
    "`windows-latest` — the batch logic was never tested there at all.\n\n"
    "The standing rules for this arc:\n"
    "1. The design is final. Refine how, never whether. If a task list is wrong, fix the task list.\n"
    "6. Tests are sacred. Never weaken an assertion, never `@skip` a real failure.\n"
) + ("Context paragraph the agent wrote for the next session. " * 60)

TYPED = "From now on always run the gate battery before committing."


def _user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


class TheMarker(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="handoff-marker-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.assertGreater(len(HANDOFF), reflect.MAX_OPERATOR_UTTERANCE_CHARS)

    def _mine(self, records):
        p = self.tmp / "t.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return reflect.mine_transcript(p)

    def test_an_unmarked_human_origin_paste_is_still_mined_as_ruled(self):
        # The override stands: the host said a person sent it. This is the
        # failure the sample found, kept as the control the marker is judged
        # against.
        out = self._mine([_user(HANDOFF, origin={"kind": "human"})])
        nevers = [c for c in out["memory_candidates"] if "never" in c.body.lower()]
        self.assertGreaterEqual(len(nevers), 4, [c.title for c in out["memory_candidates"]])

    def test_a_marked_paste_yields_nothing(self):
        out = self._mine([_user(reflect.HANDOFF_MARKER + "\n" + HANDOFF, origin={"kind": "human"})])
        self.assertEqual(out["memory_candidates"], [])
        self.assertEqual(out["idea_candidates"], [])

    def test_the_marker_anywhere_in_the_message_is_enough(self):
        # A pasted section of a longer pack carries its own marker under the
        # heading, not on line one.
        text = "## Part 5 — prompt\n\n" + reflect.HANDOFF_MARKER + "\n\n" + HANDOFF
        self.assertEqual(reflect._operator_text(_user(text, origin={"kind": "human"})), "")

    def test_a_typed_line_in_its_own_message_still_mines_beside_a_marked_paste(self):
        out = self._mine([_user(reflect.HANDOFF_MARKER + "\n" + HANDOFF, origin={"kind": "human"}),
                          _user(TYPED)])
        titles = [c.title for c in out["memory_candidates"]]
        self.assertEqual(len(titles), 1, titles)
        self.assertIn("always run the gate battery", titles[0])

    def test_the_marker_survives_envelope_stripping(self):
        self.assertIn(reflect._HANDOFF_MARKER_KEY, reflect._strip_envelopes(reflect.HANDOFF_MARKER + "\n" + TYPED))


if __name__ == "__main__":
    unittest.main()
