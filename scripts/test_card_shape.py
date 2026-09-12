#!/usr/bin/env python3
"""card_shape: the card's order, the naming rule, the two derivations a backfill
may make without a model, and the Go twin's lists (agentm-vault plan 06)."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_TOOLKIT) not in sys.path:
    sys.path.insert(0, str(_TOOLKIT))

import card_shape as cs  # noqa: E402


class TheCardsOrder(unittest.TestCase):
    def test_the_read_block_leads_and_the_machine_block_closes(self):
        text = ("---\ntitle: T\ntype: reference\nstatus: active\nconfidence: 0.82\nfiling_confidence: high\n"
                "enriched_by: enrich/1\nsource: conversation\nlifecycle: active\ncreated: 2026-08-12\n"
                "slug: t\n---\n\nBody.\n")
        self.assertEqual(cs.reorder(text),
                         "---\ntitle: T\ntype: reference\nstatus: active\nlifecycle: active\n"
                         "filing_confidence: high\nsource: conversation\ncreated: 2026-08-12\n"
                         "slug: t\nconfidence: 0.82\nenriched_by: enrich/1\n---\n\nBody.\n")

    def test_every_line_stays_with_its_key_and_the_body_is_untouched(self):
        text = ("---\nslug: s\nderived_from:\n  - memory/semantic/a.md\n# a comment\ntitle: T\n---\n\n"
                "Body\n\n---\n\nstatus: not a key\n")
        self.assertEqual(cs.reorder(text),
                         "---\ntitle: T\nslug: s\nderived_from:\n  - memory/semantic/a.md\n# a comment\n---\n\n"
                         "Body\n\n---\n\nstatus: not a key\n")

    def test_a_key_neither_block_names_sits_after_the_read_block_in_its_own_order(self):
        text = "---\nslug: t\nday: 2026-09-05\nkind: session-trace\nsession: abc\ntitle: T\n---\n"
        self.assertEqual(cs.reorder(text), "---\ntitle: T\nkind: session-trace\nday: 2026-09-05\nsession: abc\nslug: t\n---\n")

    def test_an_ordered_note_comes_back_as_the_same_string_and_a_second_pass_changes_nothing(self):
        ordered = "---\ntitle: T\ntype: idea\nstatus: unfiled\nslug: t\n---\nbody\n"
        self.assertIs(cs.reorder(ordered), ordered)
        once = cs.reorder("---\nslug: t\ntitle: T\n---\nbody\n")
        self.assertEqual(cs.reorder(once), once)

    def test_a_note_without_a_block_is_left_alone(self):
        for text in ("plain\n", "---\nunterminated: yes\n", "---\n---\nempty\n"):
            self.assertEqual(cs.reorder(text), text)

    def test_order_findings_name_what_is_out_of_order_and_pass_what_is_not(self):
        self.assertEqual(cs.order_findings(["title", "type", "status", "day", "slug", "staged_topic"]), [])
        self.assertTrue(cs.order_findings(["type", "title"]))
        self.assertTrue(any("after" in f for f in cs.order_findings(["title", "slug", "source"])))
        self.assertTrue(any("machine" in f for f in cs.order_findings(["title", "enriched_by", "slug"])))

    def test_lifecycle_since_sits_beside_lifecycle(self):
        self.assertEqual(cs.READ_ORDER.index("lifecycle_since"), cs.READ_ORDER.index("lifecycle") + 1)

    def test_the_two_blocks_name_each_key_once(self):
        both = cs.READ_ORDER + cs.MACHINE_ORDER
        self.assertEqual(len(both), len(set(both)))


class TheNamingRule(unittest.TestCase):
    def test_a_counter_is_told_from_a_number_that_is_part_of_the_name(self):
        counters = [
            ("never-tested-there-at-all-1", "windows-latest batch test failures fixed via stub_test.go"),
            ("back-to-this-in-a-few~dup", None),
            ("back-to-this-in-a-few~dup2", None),
            ("workflow-bash-259", None),
            ("follow-up-batch-3", None),
        ]
        names = [
            ("follow-up-batch-3", "Follow-up batch 3"),
            ("agentm-step-9-6-post-work-reflection-exit-0", "agentm step 9.6 always exits 0"),
            ("follow-ups-batch-excludes-287-288", "Follow-ups batch (excludes #287/#288)"),
            ("gemini-comms-profile-draft-2026-05-24", "Gemini comms-profile draft"),
            ("a-fetched-article-discord-chunk-10", None),
            ("2026-09-05-open-a-new-project-called-templecoordination-the-65016765", None),
            ("plain-fact", "Plain fact"),
        ]
        for stem, title in counters:
            self.assertTrue(cs.is_counter_slug(stem, title), stem)
        for stem, title in names:
            self.assertFalse(cs.is_counter_slug(stem, title), stem)

    def test_a_counter_takes_its_title_cut_at_a_word(self):
        self.assertEqual(
            cs.rename_target("budget-tokens-around-alongside-an-explicit-1",
                             "budget_tokens must be strictly less than max_tokens", "", set()),
            "budget-tokens-must-be-strictly-less-than-max-tokens")
        long = cs.rename_target("x-1", "word " * 30, "", set())
        self.assertLessEqual(len(long), cs.SLUG_MAX)
        self.assertFalse(long.endswith("-"))
        self.assertTrue(long.startswith("word-word"))

    def test_without_a_title_the_counter_comes_off_and_a_taken_name_grows_from_its_text(self):
        self.assertEqual(cs.rename_target("ntent-result-send-all-results-back-1", None, "...", set()),
                         "ntent-result-send-all-results-back")
        self.assertEqual(
            cs.rename_target("back-to-this-in-a-few~dup", None,
                             "...back to this in a few days aftger we've accumulated some data.",
                             {"back-to-this-in-a-few"}),
            "back-to-this-in-a-few-days")
        self.assertIsNone(cs.rename_target("x-1", None, "x", {"x"}))

    def test_a_writer_grows_a_taken_name_by_a_word_and_never_by_a_number(self):
        taken = {"my-slug", "port", "dup"}
        self.assertEqual(cs.free_name("fresh", ["a"], taken.__contains__), "fresh")
        self.assertEqual(cs.free_name("my-slug", ["my", "slug", "second"], taken.__contains__), "my-slug-second")
        self.assertEqual(cs.free_name("port", ["2", "daemon"], taken.__contains__), "port-daemon")
        self.assertEqual(cs.free_name("dup", ["dup"], taken.__contains__, digest="abcdef123"), "dup-abcdef")
        self.assertIsNone(cs.free_name("dup", ["dup"], taken.__contains__))
        # A one-letter word and a stopword say nothing about the note.
        self.assertEqual(cs.free_name("dup", ["a", "the", "to", "title"], taken.__contains__), "dup-title")


class TheDerivations(unittest.TestCase):
    def test_a_title_comes_only_from_a_first_line_h1(self):
        self.assertEqual(cs.title_from_body("\n# Backup NAS — secondary Unraid box\n\nText\n"),
                         "Backup NAS — secondary Unraid box")
        for body in ("## Not an H1\n", "When stripping trailers from history.\n# Later H1\n", "#hashtag\n", ""):
            self.assertIsNone(cs.title_from_body(body), body)

    def test_a_summary_comes_only_from_a_body_that_is_one_sentence(self):
        self.assertEqual(cs.summary_from_body("\nRebase a stale branch.\n"), "Rebase a stale branch.")
        for body in ("...back to this in a few days.", "persist the research you just did.",
                     "Follow-up batch 3", "One sentence.\nAnd another.", "> Quoted.", "- A list item."):
            self.assertIsNone(cs.summary_from_body(body), body)

    def test_an_empty_list_reads_as_empty(self):
        for raw in ("[]", "[ ]", '""', "''", ""):
            self.assertEqual(cs.flow_list(raw), [], raw)
        self.assertEqual(cs.flow_list('[a, "b c"]'), ["a", "b c"])


class TheGoTwin(unittest.TestCase):
    """daemon/internal/cardshape holds the same two lists; the writers on both
    sides must agree about the order or a card's fields move every night."""

    @staticmethod
    def _go_list(src: str, name: str) -> tuple:
        m = re.search(r"var " + name + r" = \[\]string\{(.*?)\n\}", src, re.S)
        return tuple(re.findall(r'"([^"]+)"', m.group(1))) if m else ()

    def test_the_go_lists_are_the_python_lists(self):
        src = (_REPO / "daemon" / "internal" / "cardshape" / "cardshape.go").read_text(encoding="utf-8")
        self.assertEqual(self._go_list(src, "ReadOrder"), cs.READ_ORDER)
        self.assertEqual(self._go_list(src, "MachineOrder"), cs.MACHINE_ORDER)
        self.assertEqual(self._go_list(src, "CollisionStopwords"), cs.COLLISION_STOPWORDS)

    def test_the_comparison_sees_a_list_that_moved(self):
        src = (_REPO / "daemon" / "internal" / "cardshape" / "cardshape.go").read_text(encoding="utf-8")
        moved = src.replace('"status", "lifecycle", "lifecycle_since"', '"status", "lifecycle_since", "lifecycle"', 1)
        self.assertNotEqual(moved, src)
        self.assertNotEqual(self._go_list(moved, "ReadOrder"), cs.READ_ORDER)


if __name__ == "__main__":
    unittest.main()
