#!/usr/bin/env python3
"""Rulings 3 and 4 of the miner-provenance plan, pinned by the rows the
operator labeled on the write path's sample (session e805ab79).

Ruling 3 — a fix candidate carries its cause and its remedy: the three
"Fix observed" rows were 200-character fragments of assistant status reports;
each mines to nothing now, and a real fix paragraph mines to one candidate
whose body is exactly the cause sentence and the remedy sentence.

Ruling 4 — a HIGH preference needs a durability cue: "explain … what I need
to think about" (row 2, labeled should-not-file) is an in-the-moment request
and files below HIGH; "from now on I want you to pre-judge things" is a rule
and files HIGH.
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

# The three labeled fragments, as the assistant wrote them (rows 13, 19, 20).
FRAGMENTS = [
    "The excerpts are gone, and the note doesn't record enough to get them back. I tried the obvious "
    "workaround — trim the partial word instead of reconstructing it — and it reads worse.",
    "**2 — Residuals cleared.** 2,311 → **21** ragged edges, once `repair_excerpts` learned to skip retired "
    "notes. The 4 non-converging notes resolved by being retired, so a hypothesis I'd recorded is moot.",
    "**Three defects, all fixed going forward, none cleaned up retroactively:** | defect | fixed by | cleans up? | "
    "|---|---|---| | excerpts | the snap | no |",
]

REAL_FIX = (
    "Part 4's batch tests failed on windows-latest because the path compare used a POSIX suffix "
    "against a backslashed path. Fixed by comparing at the Path level and asserting the tail parts, "
    "which is the same contract stated platform-honestly. Verified locally and with a PureWindowsPath simulation."
)


def _user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _assistant(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


class _Mine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="miner-rulings-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _mine(self, records):
        p = self.tmp / "t.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return reflect.mine_transcript(p)


class AFixCarriesItsCauseAndRemedy(_Mine):
    def test_the_labeled_fragments_mine_to_nothing(self):
        for text in FRAGMENTS:
            out = self._mine([_assistant(text)])
            fixes = [c for c in out["memory_candidates"] if c.category == "fix"]
            self.assertEqual(fixes, [], text[:60])

    def test_a_real_fix_mines_to_its_two_sentences(self):
        out = self._mine([_assistant(REAL_FIX)])
        fixes = [c for c in out["memory_candidates"] if c.category == "fix"]
        self.assertEqual(len(fixes), 1, [c.title for c in fixes])
        body = fixes[0].body
        self.assertTrue(body.startswith("Fix observed: Part 4's batch tests failed on windows-latest because"), body)
        self.assertIn("Fixed by comparing at the Path level and asserting the tail parts", body)
        self.assertNotIn("Verified locally", body)  # the two sentences, not the paragraph
        self.assertNotIn("...", body)
        self.assertIn("Fixed by comparing at the Path level", fixes[0].title)

    def test_a_remedy_without_a_cause_is_not_a_fix(self):
        out = self._mine([_assistant("Fixed by bumping the timeout to 60 seconds on the slow runner.")])
        self.assertEqual([c for c in out["memory_candidates"] if c.category == "fix"], [])

    def test_a_cause_without_a_remedy_is_not_a_fix(self):
        out = self._mine([_assistant("The gate failed because the scratch state dir detached the index. Still looking.")])
        self.assertEqual([c for c in out["memory_candidates"] if c.category == "fix"], [])

    def test_the_operator_may_report_a_fix_too(self):
        out = self._mine([_user("The hook broke because the plist pointed at the old binary. Fixed by rebuilding to ~/.local/bin and kickstarting.")])
        fixes = [c for c in out["memory_candidates"] if c.category == "fix"]
        self.assertEqual(len(fixes), 1)


class AHighPreferenceNeedsADurabilityCue(_Mine):
    def _prefs(self, text):
        out = self._mine([_user(text)])
        return [c for c in out["memory_candidates"] if c.category == "preferences"]

    def test_an_in_the_moment_request_files_below_high(self):
        prefs = self._prefs("explain in plain english what's going on here, what I need to think about and what my options are")
        self.assertTrue(prefs, "the request still mines — as a low-confidence filing, not a rule")
        self.assertTrue(all(c.confidence != "HIGH" for c in prefs), [(c.title, c.confidence) for c in prefs])

    def test_a_rule_with_a_cue_files_high(self):
        prefs = self._prefs("From now on I want you to pre-judge things, tell me why, and then I decide.")
        self.assertTrue(any(c.confidence == "HIGH" for c in prefs), [(c.title, c.confidence) for c in prefs])

    def test_every_cue_lifts_a_preference(self):
        for cue in ("always", "never", "going forward", "in general", "every time", "whenever", "by default", "as a rule"):
            prefs = self._prefs(f"I prefer short commit subjects, {cue}.")
            self.assertTrue(any(c.confidence == "HIGH" for c in prefs), cue)

    def test_a_negative_directive_needs_the_cue_too(self):
        low = self._prefs("don't add a trailer to this commit")
        self.assertTrue(all(c.confidence != "HIGH" for c in low))
        high = self._prefs("From now on, don't add a Co-Authored-By trailer to any commit.")
        self.assertTrue(any(c.confidence == "HIGH" for c in high), [(c.title, c.confidence) for c in high])

    def test_a_bare_always_never_is_never_high(self):
        # The word is the cue, so it cannot tell a rule from prose ("it always
        # crashes"); the bare pattern stays below HIGH as it always did, and a
        # single occurrence files at low confidence for review.
        prefs = self._prefs("it always crashes when the index is cold")
        self.assertTrue(prefs)
        self.assertTrue(all(c.confidence != "HIGH" for c in prefs), [(c.title, c.confidence) for c in prefs])


if __name__ == "__main__":
    unittest.main()
