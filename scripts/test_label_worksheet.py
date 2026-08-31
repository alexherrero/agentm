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


class TheFencing(unittest.TestCase):
    def test_a_fence_long_enough_to_contain_the_text(self):
        self.assertEqual(bw.fence_for("plain"), "```")
        self.assertEqual(bw.fence_for("has ``` inside"), "````")
        self.assertEqual(bw.fence_for("has ````` inside"), "``````")

    def test_a_clipped_excerpt_closes_what_it_opened(self):
        # The clip lands mid-block often enough that this is the normal case.
        self.assertEqual(bw.balance_fences("a\n```\nb").count("```"), 2)
        self.assertEqual(bw.balance_fences("a\n```\nb\n```").count("```"), 2)

    def test_a_prompt_quoting_a_fence_cannot_break_out(self):
        item = {"id": "x", "n_notes": 0, "context": "",
                "prompt": "why does\n```\ncode\n```\nbreak?"}
        text = "\n".join(bw.render_turn(1, item))
        # The wrapper must be longer than anything inside it.
        self.assertIn("````", text)

    def test_a_note_body_carrying_a_fence_leaves_the_page_balanced(self):
        # The real failure: 41 fences in one batch, so everything after the
        # last unclosed one rendered inside a code block.
        block = ("### a-note (kind: unknown, score=0.03 daemon-hybrid, "
                 "space: desk)\n\n# Title\n\n```python\nx = 1\n")
        item = {"id": "x", "n_notes": 1, "prompt": "q", "context": block}
        text = "\n".join(bw.render_turn(1, item))
        fences = sum(1 for l in text.splitlines()
                     if l.lstrip("> ").startswith("```"))
        self.assertEqual(fences % 2, 0, f"{fences} fences left the page open")

    def test_every_rendered_turn_leaves_the_page_balanced(self):
        blocks = [
            "### n (kind: unknown, score=0.1 x, space: desk)\n\n```\nopen",
            "### n (kind: unknown, score=0.1 x, space: desk)\n\n``` a ```\nb",
            "### n (kind: unknown, score=0.1 x, space: desk)\n\nplain",
        ]
        for b in blocks:
            item = {"id": "x", "n_notes": 1, "prompt": "q ``` q", "context": b}
            text = "\n".join(bw.render_turn(1, item))
            fences = sum(1 for l in text.splitlines()
                         if l.lstrip("> ").startswith("```"))
            self.assertEqual(fences % 2, 0, b[:40])


class TheCarriedLabels(unittest.TestCase):
    def test_a_carried_label_replaces_the_empty_slot(self):
        item = {"id": "x", "n_notes": 0, "prompt": "q", "context": "",
                "label": "sufficient"}
        text = "\n".join(bw.render_turn(1, item))
        self.assertIn("**LABEL: sufficient**", text)
        self.assertNotIn("**LABEL: ?**", text)

    def test_a_carried_flag_is_restored_too(self):
        item = {"id": "x", "n_notes": 0, "prompt": "q", "context": "",
                "label": "insufficient", "flag": "no_note_possible"}
        text = "\n".join(bw.render_turn(1, item))
        self.assertIn("FLAG: no_note_possible", text)

    def test_an_unlabelled_turn_still_gets_an_empty_slot(self):
        item = {"id": "x", "n_notes": 0, "prompt": "q", "context": ""}
        self.assertIn("**LABEL: ?**", "\n".join(bw.render_turn(1, item)))

    def test_a_label_never_reaches_the_repo_fixture(self):
        # The operator's answer is data about them, and it is also the thing
        # the judge is measured against — it belongs in the vault, not the repo.
        rows = bw.fixture_rows([{"id": "x", "stratum": "full/n/a", "judge": "n/a",
                                 "n_notes": 5, "label": "sufficient",
                                 "flag": "no_note_possible",
                                 "prompt": "p", "context": "c"}])
        import json as _j
        text = _j.dumps(rows)
        self.assertNotIn("sufficient", text)
        self.assertNotIn("no_note_possible", text)
        self.assertIn("full/n/a", text)


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


class TheInventory(unittest.TestCase):
    def test_it_types_notes_from_their_slug(self):
        self.assertEqual(bw.note_type("PLAN.archive.20260627-x", "unknown", ""),
                         "archived plan")
        self.assertEqual(bw.note_type("PLAN-online-recall", "unknown", ""),
                         "active plan")
        self.assertEqual(bw.note_type("progress-hybrid", "unknown", ""),
                         "progress log")
        self.assertEqual(bw.note_type("RULE-zero-hit", "unknown", ""),
                         "frozen rule")

    def test_a_declared_kind_wins_over_guessing(self):
        self.assertEqual(bw.note_type("whatever", "design", ""), "design")
        self.assertEqual(bw.note_type("whatever", "handoff-artifact", ""),
                         "handoff artifact")

    def test_tags_carry_the_type_when_kind_says_unknown(self):
        # Three quarters of retrieved notes declare kind "unknown", so the
        # header's own tags are the next best evidence.
        self.assertEqual(
            bw.note_type("blog-author", "unknown", "# Blog-author",
                         tags=["idea-incubator-graduate"]), "idea")

    def test_a_captured_preference_is_recognised(self):
        self.assertEqual(
            bw.note_type("i-want-to-x", "unknown", "User stated: ..."),
            "captured preference")

    def test_the_title_is_the_notes_own_heading(self):
        # Derived, never written by a model — a wrong gist would make the
        # operator's label wrong with nothing on the page to show it.
        self.assertEqual(
            bw.note_title("# R3 — Memory-eval benchmarks\n\nbody here"),
            "R3 — Memory-eval benchmarks")

    def test_a_note_with_no_heading_falls_back_to_its_first_sentence(self):
        self.assertEqual(bw.note_title("\n> quoted\n\nthe real opening line"),
                         "the real opening line")

    def test_status_comes_from_the_notes_own_status_line(self):
        self.assertEqual(bw.note_status("**Status:** done *(started 2026)*"),
                         "done")
        self.assertEqual(bw.note_status("no status here"), "")

    def test_the_inventory_counts_and_pluralises(self):
        notes = [
            {"slug": "PLAN.archive.a", "kind": "unknown", "body": "", "tags": []},
            {"slug": "PLAN.archive.b", "kind": "unknown", "body": "", "tags": []},
            {"slug": "x", "kind": "design", "body": "", "tags": []},
        ]
        self.assertEqual(bw.inventory(notes), "2 archived plans, 1 design")

    def test_the_header_carries_space_and_tags_through(self):
        block = ("### blog-author (kind: unknown, score=0.03 daemon-hybrid, "
                 "space: memory, tags: [idea-incubator-graduate])\n\n"
                 "# Blog-author\n")
        got = bw.split_notes(block)[0]
        self.assertEqual(got["space"], "memory")
        self.assertEqual(got["tags"], ["idea-incubator-graduate"])

    def test_the_body_still_starts_after_the_whole_header(self):
        # The header now captures a trailing group for space/tags; the body
        # must not inherit any of it.
        block = ("### a-note (kind: unknown, score=0.03 daemon-hybrid, "
                 "space: desk)\n\n# Title\n\nbody\n")
        self.assertTrue(bw.split_notes(block)[0]["body"].startswith("# Title"))


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

    def test_blind_mode_still_hides_the_verdict(self):
        # Without reasoning supplied the sheet is blind, and blind is the only
        # state that yields chance-corrected agreement. Asserted differentially:
        # checking that the word "insufficient" is absent proves nothing, since
        # it is one of the three labels the instructions offer.
        a = self._items()
        b = self._items()
        a[0].pop("judge", None)
        b[0].pop("judge", None)
        b[0]["stratum"] = "thin/sufficient"
        self.assertEqual(bw.worksheet(a, "RUBRIC.md"),
                         bw.worksheet(b, "RUBRIC.md"))
        self.assertIn("where does the vault live",
                      bw.worksheet(a, "RUBRIC.md"))

    def test_adjudication_mode_shows_the_verdict_and_why(self):
        # The operator asked for this after an unaided label missed that no
        # June-dated plan was among three retrieved plans. A label made without
        # noticing that is not ground truth either.
        item = dict(self._items()[0])
        item["judge"] = "insufficient"
        item["judge_why"] = ["no June-dated plan is present",
                             "Task 4's contents are not in the context"]
        text = "\n".join(bw.render_turn(1, item))
        self.assertIn("Machine says: insufficient", text)
        self.assertIn("no June-dated plan is present", text)
        self.assertIn("Your ruling is final", text)

    def test_the_reasoning_never_reaches_the_repo_fixture(self):
        # It quotes the operator's own request and notes by construction.
        rows = bw.fixture_rows([{"id": "x", "stratum": "full/n/a",
                                 "judge": "insufficient",
                                 "judge_why": ["quotes THE REQUEST verbatim"],
                                 "n_notes": 5, "prompt": "p", "context": "c"}])
        import json as _j
        self.assertNotIn("THE REQUEST", _j.dumps(rows))

    def test_the_assistant_reply_cannot_be_read_off_it_either(self):
        a = self._items()
        b = self._items()
        b[0]["reply"] = "THE ASSISTANT SAID THIS"
        self.assertEqual(bw.worksheet(a, "RUBRIC.md"),
                         bw.worksheet(b, "RUBRIC.md"))

    def test_the_stratum_is_never_shown(self):
        # It encodes the judge's verdict, so in blind mode it would leak one.
        a = self._items()
        a[0].pop("judge", None)
        self.assertNotIn("stratum", bw.worksheet(a, "RUBRIC.md").lower())

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
