#!/usr/bin/env python3
"""The deterministic injected-vs-used signal, and the span it reads.

Two properties carry the weight here. The **span** must be the whole response —
reading only the attachment's immediate child found prose on 11% of turns,
because most children are thinking blocks or tool calls.

And the **match** must be the note's whole name, and a name the corpus does not
produce on its own. Three mechanisms died against real traffic before that one
stood up: a hand-written stoplist could not cover domain vocabulary, an
exemption for compound slugs credited `design-doc` every time anyone wrote
"design doc", and fragment matching credited `observability` for an unrelated
`observability-email-daily.yaml`. See results/online-v1/RULE-single-word-
evidence.md.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import recall_traffic as rt  # noqa: E402

STDERR = ("[memory-recall-prompt-submit] Loaded 1 relevant entries: "
          "obsidian-vault-paths (engine: daemon, 9ms, scope=memory-root, "
          "terms: 'where is the vault')")


def rec(uuid, typ, parent=None, **kw):
    return {"uuid": uuid, "type": typ, "parentUuid": parent, **kw}


def assistant(uuid, blocks, parent=None, **kw):
    return rec(uuid, "assistant", parent, message={"role": "assistant",
                                                   "content": blocks}, **kw)


class TheAssistantText(unittest.TestCase):
    def test_thinking_counts_as_text(self):
        # 408 of 676 immediate children are thinking blocks. Excluding them
        # for tidiness is most of why the first pass saw almost nothing — and
        # thinking is where a model actually works over injected material.
        got = rt._assistant_text(assistant("a", [
            {"type": "thinking", "thinking": "the vault lives at Agent/"},
        ]))
        self.assertIn("vault lives", got)

    def test_prose_and_thinking_are_both_collected(self):
        got = rt._assistant_text(assistant("a", [
            {"type": "thinking", "thinking": "reasoning here"},
            {"type": "text", "text": "the reply"},
        ]))
        self.assertIn("reasoning here", got)
        self.assertIn("the reply", got)

    def test_a_tool_call_contributes_nothing(self):
        got = rt._assistant_text(assistant("a", [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        ]))
        self.assertEqual(got.strip(), "")


class TheTypedPromptTest(unittest.TestCase):
    def test_a_tool_result_is_not_a_typed_prompt(self):
        # Claude Code writes tool results as `user` records, so "the next user
        # record" is usually the middle of a response rather than its end.
        self.assertFalse(rt._is_typed_prompt(rec(
            "u", "user", message={"role": "user", "content": [
                {"type": "tool_result", "content": "output"}]})))

    def test_a_record_carrying_a_tool_payload_is_not_a_prompt(self):
        self.assertFalse(rt._is_typed_prompt(rec(
            "u", "user", toolUseResult={"stdout": "x"},
            message={"role": "user", "content": "looks typed but is not"})))

    def test_a_real_prompt_is_one(self):
        self.assertTrue(rt._is_typed_prompt(rec(
            "u", "user", message={"role": "user", "content": "what did we decide"})))

    def test_an_empty_user_record_is_not(self):
        self.assertFalse(rt._is_typed_prompt(rec(
            "u", "user", message={"role": "user", "content": "   "})))


class TheResponseSpan(unittest.TestCase):
    def _span(self, recs):
        order = {r["uuid"]: n for n, r in enumerate(recs) if r.get("uuid")}
        return rt._response_span("inj", recs, order)

    def test_it_walks_past_tool_calls_and_results(self):
        # The uuid chain breaks at the first tool result; file order does not.
        recs = [
            rec("inj", "attachment", attachment={"hookName": "UserPromptSubmit",
                                                 "stderr": STDERR, "stdout": ""}),
            assistant("a1", [{"type": "tool_use", "name": "Bash", "input": {}}]),
            rec("t1", "user", toolUseResult={"stdout": "x"},
                message={"role": "user", "content": [{"type": "tool_result"}]}),
            assistant("a2", [{"type": "text", "text": "the real answer"}]),
        ]
        self.assertIn("the real answer", self._span(recs))

    def test_it_stops_at_the_next_typed_prompt(self):
        recs = [
            rec("inj", "attachment", attachment={"hookName": "UserPromptSubmit",
                                                 "stderr": STDERR, "stdout": ""}),
            assistant("a1", [{"type": "text", "text": "mine"}]),
            rec("u2", "user", message={"role": "user", "content": "next question"}),
            assistant("a2", [{"type": "text", "text": "NOT MINE"}]),
        ]
        got = self._span(recs)
        self.assertIn("mine", got)
        self.assertNotIn("NOT MINE", got)

    def test_it_stops_at_the_next_injection(self):
        recs = [
            rec("inj", "attachment", attachment={"hookName": "UserPromptSubmit",
                                                 "stderr": STDERR, "stdout": ""}),
            assistant("a1", [{"type": "text", "text": "mine"}]),
            rec("inj2", "attachment", attachment={"hookName": "UserPromptSubmit",
                                                  "stderr": STDERR, "stdout": ""}),
            assistant("a2", [{"type": "text", "text": "NOT MINE"}]),
        ]
        got = self._span(recs)
        self.assertNotIn("NOT MINE", got)

    def test_a_sidechain_turn_is_not_this_turn_s_answer(self):
        # Sub-agent output is a different conversation; crediting it would
        # attribute another agent's words to this injection.
        recs = [
            rec("inj", "attachment", attachment={"hookName": "UserPromptSubmit",
                                                 "stderr": STDERR, "stdout": ""}),
            assistant("a1", [{"type": "text", "text": "SUBAGENT"}], isSidechain=True),
            assistant("a2", [{"type": "text", "text": "mine"}]),
        ]
        got = self._span(recs)
        self.assertNotIn("SUBAGENT", got)
        self.assertIn("mine", got)


class TheCandidates(unittest.TestCase):
    def test_only_the_whole_name_is_a_candidate(self):
        # Fragments were the entire source of contamination in the live run:
        # `observability` matched inside an unrelated
        # `observability-email-daily.yaml`, and `20260813` inside a different
        # timestamp. A piece of a name is not the name.
        self.assertEqual(rt._candidates("silent-source-influences"),
                         ["silent source influences", "silent-source-influences"])

    def test_a_name_too_short_to_mean_anything_is_dropped(self):
        self.assertEqual(rt._candidates("plan"), [])


class TheRarityMeasurement(unittest.TestCase):
    def test_the_share_is_over_turns_not_occurrences(self):
        # A word repeated ten times in one answer is evidence from one turn.
        got = rt.background_rates(["alpha alpha alpha", "beta"], ["alpha"])
        self.assertEqual(got["alpha"], 0.5)

    def test_it_measures_by_substring_the_way_the_match_does(self):
        # Tokenizing would report a rate for a test nobody runs: the match is
        # a substring check, so the base rate has to be one too.
        got = rt.background_rates(["see retrieval_scorecard.py"], ["retrieval"])
        self.assertEqual(got["retrieval"], 1.0)

    def test_an_empty_corpus_yields_no_rates(self):
        self.assertEqual(rt.background_rates([], ["alpha"]), {})

    def test_a_common_name_is_not_rare(self):
        # "design doc" appeared in 1.5% of real answers and earned that note
        # ten of the twenty-six verdicts then standing.
        inj = [{"slugs": ["design-doc"], "_answer": "the design doc says"}
               for _ in range(10)]
        inj += [{"slugs": ["design-doc"], "_answer": "unrelated"}
                for _ in range(200)]
        self.assertNotIn("design doc", rt.rare_evidence(inj))

    def test_a_rare_name_is_rare(self):
        inj = [{"slugs": ["agentm-auto-organization"],
                "_answer": "the agentm-auto-organization design"}]
        inj += [{"slugs": [], "_answer": "unrelated"} for _ in range(200)]
        self.assertIn("agentm-auto-organization", rt.rare_evidence(inj))

    def test_a_name_that_never_appears_is_not_measured(self):
        # Rates are computed only for candidates that matched somewhere —
        # one that matches nowhere yields no verdict either way.
        inj = [{"slugs": ["never-written-anywhere"], "_answer": "unrelated"}
               for _ in range(200)]
        self.assertEqual(rt.rare_evidence(inj), set())

    def _corpus(self, turns):
        inj = [{"slugs": ["agentm-auto-organization"],
                "_answer": "the agentm-auto-organization design"}]
        inj += [{"slugs": [], "_answer": "unrelated"}
                for _ in range(turns - 1)]
        return inj

    def test_the_refusal_threshold_sits_where_the_bar_starts_working(self):
        # 1/n is the smallest share n turns can express, so a corpus below
        # MIN_TURNS_FOR_RARITY cannot clear the bar at all. Pinned on either
        # side of the boundary rather than by restating the formula: set the
        # constant too high and the lower assert fails, too low and the upper
        # one does.
        one_short = rt.MIN_TURNS_FOR_RARITY - 1
        self.assertEqual(rt.rare_evidence(self._corpus(one_short)), set())
        self.assertIn("agentm-auto-organization",
                      rt.rare_evidence(self._corpus(rt.MIN_TURNS_FOR_RARITY)))


class TheMatch(unittest.TestCase):
    def test_a_rare_name_in_the_answer_is_evidence(self):
        rare = {"agentm-auto-organization"}
        self.assertEqual(
            rt.used_slugs(["agentm-auto-organization"],
                          "a design called agentm-auto-organization", rare),
            ["agentm-auto-organization"])

    def test_a_common_name_is_not_evidence_even_when_present(self):
        # Both conditions are load-bearing: the name is right there, and it
        # still proves nothing because the corpus produces it anyway.
        self.assertEqual(
            rt.used_slugs(["design-doc"], "the design doc says", set()), [])

    def test_a_fragment_of_the_name_is_not_evidence(self):
        # Even declaring the fragment rare must not help — it is not a
        # candidate at all.
        self.assertEqual(
            rt.used_slugs(["silent-source-influences"],
                          "whether decay influences ranking",
                          {"influences", "silent-source-influences"}), [])

    def test_the_spaced_form_counts(self):
        self.assertEqual(
            rt.used_slugs(["vault-canonical-context"],
                          "the vault canonical context note",
                          {"vault canonical context"}),
            ["vault-canonical-context"])

    def test_matching_is_case_insensitive(self):
        self.assertEqual(
            rt.used_slugs(["obsidian-vault-paths"], "See OBSIDIAN-VAULT-PATHS",
                          {"obsidian-vault-paths"}),
            ["obsidian-vault-paths"])

    def test_without_a_corpus_there_is_no_evidence(self):
        # Rarity is the whole of the test, and one turn cannot estimate it.
        # A floor computed without the means to check should come out too low.
        self.assertEqual(rt.slug_evidence("agentm-auto-organization"), [])
        self.assertEqual(
            rt.used_slugs(["agentm-auto-organization"],
                          "a design called agentm-auto-organization"), [])


class TheSummary(unittest.TestCase):
    def test_rates_and_the_naming_caveat(self):
        # `rare-distinct-notename` is named in one turn of 201, so it clears
        # the 1% bar; `ordinary-phrase` is in every turn and does not.
        inj = [
            {"slugs": ["rare-distinct-notename", "ordinary-phrase"],
             "has_answer": True,
             "_answer": "see rare-distinct-notename and the ordinary phrase"},
        ]
        inj += [{"slugs": ["ordinary-phrase"], "has_answer": True,
                 "_answer": "just the ordinary phrase here"}
                for _ in range(200)]
        got = rt.overlap_summary(inj)
        self.assertEqual(got["turns"], 201)
        self.assertEqual(got["notes_injected"], 202)
        self.assertEqual(got["notes_visibly_named"], 1)
        self.assertEqual(got["turns_naming_no_note"], 200)
        self.assertEqual(got["turns_naming_no_note_rate"], round(200/201, 4))
        self.assertEqual(got["rare_evidence_strings"], 1)
        self.assertIn("naming, not use", got["floor_caveat"])

    def test_the_caveat_disowns_the_wasted_injection_reading(self):
        # 99% of turns name no note. That is not a 99% waste rate, and the
        # field a reader quotes should say so without needing this session.
        inj = [{"slugs": ["rare-distinct-notename"], "has_answer": True,
                "_answer": "see rare-distinct-notename"}]
        inj += [{"slugs": ["x"], "has_answer": True, "_answer": "unrelated"}
                for _ in range(200)]
        got = rt.overlap_summary(inj)
        self.assertNotIn("wasted_injection_rate", got)
        self.assertIn("not one", got["floor_caveat"])

    def test_no_usable_turns_is_a_note_not_a_zero(self):
        got = rt.overlap_summary([{"slugs": [], "has_answer": False}])
        self.assertEqual(got["turns"], 0)
        self.assertIn("note", got)

    def test_a_short_run_is_refused_rather_than_scored(self):
        # Reporting 0 named notes over 40 turns would read as a finding about
        # recall. It is a fact about the corpus, and the run should say so.
        inj = [{"slugs": ["rare-distinct-notename"], "has_answer": True,
                "_answer": "see rare-distinct-notename"} for _ in range(40)]
        got = rt.overlap_summary(inj)
        self.assertEqual(got["turns"], 40)
        self.assertIn("cannot tell a rare", got["note"])
        self.assertNotIn("note_named_rate", got)


if __name__ == "__main__":
    unittest.main()
