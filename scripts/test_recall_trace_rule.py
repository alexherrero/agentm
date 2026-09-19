#!/usr/bin/env python3
"""The trace rule: a session trace is served only on a time question.

A trace is the handoff record of a session — what was asked and what happened.
It answers a time question well and almost nothing else, and it is the best
lexical match in the corpus for a prompt that was itself pasted into a session.
That is not a hypothetical: session 6's own first recall came back as five
session traces of the same pasted brief, one per session that had pasted it,
and six of the nine files in `episodic/` were such traces.

So the prompt hook admits a trace when the prompt carries a temporal bound the
extractor resolves, and drops it before the five are chosen otherwise
(agentm-vault § Surfaces). Traces stay indexed; `memory_search` returns them
when asked, and promote reads them. This is a rule about what gets injected
unasked, not a wall.

The two arms are tested on one corpus, because two implementations of one rule
is a drift surface and the only honest check is to run both.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import recall  # noqa: E402

TIME_QUESTION = "what did we decide last week"
NAMING_QUESTION = "how do we name plans"


class TheExtractorDrawsTheLine(unittest.TestCase):
    """The rule reuses `_extract_temporal_bound` rather than writing a second
    'is this a time question' test. Two heuristics for one question drift."""

    def test_a_time_question_admits(self):
        for prompt in ("what did we decide last week",
                       "what happened yesterday",
                       "what did I work on in June",
                       "anything since 2026-08-01"):
            with self.subTest(prompt=prompt):
                self.assertTrue(recall.prompt_admits_traces(prompt))

    def test_a_naming_question_does_not(self):
        for prompt in ("how do we name plans",
                       "what is our commit-message convention",
                       "where does a new capture go"):
            with self.subTest(prompt=prompt):
                self.assertFalse(recall.prompt_admits_traces(prompt))

    def test_asking_for_a_date_is_not_supplying_one(self):
        """`when did I decide X` bounds nothing — it asks for a date rather
        than supplying one, and a trace is not its answer either: the note
        recording the decision is. The extractor already abstains here, and
        this pins that the trace rule inherits the abstention rather than
        second-guessing it."""
        for prompt in ("when did I decide to use worktrees",
                       "how long has it been since my last release",
                       "the last time we shipped"):
            with self.subTest(prompt=prompt):
                self.assertFalse(recall.prompt_admits_traces(prompt))


class _Corpus(unittest.TestCase):
    """One vault, one trace, one memory note, both matching both prompts."""

    TRACE = "memory/episodic/2026-09-10-a-session.md"
    NOTE = "memory/semantic/naming-plans.md"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "agent"
        self._write(self.TRACE,
                    "kind: session-trace",
                    "We decided last week how we name plans. Decide naming.")
        self._write(self.NOTE,
                    "type: convention",
                    "How we name plans: we decided last week to use a verb slug.")

    def _write(self, rel: str, kind_line: str, body: str) -> None:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\ntitle: {p.stem}\n{kind_line}\nstatus: active\n"
                     f"tags: []\n---\n\n{body}\n", encoding="utf-8")


class TheInProcessArm(_Corpus):
    def _paths(self, prompt: str) -> list:
        rows = recall.query(
            vault=self.vault, query_text=prompt, k=5,
            admit_traces=recall.prompt_admits_traces(prompt),
            deadline=time.monotonic() + 60, stderr=io.StringIO())
        return [r["path"] for r in rows]

    def test_a_time_question_admits_the_trace(self):
        self.assertIn(self.TRACE, self._paths(TIME_QUESTION))

    def test_a_naming_question_admits_none_on_the_same_corpus(self):
        got = self._paths(NAMING_QUESTION)
        self.assertNotIn(self.TRACE, got)
        # And the corpus is not silenced to buy that: the note still answers.
        self.assertIn(self.NOTE, got)


class TheDaemonArm(_Corpus):
    """The daemon returns both; the hook is what drops one."""

    def _payload(self) -> str:
        return json.dumps({
            "results": [{"path": self.TRACE, "score": 9.0},
                        {"path": self.NOTE, "score": 8.0}],
            "matched": 2,
            "always_load_hidden": 0,
        })

    def _paths(self, prompt: str) -> tuple:
        drops: dict = {}
        done = subprocess.CompletedProcess([], 0, stdout=self._payload(), stderr="")
        with mock.patch.object(recall.subprocess, "run", return_value=done):
            rows = recall._daemon_search(
                vault=self.vault, query_text=prompt, k=5, drops=drops,
                admit_traces=recall.prompt_admits_traces(prompt))
        return [r["path"] for r in (rows or [])], drops

    def test_a_time_question_admits_the_trace(self):
        got, _ = self._paths(TIME_QUESTION)
        self.assertIn(self.TRACE, got)

    def test_a_naming_question_admits_none_on_the_same_corpus(self):
        got, drops = self._paths(NAMING_QUESTION)
        self.assertNotIn(self.TRACE, got)
        self.assertIn(self.NOTE, got)
        # Counted, not silent: why a recall came back shorter has to be
        # readable from the ledger rather than inferred.
        self.assertEqual(drops.get("session_trace_no_temporal_bound"), 1)

    def test_the_drop_happens_before_the_k_are_chosen(self):
        """Otherwise a dropped trace shortens the injection instead of leaving
        a slot for a real hit — the design says 'dropped before the five are
        chosen' for exactly this reason."""
        extra = [f"memory/semantic/filler-{i}.md" for i in range(5)]
        for rel in extra:
            self._write(rel, "type: reference", "How we name plans.")
        payload = json.dumps({
            "results": ([{"path": self.TRACE, "score": 9.0}]
                        + [{"path": p, "score": 8.0 - i} for i, p in enumerate(extra)]),
            "matched": 6, "always_load_hidden": 0})
        done = subprocess.CompletedProcess([], 0, stdout=payload, stderr="")
        with mock.patch.object(recall.subprocess, "run", return_value=done):
            rows = recall._daemon_search(
                vault=self.vault, query_text=NAMING_QUESTION, k=5, drops={},
                admit_traces=False)
        got = [r["path"] for r in (rows or [])]
        self.assertNotIn(self.TRACE, got)
        self.assertEqual(len(got), 5, "the trace's slot was not refilled")


class TheRuleIsNotAWall(_Corpus):
    def test_a_trace_stays_indexed_and_reachable_when_asked(self):
        """`memory_search` returns a trace when asked, and promote reads them.
        The hook's own default is what this rule changes."""
        rows = recall.query(vault=self.vault, query_text=NAMING_QUESTION, k=5,
                            admit_traces=True,
                            deadline=time.monotonic() + 60, stderr=io.StringIO())
        self.assertIn(self.TRACE, [r["path"] for r in rows])

    def test_the_default_admits(self):
        """Every caller that is not the prompt hook — the eval, the probe, the
        CLI — keeps the behaviour it was measured under."""
        import inspect
        for fn in (recall.query, recall._daemon_search):
            with self.subTest(fn=fn.__name__):
                self.assertIs(
                    inspect.signature(fn).parameters["admit_traces"].default, True)


class TheKindIsReadNotGuessed(_Corpus):
    def test_a_non_trace_in_the_episodic_class_is_not_dropped(self):
        """`episodic/` holds traces and other things. Dropping a whole class
        directory would be a different and much larger change than the one the
        design asked for."""
        other = "memory/episodic/2026-09-11-a-real-memory.md"
        self._write(other, "type: reference", "How we name plans, recorded.")
        self.assertFalse(recall._is_session_trace(self.vault / other))
        self.assertTrue(recall._is_session_trace(self.vault / self.TRACE))

    def test_an_unreadable_file_is_not_called_a_trace(self):
        """Fails toward serving. A hit dropped because its file could not be
        read is a silent hole in the answer."""
        self.assertFalse(recall._is_session_trace(self.vault / "memory/nope.md"))

    def test_a_trace_with_a_thousand_character_touched_line_is_still_a_trace(self):
        """The shape that defeated the first version of this, found by running
        it against the live corpus rather than against these fixtures.

        A real trace's `touched:` is a single line listing every note the
        session opened — over a thousand characters. Reading a fixed 512-char
        head ended inside the frontmatter, so the closing `---` never arrived,
        the parser correctly returned nothing, and every real trace read as
        not-a-trace. Every fixture in this file had short frontmatter and
        passed.
        """
        rel = "memory/episodic/2026-09-05-a-long-one.md"
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        touched = ", ".join(f'"note-{i}"' for i in range(200))
        p.write_text(
            "---\n"
            'title: "A session with a long memory"\n'
            "kind: session-trace\n"
            "status: active\n"
            f"touched: [{touched}]\n"
            "---\n\nA body.\n", encoding="utf-8")
        self.assertGreater(len(p.read_text(encoding="utf-8")), 1500)
        self.assertTrue(recall._is_session_trace(p))

    def test_a_quoted_kind_is_read(self):
        rel = "memory/episodic/quoted.md"
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('---\ntitle: T\nkind: "session-trace"\n---\n\nBody.\n',
                     encoding="utf-8")
        self.assertTrue(recall._is_session_trace(p))

    def test_a_file_with_no_frontmatter_is_not_a_trace(self):
        rel = "memory/episodic/bare.md"
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("Just a body, no frontmatter.\n", encoding="utf-8")
        self.assertFalse(recall._is_session_trace(p))


if __name__ == "__main__":
    unittest.main()
