#!/usr/bin/env python3
"""The nightly pass: does it accumulate, and does it stop when told?

Three properties carry this. It must judge only what is new, or it spends every
night re-learning the same thing. It must stop at its cap, because unattended
and uncapped it is a three-figure monthly bill. And it must never rewrite a
verdict, because a pool whose old entries change is a mixture of measurements
from an instrument known to drift.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))

import online_recall_job as job  # noqa: E402
import sufficient_context as sc  # noqa: E402


def turns(n, start=0):
    return [{"session": f"s{i}", "ts": f"t{i}", "prompt_hash": f"h{i}",
             "_prompt": f"question {i}", "_injected": "some notes",
             "arm": "hybrid"}
            for i in range(start, start + n)]


class TheAccumulation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = pathlib.Path(self.tmp.name)
        self._iter = job.recall_traffic.iter_injections
        self._call = sc.completeness_grade._call_claude_json
        sc.completeness_grade._call_claude_json = lambda _p, **_kw: {
            "result": '{"verdict": "insufficient", "missing": ["x"]}',
            "total_cost_usd": 0.20}

    def tearDown(self):
        job.recall_traffic.iter_injections = self._iter
        sc.completeness_grade._call_claude_json = self._call
        self.tmp.cleanup()

    def _serve(self, ts):
        job.recall_traffic.iter_injections = lambda **_kw: iter(ts)

    def test_a_second_night_judges_only_what_is_new(self):
        # Re-judging the same turns burns money to learn nothing; the whole
        # point of a schedule here is that the sample grows.
        self._serve(turns(5))
        first = job.run(self.vault, cap=10.0)
        self.assertEqual(first["judged_tonight"], 5)

        self._serve(turns(8))          # three of them new
        second = job.run(self.vault, cap=10.0)
        self.assertEqual(second["judged_tonight"], 3)
        self.assertEqual(second["pool_total"], 8)

    def test_nothing_new_costs_nothing(self):
        self._serve(turns(4))
        job.run(self.vault, cap=10.0)
        again = job.run(self.vault, cap=10.0)
        self.assertEqual(again["judged_tonight"], 0)
        self.assertEqual(again["cost_usd"], 0.0)

    def test_it_stops_at_the_cap_and_says_what_is_left(self):
        # $0.20 a turn against a $1 cap: five turns, and the rest named as
        # waiting rather than silently dropped.
        self._serve(turns(40))
        got = job.run(self.vault, cap=1.0)
        self.assertEqual(got["judged_tonight"], 5)
        self.assertEqual(got["unjudged_remaining"], 35)

    def test_the_pool_never_shrinks(self):
        # A pool that dropped aged-out traffic would make the interval widen
        # and narrow for reasons unrelated to recall.
        self._serve(turns(6))
        job.run(self.vault, cap=10.0)
        self._serve(turns(3, start=100))     # entirely different turns
        got = job.run(self.vault, cap=10.0)
        self.assertEqual(got["pool_total"], 9)

    def test_an_existing_verdict_is_never_rewritten(self):
        self._serve(turns(3))
        job.run(self.vault, cap=10.0)
        before = json.loads(job.pool_path(self.vault).read_text())["rows"]
        # A judge that would now answer differently.
        sc.completeness_grade._call_claude_json = lambda _p, **_kw: {
            "result": '{"verdict": "sufficient"}', "total_cost_usd": 0.20}
        job.run(self.vault, cap=10.0)
        after = json.loads(job.pool_path(self.vault).read_text())["rows"]
        self.assertEqual([r["verdict"] for r in before],
                         [r["verdict"] for r in after[:len(before)]])

    def test_machine_prompts_never_enter_the_pool(self):
        # A third of live retrievals fire on task notifications; judging them
        # would spend the budget on text nobody typed.
        ts = turns(2) + [{"session": "s9", "ts": "t9", "prompt_hash": "h9",
                          "_prompt": "<task-notification> done",
                          "_injected": "notes", "arm": "hybrid"}]
        self._serve(ts)
        got = job.run(self.vault, cap=10.0)
        self.assertEqual(got["judged_tonight"], 2)

    def test_every_run_is_recorded_even_an_empty_one(self):
        # A job that stopped running should be visible as a gap in the log,
        # not as silence.
        self._serve(turns(2))
        job.run(self.vault, cap=10.0)
        job.run(self.vault, cap=10.0)
        runs = json.loads(job.pool_path(self.vault).read_text())["runs"]
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[1]["judged"], 0)

    def test_turns_are_keyed_on_the_sampler_key_not_the_prompt_hash(self):
        # 32 prompt hashes cover 83 turns in the real corpus, so a hash-keyed
        # check would skip turns it had never judged.
        same_hash = [
            {"session": "a", "ts": "1", "prompt_hash": "SAME",
             "_prompt": "q", "_injected": "n", "arm": "hybrid"},
            {"session": "b", "ts": "2", "prompt_hash": "SAME",
             "_prompt": "q", "_injected": "n", "arm": "hybrid"},
        ]
        self._serve(same_hash)
        got = job.run(self.vault, cap=10.0)
        self.assertEqual(got["judged_tonight"], 2)


if __name__ == "__main__":
    unittest.main()
