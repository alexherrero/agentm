#!/usr/bin/env python3
"""The labelling draw and the worksheet it writes.

The properties that matter here are all about what the operator can and cannot
see. A worksheet that leaks the judge's verdict, or the assistant's reply, or
which set a turn came from, produces labels that agree with the judge for the
wrong reason — and no downstream statistic can recover from that.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))

import build_label_worksheet as bw  # noqa: E402


def pool(n, verdicts=("sufficient", "insufficient", "n/a"), slugs=5):
    rows = []
    for i in range(n):
        rows.append({
            "turn": f"{i:04d}-aaaa-bbbb-cccc",
            "verdict": verdicts[i % len(verdicts)],
            "slugs": ["x"] * (slugs if isinstance(slugs, int)
                              else slugs[i % len(slugs)]),
        })
    return rows


class TheBands(unittest.TestCase):
    def test_the_bands_match_the_measured_distribution(self):
        # 684 injections split 31 / 139 / 514 at one note, two-to-four, and the
        # five-note cap.
        self.assertEqual(bw.band(0), "thin")
        self.assertEqual(bw.band(1), "thin")
        self.assertEqual(bw.band(2), "mid")
        self.assertEqual(bw.band(4), "mid")
        self.assertEqual(bw.band(5), "full")
        self.assertEqual(bw.band(9), "full")


class TheMachinePromptFilter(unittest.TestCase):
    def test_it_catches_what_the_system_injects(self):
        for p in ("<task-notification> <task-id>abc</task-id>",
                  "<system-reminder>\nThe user started a task",
                  "  <local-command-stdout>out</local-command-stdout>",
                  "<command-name>/work</command-name>",
                  "# Autonomous loop check\nkeep going"):
            self.assertTrue(bw.is_machine_prompt(p), p[:40])

    def test_a_human_prompt_quoting_a_tag_is_still_human(self):
        # This repository's sessions discuss these tags constantly — the
        # message that started this investigation did. A substring rule would
        # drop real prompts as machine noise.
        for p in ("why did <task-notification> entries get recall hits?",
                  "many entries look like <system-reminder> blocks, why?",
                  "explain the <command-name> record shape"):
            self.assertFalse(bw.is_machine_prompt(p), p[:40])

    def test_ordinary_prompts_pass(self):
        for p in ("where does the vault live", "retry", "yes", "", None):
            self.assertFalse(bw.is_machine_prompt(p), repr(p))


class TheStratum(unittest.TestCase):
    def test_it_reads_the_note_count_the_judge_recorded(self):
        self.assertEqual(
            bw.stratum({"verdict": "insufficient", "n_notes": 5}),
            "full/insufficient")
        self.assertEqual(
            bw.stratum({"verdict": "n/a", "n_notes": 1}), "thin/n/a")

    def test_a_pool_row_without_slugs_is_not_silently_thin(self):
        # The bug: judged rows carry no `slugs`, so every turn banded as
        # "thin" and the hit-count half of the stratification was dead. An
        # explicit count has to win over an absent one.
        row = {"verdict": "sufficient"}  # no slugs, no n_notes
        self.assertEqual(bw.stratum(row, 5), "full/sufficient")

    def test_the_explicit_count_beats_a_stale_field(self):
        row = {"verdict": "sufficient", "n_notes": 1, "slugs": []}
        self.assertEqual(bw.stratum(row, 5), "full/sufficient")

    def test_a_real_pool_row_shape_bands_correctly(self):
        # The shape `sufficient_context.judge_turn` actually emits.
        row = {"turn": "aaaa", "verdict": "insufficient", "unanimous": True,
               "replicates": 1, "failures": 0, "n_notes": 3, "cost_usd": 0.2}
        self.assertEqual(bw.stratum(row), "mid/insufficient")


class TheNoteSplit(unittest.TestCase):
    BLOCK = ("preamble text\n\n"
             "### alpha-note (kind: reference, score=0.41 daemon-hybrid, "
             "space: desk)\n\n"
             "# Alpha\n\nthe alpha body\n\n"
             "### beta-note (kind: progress, score=0.22 daemon-hybrid, "
             "space: memory)\n\n"
             "# Beta\n\nthe beta body\n")

    def test_it_finds_each_note(self):
        got = bw.split_notes(self.BLOCK)
        self.assertEqual([n["slug"] for n in got],
                         ["alpha-note", "beta-note"])
        self.assertEqual([n["kind"] for n in got], ["reference", "progress"])

    def test_the_body_does_not_start_with_the_rest_of_its_own_header(self):
        # The bug: the header pattern stopped at the score, so every note body
        # opened with "daemon-hybrid, space: desk)".
        for n in bw.split_notes(self.BLOCK):
            self.assertFalse(n["body"].startswith("daemon-hybrid"), n["body"][:40])
            self.assertFalse(n["body"].startswith(")"), n["body"][:40])
        self.assertTrue(bw.split_notes(self.BLOCK)[0]["body"].startswith("# Alpha"))

    def test_a_block_with_no_headers_is_kept_whole(self):
        # Showing nothing would be worse than showing an unsplit wall.
        got = bw.split_notes("just some text with no note headers")
        self.assertEqual(len(got), 1)
        self.assertIn("just some text", got[0]["body"])

    def test_an_empty_block_does_not_crash(self):
        self.assertEqual(len(bw.split_notes("")), 1)


class TheOrder(unittest.TestCase):
    def test_it_keeps_every_turn(self):
        # Nothing is held back. An earlier design reserved 30 turns to enrich
        # rare strata; at a 139-turn pool that took them out of the only
        # sample there was.
        p = pool(139)
        self.assertEqual(len(bw.order(p)), 139)
        self.assertEqual({r["turn"] for r in bw.order(p)},
                         {r["turn"] for r in p})

    def test_the_order_is_reproducible(self):
        p = pool(400)
        self.assertEqual([r["turn"] for r in bw.order(p)],
                         [r["turn"] for r in bw.order(p)])

    def test_a_different_seed_orders_differently(self):
        p = pool(400)
        self.assertNotEqual([r["turn"] for r in bw.order(p, seed=1)],
                            [r["turn"] for r in bw.order(p, seed=2)])

    def test_input_order_does_not_change_the_result(self):
        # The pool arrives in whatever order a JSON file happened to hold, and
        # a shuffle seeded over an unstable order is not reproducible.
        p = pool(400)
        self.assertEqual([r["turn"] for r in bw.order(p)],
                         [r["turn"] for r in bw.order(list(reversed(p)))])

    def test_a_prefix_is_a_sample_not_a_stratum(self):
        # The property the whole design rests on. If the order preserved the
        # pool's grouping, the first 40 turns would be one verdict class and a
        # partially labelled worksheet would be biased.
        p = pool(300, verdicts=("sufficient",) * 100 + ("insufficient",) * 100
                 + ("n/a",) * 100)
        first40 = bw.order(p)[:40]
        kinds = {r["verdict"] for r in first40}
        self.assertEqual(kinds, {"sufficient", "insufficient", "n/a"})
        # And roughly in population proportion rather than lumped.
        for v in kinds:
            n = sum(1 for r in first40 if r["verdict"] == v)
            self.assertGreater(n, 4, f"{v} nearly absent from the first 40")

    def test_a_prefix_does_not_favour_one_hit_count_band(self):
        p = pool(300, slugs=(1, 3, 5))
        first60 = bw.order(p)[:60]
        bands = {bw.band(len(r["slugs"])) for r in first60}
        self.assertEqual(bands, {"thin", "mid", "full"})


class TheWorksheet(unittest.TestCase):
    def _items(self):
        return [{"id": "aaaa-bbbb", "set": "a", "stratum": "full/insufficient",
                 "judge": "insufficient", "n_notes": 5,
                 "prompt": "where does the vault live",
                 "context": "### obsidian-vault-paths\nthe root is ..."}]

    def test_the_judge_verdict_cannot_be_read_off_the_worksheet(self):
        # The single most important property: the operator is measured against
        # the judge, so seeing its answer destroys the measurement.
        #
        # Asserted differentially. Checking that the word "insufficient" is
        # absent proves nothing — it is one of the three labels the operator is
        # told to choose from, and appears in the instructions by design. Two
        # turns alike but for the verdict must render identically.
        a = self._items()
        b = self._items()
        b[0]["judge"] = "sufficient"
        self.assertEqual(bw.worksheet(a, "RUBRIC.md"),
                         bw.worksheet(b, "RUBRIC.md"))
        self.assertIn("where does the vault live",
                      bw.worksheet(a, "RUBRIC.md"))

    def test_the_assistant_reply_cannot_be_read_off_it_either(self):
        a = self._items()
        b = self._items()
        b[0]["reply"] = "THE ASSISTANT SAID THIS"
        self.assertEqual(bw.worksheet(a, "RUBRIC.md"),
                         bw.worksheet(b, "RUBRIC.md"))

    def test_which_set_a_turn_came_from_cannot_be_read_off_it(self):
        # Knowing a turn came from the enriched set is a hint about the verdict.
        a = self._items()
        b = self._items()
        b[0]["set"] = "b"
        b[0]["stratum"] = "thin/sufficient"
        self.assertEqual(bw.worksheet(a, "RUBRIC.md"),
                         bw.worksheet(b, "RUBRIC.md"))

    def test_it_carries_a_slot_for_every_turn(self):
        # The bolded slot, not the bare string — the instructions quote
        # `LABEL: ?` when explaining what to replace, so a naive count is one
        # too high and would have passed a worksheet with a missing slot.
        text = bw.worksheet(self._items() * 3, "RUBRIC.md")
        self.assertEqual(text.count("**LABEL: ?**"), 3)

    def test_it_points_at_the_frozen_rubric(self):
        text = bw.worksheet(self._items(), "RUBRIC.md")
        self.assertIn("RUBRIC.md", text)
        self.assertIn("frozen", text)

    def test_it_says_the_reply_is_withheld_on_purpose(self):
        text = bw.worksheet(self._items(), "RUBRIC.md")
        self.assertIn("not** shown", text)


class TheFixture(unittest.TestCase):
    def test_the_repo_fixture_carries_no_prompt_or_note_text(self):
        # Same contract as every other file in this arc: hashes and counts in
        # the repo, text only in the operator's own vault.
        items = [{"id": "aaaa", "set": "a", "stratum": "full/n\\a",
                  "judge": "n/a", "n_notes": 5,
                  "prompt": "SECRET PROMPT TEXT",
                  "context": "SECRET NOTE TEXT"}]
        # The real writer, not a copy of it. A test that reimplements the
        # stripping passes while the writer leaks.
        payload = json.dumps({"items": bw.fixture_rows(items)})
        self.assertNotIn("SECRET PROMPT", payload)
        self.assertNotIn("SECRET NOTE", payload)
        self.assertIn("aaaa", payload)


if __name__ == "__main__":
    unittest.main()
