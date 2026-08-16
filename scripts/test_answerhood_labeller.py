#!/usr/bin/env python3
"""Tests for answerhood_labeller.py.

Three things are worth pinning, and they are the three the probe paid to learn.

**The excerpt selector**, including the case the thin instrument got wrong: a
note whose decisive words appear once, against a common term repeated in its
head. That case produced a confidently wrong verdict and 43.2% of the probe's
apparent over-rejections, so it is a regression pin rather than a nicety.

**Verdict parsing**, because the model's output is prose-adjacent and a strict
parser here costs an unlabelled brief rather than catching a bug.

**The degrade contract**: a failed call returns every candidate, unlabelled,
with a visible marker — mirroring the embedder's. A labeller that drops
candidates when it breaks is the deletion this design exists to avoid, arriving
by accident instead of by choice.

Expected values are hand-written literals. A check that recomputes its
expectation from the implementation verifies only that the code agrees with
itself.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import answerhood_labeller as al  # noqa: E402


class TestExcerpt(unittest.TestCase):
    def test_small_note_is_shown_whole(self):
        text = "# A short note\n\nIt fits, so there is no selection to get wrong."
        self.assertEqual(al.excerpt(text, "anything at all"), text)

    def test_boundary_is_inclusive(self):
        text = "x" * al.WHOLE_IF_UNDER
        self.assertEqual(al.excerpt(text, "q"), text)
        self.assertNotEqual(al.excerpt("x" * (al.WHOLE_IF_UNDER + 1), "q"),
                            "x" * (al.WHOLE_IF_UNDER + 1))

    def test_long_note_keeps_head_and_tail(self):
        head = "HEADMARKER " + "a" * 1000
        tail = "b" * 1000 + " TAILMARKER"
        text = head + ("filler " * 2000) + tail
        got = al.excerpt(text, "filler")
        self.assertIn("HEADMARKER", got)
        self.assertIn("TAILMARKER", got)
        self.assertIn("[...]", got)
        self.assertLess(len(got), len(text))

    def test_idf_beats_raw_counts_on_the_ep05_shape(self):
        """The case the thin instrument got wrong, built so it can only pass
        for the right reason.

        `ep05` was a note whose common term repeated in the head while the
        decisive words appeared once in the body, so raw overlap showed the gate
        the wrong passage and it correctly judged that passage not to answer.

        The fixture reproduces the ranking that causes it: several distractor
        chunks each carrying the COMMON query term three or more times, and one
        decisive chunk carrying only the RARE query term, once. Under raw counts
        the distractors outrank the decisive chunk and — with only two middle
        chunks shown — push it out entirely. Under IDF the rare term outweighs
        them and it survives.

        Verified to discriminate: replacing idf() with a constant makes this
        fail, which is the only reason it is worth having.
        """
        # Carries no query term, so it never competes on its own.
        filler = "lorem ipsum dolor sit amet consectetur adipiscing elit. " * 3
        # The common query term, three-plus times per block — the raw-count
        # winner, since the selector caps a term's contribution at three.
        distractor = "agentm agentm agentm notes about agentm. " + filler
        # The rare query term, exactly once, buffered by enough plain filler on
        # both sides that a whole chunk window fits around it without touching a
        # distractor. The buffer is the point, and it took a mutation run to
        # find: a decisive passage merely adjacent to common-term text inherits
        # that text's score through the overlapping window, wins under raw
        # counts too, and the test silently stops discriminating.
        decisive = filler * 5 + "splitting. " + filler * 8

        text = ("h" * al.HEAD + " "
                + distractor * 6
                + decisive
                + distractor * 6
                + "t" * al.TAIL)
        self.assertGreater(len(text), al.WHOLE_IF_UNDER)
        self.assertGreater(text.index("splitting"), al.HEAD)
        self.assertGreater(len(decisive), al.MID + al.MID // 2)

        # A pool shaped like the real corpus: the common term in every document,
        # the rare one in a single document.
        pool = [distractor for _ in range(40)] + [text]
        df, n_docs = al.build_df(pool)

        # The behavioural claim first, so a regression reports the defect that
        # matters — the selector showed the wrong passage — rather than a
        # helper's return value.
        got = al.excerpt(text, "agentm splitting", df, n_docs)
        self.assertIn(
            "splitting", got,
            "the decisive passage was not selected: the chunk carrying the rare "
            "term lost to chunks dense in the common one, which is exactly the "
            "ep05 failure and produced 43.2% of the probe's apparent "
            "over-rejections")

        # Then the mechanism, so a failure distinguishes "weights are wrong"
        # from "weights are right and selection is still wrong".
        self.assertGreater(df["agentm"], df["splitting"])
        self.assertGreater(al.idf("splitting", df, n_docs),
                           al.idf("agentm", df, n_docs))

    def test_missing_df_still_returns_an_excerpt(self):
        """A thin pool must degrade selection, never raise — a labeller that
        crashes on its own input takes the whole brief down with it."""
        text = "z" * 9000
        got = al.excerpt(text, "some question")
        self.assertTrue(got)
        self.assertIn("[...]", got)


class TestParseVerdict(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(al.parse_verdict('{"answers": [1, 3]}', 5), ([1, 3], ""))

    def test_empty_list_is_a_verdict_not_an_error(self):
        """"None of these answers" is the outcome this design exists to make
        sayable. It must not be confused with a broken call."""
        self.assertEqual(al.parse_verdict('{"answers": []}', 5), ([], ""))

    def test_code_fence_is_tolerated(self):
        self.assertEqual(
            al.parse_verdict('```json\n{"answers": [2]}\n```', 4), ([2], ""))

    def test_surrounding_prose_is_tolerated(self):
        self.assertEqual(
            al.parse_verdict('Sure! {"answers": [1]} Hope that helps.', 3),
            ([1], ""))

    def test_probe_key_is_accepted(self):
        """The promoted probe emitted `keep`. Accepting it keeps replaying old
        transcripts possible without rewriting them."""
        self.assertEqual(al.parse_verdict('{"keep": [2]}', 3), ([2], ""))

    def test_out_of_range_indices_are_dropped(self):
        self.assertEqual(al.parse_verdict('{"answers": [1, 47, 0, -2]}', 5),
                         ([1], ""))

    def test_booleans_are_not_indices(self):
        self.assertEqual(al.parse_verdict('{"answers": [true, 2]}', 3), ([2], ""))

    def test_duplicates_collapse(self):
        self.assertEqual(al.parse_verdict('{"answers": [2, 2, 1]}', 3),
                         ([1, 2], ""))

    def test_failures_report_why(self):
        for raw in ("", "   ", "no json here", '{"answers": "1"}', "{broken",
                    '{"other": [1]}'):
            got, err = al.parse_verdict(raw, 3)
            self.assertEqual(got, [], raw)
            self.assertTrue(err, f"expected an error string for {raw!r}")


class TestLabel(unittest.TestCase):
    def candidates(self, n=3):
        return [al.Candidate(path=f"Agent/n{i}.md", text=f"note {i} body")
                for i in range(1, n + 1)]

    def test_every_candidate_comes_back_labelled(self):
        cands = self.candidates()
        res = al.label("q", cands, caller=lambda p: ('{"answers": [2]}', 0.004, ""))
        self.assertEqual(len(res.candidates), 3)
        self.assertEqual([c.verdict for c in res.candidates],
                         [al.VERDICT_RELATED, al.VERDICT_ANSWERS, al.VERDICT_RELATED])
        self.assertTrue(res.labelled)
        self.assertEqual([c.path for c in res.answering], ["Agent/n2.md"])

    def test_nothing_answers_is_said_out_loud(self):
        res = al.label("q", self.candidates(),
                       caller=lambda p: ('{"answers": []}', 0.004, ""))
        self.assertTrue(res.labelled)
        self.assertEqual(res.answering, [])
        self.assertIn("no candidate appears to answer", res.note)
        # And still hands back everything — the rejection signal is a label, not
        # a deletion.
        self.assertEqual(len(res.candidates), 3)

    def test_call_failure_degrades_visibly_and_drops_nothing(self):
        cands = self.candidates(4)
        res = al.label("q", cands, caller=lambda p: ("", 0.0, "claude exited 1"))
        self.assertFalse(res.labelled)
        self.assertEqual(len(res.candidates), 4)
        self.assertTrue(all(c.verdict == al.VERDICT_UNLABELLED
                            for c in res.candidates))
        self.assertIn(al.DEGRADE_MARK, res.note)
        self.assertIn("claude exited 1", res.note)

    def test_unparseable_response_degrades_the_same_way(self):
        res = al.label("q", self.candidates(),
                       caller=lambda p: ("thinking about it...", 0.004, ""))
        self.assertFalse(res.labelled)
        self.assertTrue(all(c.verdict == al.VERDICT_UNLABELLED
                            for c in res.candidates))
        self.assertIn(al.DEGRADE_MARK, res.note)

    def test_degrade_marker_cannot_be_mistaken_for_another_subsystem(self):
        """retrieval_scorecard.py refuses to publish a row carrying any of these.
        A labeller degrade must be distinguishable from a retrieval degrade, or
        one subsystem's outage reads as another's."""
        for other in ("lexical arm alone", "no query vector was available",
                      "no reranker was available", "(hook skipped:"):
            self.assertNotIn(other, al.DEGRADE_MARK)

    def test_candidates_past_the_cap_are_unlabelled_not_judged(self):
        cands = self.candidates(al.MAX_CANDIDATES + 3)
        res = al.label("q", cands, caller=lambda p: ('{"answers": [1]}', 0.004, ""))
        self.assertEqual(len(res.candidates), al.MAX_CANDIDATES + 3)
        self.assertEqual(res.truncated, 3)
        self.assertTrue(all(c.verdict == al.VERDICT_UNLABELLED
                            for c in res.candidates[al.MAX_CANDIDATES:]))
        self.assertIn("cap", res.note)

    def test_supplied_df_is_used_instead_of_the_per_call_pool(self):
        """A caller with a wider frequency table must actually get it used.

        Measured on the episodic slice: 49.7% of long notes receive a different
        excerpt depending on which pool the frequencies came from, so this is a
        behavioural difference and not a plumbing detail.
        """
        filler = "lorem ipsum dolor sit amet consectetur adipiscing elit. " * 3
        distractor = "agentm agentm agentm notes about agentm. " + filler
        # The sentinel rides with the decisive passage but is NOT a query term —
        # asserting on the query word itself always passes, since the prompt
        # quotes the question back.
        decisive = filler * 5 + "splitting sentinelword. " + filler * 8
        text = ("h" * al.HEAD + " " + distractor * 6 + decisive
                + distractor * 6 + "t" * al.TAIL)

        seen = {}

        def caller(prompt):
            seen["prompt"] = prompt
            return '{"answers": []}', 0.0, ""

        def prompt_with(df, n):
            al.label("agentm splitting",
                     [al.Candidate(path="Agent/n.md", text=text)],
                     caller=caller, df=df, n_docs=n)
            return seen["prompt"]

        # Two explicit tables, opposite in the one term that decides selection,
        # so the assertion rests on the supplied frequencies rather than on
        # whatever the per-call pool happens to do with a single document.
        rare_split = prompt_with({"agentm": 1000, "splitting": 1}, 1000)
        rare_agentm = prompt_with({"agentm": 1, "splitting": 1000}, 1000)

        self.assertIn("sentinelword", rare_split,
                      "with the decisive term rare, its passage must be selected")
        self.assertNotIn("sentinelword", rare_agentm,
                         "with the decisive term common, its passage must lose — "
                         "otherwise the supplied df never reached the selector")

    def test_empty_candidate_list_makes_no_call(self):
        called = []
        res = al.label("q", [], caller=lambda p: called.append(p) or ("", 0.0, ""))
        self.assertEqual(called, [])
        self.assertEqual(res.candidates, [])
        self.assertTrue(res.labelled)

    def test_the_prompt_carries_the_question_not_a_reduced_query(self):
        """8.9% versus 86.7% of recorded failures fixed, on the same instrument.
        The question reaching the prompt verbatim is the whole placement
        argument for this module."""
        seen = {}
        al.label("How long since my last blog post?", self.candidates(),
                 caller=lambda p: (seen.setdefault("prompt", p), ('{"answers": []}', 0.0, ""))[1])
        self.assertIn("How long since my last blog post?", seen["prompt"])

    def test_derived_answers_are_asked_for_explicitly(self):
        """Strict answerhood preserved only 58.7% of the episodic-temporal
        stratum, because those answers are computed from a note rather than
        stated in it. The prompt has to say so."""
        self.assertIn("work out", al.PROMPT)
        self.assertIn("derived", al.PROMPT)


if __name__ == "__main__":
    unittest.main()
