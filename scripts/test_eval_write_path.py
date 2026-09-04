#!/usr/bin/env python3
"""The labeled-sample eval harness (filing v2, the write path, task 5): the
worksheet carries every decision with its provenance and an empty label; the
scorer counts labels with n and refuses a half-filled sheet."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (_SKILL, _HERE / "health"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import eval_write_path as ev  # noqa: E402
import reflect  # noqa: E402
import save  # noqa: E402


def _cand(slug, body, *, category="preferences", confidence="HIGH"):
    return reflect.Candidate(category=category, confidence=confidence, slug=slug,
                             title=slug.replace("-", " "), body=body, rationale="test", excerpts=[])


class TheWorksheet(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="eval-wp-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "memory").mkdir(parents=True)

    def test_rows_are_decided_read_only_against_the_corpus(self):
        home = save.save_entry(self.root, "preference", "short-subjects", "Prefer short commit subjects.")
        before = sorted(p.as_posix() for p in (self.root / "memory").rglob("*.md"))
        rows = ev.build_rows([
            _cand("short-subjects", "Prefer short commit subjects."),          # an exact twin
            _cand("battery-first", "Run the battery first.", category="workflows"),
        ], self.root, search=lambda q: [])
        after = sorted(p.as_posix() for p in (self.root / "memory").rglob("*.md"))
        self.assertEqual(before, after, "the eval must not write")
        self.assertEqual(rows[0]["op"], "noop")
        self.assertEqual(rows[0]["dest"], home.relative_to(self.root).as_posix())
        self.assertEqual((rows[1]["op"], rows[1]["type"], rows[1]["class"]), ("add", "workflow", "memory/procedural"))

    def test_the_sheet_names_its_inputs_and_leaves_every_label_empty(self):
        rows = ev.build_rows([_cand("a", "Alpha."), _cand("b", "Beta.")], self.root, search=lambda q: [])
        text = ev.render(rows, transcript=Path("/tmp/abcdef12-session.jsonl"), messages=42,
                         provenance={"memory_root": str(self.root), "contract_hash": "deadbeef",
                                     "index_documents": 3134}, today="2026-09-04")
        self.assertIn("kind: report", text)
        self.assertIn("contract hash: `deadbeef`", text)
        self.assertIn("index documents at build time: 3134", text)
        self.assertIn("judge: none", text)
        self.assertEqual(text.count("\nlabel: \n"), 2)
        self.assertIn("### 1. a", text)
        self.assertIn("### 2. b", text)


class TheScorer(unittest.TestCase):
    def _sheet(self, labels):
        text = "# x\n\n## Decisions\n\n" + "".join(
            f"### {i}. row {i}\n\n- decision: add\n\nlabel: {l}\n\n" for i, l in enumerate(labels, 1))
        p = Path(tempfile.mkdtemp(prefix="eval-score-")) / "sheet.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_agreement_is_a_count_with_n_and_an_interval(self):
        r = ev.score(self._sheet(["right", "right", "wrong-type", "right", "should-not-file", "right"]))
        self.assertEqual((r["n"], r["right"]), (6, 4))
        self.assertAlmostEqual(r["agreement"], 4 / 6)
        lo, hi = r["wilson95"]
        self.assertLess(lo, 4 / 6)
        self.assertGreater(hi, 4 / 6)
        self.assertEqual(r["by_label"]["wrong-type"], 1)

    def test_a_half_filled_sheet_is_not_scorable(self):
        with self.assertRaises(ValueError):
            ev.score(self._sheet(["right", "", "right"]))

    def test_an_unknown_label_is_not_scorable(self):
        with self.assertRaises(ValueError):
            ev.score(self._sheet(["right", "meh"]))

    def test_a_label_with_a_note_and_the_operators_synonyms_score(self):
        # What an actual labelling produced: notes after a dash, `do-not-file`
        # for should-not-file, and "not sure" for a doubt.
        r = ev.score(self._sheet(["right", "wrong-type - not a preference, just a fact",
                                  "do-not-file", "not sure - this doesn't look like my own writing",
                                  "not sure", "do-not-record"]))
        self.assertEqual((r["n"], r["right"]), (6, 1))
        self.assertEqual(r["by_label"]["should-not-file"], 2)
        self.assertEqual(r["by_label"]["wrong-type"], 1)
        self.assertEqual(r["by_label"]["unsure"], 2)

    def test_wilson_is_bounded(self):
        self.assertEqual(ev.wilson(0, 0), (0.0, 0.0))
        lo, hi = ev.wilson(10, 10)
        self.assertEqual(hi, 1.0)
        self.assertGreater(lo, 0.7)


if __name__ == "__main__":
    unittest.main()
