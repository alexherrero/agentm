#!/usr/bin/env python3
"""The three card gates (agentm-vault plan 06). Each fails on a fixture that
breaks its rule and passes one that keeps it; the two with a pre-backfill state
report without failing until the backfill's marker exists. check-card-shape's
second walk, the idea cards in `personal/ideas/` (agentm-vault plan 13), has a
shape and a marker of its own."""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_REPO / "scripts"), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import card_shape as cs  # noqa: E402
import idea_cards as ic  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), _REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shape = _load("check-card-shape")
classes = _load("check-class-directories")
empty = _load("check-no-empty-tags")

CONTRACT = shape.Contract(
    {"operator-direct": "trusted", "conversation": "trusted", "external-fetch": "untrusted", "email": "untrusted"},
    0.65, {"pinned", "active", "dormant", "archived", "superseded"})

GOOD = """---
title: A good card
type: convention
summary: "One line."
status: active
lifecycle: pinned
filing_confidence: high
source: conversation
trust: trusted
created: 2026-09-01
updated: 2026-09-02
tags: [a]
slug: a-good-card
confidence: 0.9
enriched_by: enrich/1+prompt/x
enriched_at: "2026-09-12T09:00:00Z"
rules_hash: h
backfilled: [trust]
---

A body.
"""

TRACE = """---
title: A session
kind: session-trace
status: active
lifecycle: active
source: conversation
created: 2026-09-05
day: 2026-09-05
session: abc
touched: [a-good-card]
slug: 2026-09-05-a-session-65016765
---

## Asked
"""


class _Root(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for c in ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs"):
            (self.root / "memory" / c).mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> Path:
        p = self.root / "memory" / rel
        p.write_text(text, encoding="utf-8")
        return p

    def clear(self, cls: str = "semantic"):
        for p in list((self.root / "memory" / cls).iterdir()):
            p.rmdir() if p.is_dir() else p.unlink()

    def mark(self):
        (self.root / "memory" / cs.MARKER_NAME).write_text("run test\n", encoding="utf-8")

    def gate(self, fn, *args):
        out = io.StringIO()
        return fn(self.root, *args, out=out), out.getvalue()


class CardShape(_Root):
    def test_a_card_and_a_trace_in_shape_pass_with_pinned_on_the_axis(self):
        self.write("semantic/a-good-card.md", GOOD)
        self.write("episodic/2026-09-05-a-session-65016765.md", TRACE)
        self.mark()
        code, out = self.gate(shape.check, CONTRACT)
        self.assertEqual(code, 0, out)
        self.assertIn("clean", out)

    def test_before_the_marker_it_counts_and_passes(self):
        self.write("semantic/bad-card.md", "---\ntype: idea\naltitude: artifact\n---\nb\n")
        code, out = self.gate(shape.check, CONTRACT)
        self.assertEqual(code, 0, out)
        self.assertIn("pre-backfill", out)

    def test_each_rule_fails_on_its_own_breach(self):
        breaches = {
            "a missing required field": ("a-good-card", GOOD.replace("trust: trusted\n", "")),
            "a retired field": ("a-good-card", GOOD.replace("rules_hash: h\n", "rules_hash: h\naltitude: artifact\n")),
            "the read block out of order": ("a-good-card", GOOD.replace(
                "title: A good card\ntype: convention\n", "type: convention\ntitle: A good card\n")),
            "a read field after the machine block": ("a-good-card", GOOD.replace(
                "summary: \"One line.\"\n", "").replace("rules_hash: h\n", "rules_hash: h\nsummary: x\n")),
            "trust against the contract": ("a-good-card", GOOD.replace("source: conversation", "source: external-fetch")),
            "high below the floor": ("a-good-card", GOOD.replace("confidence: 0.9", "confidence: 0.40")),
            "active at low": ("a-good-card", GOOD.replace("filing_confidence: high", "filing_confidence: low")
                              .replace("confidence: 0.9", "confidence: 0.40")),
            "a lifecycle off the axis": ("a-good-card", GOOD.replace("lifecycle: pinned", "lifecycle: forgotten")),
            "a judged card without a title": ("a-good-card", GOOD.replace("title: A good card\n", "")),
            "a slug that is not the file name": ("a-good-card", GOOD.replace("slug: a-good-card", "slug: other")),
            "a counter name": ("never-tested-there-at-all-1",
                               GOOD.replace("slug: a-good-card", "slug: never-tested-there-at-all-1")),
        }
        for label, (stem, text) in breaches.items():
            with self.subTest(label):
                self.clear()
                self.write(f"semantic/{stem}.md", text)
                self.mark()
                code, out = self.gate(shape.check, CONTRACT)
                self.assertEqual(code, 1, f"{label} passed:\n{out}")

    def test_an_unjudged_card_may_wait_for_its_title_and_the_probe_names_no_transport(self):
        unjudged = (GOOD.replace("title: A good card\n", "").replace("confidence: 0.9\n", "")
                    .replace("enriched_by: enrich/1+prompt/x\n", "").replace("enriched_at: \"2026-09-12T09:00:00Z\"\n", "")
                    .replace("rules_hash: h\n", ""))
        probe = (GOOD.replace("source: conversation\n", "").replace("trust: trusted\n", "")
                 .replace("backfilled: [trust]\n", "probe: self-probe\n"))
        self.write("semantic/a-good-card.md", unjudged)
        self.write("semantic/agentm-self-probe.md", probe.replace("slug: a-good-card", "slug: agentm-self-probe"))
        self.mark()
        code, out = self.gate(shape.check, CONTRACT)
        self.assertEqual(code, 0, out)

    def test_a_record_never_carries_card_fields_and_a_trace_names_what_it_touched(self):
        for label, text in {
            "importance on a record": TRACE.replace("status: active\n", "importance: 5\nstatus: active\n"),
            "entities on a trace": TRACE.replace("touched: [a-good-card]", "entities: [a-good-card]"),
        }.items():
            with self.subTest(label):
                self.clear("episodic")
                self.write("episodic/2026-09-05-a-session-65016765.md", text)
                self.mark()
                code, out = self.gate(shape.check, CONTRACT)
                self.assertEqual(code, 1, f"{label} passed:\n{out}")


class ClassDirectories(_Root):
    REGISTERS = ({"convention", "idea"}, {"session-trace", "moc", "dir-index"})

    def test_cards_and_records_pass_and_sync_litter_is_ignored(self):
        self.write("semantic/a-good-card.md", GOOD)
        self.write("episodic/2026-09-05-a-session-65016765.md", TRACE)
        (self.root / "memory" / "semantic" / ".DS_Store").write_text("x", encoding="utf-8")
        if os.name != "nt":  # Windows refuses a carriage return in a file name
            (self.root / "memory" / "semantic" / "Icon\r").write_text("", encoding="utf-8")
        self.mark()
        code, out = self.gate(classes.check, self.REGISTERS)
        self.assertEqual(code, 0, out)

    def test_before_the_marker_it_counts_and_passes(self):
        (self.root / "memory" / "crystallized" / "good").mkdir()
        code, out = self.gate(classes.check, self.REGISTERS)
        self.assertEqual(code, 0, out)
        self.assertIn("pre-backfill", out)

    def test_each_intruder_fails(self):
        self.mark()
        intruders = {
            "a directory": lambda: (self.root / "memory" / "semantic" / "lane").mkdir(),
            "a file that is not a note": lambda: self.write("semantic/state.json", "{}"),
            "a note without frontmatter": lambda: self.write("semantic/plain.md", "just text\n"),
            "type and kind together": lambda: self.write("semantic/both.md", "---\ntype: idea\nkind: moc\n---\n"),
            "neither type nor kind": lambda: self.write("semantic/neither.md", "---\ntitle: x\n---\n"),
            "an unregistered type": lambda: self.write("semantic/odd.md", "---\ntype: tally\n---\n"),
        }
        for label, make in intruders.items():
            with self.subTest(label):
                self.clear()
                make()
                code, out = self.gate(classes.check, self.REGISTERS)
                self.assertEqual(code, 1, f"{label} passed:\n{out}")


class NoEmptyTags(_Root):
    def test_an_empty_list_fails_once_the_marker_exists_and_is_only_counted_before(self):
        for label, tags in {"[]": "tags: []\n", "quoted": 'tags: ""\n', "bare": "tags:\n"}.items():
            with self.subTest(label):
                self.clear()
                marker = self.root / "memory" / cs.MARKER_NAME
                if marker.exists():
                    marker.unlink()
                self.write("semantic/card.md", f"---\ntype: idea\n{tags}---\nbody\n")
                code, out = self.gate(empty.check)
                self.assertEqual(code, 0, out)
                self.assertIn("pre-backfill", out)
                self.mark()
                code, out = self.gate(empty.check)
                self.assertEqual(code, 1, out)

    def test_a_list_with_items_passes_in_either_form(self):
        self.write("semantic/flow.md", "---\ntype: idea\ntags: [a]\n---\nbody\n")
        self.write("semantic/block.md", "---\ntype: idea\ntags:\n  - a\n---\nbody\n")
        self.write("semantic/none.md", "---\ntype: idea\n---\nbody\n")
        self.mark()
        code, out = self.gate(empty.check)
        self.assertEqual(code, 0, out)


# An idea card as the ideas move left it: no `lifecycle`, `filing_confidence:
# high` kept below the contract's floor (ruling 6: whatever the score), and the
# machine block after the read block.
IDEA = """---
title: Port SimCity to the browser
type: idea
area: coding
summary: "One line."
status: active
filing_confidence: high
source: operator-direct
trust: trusted
created: 2026-05-20
updated: 2026-09-21
tags: [games]
slug: port-simcity
confidence: 0.45
enriched_by: enrich/1+prompt/x
enriched_at: "2026-09-22T09:00:00Z"
rules_hash: h
---

An idea.
"""

# A card made by hand, as the how-to allows: a title, the type and the group,
# plus the day it was dismissed. No status yet; the night writes `active`.
BY_HAND = """---
title: Unraid GitHub Actions runner
type: idea
area: home-tech
dismissed: 2026-05-24
---

Run CI on the home server.
"""


class IdeaCards(unittest.TestCase):
    """The idea walk, in the live layout: the memory root nested in an Obsidian
    vault, and the cards at the vault root's `personal/ideas/`, not the memory
    root's."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        (self.vault / ".obsidian").mkdir()
        self.root = self.vault / "agent"
        for c in ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs"):
            (self.root / "memory" / c).mkdir(parents=True)
        # The class walk enforces and holds nothing, so the exit code is the idea walk's.
        (self.root / "memory" / cs.MARKER_NAME).write_text("run test\n", encoding="utf-8")
        self.ideas = self.vault / "personal" / "ideas"
        self.ideas.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> None:
        (self.ideas / name).write_text(text, encoding="utf-8")

    def clear(self) -> None:
        for p in list(self.ideas.iterdir()):
            p.unlink()

    def mark(self) -> None:
        ic.marker_path(self.root).write_text("ideas move finished test\ncards 1\n", encoding="utf-8")

    def gate(self):
        out = io.StringIO()
        return shape.check(self.root, CONTRACT, out=out), out.getvalue()

    def test_an_idea_card_in_shape_passes_where_the_class_rules_would_fail_it(self):
        self.write("port-simcity.md", IDEA)
        self.write("unraid-runner.md", BY_HAND)
        self.mark()
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("clean — 2 idea card(s)", out)
        # The class card's rules would fail the same card twice over, which is
        # why the idea walk has its own: they require `lifecycle`, and they hold
        # `filing_confidence` to the floor that ruling 6 sets aside.
        as_class = shape.note_findings("memory/semantic/port-simcity.md", IDEA, CONTRACT)
        self.assertTrue(any("missing `lifecycle`" in f for f in as_class), as_class)
        self.assertTrue(any("the floor is" in f for f in as_class), as_class)

    def test_before_the_marker_it_lists_each_finding_and_passes(self):
        self.write("port-simcity.md", IDEA.replace("area: coding\n", "")
                   .replace("status: active\n", "status: active\nlifecycle: active\n"))
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("idea cards report only — 2 finding(s) over 1 card(s)", out)
        self.assertIn(f"enforced once memory/{ic.MARKER_NAME} exists", out)
        self.assertIn("pending: personal/ideas/port-simcity.md: missing `area`", out)
        self.assertIn("pending: personal/ideas/port-simcity.md: carries `lifecycle`", out)

    def test_each_rule_fails_on_its_own_breach_and_names_the_file_and_field(self):
        breaches = {
            "lifecycle": (IDEA.replace("status: active\n", "status: active\nlifecycle: active\n"),
                          "carries `lifecycle`"),
            "lifecycle_since": (IDEA.replace("filing_confidence: high\n",
                                             "lifecycle_since: 2026-09-01\nfiling_confidence: high\n"),
                                "carries `lifecycle_since`"),
            "a status other than active": (IDEA.replace("status: active", "status: dormant"),
                                           "`status: dormant`"),
            "no area": (IDEA.replace("area: coding\n", ""), "missing `area`"),
            "an empty area": (IDEA.replace("area: coding", 'area: ""'), "missing `area`"),
            "another type": (IDEA.replace("type: idea", "type: reference"), "`type: reference`"),
            "a record": (IDEA.replace("type: idea", "kind: session-trace"), "`kind: session-trace`"),
            "no type": (IDEA.replace("type: idea\n", ""), "no `type`"),
            "no frontmatter": ("An idea with no block.\n", "no frontmatter block"),
            "a dismissal that is not a day": (IDEA.replace('summary: "One line."\n',
                                                           'summary: "One line."\ndismissed: someday\n'),
                                              "`dismissed: someday`"),
            "the read block out of order": (IDEA.replace("title: Port SimCity to the browser\ntype: idea\n",
                                                         "type: idea\ntitle: Port SimCity to the browser\n"),
                                            "out of order"),
            "a retired field": (IDEA.replace("rules_hash: h\n", "rules_hash: h\ngroup: coding\n"),
                                "retired field(s) ['group']"),
        }
        for label, (text, words) in breaches.items():
            with self.subTest(label):
                self.clear()
                self.write("port-simcity.md", text)
                self.mark()
                code, out = self.gate()
                self.assertEqual(code, 1, f"{label} passed:\n{out}")
                self.assertTrue(any("personal/ideas/port-simcity.md" in line and words in line
                                    for line in out.splitlines()),
                                f"{label}: no finding names the file and {words!r}:\n{out}")

    def test_a_card_is_a_markdown_file_directly_in_the_folder(self):
        # Dotfiles (the inbox's temporary name), a subfolder the operator keeps
        # for their own reasons and a file that is not a note are not cards:
        # the set enrich.IsIdeaCard and the dreaming binary read.
        stray = "---\ntype: reference\nlifecycle: active\n---\nnot an idea\n"
        self.write("port-simcity.md", IDEA)
        self.write(".port-simcity.md.tmp", stray)
        self.write(".draft.md", stray)
        (self.ideas / "later").mkdir()
        (self.ideas / "later" / "old.md").write_text(stray, encoding="utf-8")
        self.write("sketch.png", "not a note")
        self.mark()
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("clean — 1 idea card(s)", out)

    def test_a_card_the_writer_files_has_the_shape_the_gate_holds(self):
        # as_idea_card is what the inbox and the move both write through: a
        # class card carrying `lifecycle: pinned` comes out in the idea card's
        # shape, so the writer and the gate agree.
        card = ic.as_idea_card(GOOD.replace("type: convention", "type: idea"), area="coding",
                               dismissed="2026-09-01", today="2026-09-22")
        self.write("a-good-card.md", card)
        self.mark()
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("clean — 1 idea card(s)", out)

    def test_with_no_folder_it_says_so_and_passes(self):
        self.ideas.rmdir()
        self.mark()
        code, out = self.gate()
        self.assertEqual(code, 0, out)
        self.assertIn("no idea card to check", out)


if __name__ == "__main__":
    unittest.main()
