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

import json
import os
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
import dream  # noqa: E402
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
        # No dream findings: these tests read the notes' own marks, and must
        # not pick up whatever a real cycle left in the engine state.
        return needs_review.write(self.root, today="2026-09-04",
                                  proposals={}).read_text(encoding="utf-8")


class TheReading(_Vault):
    def test_a_low_confidence_filing_appears_with_a_context_phrase(self):
        save.save_entry(self.root, "preference", "short-subjects", "Prefer short commit subjects.",
                        filing_confidence="low", status="unfiled")
        text = self._moc()
        # A note the writer could not place is `unfiled` as well as low, so it
        # is listed once, under the first reason it carries — and still
        # explains itself: the confidence phrase rides along either way.
        self.assertIn("## Unfiled captures (1)", text)
        self.assertIn("- [[short-subjects]] — short subjects · unfiled since ", text)
        self.assertIn("filed as preference at low confidence via conversation", text)
        s = needs_review.summary(self.root)
        self.assertEqual(s["total"], 1)
        self.assertEqual(s["by_reason"]["low-confidence"], 1)

    def test_a_legacy_active_low_confidence_note_still_reads_as_low_confidence(self):
        """The corpus predates the one-meaning rule: 74 notes sit `active` at
        low confidence, and the card backfill re-stamps them. Until it does,
        the reading has to keep listing them under their own section — a
        section no *new* write can reach is still the section they land in."""
        p = save.save_entry(self.root, "preference", "legacy-note", "Prefer short commit subjects.")
        _flip(p, "filing_confidence: high", "filing_confidence: low")
        text = self._moc()
        self.assertIn("## Filed at low confidence (1)", text)
        self.assertIn("- [[legacy-note]] — legacy note · filed as preference at low confidence via conversation", text)

    def test_an_unfiled_capture_is_awaiting_the_batch(self):
        r = cap.capture(self.root, "a thought worth keeping", now=_NOW)
        text = self._moc()
        self.assertIn("## Unfiled captures (1)", text)
        self.assertIn(f"- [[{r.slug}]] — ", text)
        self.assertIn("unfiled since 2026-09-04 — awaiting the batch", text)
        # A capture is also low confidence; it is listed once, under its
        # primary reason, with both reasons in the phrase.
        self.assertEqual(text.count(f"[[{r.slug}]]"), 1)
        self.assertIn("at low confidence via operator-direct", text)

    def test_an_unfiled_note_the_batch_judged_says_so(self):
        # Plan 04: the batch scores a note and leaves it unfiled when it lands
        # under the floor. That note is not waiting for the batch any more; a
        # judgment is on record and the move is the reviewer's. The two must
        # read differently, or the page tells you to wait for something that
        # already happened.
        r = cap.capture(self.root, "a thought worth keeping", now=_NOW)
        _flip(r.path, "status: unfiled\n", "status: unfiled\nenriched_at: 2026-09-10T02:14:00Z\n")
        text = self._moc()
        self.assertIn("unfiled since 2026-09-04 — judged below the floor on 2026-09-10", text)
        self.assertNotIn("awaiting the batch", text)

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
        # A low-confidence note is `unfiled` by contract now — `active` at low
        # confidence is a state no writer may land in — so re-judging it means
        # raising both stamps, exactly as it does for the capture below.
        p = save.save_entry(self.root, "preference", "short-subjects", "Prefer short commit subjects.",
                            filing_confidence="low", status="unfiled")
        r = cap.capture(self.root, "a thought worth keeping", now=_NOW)
        self.assertEqual(needs_review.summary(self.root)["total"], 2)
        _flip(p, "filing_confidence: low", "filing_confidence: high")
        _flip(p, "status: unfiled", "status: active")
        _flip(r.path, "status: unfiled", "status: active")
        _flip(r.path, "filing_confidence: low", "filing_confidence: high")
        text = self._moc()
        self.assertIn("0 note(s) waiting", text)
        self.assertNotIn("[[short-subjects]]", text)
        self.assertNotIn(f"[[{r.slug}]]", text)

    def test_a_superseded_note_is_no_longer_waiting(self):
        p = save.save_entry(self.root, "preference", "old-value", "The port is 8901.",
                            filing_confidence="low", status="unfiled")
        _flip(p, "lifecycle: active", "lifecycle: superseded")
        self.assertEqual(needs_review.summary(self.root)["total"], 0)

    def test_regeneration_is_deterministic_and_keeps_created(self):
        save.save_entry(self.root, "preference", "short-subjects", "Prefer short commit subjects.",
                        filing_confidence="low", status="unfiled")
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

    def test_the_proposals_file_name_agrees_with_the_cycle(self):
        self.assertEqual(needs_review.REVIEW_PROPOSALS_NAME, dream.REVIEW_PROPOSALS_NAME)

    def test_the_class_list_agrees_with_the_scorecard(self):
        self.assertEqual(needs_review.CLASS_DIRS, corpus_scorecard.CLASS_DIRS)

    def test_the_scorecard_carries_the_count(self):
        save.save_entry(self.root, "preference", "short-subjects", "Prefer short commit subjects.",
                        filing_confidence="low", status="unfiled")
        cap.capture(self.root, "a thought worth keeping", now=_NOW)
        reading = corpus_scorecard._needs_review_reading(self.root)
        self.assertEqual(reading.value, 2)
        # Two notes, each carrying both reasons: a low-confidence note is
        # `unfiled` now, so the two counts move together rather than apart.
        self.assertIn("low-confidence 2", reading.note)
        self.assertIn("unfiled 2", reading.note)
        self.assertIn(needs_review.MOC_REL, reading.note)



class TheDreamSections(_Vault):
    """Plan 04: dedup at 0.92, contradiction triage and facet promotion stay in
    the cycle, but as sections of this page. The cycle writes its findings to
    the engine state; the page reads them and acts on none of them."""

    def setUp(self):
        super().setUp()
        self.state = self.root / "state"
        old = os.environ.get("AGENTM_STATE_DIR")
        os.environ["AGENTM_STATE_DIR"] = str(self.state)
        self.addCleanup(lambda: os.environ.__setitem__("AGENTM_STATE_DIR", old)
                        if old is not None else os.environ.pop("AGENTM_STATE_DIR", None))

    def _cycle_writes(self, proposals):
        # The real writer, so the page is tested against what the cycle
        # actually leaves rather than a hand-made copy of its shape.
        return dream._write_review_proposals("dream-test", proposals, 1788998400.0)

    def _proposals(self):
        return [
            dream.Proposal(stage="dedup", kind="possible-twin",
                           paths=["memory/semantic/port-a.md", "memory/semantic/port-b.md"],
                           summary="port-a.md and port-b.md are 94% alike",
                           detail={"similarity": 0.94, "a": "memory/semantic/port-a.md",
                                   "b": "memory/semantic/port-b.md", "a_title": "a", "b_title": "b"}),
            dream.Proposal(stage="contradiction_triage", kind="same-key",
                           paths=["memory/semantic/editor.md", "memory/procedural/editor.md"],
                           summary="2 notes share the key 'editor' with different bodies",
                           detail={"slug": "editor", "titles": ["editor", "editor"]}),
            dream.Proposal(stage="facet_promotion", kind="proposed-facet",
                           paths=["standards/storage-rules.md"],
                           summary="the diary carries `garden` on 3 days",
                           detail={"label": "garden", "days": 3, "first": "2026-09-01",
                                   "last": "2026-09-08", "entries": 4, "sample": "tomatoes"}),
        ]

    def test_twins_shared_keys_and_facets_each_get_a_section(self):
        self._cycle_writes(self._proposals())
        text = needs_review.write(self.root, today="2026-09-10").read_text(encoding="utf-8")
        self.assertIn("## Possible twins (1)", text)
        self.assertIn("- [[port-a]] and [[port-b]] — 94% alike · merge by hand", text)
        self.assertIn("## Shared keys, different bodies (1)", text)
        self.assertIn("- key `editor`: [[editor]], [[editor]]", text)
        self.assertIn("## Proposed facets (1)", text)
        self.assertIn("- `garden` on 3 days (2026-09-01 … 2026-09-08)", text)
        self.assertIn("from the dream cycle of 2026-09-10", text)

    def test_the_summary_counts_the_findings_apart_from_the_notes(self):
        self._cycle_writes(self._proposals())
        s = needs_review.summary(self.root)
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["dreaming"], {"twins": 1, "same_key": 1, "facets": 1})

    def test_no_findings_file_means_no_dream_sections(self):
        text = needs_review.write(self.root, today="2026-09-10").read_text(encoding="utf-8")
        self.assertNotIn("dream cycle", text)
        self.assertNotIn("## Possible twins", text)
        self.assertEqual(needs_review.summary(self.root)["dreaming"],
                         {"twins": 0, "same_key": 0, "facets": 0})

    def test_a_malformed_findings_file_reads_as_none(self):
        target = self.state / "dreaming" / needs_review.REVIEW_PROPOSALS_NAME
        target.parent.mkdir(parents=True)
        target.write_text("{not json", encoding="utf-8")
        self.assertEqual(needs_review.read_proposals()["twins"], [])
        target.write_text(json.dumps({"twins": "not a list", "facets": [1, {"label": "x"}]}),
                          encoding="utf-8")
        got = needs_review.read_proposals()
        self.assertEqual(got["twins"], [])
        self.assertEqual(got["facets"], [{"label": "x"}])

    def test_the_page_writes_to_no_note(self):
        p = save.save_entry(self.root, "preference", "port-a", "The port is 8901.")
        before = p.read_bytes()
        self._cycle_writes(self._proposals())
        needs_review.write(self.root, today="2026-09-10")
        self.assertEqual(p.read_bytes(), before)

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


# Every test here gets its own engine state dir: the map reads the dream
# cycle's findings from it, and a hand run must not read the operator's.
import os.path as _osp  # noqa: E402
import sys as _sys  # noqa: E402

if _osp.dirname(_osp.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _osp.dirname(_osp.abspath(__file__)))
from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main()
