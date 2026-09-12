#!/usr/bin/env python3
"""The three card gates (agentm-vault plan 06). Each fails on a fixture that
breaks its rule and passes one that keeps it; the two with a pre-backfill state
report without failing until the backfill's marker exists."""
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


if __name__ == "__main__":
    unittest.main()
