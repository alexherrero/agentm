#!/usr/bin/env python3
"""Retiring what the miner should never have written.

This pass expires about two thousand notes, so the properties that matter are
the ones that stop it expiding the wrong ones.

The bar, written before the pass ran on anything:

  1. A whole-message capture is never swept. It is the idea/workflow lane, which
     stores what a person typed rather than a window around a pattern, and it is
     only "untestable" in the sense that the excerpt test does not apply to it.
  2. Proven and presumed are different verdicts carrying different reasons. What
     was checked against a transcript and what was inferred from a population
     cannot arrive under the same word.
  3. The sweep is off unless asked for.
  4. Retiring sets a status and a reason. It never deletes and never edits a body.
  5. An already-expired note is left alone.
  6. Everything reverts byte-identically.
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "harness/skills/memory/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import retro_mining_cleanup as rc  # noqa: E402
from revert_log import RevertLog  # noqa: E402

TYPED = "we should always announce the downgrade rather than hard-stopping the run"
INJECTED = ("the founding doctrine ranks deterministic checks as cheap and truthful "
            "and LLM judgment as sycophantic, so what got verified was structure")


class Case(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.vault = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.transcripts = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        logs = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        locks = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.log = RevertLog(self.vault, log_root=logs, lock_root=locks)

    def excerpt_note(self, rel, text, *, session=None, status="active"):
        """A preference-lane note: an excerpt behind a `User stated:` prefix."""
        fm = ["---", f"status: {status}", "mining_confidence: LOW"]
        if session:
            fm.append(f"sessions: [{session}]")
        fm.append("---")
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(fm) + f"\n\nUser stated: ...{text}...\n",
                     encoding="utf-8")
        return p

    def whole_message_note(self, rel, text, *, kind="workflow"):
        """An idea/workflow-lane note: the message itself, no prefix, no ellipsis."""
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\ntype: {kind}\nstatus: active\n"
                     f"mining_confidence: MEDIUM\n---\n\n{text}\n", encoding="utf-8")
        return p

    def transcript(self, slug, uuid, text, *, typed=True):
        p = self.transcripts / slug / f"{uuid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        msg = {"type": "user", "message": {"role": "user", "content": text}}
        if not typed:
            # What the host says when the turn was injected rather than typed.
            msg["isMeta"] = True
        p.write_text(json.dumps(msg) + "\n", encoding="utf-8")
        return p

    def scan(self, **kw):
        return rc.scan(self.vault, transcripts=self.transcripts, **kw)

    def outcome(self, rel, **kw):
        for v in self.scan(**kw).verdicts:
            if v.rel == rel:
                return v
        return None


class WholeMessageIsNeverSweptTests(Case):
    """Bar 1. The one that would have cost 900 real notes."""

    def test_a_whole_message_capture_is_kept_even_under_the_sweep(self):
        self.whole_message_note("m/idea.md",
                                "add a follow-up to rename the agent space in "
                                "the vault from AgentMemory to Agent")
        v = self.outcome("m/idea.md", sweep_untestable=True)
        self.assertEqual(v.outcome, "keep",
                         f"a typed capture was swept: {v.reason}")

    def test_its_reason_says_why_the_test_does_not_apply(self):
        self.whole_message_note("m/idea.md", "some idea the operator typed out")
        v = self.outcome("m/idea.md", sweep_untestable=True)
        self.assertIn("whole-message", v.reason,
                      "it was filed as untestable rather than as a different lane")
        self.assertNotIn("untestable", v.reason)

    def test_the_sweep_does_not_write_to_it(self):
        p = self.whole_message_note("m/idea.md", "another typed idea entirely")
        before = p.read_bytes()
        rc.apply(self.vault, self.scan(sweep_untestable=True), self.log, "run-1")
        self.assertEqual(p.read_bytes(), before)


class ProvenVersusPresumedTests(Case):
    """Bar 2. Two words, because they are two different claims."""

    def test_a_transcript_that_proves_injection_says_injected(self):
        self.transcript("proj", "abc", INJECTED, typed=False)
        self.excerpt_note("m/a.md", INJECTED, session="proj/abc")
        self.assertEqual(self.outcome("m/a.md").outcome, "injected")

    def test_a_transcript_that_proves_typing_keeps_the_note(self):
        self.transcript("proj", "abc", TYPED, typed=True)
        self.excerpt_note("m/a.md", TYPED, session="proj/abc")
        v = self.outcome("m/a.md", sweep_untestable=True)
        self.assertIn(v.outcome, ("keep", "ragged"),
                      f"a note the transcript vouches for was retired: {v.reason}")
        self.assertEqual(v.reason, "the operator typed this")

    def test_a_missing_transcript_under_the_sweep_says_presumed(self):
        self.excerpt_note("m/a.md", INJECTED, session="proj/gone")
        self.assertEqual(
            self.outcome("m/a.md", sweep_untestable=True).outcome,
            "presumed-injected")

    def test_the_written_reason_distinguishes_them(self):
        self.transcript("proj", "abc", INJECTED, typed=False)
        proven = self.excerpt_note("m/proven.md", INJECTED, session="proj/abc")
        presumed = self.excerpt_note("m/presumed.md", INJECTED, session="proj/gone")
        rc.apply(self.vault, self.scan(sweep_untestable=True), self.log, "run-1")

        a = proven.read_text(encoding="utf-8")
        b = presumed.read_text(encoding="utf-8")
        self.assertIn("did not attribute", a)
        self.assertIn("presumed injected", b)
        self.assertIn("unverifiable", b,
                      "the inferred case does not say it was inferred")
        self.assertNotIn("presumed", a,
                         "a proven retirement was written as a guess")

    def test_the_presumed_reason_names_its_evidence(self):
        # So somebody reading one note a year from now can find the measurement
        # that retired it, rather than taking the verdict on faith.
        p = self.excerpt_note("m/a.md", INJECTED, session="proj/gone")
        rc.apply(self.vault, self.scan(sweep_untestable=True), self.log, "run-1")
        self.assertIn("1,452 of 1,490", p.read_text(encoding="utf-8"))


class SweepIsOptInTests(Case):
    """Bar 3."""

    def test_without_the_sweep_an_untestable_note_is_untouched(self):
        p = self.excerpt_note("m/a.md", INJECTED, session="proj/gone")
        before = p.read_bytes()
        v = self.outcome("m/a.md")
        self.assertNotEqual(v.outcome, "presumed-injected")
        rc.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertEqual(p.read_bytes(), before)

    def test_the_proven_case_needs_no_sweep(self):
        self.transcript("proj", "abc", INJECTED, typed=False)
        p = self.excerpt_note("m/a.md", INJECTED, session="proj/abc")
        rc.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertIn("status: expired", p.read_text(encoding="utf-8"))


class RetiringTests(Case):
    """Bars 4 and 5. Expired is not deleted."""

    def test_the_body_is_untouched(self):
        p = self.excerpt_note("m/a.md", INJECTED, session="proj/gone")
        before = p.read_text(encoding="utf-8").split("---\n", 2)[2]
        rc.apply(self.vault, self.scan(sweep_untestable=True), self.log, "run-1")
        after = p.read_text(encoding="utf-8").split("---\n", 2)[2]
        self.assertEqual(after, before, "retiring edited the note's text")

    def test_the_file_still_exists(self):
        p = self.excerpt_note("m/a.md", INJECTED, session="proj/gone")
        rc.apply(self.vault, self.scan(sweep_untestable=True), self.log, "run-1")
        self.assertTrue(p.exists(), "a retirement deleted the file")

    def test_an_existing_status_is_replaced_rather_than_duplicated(self):
        p = self.excerpt_note("m/a.md", INJECTED, session="proj/gone")
        rc.apply(self.vault, self.scan(sweep_untestable=True), self.log, "run-1")
        head = p.read_text(encoding="utf-8").split("---")[1]
        self.assertEqual(head.count("status:"), 1, head)
        self.assertNotIn("status: active", head)

    def test_an_already_expired_note_is_left_alone(self):
        p = self.excerpt_note("m/a.md", INJECTED, session="proj/gone",
                              status="expired")
        before = p.read_bytes()
        v = self.outcome("m/a.md", sweep_untestable=True)
        self.assertEqual(v.outcome, "keep")
        self.assertEqual(v.reason, "already expired")
        rc.apply(self.vault, self.scan(sweep_untestable=True), self.log, "run-1")
        self.assertEqual(p.read_bytes(), before)

    def test_a_second_run_writes_nothing(self):
        self.excerpt_note("m/a.md", INJECTED, session="proj/gone")
        rc.apply(self.vault, self.scan(sweep_untestable=True), self.log, "run-1")
        self.assertEqual(
            rc.apply(self.vault, self.scan(sweep_untestable=True), self.log, "run-2"),
            "", "the pass wants to retire what it already retired")


class DuplicateTests(Case):
    """#487's key, applied backwards."""

    def test_a_byte_identical_body_in_the_same_directory_is_a_duplicate(self):
        self.excerpt_note("m/a.md", INJECTED)
        self.excerpt_note("m/a-1.md", INJECTED)
        outs = {v.rel: v.outcome for v in self.scan().verdicts}
        self.assertEqual(sorted(outs.values()).count("duplicate"), 1,
                         f"{outs} — one survives, one is redundant")

    def test_a_different_body_is_not_a_duplicate(self):
        self.excerpt_note("m/a.md", INJECTED)
        self.excerpt_note("m/b.md", TYPED)
        outs = [v.outcome for v in self.scan().verdicts]
        self.assertNotIn("duplicate", outs)

    def test_the_same_body_in_another_directory_is_not_collapsed(self):
        # #487 scopes its key to the directory, and so does this. An inbox
        # capture and the opinion derived from it are the same words in two
        # lanes, and collapsing across them would delete the source of the other.
        self.excerpt_note("m/_inbox/a.md", INJECTED)
        self.excerpt_note("m/_opinions/a.md", INJECTED)
        outs = [v.outcome for v in self.scan().verdicts]
        self.assertNotIn("duplicate", outs)

    def test_the_original_survives_and_the_numbered_copy_is_retired(self):
        # Not arbitrary. `a-1.md` is what the collision handler appended; `a.md`
        # is what anything pointing at this note points at. Plain path order gets
        # this backwards, because a hyphen sorts before a dot.
        self.excerpt_note("m/a.md", INJECTED)
        self.excerpt_note("m/a-1.md", INJECTED)
        rc.apply(self.vault, self.scan(), self.log, "run-1")

        original = (self.vault / "m/a.md").read_text(encoding="utf-8")
        copy = (self.vault / "m/a-1.md").read_text(encoding="utf-8")
        self.assertNotIn("status: expired", original, "the original was retired")
        self.assertIn("status: expired", copy)
        self.assertIn("duplicate of m/a.md", copy)

    def test_the_original_survives_whatever_the_copy_is_numbered(self):
        self.excerpt_note("m/a.md", INJECTED)
        for n in (1, 2, 10, 30):
            self.excerpt_note(f"m/a-{n}.md", INJECTED)
        rc.apply(self.vault, self.scan(), self.log, "run-1", batch=50)
        self.assertNotIn("status: expired",
                         (self.vault / "m/a.md").read_text(encoding="utf-8"))
        for n in (1, 2, 10, 30):
            self.assertIn("status: expired",
                          (self.vault / f"m/a-{n}.md").read_text(encoding="utf-8"),
                          f"a-{n} survived")


class RevertTests(Case):
    """Bar 6."""

    def test_the_whole_run_reverts_byte_identically(self):
        self.transcript("proj", "abc", INJECTED, typed=False)
        paths = [
            self.excerpt_note("m/proven.md", INJECTED, session="proj/abc"),
            self.excerpt_note("m/presumed.md", INJECTED, session="proj/gone"),
            self.whole_message_note("m/kept.md", "a typed idea that stays"),
        ]
        # CRLF and no trailing newline, so "byte-identical" is a claim the fixture
        # can tell apart from "near enough".
        awkward = self.vault / "m/awkward.md"
        awkward.write_bytes(b"---\r\nstatus: active\r\nsessions: [proj/gone]\r\n"
                            b"mining_confidence: LOW\r\n---\r\n\r\n"
                            b"User stated: ...some injected passage of prose here...")
        paths.append(awkward)
        before = {p: p.read_bytes() for p in paths}

        entry = rc.apply(self.vault, self.scan(sweep_untestable=True), self.log,
                         "run-1", batch=50)
        self.assertTrue(any(p.read_bytes() != before[p] for p in paths))

        self.log.revert("run-1", entry)
        for p in paths:
            self.assertEqual(p.read_bytes(), before[p], str(p))


class BatchTests(Case):
    def test_the_cap_bounds_one_run(self):
        for i in range(10):
            self.excerpt_note(f"m/n{i}.md", INJECTED + f" number {i}",
                              session="proj/gone")
        rc.apply(self.vault, self.scan(sweep_untestable=True), self.log, "run-1",
                 batch=3)
        expired = sum(1 for i in range(10)
                      if "status: expired" in
                      (self.vault / f"m/n{i}.md").read_text(encoding="utf-8"))
        self.assertEqual(expired, 3)

    def test_the_cap_matches_the_dreaming_one(self):
        import dream_confirm
        self.assertEqual(rc.DEFAULT_BATCH,
                         dream_confirm.DEFAULT_AUTO_APPLY_BATCH_CAP)

    def test_scanning_writes_nothing(self):
        p = self.excerpt_note("m/a.md", INJECTED, session="proj/gone")
        before = p.read_bytes()
        self.scan(sweep_untestable=True)
        self.assertEqual(p.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
