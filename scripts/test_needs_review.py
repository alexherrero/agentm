#!/usr/bin/env python3
"""Filing v2, the write path, task 3: the stamps on every writer and the
needs-review reading over them.

The metadata soft inbox has to be readable somewhere. These tests pin the
reading — a low-confidence filing, an unfiled capture and a flagged duplicate
each appear in the generated MOC with a context phrase saying why; an entry
clears when the note is re-judged; the page is deterministic — and the stamps
every writer now leaves: a caller that names a type stands behind it, a record
keeps its own shape, an always-load rule never ages.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import capture as cap  # noqa: E402
import corpus_scorecard  # noqa: E402
import filing_engine as fe  # noqa: E402
import needs_review  # noqa: E402
import save  # noqa: E402

_NOW = datetime(2026, 9, 4, 9, 0, 0, tzinfo=timezone.utc)


def _fm(path: Path) -> dict:
    return fe._frontmatter(path.read_text(encoding="utf-8"))[0]


def _flip(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (path, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


class _Vault(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="needs-review-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "memory").mkdir(parents=True)

    def _moc(self) -> str:
        return needs_review.write(self.root, today="2026-09-04").read_text(encoding="utf-8")


class TheReading(_Vault):
    def test_a_low_confidence_filing_appears_with_a_context_phrase(self):
        save.save_entry(self.root, "preference", "short-subjects", "Prefer short commit subjects.",
                        filing_confidence="low")
        text = self._moc()
        self.assertIn("## Filed at low confidence (1)", text)
        self.assertIn("- [[short-subjects]] — short subjects · filed as preference at low confidence via conversation", text)
        s = needs_review.summary(self.root)
        self.assertEqual(s["total"], 1)
        self.assertEqual(s["by_reason"]["low-confidence"], 1)

    def test_an_unfiled_capture_is_awaiting_enrichment(self):
        r = cap.capture(self.root, "a thought worth keeping", now=_NOW)
        text = self._moc()
        self.assertIn("## Unfiled captures (1)", text)
        self.assertIn(f"- [[{r.slug}]] — ", text)
        self.assertIn("unfiled since 2026-09-04 — awaiting enrichment", text)
        # A capture is also low confidence; it is listed once, under its
        # primary reason, with both reasons in the phrase.
        self.assertEqual(text.count(f"[[{r.slug}]]"), 1)
        self.assertIn("at low confidence via operator-direct", text)

    def test_a_flagged_duplicate_names_its_twin(self):
        save.save_entry(self.root, "preference", "vault-root-outside",
                        "The vault root sits outside the checkout.")
        decision = fe.FilingDecision(
            type="preference", class_dir="memory/semantic",
            dest_rel="memory/semantic/vault-root-outside-2.md", op="add",
            related="memory/semantic/vault-root-outside.md", filing_confidence="low",
            source="conversation", flags=["near-duplicate"],
        )
        written = fe.apply(self.root, decision, body="The vault root sits outside of the checkout tree.")
        fm = _fm(written)
        self.assertEqual(fm["review_flags"], "[near-duplicate]")
        self.assertEqual(fm["related"], "memory/semantic/vault-root-outside.md")
        text = self._moc()
        self.assertIn("## Probable duplicates (1)", text)
        self.assertIn("probable duplicate of [[vault-root-outside]] — filed beside it, never merged", text)
        # The twin itself is not waiting for anything.
        self.assertNotIn("- [[vault-root-outside]] —", text)

    def test_an_entry_clears_when_the_note_is_re_judged(self):
        p = save.save_entry(self.root, "preference", "short-subjects", "Prefer short commit subjects.",
                            filing_confidence="low")
        r = cap.capture(self.root, "a thought worth keeping", now=_NOW)
        self.assertEqual(needs_review.summary(self.root)["total"], 2)
        _flip(p, "filing_confidence: low", "filing_confidence: high")
        _flip(r.path, "status: unfiled", "status: active")
        _flip(r.path, "filing_confidence: low", "filing_confidence: high")
        text = self._moc()
        self.assertIn("0 note(s) waiting", text)
        self.assertNotIn("[[short-subjects]]", text)
        self.assertNotIn(f"[[{r.slug}]]", text)

    def test_a_superseded_note_is_no_longer_waiting(self):
        p = save.save_entry(self.root, "preference", "old-value", "The port is 8901.",
                            filing_confidence="low")
        _flip(p, "lifecycle: active", "lifecycle: superseded")
        self.assertEqual(needs_review.summary(self.root)["total"], 0)

    def test_regeneration_is_deterministic_and_keeps_created(self):
        save.save_entry(self.root, "preference", "short-subjects", "Prefer short commit subjects.",
                        filing_confidence="low")
        first = needs_review.write(self.root, today="2026-09-04").read_text(encoding="utf-8")
        second = needs_review.write(self.root, today="2026-09-04").read_text(encoding="utf-8")
        self.assertEqual(first, second)
        later = needs_review.write(self.root, today="2026-09-05").read_text(encoding="utf-8")
        self.assertIn("created: 2026-09-04", later)
        self.assertIn("updated: 2026-09-05", later)

    def test_the_page_is_a_moc_that_never_lists_itself(self):
        text = self._moc()
        fm = fe._frontmatter(text)[0]
        self.assertEqual(fm["kind"], "moc")
        self.assertEqual(fm["slug"], "needs-review")
        self.assertEqual(fm["status"], "active")
        self.assertEqual(needs_review.summary(self.root)["total"], 0)
        self.assertEqual((self.root / needs_review.MOC_REL).parent.name, "mocs")

    def test_the_class_list_agrees_with_the_scorecard(self):
        self.assertEqual(needs_review.CLASS_DIRS, corpus_scorecard.CLASS_DIRS)

    def test_the_scorecard_carries_the_count(self):
        save.save_entry(self.root, "preference", "short-subjects", "Prefer short commit subjects.",
                        filing_confidence="low")
        cap.capture(self.root, "a thought worth keeping", now=_NOW)
        reading = corpus_scorecard._needs_review_reading(self.root)
        self.assertEqual(reading.value, 2)
        self.assertIn("low-confidence 2", reading.note)
        self.assertIn("unfiled 1", reading.note)
        self.assertIn(needs_review.MOC_REL, reading.note)


class TheStampsOnEveryWriter(_Vault):
    def test_a_named_type_is_filed_active_at_high_confidence_by_conversation(self):
        p = save.save_entry(self.root, "workflow", "battery-first", "Run the battery first.")
        fm = _fm(p)
        self.assertEqual(fm["lifecycle"], "active")
        self.assertEqual(fm["source"], "conversation")
        self.assertEqual(fm["filing_confidence"], "high")

    def test_a_caller_may_name_the_transport(self):
        p = save.save_entry(self.root, "reference", "fetched-page", "Body of a page.",
                            source="external-fetch")
        self.assertEqual(_fm(p)["source"], "external-fetch")

    def test_a_record_keeps_its_own_shape(self):
        p = save.save_entry(self.root, "report", "weekly-report", "What happened this week.")
        fm = _fm(p)
        for stamp in ("lifecycle", "source", "filing_confidence"):
            self.assertNotIn(stamp, fm, stamp)

    def test_an_always_load_rule_is_pinned(self):
        # The contract's word for "never decays": an always-load rule takes
        # `pinned`, not the default the writer stamps on everything else.
        p = save.save_entry(self.root, "convention", "no-trailer", "No Co-Authored-By trailer.",
                            always_load=True)
        fm = _fm(p)
        self.assertEqual(fm["lifecycle"], "pinned")
        self.assertEqual(fm["filing_confidence"], "high")

    def test_extra_fields_keep_the_locked_order(self):
        p = save.save_entry(self.root, "preference", "ordered", "x",
                            extra={"related": "memory/semantic/y.md", "via": "cli",
                                   "review_flags": ["near-duplicate"]})
        keys = [line.split(":", 1)[0] for line in p.read_text(encoding="utf-8").split("\n---\n", 1)[0].splitlines()[1:]]
        self.assertLess(keys.index("via"), keys.index("review_flags"))
        self.assertLess(keys.index("review_flags"), keys.index("related"))


if __name__ == "__main__":
    unittest.main()
