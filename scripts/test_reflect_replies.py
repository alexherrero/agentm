#!/usr/bin/env python3
"""A reply to the agent is not a memory (agentm-vault § Capture, amended
2026-09-28; task 178 step 7).

The operator's side of a session is mostly direction. The idea lane files every
idea candidate, and its follow-up marker fired on replies such as "yes, and then
please file a follow-up", so those were filed as memories. The miner now drops a
user message that reads as a reply to the agent and carries no durability cue,
and lists it in the session's trace instead.

Run: python3 scripts/test_reflect_replies.py
"""
from __future__ import annotations

import io
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

import episodic_trace  # noqa: E402
import reflect  # noqa: E402

# The three replies the 2026-09-24 survey found filed as memories, as typed.
REPLIES = [
    "Yes, and then please file a follow-up in the project file in the vault for us to come back to this "
    "in a few days after we've accumulated some data. Once you file the follow-up, prepare a prompt for me "
    "to start in a new session",
    "let's do follow-up b",
    "back to this in a few days",
]
# Two real preferences, each with the durability cue the HIGH lane files on.
PREFERENCES = [
    "From now on I want the gate battery run before every commit.",
    "I prefer squash merges for every plan PR going forward.",
]


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


class ConversationalReplyTests(unittest.TestCase):
    def test_each_reply_is_one_and_each_preference_is_not(self):
        for text in REPLIES:
            self.assertIsNotNone(reflect.conversational_reply(text), text)
        for text in PREFERENCES:
            self.assertIsNone(reflect.conversational_reply(text), text)

    def test_a_rule_stated_in_a_reply_is_still_a_rule(self):
        self.assertIsNone(reflect.conversational_reply("Yes, and from now on always squash-merge."))

    def test_ordinary_ideas_and_statements_are_not_replies(self):
        for text in ("We should also measure recall on long articles later.",
                     "The daemon commits whatever git reports dirty.",
                     "idea: a blog post per shipped plan",
                     "Nobody touched the retention thread yet."):
            self.assertIsNone(reflect.conversational_reply(text), text)

    def test_the_other_survey_replies_are_caught(self):
        for text in ("change nothing for now, but file this as something we need to follow-up on during "
                     "the vault perfection pass after this series of plans is up. what's next in this plan?",
                     "ok, circle back to it after the release",
                     "leave the operator-side step for a later follow-up"):
            self.assertIsNotNone(reflect.conversational_reply(text), text)


class OnlyThePreferencesFileTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="reflect-replies-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "memory").mkdir(parents=True)
        self.messages = [_user(t) for t in REPLIES + PREFERENCES]

    def test_the_replies_are_dropped_and_the_preferences_file(self):
        mined = reflect.mine_transcript_messages(self.messages)
        self.assertEqual(len(mined["dropped_replies"]), 3)
        self.assertEqual(mined["idea_candidates"], [], "a reply no longer reaches the idea lane")
        stats = reflect.route_candidates(
            mined["memory_candidates"], mined["idea_candidates"], vault=self.root,
            mode=reflect.ROUTE_MODE_AUTO, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
        filed = sorted(p for p in (self.root / "memory").rglob("*.md"))
        bodies = "\n".join(p.read_text(encoding="utf-8") for p in filed)
        self.assertEqual(len(filed), 2, bodies)
        self.assertIn("gate battery run before every commit", bodies)
        self.assertIn("squash merges for every plan PR", bodies)
        for text in REPLIES:
            self.assertNotIn(text[:30], bodies)
        self.assertEqual(stats["ideas_filed"], 0)

    def test_the_trace_logs_what_was_dropped(self):
        lines = episodic_trace.mined_candidates(self.messages)
        dropped = [c for c in lines if c["rule"].startswith("not filed: ")]
        self.assertEqual(len(dropped), 3)
        self.assertTrue(any("let's do follow-up b" in c["excerpt"] for c in dropped))

    def test_the_route_pass_counts_them(self):
        transcript = self.root / "t.jsonl"
        transcript.write_text("\n".join(json.dumps(m) for m in self.messages) + "\n", encoding="utf-8")
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            reflect.main([str(transcript), "--vault-path", str(self.root), "--route",
                          "--route-mode", "silent"])
        route = [json.loads(ln) for ln in out.getvalue().splitlines() if '"pass": "route"' in ln]
        self.assertEqual(route[-1]["dropped_replies"], 3)


class TheAuditsRepliesTests(unittest.TestCase):
    """Task 186: the vault growth audit of 2026-10-03 found replies the idea lane
    still filed as cards after the 2026-09-28 filter, as typed."""

    NUMBERED = ("1. agree, 2. private on github ok, and make a note so we can follow-up later in another "
                "session on moving over the jellyfin template.")
    ACK = ("ack on the minor follow-up, and that reminded me that probably every design needs some a "
           "diagrams if they don't have them now to help the reader visualize how things are structured.")
    CHIPS = ("...the handoff prompts.md file, we won't need it. I have 3 chips doing some of those followups, "
             "are there any others you need from me that we should spawn a chip to fix?")

    def test_a_numbered_answer_and_an_ack_are_replies(self):
        for text in (self.NUMBERED, self.ACK, "Agreed, ship it after the gates.", "1) yes\n2) no, keep it private"):
            self.assertIsNotNone(reflect.conversational_reply(text), text)
        for text in ("1. Install the plugin.", "Acknowledgements go at the end of the post.",
                     "The agreement with the NAS vendor lapsed."):
            self.assertIsNone(reflect.conversational_reply(text), text)

    def test_the_bare_follow_up_marker_no_longer_files_an_idea(self):
        mined = reflect.mine_transcript_messages([_user(self.CHIPS)])
        self.assertEqual(mined["idea_candidates"], [], "\"followups\" alone is not an idea")

    def test_a_real_idea_still_does(self):
        mined = reflect.mine_transcript_messages(
            [_user("We should also look into automating the PlayOn recordings, as that's tedious by hand.")])
        self.assertEqual(len(mined["idea_candidates"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
