#!/usr/bin/env python3
"""The sufficient-context judge: its parser, its sampler, and its arithmetic.

The judge itself is a model and cannot be unit-tested. What can be tested is
everything around it, and that is where the failures this arc has actually seen
live: a malformed answer scored as zero, an excluded turn folded into a rate, a
sampler that re-samples differently on every run.

See results/online-v1/RULE-sufficient-context.md for the bars the live judge
has to clear.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))

import sufficient_context as sc  # noqa: E402


class TheParser(unittest.TestCase):
    def test_a_plain_sufficient_verdict(self):
        self.assertEqual(sc.parse_verdict('{"verdict": "sufficient"}'),
                         {"verdict": "sufficient", "missing": []})

    def test_prose_around_the_json_is_tolerated(self):
        got = sc.parse_verdict('Here you go:\n{"verdict": "n/a"}\nhope that helps')
        self.assertEqual(got["verdict"], "n/a")

    def test_a_rejection_must_name_the_gap(self):
        # Borrowed from grounding.go: a rejection with nothing named is a judge
        # that disliked the context, not one that found a problem.
        self.assertIsNone(sc.parse_verdict('{"verdict": "insufficient"}'))
        self.assertIsNone(
            sc.parse_verdict('{"verdict": "insufficient", "missing": []}'))
        self.assertIsNotNone(
            sc.parse_verdict('{"verdict": "insufficient", "missing": ["a date"]}'))

    def test_an_unknown_verdict_is_not_a_verdict(self):
        self.assertIsNone(sc.parse_verdict('{"verdict": "maybe"}'))
        self.assertIsNone(sc.parse_verdict('{"verdict": true}'))

    def test_junk_is_none_rather_than_a_guess(self):
        for junk in ("", "I think it is sufficient", "{not json", "[1,2]", None):
            self.assertIsNone(sc.parse_verdict(junk), junk)

    def test_a_missing_field_of_the_wrong_shape_is_rejected(self):
        self.assertIsNone(
            sc.parse_verdict('{"verdict": "insufficient", "missing": "a date"}'))


class TheSampler(unittest.TestCase):
    def test_it_picks_the_same_turns_every_run(self):
        keys = [f"session-{i}:2026-08-29T0{i}:00:00Z" for i in range(50)]
        first = [k for k in keys if sc.sample_every(5)(k)]
        second = [k for k in keys if sc.sample_every(5)(k)]
        self.assertEqual(first, second)
        self.assertTrue(first, "a sampler that selects nothing proves nothing")

    def test_it_spreads_evenly_on_the_keys_that_break_the_raw_hash(self):
        # FNV-1a's low bit is close to input parity, so `h % 10` on keys shaped
        # `s0:t0, s1:t1, …` lands only on even residues and a 1-in-10 sample
        # takes one in five. Measured before the finalizer went in:
        # [401, 0, 377, 0, 401, 0, 410, 0, 411, 0].
        keys = [f"s{i}:t{i}" for i in range(2000)]
        picked = sum(1 for k in keys if sc.sample_every(10)(k))
        self.assertGreater(picked, 140)
        self.assertLess(picked, 260)

    def test_no_residue_is_starved(self):
        # The failure this guards is not "the count is off" but "half the key
        # space can never be selected", which a count alone can miss. Asserted
        # through `sample_every`, not `_mix` — reaching past the sampler into
        # its helper let a mutation that dropped the finalizer from the real
        # path stay green.
        keys = [f"s{i}:t{i}" for i in range(2000)]
        half = sum(1 for k in keys if sc.sample_every(2)(k))
        # Without the finalizer FNV-1a's low bit tracks input parity, and on
        # these keys every hash came out even: a "one in two" sample took all
        # 2000. Anything near 0% or 100% means the modulus is reading structure
        # rather than a hash.
        self.assertGreater(half, 800)
        self.assertLess(half, 1200)

    def test_every_residue_class_is_reachable_through_the_sampler(self):
        keys = [f"s{i}:t{i}" for i in range(2000)]
        # If a residue class were unreachable, one of these divisors would
        # select nothing at all.
        for n in (2, 4, 10):
            picked = sum(1 for k in keys if sc.sample_every(n)(k))
            self.assertGreater(picked, len(keys) / n * 0.6, f"1-in-{n}")
            self.assertLess(picked, len(keys) / n * 1.6, f"1-in-{n}")

    def test_the_degenerate_settings_match_the_daemon(self):
        self.assertTrue(sc.sample_every(1)("anything"))
        self.assertFalse(sc.sample_every(0)("anything"))
        self.assertFalse(sc.sample_every(-3)("anything"))

    def test_the_key_is_the_turn_not_the_query(self):
        # The same prompt asked twice is two turns with two contexts. Keying on
        # the prompt hash would judge one and skip the other, which is sampling
        # contexts by their queries.
        a = {"session": "s1", "ts": "t1", "prompt_hash": "same"}
        b = {"session": "s2", "ts": "t2", "prompt_hash": "same"}
        self.assertNotEqual(sc.turn_key(a), sc.turn_key(b))


class TheJudgeLoop(unittest.TestCase):
    def _caller(self, *answers):
        it = iter(answers)
        return lambda _p, **_kw: next(it)

    def test_it_reports_unanimity_when_replicates_agree(self):
        got = sc.judge_turn({"prompt_hash": "abc", "_prompt": "q",
                             "_injected": "c"}, replicates=3,
                            caller=self._caller(*['{"verdict": "sufficient"}'] * 3))
        self.assertEqual(got["verdict"], "sufficient")
        self.assertTrue(got["unanimous"])
        self.assertEqual(got["failures"], 0)

    def test_a_split_is_reported_as_a_split(self):
        got = sc.judge_turn(
            {"prompt_hash": "abc", "_prompt": "q", "_injected": "c"},
            replicates=3,
            caller=self._caller('{"verdict": "sufficient"}',
                                '{"verdict": "insufficient", "missing": ["x"]}',
                                '{"verdict": "sufficient"}'))
        self.assertEqual(got["verdict"], "sufficient")
        self.assertFalse(got["unanimous"])

    def test_a_failed_call_is_counted_and_the_rest_still_answer(self):
        got = sc.judge_turn(
            {"prompt_hash": "abc", "_prompt": "q", "_injected": "c"},
            replicates=3,
            caller=self._caller("timed out", '{"verdict": "sufficient"}',
                                '{"verdict": "sufficient"}'))
        self.assertEqual(got["verdict"], "sufficient")
        self.assertEqual(got["failures"], 1)

    def test_a_turn_where_every_call_failed_has_no_verdict(self):
        # Not zero, not "insufficient". The completeness-v1 run scored failures
        # as zero and spent a day explaining a number made of timeouts.
        got = sc.judge_turn(
            {"prompt_hash": "abc", "_prompt": "q", "_injected": "c"},
            replicates=2, caller=self._caller("", "nonsense"))
        self.assertIsNone(got["verdict"])
        self.assertEqual(got["failures"], 2)

    def test_the_gap_wording_is_kept_out_of_the_persisted_fields(self):
        # The judge's wording of a gap restates the query, and the query does
        # not go to disk. Underscore-prefixed keys are stripped by the writer.
        got = sc.judge_turn(
            {"prompt_hash": "abc", "_prompt": "q", "_injected": "c"},
            replicates=1,
            caller=self._caller(
                '{"verdict": "insufficient", "missing": ["the vault path"]}'))
        persisted = {k: v for k, v in got.items() if not k.startswith("_")}
        self.assertEqual(got["missing_count"], 1)
        self.assertNotIn("the vault path", repr(persisted))


class TheCostAccounting(unittest.TestCase):
    def test_cost_is_summed_from_the_envelope_not_estimated(self):
        # The plan budgeted ~$0.014/turn. A real call with an empty prompt
        # bills about $0.14, because the CLI ships a large system prompt even
        # with `--tools none`. Estimating would have been wrong by ~30x, so the
        # number comes from the envelope.
        env = {"result": '{"verdict": "sufficient"}', "total_cost_usd": 0.05}
        got = sc.judge_turn({"prompt_hash": "abc", "_prompt": "q",
                             "_injected": "c"}, replicates=3,
                            caller=lambda _p, **_kw: env)
        self.assertEqual(got["cost_usd"], 0.15)

    def test_a_failed_turn_still_reports_what_it_spent(self):
        # A call that fails still bills. Dropping its cost would understate the
        # run by exactly the calls that went wrong.
        env = {"result": "garbage", "total_cost_usd": 0.05}
        got = sc.judge_turn({"prompt_hash": "abc", "_prompt": "q",
                             "_injected": "c"}, replicates=2,
                            caller=lambda _p, **_kw: env)
        self.assertIsNone(got["verdict"])
        self.assertEqual(got["cost_usd"], 0.10)

    def test_the_run_total_is_reported(self):
        got = sc.aggregate([{"verdict": "sufficient", "unanimous": True,
                             "cost_usd": 0.15},
                            {"verdict": "n/a", "unanimous": True,
                             "cost_usd": 0.05}])
        self.assertEqual(got["cost_usd"], 0.20)
        self.assertEqual(got["cost_per_turn_usd"], 0.1)


class TheArithmetic(unittest.TestCase):
    def test_excluded_turns_are_not_in_the_denominator(self):
        rows = [
            {"verdict": "sufficient", "unanimous": True},
            {"verdict": "insufficient", "unanimous": True},
            {"verdict": "n/a", "unanimous": True},
            {"verdict": None, "unanimous": None},
        ]
        got = sc.aggregate(rows)
        self.assertEqual(got["turns_seen"], 4)
        self.assertEqual(got["scored"], 2)
        self.assertEqual(got["sufficient_rate"], 0.5)
        self.assertEqual(got["excluded_not_an_information_need"], 1)
        self.assertEqual(got["excluded_judge_failed"], 1)

    def test_nothing_scored_is_a_note_not_a_zero(self):
        got = sc.aggregate([{"verdict": None, "unanimous": None},
                            {"verdict": "n/a", "unanimous": True}])
        self.assertNotIn("sufficient_rate", got)
        self.assertIn("note", got)

    def test_unanimity_is_reported_over_turns_that_produced_a_verdict(self):
        rows = [
            {"verdict": "sufficient", "unanimous": True},
            {"verdict": "sufficient", "unanimous": False},
            {"verdict": None, "unanimous": None},
        ]
        got = sc.aggregate(rows)
        self.assertEqual(got["unanimity_rate"], 0.5)

    def test_an_unstable_na_turn_counts_against_stability(self):
        # The failure this guards: unanimity computed over scored turns alone
        # reports 100% here, while the judge in fact disagreed with itself on
        # two thirds of what it saw. A calibration run had exactly this shape —
        # 2 of 3 n/a turns split.
        rows = [
            {"verdict": "sufficient", "unanimous": True, "scoreable_split": False},
            {"verdict": "n/a", "unanimous": False, "scoreable_split": True},
            {"verdict": "n/a", "unanimous": False, "scoreable_split": True},
        ]
        got = sc.aggregate(rows)
        self.assertEqual(got["unanimity_rate"], round(1 / 3, 4))

    def test_a_split_over_scoreability_is_reported_separately(self):
        # Disagreeing about whether a turn is an information need is not one
        # row wobbling — it moves that turn in or out of the denominator of
        # sufficient_rate, so it gets its own number.
        rows = [
            {"verdict": "sufficient", "unanimous": True, "scoreable_split": False},
            {"verdict": "insufficient", "unanimous": False,
             "scoreable_split": False},
            {"verdict": "n/a", "unanimous": False, "scoreable_split": True},
        ]
        got = sc.aggregate(rows)
        self.assertEqual(got["scoreability_split_rate"], round(1 / 3, 4))

    def test_the_judge_marks_a_scoreability_split(self):
        # End to end from the replicates, not just the aggregate: a turn whose
        # replicates straddle n/a must carry the flag.
        answers = iter(['{"verdict": "n/a"}',
                        '{"verdict": "insufficient", "missing": ["x"]}',
                        '{"verdict": "n/a"}'])
        got = sc.judge_turn({"prompt_hash": "abc", "_prompt": "q",
                             "_injected": "c"}, replicates=3,
                            caller=lambda _p, **_kw: next(answers))
        self.assertTrue(got["scoreable_split"])
        self.assertFalse(got["unanimous"])

    def test_a_turn_split_only_on_sufficiency_is_not_a_scoreability_split(self):
        answers = iter(['{"verdict": "sufficient"}',
                        '{"verdict": "insufficient", "missing": ["x"]}',
                        '{"verdict": "sufficient"}'])
        got = sc.judge_turn({"prompt_hash": "abc", "_prompt": "q",
                             "_injected": "c"}, replicates=3,
                            caller=lambda _p, **_kw: next(answers))
        self.assertFalse(got["scoreable_split"])
        self.assertFalse(got["unanimous"])

    def test_stability_is_labelled_as_measured(self):
        # `claude -p` has no temperature flag, so a reader must not take the
        # judge for deterministic just because it is a judge.
        got = sc.aggregate([{"verdict": "sufficient", "unanimous": True}])
        self.assertIn("measured, not assumed", got["stability_note"])


class TheWriter(unittest.TestCase):
    def test_a_hash_is_grouped_so_it_is_not_read_as_a_phone_number(self):
        # Sixteen bare hex characters match the repo's US-phone pattern; the
        # PII gate has stopped four pushes over exactly that.
        self.assertEqual(sc.grouped_hash("0123456789abcdef"),
                         "0123-4567-89ab-cdef")

    def test_grouping_keeps_the_value(self):
        for h in ("0123456789abcdef", "abc", ""):
            self.assertEqual(sc.grouped_hash(h).replace("-", ""), h)

    def test_the_written_rows_carry_no_text_and_no_bare_hash(self):
        # The real writer, not a copy of it — a test that reimplements the
        # serialization would pass while the writer leaked.
        rows = [{"turn": "0123456789abcdef", "verdict": "insufficient",
                 "missing_count": 1, "_missing": ["the vault path"],
                 "_prompt": "where is the vault",
                 "_injected": "the injected block"}]
        text = json.dumps(sc.persist_rows(rows))
        self.assertNotIn("the vault path", text)
        self.assertNotIn("where is the vault", text)
        self.assertNotIn("the injected block", text)
        self.assertNotIn("0123456789abcdef", text)
        self.assertIn("0123-4567-89ab-cdef", text)
        self.assertIn('"missing_count": 1', text)


class TheSpendCap(unittest.TestCase):
    def _run(self, argv, turns, per_call=0.10):
        import recall_traffic
        real_iter = recall_traffic.iter_injections
        real_call = sc.completeness_grade._call_claude_json
        recall_traffic.iter_injections = lambda **_kw: iter(turns)
        sc.completeness_grade._call_claude_json = lambda _p, **_kw: {
            "result": '{"verdict": "sufficient"}', "total_cost_usd": per_call}
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                sc.main(argv)
        finally:
            recall_traffic.iter_injections = real_iter
            sc.completeness_grade._call_claude_json = real_call
        return buf.getvalue()

    def _turns(self, n):
        return [{"session": f"s{i}", "ts": f"t{i}", "prompt_hash": f"h{i}",
                 "_prompt": "q", "_injected": "c"} for i in range(n)]

    def test_the_cap_stops_the_loop_and_says_so(self):
        # 20 turns at $0.30 each (3 replicates x $0.10) against a $1.00 cap:
        # the run must stop, and must not present its rate as a full sweep.
        out = self._run(["--sample-every", "1", "--replicates", "3",
                         "--max-spend", "1.0"], self._turns(20))
        self.assertIn("STOPPED", out)
        self.assertIn("were not judged", out)

    def test_the_truncation_reaches_the_machine_readable_summary(self):
        # Printed prose is for a person reading now; the JSON is what a
        # scheduled job and a later reader consume. A truncated sweep that says
        # so only on the terminal is a sweep that looks complete in the file.
        out = self._run(["--sample-every", "1", "--replicates", "3",
                         "--max-spend", "1.0", "--json"], self._turns(20))
        got = json.loads(out)
        self.assertEqual(got["stopped_at_spend_cap"], 1.0)
        self.assertGreater(got["turns_not_judged"], 0)

    def test_a_complete_sweep_carries_no_truncation_fields(self):
        out = self._run(["--sample-every", "1", "--replicates", "1",
                         "--max-spend", "0", "--json"], self._turns(4))
        got = json.loads(out)
        self.assertNotIn("stopped_at_spend_cap", got)
        self.assertNotIn("turns_not_judged", got)

    def test_without_a_cap_every_sampled_turn_is_judged(self):
        out = self._run(["--sample-every", "1", "--replicates", "1",
                         "--max-spend", "0"], self._turns(5))
        self.assertNotIn("STOPPED", out)
        self.assertIn("5 scored of 5", out)

    def test_the_spend_is_printed_as_measured(self):
        out = self._run(["--sample-every", "1", "--replicates", "1",
                         "--max-spend", "0"], self._turns(4))
        self.assertIn("measured not estimated", out)
        self.assertIn("$0.40", out)


class TheCallShape(unittest.TestCase):
    def test_the_judge_runs_with_hooks_disabled(self):
        # Safety-critical and easy to lose: a live reflect hook would write
        # into the operator's real vault from a judging run — a scorer that
        # mutates the corpus it is scoring. Only `--settings` closes the
        # deferred-tool surface; `--disallowedTools` does not.
        import completeness_grade
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            raise OSError("not actually calling out")

        real = completeness_grade.subprocess.run
        completeness_grade.subprocess.run = fake_run
        try:
            completeness_grade._call_claude("hi", system=sc.SYSTEM)
        finally:
            completeness_grade.subprocess.run = real
        self.assertIn('{"disableAllHooks":true}', seen["cmd"])
        self.assertIn("--strict-mcp-config", seen["cmd"])
        self.assertIn(sc.SYSTEM, seen["cmd"])


if __name__ == "__main__":
    unittest.main()
