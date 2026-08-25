#!/usr/bin/env python3
"""Repairing the notes a character-offset excerpt cut in half.

The bar, written before the pass:

  1. A note whose transcript survives and contains the passage is re-cut, and the
     result is source text rather than anything inferred.
  2. A note whose transcript is gone is marked and its body is untouched.
  3. A document that merely quotes mined bodies is never touched at all. This is
     the one that matters most: the first measurement of the damage swept in the
     labelling worksheets written to review it.
  4. Nothing is trimmed, reconstructed or guessed at.
  5. Every write goes through the revert log and comes back byte-identical.
  6. A dry run writes nothing.
  7. A second run over an already-marked note does not mark it twice.
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

import repair_excerpts as rx  # noqa: E402
from revert_log import RevertLog  # noqa: E402

# The sentence a real transcript held, and the mangled excerpt cut out of it.
SOURCE = ("the run falls back to direct push and announces the downgrade, because "
          "a completed unit of work is never hard-stopped by a missing tool")
MANGLED = ("...alls back to direct push and announces the downgrade, because a "
           "completed unit of work is never hard-stopp...")


class Case(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.vault = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.transcripts = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        logs = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        locks = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.log = RevertLog(self.vault, log_root=logs, lock_root=locks)

    def note(self, rel, body, *, session=None, mining=False):
        fm = ["---", "status: active"]
        if session:
            fm.append(f"sessions: [{session}]")
        if mining:
            fm.append("mining_confidence: LOW")
        fm.append("---")
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(fm) + "\n\n" + body + "\n", encoding="utf-8")
        return p

    def transcript(self, slug, uuid, *lines):
        p = self.transcripts / slug / f"{uuid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        # The real Claude Code shape: content is nested under `message`.
        # A first version wrote `{"role":..., "content":...}` at the top level,
        # `_extract_text` found nothing, and every repair silently became a mark —
        # a fixture that made the pass look more cautious than it is.
        p.write_text("\n".join(
            json.dumps({"type": "user", "message": {"role": "user", "content": ln}})
            for ln in lines) + "\n", encoding="utf-8")
        return p

    def scan(self, **kw):
        return rx.scan(self.vault, transcripts=self.transcripts, **kw)


class RepairFromTranscriptTests(Case):
    """Bar 1: exact, or not at all."""

    def test_a_surviving_transcript_repairs_the_edges(self):
        self.transcript("proj", "abc", "some preamble", SOURCE, "some epilogue")
        self.note("m/a.md", f"User stated: {MANGLED}", session="proj/abc")

        f = self.scan().findings[0]
        self.assertEqual(f.outcome, "repaired", f.reason)
        self.assertNotIn("...alls", f.new_body)
        self.assertIn("falls", f.new_body,
                      "the recovered word came back from the transcript")

    def test_the_repaired_text_is_source_and_not_invention(self):
        self.transcript("proj", "abc", SOURCE)
        self.note("m/a.md", f"User stated: {MANGLED}", session="proj/abc")
        f = self.scan().findings[0]
        core = f.new_body.strip(".").strip()
        # Every word of the result appears in the source. A repair that added a
        # word would be a repair that made one up.
        for w in core.split():
            self.assertIn(w.strip(".,"), SOURCE,
                          f"{w!r} is in the repair and not in the source")

    def test_a_damaged_edge_still_finds_its_source(self):
        # The non-obvious fact the search rests on: `alls` is a substring of
        # `falls`, so a needle carrying the damaged edges still matches, and the
        # position it returns is *inside* the real word — which the boundary snap
        # then widens back out to the whole of it.
        self.transcript("proj", "abc", SOURCE)
        self.note("m/a.md", f"User stated: {MANGLED}", session="proj/abc")
        f = self.scan().findings[0]
        self.assertEqual(f.outcome, "repaired", f.reason)
        self.assertIn("falls", f.new_body)

    def test_a_near_miss_passage_does_not_match(self):
        # The reason the needle keeps its edges: precision. A transcript holding a
        # *similar* sentence must not be mistaken for the source, because the
        # repair replaces a body wholesale.
        self.transcript("proj", "abc",
                        "the run falls back to direct push and announces the "
                        "downgrade, because a completed unit of work is never "
                        "interrupted by a missing tool")
        self.note("m/a.md", f"User stated: {MANGLED}", session="proj/abc")
        f = self.scan().findings[0]
        self.assertEqual(f.outcome, "marked",
                         "a similar-but-different passage was treated as the source")

    def test_a_transcript_without_the_passage_marks_rather_than_guesses(self):
        self.transcript("proj", "abc", "an entirely unrelated conversation")
        self.note("m/a.md", f"User stated: {MANGLED}", session="proj/abc")
        f = self.scan().findings[0]
        self.assertEqual(f.outcome, "marked")
        self.assertIn("does not contain", f.reason)

    def test_a_named_transcript_that_no_longer_exists_marks(self):
        self.note("m/a.md", f"User stated: {MANGLED}", session="proj/vanished")
        f = self.scan().findings[0]
        self.assertEqual(f.outcome, "marked")
        self.assertIn("no surviving transcript", f.reason)

    def test_a_missing_transcript_is_decided_before_any_re_cut(self):
        # Stated as an outcome rather than an absence of one. `recut_from` swallows
        # OSError and returns empty, so dropping the `t is None` guard produces the
        # same *label* by a different route — and that route calls a method on
        # None, which is a crash waiting for an input shape that reaches it.
        self.note("m/a.md", f"User stated: {MANGLED}", session="proj/vanished")
        f = self.scan().findings[0]
        self.assertEqual(f.reason, "no surviving transcript to re-cut from",
                         "the missing-transcript case was decided somewhere else")
        self.assertEqual(f.new_body, "", "a re-cut was attempted with no transcript")

    def test_a_note_with_no_session_at_all_marks(self):
        # 80.6% of the real damaged corpus. The common case, not the edge one.
        self.note("m/a.md", f"User stated: {MANGLED}", mining=True)
        f = self.scan().findings[0]
        self.assertEqual(f.outcome, "marked")


class MarkingTests(Case):
    """Bar 2: marking changes the frontmatter and nothing else."""

    def test_marking_leaves_the_body_byte_identical(self):
        p = self.note("m/a.md", f"User stated: {MANGLED}", mining=True)
        before = p.read_text(encoding="utf-8").split("---\n", 2)[2]
        rep = self.scan()
        rx.apply(self.vault, rep, self.log, "run-1")
        after = p.read_text(encoding="utf-8").split("---\n", 2)[2]
        self.assertEqual(after, before, "marking edited the body")

    def test_the_marker_lands_in_the_frontmatter(self):
        p = self.note("m/a.md", f"User stated: {MANGLED}", mining=True)
        rx.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertIn("excerpt_edges_unverified: true", p.read_text(encoding="utf-8"))

    def test_a_second_run_does_not_mark_twice(self):
        p = self.note("m/a.md", f"User stated: {MANGLED}", mining=True)
        rx.apply(self.vault, self.scan(), self.log, "run-1")
        first = p.read_text(encoding="utf-8")

        second = self.scan()
        self.assertEqual(second.findings[0].outcome, "already-marked")
        rx.apply(self.vault, second, self.log, "run-2")
        self.assertEqual(p.read_text(encoding="utf-8"), first)
        self.assertEqual(first.count("excerpt_edges_unverified"), 1)

    def test_a_note_with_no_frontmatter_still_gets_one(self):
        p = self.vault / "m/bare.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"User stated: {MANGLED}\n", encoding="utf-8")
        rx.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertIn("excerpt_edges_unverified: true", p.read_text(encoding="utf-8"))


class PopulationTests(Case):
    """`is_mined_note`, tested directly.

    The end-to-end quoting-document tests below stayed green when this function
    was mutated to return True for everything, because `find_excerpt` gives up on
    prose before any damage is done. That is a real second line of defence and a
    poor test of the first one, so the first one gets its own.
    """

    def test_a_worksheet_is_not_a_mined_note(self):
        raw = ("---\ntitle: Slop labelling\nkind: report\n---\n\n"
               "# Slop labelling v2\n\nRead the rubric first.\n\n"
               "```\nUser stated: ...alls back to direct push...\n```\n")
        self.assertFalse(rx.is_mined_note(raw))

    def test_a_document_leading_with_a_quote_is_not_a_mined_note(self):
        # The hard case: no prose before the quoted body, so nothing downstream
        # would save it. Only the population test stands between this and a
        # rewrite.
        raw = ("---\nkind: report\n---\n\n"
               "> User stated: ...alls back to direct push...\n\n"
               "> User stated: ...nother one entirely...\n")
        self.assertFalse(rx.is_mined_note(raw))

    def test_a_mined_body_is_a_mined_note(self):
        raw = "---\nstatus: active\n---\n\nUser stated: ...alls back...\n"
        self.assertTrue(rx.is_mined_note(raw))

    def test_a_mined_body_under_a_heading_is_a_mined_note(self):
        raw = ("---\nstatus: active\n---\n\n## always something\n\n"
               "User stated: ...alls back...\n")
        self.assertTrue(rx.is_mined_note(raw))

    def test_mining_frontmatter_qualifies_a_body_that_does_not(self):
        # The other branch, on its own. Every `mining=True` fixture elsewhere also
        # has a qualifying body, so removing this branch changed nothing.
        raw = ("---\nkind: opinion-supplement\nmining_confidence: LOW\n---\n\n"
               "## always check rows\n\nSome prose that does not use a prefix.\n\n"
               "## Supporting excerpts\n\n> ...ited 0 on a failing run...\n")
        self.assertTrue(rx.is_mined_note(raw))

    def test_the_prefix_must_lead_the_body(self):
        # `re.search` instead of `re.match` would qualify any document mentioning
        # the phrase halfway down.
        raw = ("---\nkind: report\n---\n\n"
               "A long discussion of the mining pipeline follows.\n\n"
               "Notes look like `User stated: ...alls back` after it runs.\n")
        self.assertFalse(rx.is_mined_note(raw))


class MarkIsIdempotentTests(Case):
    """`mark`, called directly and twice.

    The end-to-end test never reaches a second `mark` — the `already-marked`
    branch skips the write — so duplicating the key inside `mark` stayed green.
    """

    def test_marking_twice_leaves_one_key(self):
        raw = "---\nstatus: active\n---\n\nUser stated: ...alls back...\n"
        once = rx.mark(raw)
        twice = rx.mark(once)
        self.assertEqual(twice.count(rx.MARKER), 1, twice)
        self.assertEqual(once, twice, "a second mark changed the note")

    def test_marking_keeps_the_other_frontmatter(self):
        raw = ("---\nstatus: active\ntitle: a thing\n---\n\n"
               "User stated: ...alls back...\n")
        got = rx.mark(raw)
        self.assertIn("status: active", got)
        self.assertIn("title: a thing", got)


class QuotingDocumentsTests(Case):
    """Bar 3: the evidence is not the patient."""

    def test_a_document_quoting_mined_bodies_is_left_alone(self):
        # The labelling worksheets, verbatim in shape: prose, then a fenced block
        # holding a mined body. The first measurement of the damage counted 1,467
        # of these, and a pass driven by that count would have rewritten them.
        p = self.note("desk/worksheet.md",
                      "# Labelling worksheet\n\nRead the rubric first.\n\n"
                      "```\n---\nstatus: active\n---\n\n"
                      f"User stated: {MANGLED}\n```\n\n**01 answer:** ")
        before = p.read_bytes()
        rep = self.scan()
        self.assertEqual(rep.findings, [], "a quoting document was picked up")
        rx.apply(self.vault, rep, self.log, "run-1")
        self.assertEqual(p.read_bytes(), before)

    def test_a_dream_proposal_quoting_two_bodies_is_left_alone(self):
        p = self.note("desk/scratch/r/01-dedup-merge.proposal.md",
                      "# Proposal 1: merge\n\nThese two are 99% similar.\n\n"
                      f"> User stated: {MANGLED}\n\n"
                      f"> User stated: {MANGLED}\n")
        before = p.read_bytes()
        rx.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertEqual(p.read_bytes(), before)

    def test_mining_frontmatter_alone_is_enough_to_qualify(self):
        # The other half of the population test: a mined note whose body starts
        # with a heading rather than the prefix.
        self.note("m/a.md", f"## always something\n\nUser stated: {MANGLED}",
                  mining=True)
        self.assertEqual(len(self.scan().findings), 1)

    def test_a_body_prefix_alone_is_enough_to_qualify(self):
        # And a mined note with no mining frontmatter, which is most of the inbox.
        self.note("m/b.md", f"User stated: {MANGLED}")
        self.assertEqual(len(self.scan().findings), 1)


class NoGuessingTests(Case):
    """Bar 4: nothing is trimmed or reconstructed."""

    def test_a_marked_note_keeps_its_partial_word(self):
        # The trim was measured and rejected: `...preface with "I'll continue"`
        # loses a complete word, and nothing in the note distinguishes that from
        # `...all back to direct push`.
        p = self.note("m/a.md", f"User stated: {MANGLED}", mining=True)
        rx.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertIn("...alls back", p.read_text(encoding="utf-8"),
                      "the partial word was trimmed rather than left and marked")

    def test_an_edge_that_only_looks_clean_is_still_unverified(self):
        # `...back` reads as a whole word and may be the tail of `fallback`. The
        # note cannot tell, so the pass does not claim to either — it marks the
        # edges unverified rather than pronouncing them clean.
        self.note("m/a.md",
                  "User stated: ...back to direct push and announces the...",
                  mining=True)
        f = self.scan().findings[0]
        self.assertEqual(f.outcome, "marked")

    def test_an_elision_at_the_head_alone_is_a_finding(self):
        # Every other fixture is elided at both ends, so the head test was
        # carried by the tail: blanking the head check changed nothing.
        self.note("m/a.md",
                  "User stated: ...alls back to direct push and announces it.",
                  mining=True)
        self.assertEqual(len(self.scan().findings), 1,
                         "a head-only elision was not reported")

    def test_an_elision_at_the_tail_alone_is_a_finding(self):
        self.note("m/b.md",
                  "User stated: The run falls back to direct push and announ...",
                  mining=True)
        self.assertEqual(len(self.scan().findings), 1,
                         "a tail-only elision was not reported")

    def test_an_excerpt_with_no_elision_is_not_a_finding(self):
        # Nothing was cut, so there is nothing unverified about it.
        self.note("m/a.md", "User stated: the whole passage survives intact here",
                  mining=True)
        self.assertEqual(self.scan().findings, [],
                         "a note with no ellipsis was reported")

    def test_a_note_with_no_ellipsis_is_not_a_finding(self):
        self.note("m/a.md", "User stated: the whole sentence survives here",
                  mining=True)
        self.assertEqual(self.scan().findings, [])


class RevertTests(Case):
    """Bar 5: every write comes back."""

    def test_the_whole_run_reverts_byte_identically(self):
        self.transcript("proj", "abc", SOURCE)
        paths = [
            self.note("m/repaired.md", f"User stated: {MANGLED}", session="proj/abc"),
            self.note("m/marked.md", f"User stated: {MANGLED}", mining=True),
            # CRLF and a missing final newline, so "byte-identical" is a claim the
            # fixture can actually distinguish from "close enough".
            self.vault / "m/awkward.md",
        ]
        paths[2].write_bytes(
            f"---\r\nstatus: active\r\n---\r\n\r\nUser stated: {MANGLED}".encode())
        before = {p: p.read_bytes() for p in paths}

        entry = rx.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertTrue(any(p.read_bytes() != before[p] for p in paths),
                        "the run wrote nothing to revert")

        self.log.revert("run-1", entry)
        for p in paths:
            self.assertEqual(p.read_bytes(), before[p], str(p))

    def test_one_entry_covers_the_whole_batch(self):
        for i in range(4):
            self.note(f"m/n{i}.md", f"User stated: {MANGLED}", mining=True)
        entry = rx.apply(self.vault, self.scan(), self.log, "run-1")
        self.log.revert("run-1", entry)
        for i in range(4):
            self.assertNotIn("excerpt_edges_unverified",
                             (self.vault / f"m/n{i}.md").read_text(encoding="utf-8"))


class BatchTests(Case):
    """Bar 6: a run touches what it said it would."""

    def test_the_batch_cap_bounds_one_run(self):
        for i in range(10):
            self.note(f"m/n{i}.md", f"User stated: {MANGLED}", mining=True)
        rx.apply(self.vault, self.scan(), self.log, "run-1", batch=3)
        marked = sum(1 for i in range(10)
                     if "excerpt_edges_unverified" in (self.vault / f"m/n{i}.md").read_text(
                         encoding="utf-8"))
        self.assertEqual(marked, 3)

    def test_the_default_cap_matches_the_dreaming_one(self):
        # A second number would be a second thing to keep in step.
        import dream_confirm
        self.assertEqual(rx.DEFAULT_BATCH,
                         dream_confirm.DEFAULT_AUTO_APPLY_BATCH_CAP)

    def test_scanning_writes_nothing(self):
        p = self.note("m/a.md", f"User stated: {MANGLED}", mining=True)
        before = p.read_bytes()
        self.scan()
        self.assertEqual(p.read_bytes(), before)

    def test_a_run_with_nothing_to_do_returns_no_entry(self):
        self.note("m/a.md", "User stated: a clean sentence with no ellipsis",
                  mining=True)
        self.assertEqual(rx.apply(self.vault, self.scan(), self.log, "run-1"), "")


class OnlyTests(Case):
    """The two outcomes land separately when asked.

    A repair rewrites a body from a transcript; a mark adds a frontmatter key.
    They are not equally consequential, and on the live corpus they arrive 51
    against 2,249 — so without a filter the first thing anyone sees is the larger,
    duller half.
    """

    def _mixed(self):
        self.transcript("proj", "abc", SOURCE)
        a = self.note("m/repairable.md", f"User stated: {MANGLED}", session="proj/abc")
        b = self.note("m/markable.md", f"User stated: {MANGLED}", mining=True)
        return a, b

    def test_only_repaired_leaves_the_markable_note_alone(self):
        a, b = self._mixed()
        before_b = b.read_bytes()
        rx.apply(self.vault, self.scan(), self.log, "run-1", only="repaired")
        self.assertNotIn("...alls", a.read_text(encoding="utf-8"))
        self.assertEqual(b.read_bytes(), before_b, "a mark was written anyway")

    def test_only_marked_leaves_the_repairable_note_alone(self):
        a, b = self._mixed()
        before_a = a.read_bytes()
        rx.apply(self.vault, self.scan(), self.log, "run-1", only="marked")
        self.assertIn(rx.MARKER, b.read_text(encoding="utf-8"))
        self.assertEqual(a.read_bytes(), before_a, "a repair was written anyway")

    def test_no_filter_writes_both(self):
        # The default, stated so the filter cannot become mandatory by accident.
        a, b = self._mixed()
        rx.apply(self.vault, self.scan(), self.log, "run-1")
        self.assertNotIn("...alls", a.read_text(encoding="utf-8"))
        self.assertIn(rx.MARKER, b.read_text(encoding="utf-8"))

    def test_a_filter_matching_nothing_writes_nothing(self):
        self.note("m/markable.md", f"User stated: {MANGLED}", mining=True)
        self.assertEqual(
            rx.apply(self.vault, self.scan(), self.log, "run-1", only="repaired"), "",
            "an empty selection produced a revert-log entry")


class CommandTests(Case):
    """The command's own promise: no `--apply`, no writes."""

    def test_a_dry_run_writes_nothing(self):
        p = self.note("m/a.md", f"User stated: {MANGLED}", mining=True)
        before = p.read_bytes()
        rx.main(["--vault", str(self.vault),
                 "--transcripts", str(self.transcripts), "--json"])
        self.assertEqual(p.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
