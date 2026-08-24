#!/usr/bin/env python3
"""Mining tests for what counts as something the operator actually said.

The miner's premise was that a user-role message is operator intent. In Claude
Code the user role carries far more than that: slash-command expansions,
sub-agent dispatch prompts, hook-injected context, compaction summaries. So
`AGENTS.md` writing "never fan out parallel implementers" came back as a mined
preference, and agentm's own retrieval-gate prompt — which quotes vault notes
into its text — came back as dozens more. Measured over 400 transcripts, 1,452
of 1,490 preference matches came from messages that were not utterances; the
ones that were had a median length of 270 characters against 8,266.

These tests pin the discrimination. They are deliberately about SHAPE rather
than about any one envelope tag: the tags change with the host, whereas "a
person did not type thirty thousand characters" does not.

Run: python3 scripts/test_reflect_operator_text.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import reflect  # noqa: E402

DIRECTIVE = "Always run the gate battery before committing."


def _user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _user_blocks(text, **extra):
    return {"type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            **extra}


class TestOperatorText(unittest.TestCase):

    def _mined(self, msg) -> str:
        return reflect._operator_text(msg)

    # ── what survives ────────────────────────────────────────────────────────

    def test_a_typed_sentence_is_minable(self):
        self.assertEqual(self._mined(_user(DIRECTIVE)), DIRECTIVE)

    def test_human_origin_is_minable_even_when_long(self):
        # A long message the host positively attributes to a person is a long
        # message a person wrote. The ceiling is a fallback for when nothing
        # says who spoke, and must never override something that does.
        long_typed = DIRECTIVE + " " + ("and here is more context. " * 400)
        got = self._mined(_user(long_typed, origin={"kind": "human"}))
        self.assertEqual(got, long_typed.strip())

    # ── what does not ────────────────────────────────────────────────────────

    def test_a_slash_command_expansion_is_not_minable(self):
        # The `/work` expansion: ~31,000 characters of second-person directive
        # prose, arriving as a user-role text block. This is the single biggest
        # source of the mined `never-*` clusters.
        expansion = ("You are running the work phase of the developer-workflows loop. "
                     + DIRECTIVE + " " + ("Follow the plan. " * 3000))
        self.assertEqual(self._mined(_user_blocks(expansion)), "")

    def test_non_human_origin_is_not_minable(self):
        self.assertEqual(self._mined(_user(DIRECTIVE, origin={"kind": "tool"})), "")

    def test_meta_records_are_not_minable(self):
        # Claude Code's own flag for injected/meta content. Precise where it is
        # present — it just is not present often enough to rely on alone.
        self.assertEqual(self._mined(_user(DIRECTIVE, isMeta=True)), "")

    # ── envelopes: strip the injection, keep the utterance ───────────────────

    def test_a_system_reminder_is_stripped_but_the_sentence_survives(self):
        msg = _user(f"do the thing\n<system-reminder>\n{DIRECTIVE}\n</system-reminder>")
        got = self._mined(msg)
        self.assertIn("do the thing", got)
        self.assertNotIn("Always run the gate battery", got,
                         "mined a directive out of an injected reminder")

    def test_a_recall_block_is_stripped(self):
        # The feedback loop that matters most: recall injects a vault note into
        # the prompt, the miner reads it back as something the operator said,
        # and writes a fresh note. Left alone this compounds on itself.
        msg = _user(f"what changed?\n<!-- BEGIN recall -->\n{DIRECTIVE}\n<!-- END recall -->")
        got = self._mined(msg)
        self.assertIn("what changed?", got)
        self.assertNotIn("Always run the gate battery", got)

    def test_command_tags_are_stripped(self):
        msg = _user("<command-name>/work</command-name>"
                    f"<command-message>{DIRECTIVE}</command-message>")
        self.assertNotIn("Always run the gate battery", self._mined(msg))


class TestMiningEndToEnd(unittest.TestCase):
    """The filter has to hold through `mine_transcript`, not just in isolation."""

    def _mine(self, records, tmp: Path):
        import json
        p = tmp / "t.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return reflect.mine_transcript(p)

    def setUp(self):
        import tempfile, shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="reflect-optext-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_expansion_yields_no_memory_candidates(self):
        expansion = "You are running the work phase. " + DIRECTIVE + (" step. " * 3000)
        out = self._mine([_user_blocks(expansion)], self.tmp)
        self.assertEqual(out["memory_candidates"], [])

    def test_a_typed_directive_still_yields_one(self):
        out = self._mine([_user(DIRECTIVE)], self.tmp)
        self.assertEqual(len(out["memory_candidates"]), 1)

    def test_tool_frequency_mining_is_untouched(self):
        # Workflow candidates come off ASSISTANT records and must not be caught
        # by a filter aimed at the user side.
        recs = [{"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash"}]}} for _ in range(6)]
        out = self._mine(recs, self.tmp)
        self.assertTrue(any(c.category == "workflow" for c in out["memory_candidates"]),
                        "workflow mining broke")


if __name__ == "__main__":
    unittest.main(verbosity=2)
