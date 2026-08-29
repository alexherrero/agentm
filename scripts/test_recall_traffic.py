#!/usr/bin/env python3
"""The production-traffic reader, and the join everything else rests on.

The property under test throughout: this module describes what recall actually
did, or it says it cannot. A reader that silently returns nothing — because the
hash convention moved, or because the record chain is not the shape it assumes —
would be the silent-total-null that shipped two false refutations in the offline
arc, wearing production clothes this time.
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

import recall_counter  # noqa: E402
import recall_traffic as rt  # noqa: E402

STDERR = ("[memory-recall-prompt-submit] Loaded 3 relevant entries: alpha, "
          "beta, gamma (engine: daemon, 68ms, scope=memory-root, terms: 'two "
          "related pieces') (token budget: 2 entries excerpted to fit, 1 "
          "entries omitted; budget=20,000)")
STDOUT = ("# MemoryVault — recall hits for your prompt\n\nThe following entries "
          "match your prompt (top 3 by daemon lexical rank; deduped).\n")


class TheHashConvention(unittest.TestCase):
    def test_it_is_the_ledger_writer_s_own_function(self):
        # Not a reimplementation that happens to agree today: the writer's
        # private helper is called and compared, so a change there fails here
        # rather than silently unjoining every future row.
        for probe in ("what did we decide about worktrees", "", "unicode — é"):
            self.assertEqual(rt.query_hash(probe),
                             recall_counter._hash_query(probe), probe)

    def test_it_is_sixteen_chars(self):
        self.assertEqual(len(rt.query_hash("anything")), 16)


class TheStderrLine(unittest.TestCase):
    def test_every_field_is_read(self):
        got = rt.parse_stderr(STDERR)
        self.assertEqual(got["loaded"], 3)
        self.assertEqual(got["slugs"], ["alpha", "beta", "gamma"])
        self.assertEqual(got["engine"], "daemon")
        self.assertEqual(got["elapsed_ms"], 68)
        self.assertEqual(got["scope"], "memory-root")
        self.assertEqual(got["excerpted"], 2)
        self.assertEqual(got["omitted"], 1)

    def test_a_grown_line_parses_partially_rather_than_failing(self):
        # The hook owns this format and may add clauses. Dropping every
        # injection because one suffix appeared would be worse than a partial
        # read, so the parser degrades instead of raising.
        got = rt.parse_stderr(STDERR + " (some future clause)")
        self.assertEqual(got["loaded"], 3)

    def test_an_unrelated_line_yields_nothing_rather_than_guesses(self):
        self.assertEqual(rt.parse_stderr("[some-other-hook] did a thing"), {})

    def test_no_omission_clause_is_zero_not_missing(self):
        line = STDERR.split(" (token budget")[0] + " (token budget: 2 entries excerpted to fit)"
        self.assertEqual(rt.parse_stderr(line)["omitted"], 0)


class TheInjectedBlock(unittest.TestCase):
    def test_the_ranking_arm_is_read(self):
        # The degrade-to-lexical signal: a hybrid run that silently lost its
        # dense arm looks identical in every other field.
        self.assertEqual(rt.parse_stdout(STDOUT)["arm"], "lexical")

    def test_a_hybrid_block_says_hybrid(self):
        self.assertEqual(
            rt.parse_stdout("(top 5 by daemon hybrid rank; deduped)")["arm"],
            "hybrid")


def _transcript(tmp: pathlib.Path, name: str, records: list) -> pathlib.Path:
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "session.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return f


def _chain(prompt: str = "what did we decide"):
    """A user turn, two chained attachments, interleaved noise, then the answer.

    Shaped from the real thing: the recall attachment's parent is *another
    attachment*, and the assistant is its child rather than the next record —
    both traps this fixture exists to hold the reader to.
    """
    return [
        {"uuid": "u1", "type": "user", "parentUuid": None,
         "message": {"role": "user", "content": prompt}},
        {"uuid": "a0", "type": "attachment", "parentUuid": "u1",
         "attachment": {"hookName": "UserPromptSubmit", "stderr": "[other] noop",
                        "stdout": "", "durationMs": 5, "exitCode": 0}},
        {"uuid": "a1", "type": "attachment", "parentUuid": "a0",
         "attachment": {"hookName": "UserPromptSubmit", "stderr": STDERR,
                        "stdout": STDOUT, "durationMs": 240, "exitCode": 0}},
        {"uuid": "n1", "type": "last-prompt", "parentUuid": None},
        {"uuid": "n2", "type": "custom-title", "parentUuid": None},
        {"uuid": "s1", "type": "assistant", "parentUuid": "a1",
         "message": {"role": "assistant", "content": "the answer text"}},
    ]


class TheRecordChain(unittest.TestCase):
    def test_the_prompt_is_found_through_the_attachment_chain(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            _transcript(tmp, "proj", _chain())
            rows = list(rt.iter_injections(tmp))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt_hash"], rt.query_hash("what did we decide"))

    def test_the_answer_is_the_child_not_the_next_record(self):
        # `last-prompt` and `custom-title` sit between them in the real files;
        # a reader taking the next record would attribute an empty turn.
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            _transcript(tmp, "proj", _chain())
            rows = list(rt.iter_injections(tmp))
        self.assertTrue(rows[0]["has_answer"])
        self.assertEqual(rows[0]["answer_chars"], len("the answer text"))

    def test_a_non_recall_hook_is_not_an_injection(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            _transcript(tmp, "proj", [_chain()[0], _chain()[1]])
            self.assertEqual(list(rt.iter_injections(tmp)), [])

    def test_the_eval_harness_s_own_runs_are_excluded_by_default(self):
        # 5,736 of the transcripts on this machine are hook-disabled `claude -p`
        # calls. Counting them as real work would be the corpus-vs-corpus error
        # from the offline arc, in a new place.
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            _transcript(tmp, "agentm-neutral-cwd-abc", _chain())
            self.assertEqual(list(rt.iter_injections(tmp)), [])
            self.assertEqual(len(list(rt.iter_injections(tmp,
                                                         include_synthetic=True))), 1)

    def test_a_half_written_line_does_not_lose_the_file(self):
        # Transcripts are appended live; the tail of one can be a partial line.
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            f = _transcript(tmp, "proj", _chain())
            f.write_text(f.read_text(encoding="utf-8") + '\n{"uuid": "trunc',
                         encoding="utf-8")
            self.assertEqual(len(list(rt.iter_injections(tmp))), 1)


class TheJoin(unittest.TestCase):
    def test_a_matching_hash_joins(self):
        inj = [{"prompt_hash": "abc123"}]
        got = rt.verify_join([{"query_hash": "abc123"}], inj)
        self.assertEqual(got["matched"], 1)
        self.assertEqual(got["match_rate"], 1.0)

    def test_zero_overlap_raises_rather_than_reporting_nothing(self):
        # The whole instrument rests on this hash. A silent zero would make
        # every downstream number a statement about nothing — the exact shape
        # that shipped two false refutations offline.
        with self.assertRaises(rt.JoinError) as caught:
            rt.verify_join([{"query_hash": "aaaa"}], [{"prompt_hash": "bbbb"}])
        self.assertIn("moved", str(caught.exception))

    def test_a_partial_match_is_a_rate_not_a_failure(self):
        got = rt.verify_join(
            [{"query_hash": "a"}],
            [{"prompt_hash": "a"}, {"prompt_hash": "z"}])
        self.assertEqual(got["matched"], 1)
        self.assertEqual(got["match_rate"], 0.5)

    def test_no_injections_is_not_an_error(self):
        # A fresh machine has a ledger and no attachments yet.
        got = rt.verify_join([{"query_hash": "a"}], [])
        self.assertEqual(got["matched"], 0)


class TheSummary(unittest.TestCase):
    def test_an_empty_corpus_reports_none_not_zero(self):
        # Same rule the scorecard lives by: a rate nobody measured must not
        # render as a measured 0%.
        got = rt.summarize([], [])
        self.assertIsNone(got["zero_hit_rate"])
        self.assertIsNone(got["window"])

    def test_the_zero_hit_rate_is_over_all_recalls(self):
        rows = [{"ts": "2026-08-01T00:00:00Z", "hit_count": 0},
                {"ts": "2026-08-02T00:00:00Z", "hit_count": 2}]
        got = rt.summarize(rows, [])
        self.assertEqual(got["zero_hit"], 1)
        self.assertEqual(got["zero_hit_rate"], 0.5)
        self.assertEqual(got["window"], ["2026-08-01", "2026-08-02"])

    def test_the_arm_split_is_counted(self):
        inj = [{"arm": "hybrid"}, {"arm": "hybrid"}, {"arm": "lexical"}]
        self.assertEqual(rt.summarize([], inj)["arm"],
                         {"hybrid": 2, "lexical": 1})


if __name__ == "__main__":
    unittest.main()
