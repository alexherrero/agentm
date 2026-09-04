#!/usr/bin/env python3
"""Filing v2, the write path, task 2: every writer files through the engine.

The reflect hook's routing lanes, the capture front door, and the ingest sweep
used to meet in `memory/_inbox/`. Now a candidate lands at the class directory
the contract routes its type to, carrying the write-time stamps — `lifecycle`,
`source`, `filing_confidence` — and the low-confidence ones are the inbox: a
reading over metadata, not a directory. These tests pin that contract from
each writer's side and check the ingest sweep still finds what it drains.
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import capture as cap  # noqa: E402
import ingest_sweep  # noqa: E402
import reflect  # noqa: E402

_NOW = datetime(2026, 9, 4, 9, 0, 0, tzinfo=timezone.utc)


def _cand(body, *, confidence="LOW", category="preferences", slug="a-slug",
          title="A title"):
    return reflect.Candidate(
        category=category, confidence=confidence, slug=slug,
        title=title, body=body, rationale="test", excerpts=[],
    )


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    block = text.split("\n---\n", 1)[0][4:]
    out = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


class _Vault(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="write-path-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "memory").mkdir(parents=True)

    def _no_staging_dir(self):
        self.assertFalse((self.root / "memory" / "_inbox").exists(),
                         "a staging directory came into being")

    def _filed(self):
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in (self.root / "memory").rglob("*.md")
            if p.name != "_index.md"
        )


class TheReflectLanesFileAtClass(_Vault):
    def _route(self, cands, ideas=(), **kw):
        kw.setdefault("mode", reflect.ROUTE_MODE_AUTO)
        return reflect.route_candidates(
            cands, list(ideas), vault=self.root,
            stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO(), **kw,
        )

    def test_a_high_candidate_lands_in_its_class_with_the_stamps(self):
        stats = self._route([_cand("User stated: always run the battery first.",
                                   confidence="HIGH", category="workflows", slug="battery-first")])
        self.assertEqual(stats["auto_saved"], 1, stats)
        self.assertEqual(self._filed(), ["memory/procedural/battery-first.md"])
        fm = _frontmatter(self.root / "memory/procedural/battery-first.md")
        self.assertEqual(fm["type"], "workflow")
        self.assertEqual(fm["lifecycle"], "active")
        self.assertEqual(fm["source"], "conversation")
        self.assertEqual(fm["filing_confidence"], "high")
        self._no_staging_dir()

    def test_a_low_candidate_is_filed_flagged_not_staged(self):
        # The soft inbox: the note is home, and `filing_confidence: low` is
        # what the needs-review reading selects on.
        stats = self._route([_cand("User stated: the vault root sits outside the checkout.",
                                   confidence="LOW", slug="vault-root-outside")])
        self.assertEqual(stats["filed_low"], 1, stats)
        self.assertEqual(stats["auto_saved"], 0)
        self.assertEqual(self._filed(), ["memory/semantic/vault-root-outside.md"])
        fm = _frontmatter(self.root / "memory/semantic/vault-root-outside.md")
        self.assertEqual(fm["type"], "preference")
        self.assertEqual(fm["filing_confidence"], "low")
        self.assertEqual(fm["status"], "active")
        self._no_staging_dir()

    def test_a_machine_session_is_a_tag_and_the_transport_stays_conversation(self):
        # L1 ruling 8 asked for the origin on every low-confidence entry so a
        # bulk review can batch a flood. `source:` is the contract's transport
        # vocabulary now, so the origin rides as a tag instead.
        self._route([_cand("User stated: prefer short commit subjects.", slug="short-subjects")],
                    source="machine-session")
        fm = _frontmatter(self.root / "memory/semantic/short-subjects.md")
        self.assertEqual(fm["source"], "conversation")
        self.assertIn("machine-session", fm.get("tags", ""))

    def test_the_per_session_cap_still_bounds_low_confidence_filings(self):
        cands = [_cand(f"User stated: observation number {i}.", slug=f"obs-{i}") for i in range(4)]
        stats = self._route(cands, max_inbox=2)
        self.assertEqual(stats["filed_low"], 2, stats)
        self.assertEqual(stats["capped"], 2, stats)
        self.assertEqual(len(self._filed()), 2)

    def test_a_repeat_is_a_noop_that_reinforces_not_a_second_file(self):
        c = _cand("User stated: the build machine has two GPUs.", slug="two-gpus")
        self._route([c])
        stats = self._route([_cand("User stated: the build machine has two GPUs.", slug="two-gpus")])
        self.assertEqual(stats["deduped"], 1, stats)
        self.assertEqual(stats["filed_low"], 0)
        self.assertEqual(self._filed(), ["memory/semantic/two-gpus.md"])

    def test_an_idea_files_as_type_idea(self):
        idea = _cand("What if the digest ran per project?", category="idea", slug="digest-per-project")
        stats = self._route([], ideas=[idea])
        self.assertEqual(stats["ideas_filed"], 1, stats)
        self.assertEqual(self._filed(), ["memory/semantic/digest-per-project.md"])
        fm = _frontmatter(self.root / "memory/semantic/digest-per-project.md")
        self.assertEqual(fm["type"], "idea")
        self.assertEqual(fm["filing_confidence"], "low")
        self._no_staging_dir()


class TheCaptureFrontDoorFilesAtClass(_Vault):
    def test_a_plain_capture_is_unfiled_at_the_default_class(self):
        r = cap.capture(self.root, "a thought worth keeping", now=_NOW)
        self.assertTrue(r.success, r.error)
        self.assertEqual(self._filed(), ["memory/semantic/" + r.path.name])
        fm = _frontmatter(r.path)
        self.assertEqual(fm["status"], "unfiled")
        self.assertEqual(fm["lifecycle"], "active")
        self.assertEqual(fm["source"], "operator-direct")
        self.assertEqual(fm["filing_confidence"], "low")
        self.assertTrue(fm["captured"].startswith("2026-09-04T09:00:00"), fm["captured"])
        self._no_staging_dir()

    def test_a_link_capture_is_external_fetch_and_keeps_its_url(self):
        r = cap.capture(self.root, "worth reading", source_url="https://example.com/a", now=_NOW)
        fm = _frontmatter(r.path)
        self.assertEqual(fm["source"], "external-fetch")
        self.assertEqual(fm["source_url"], "https://example.com/a")

    def test_instructions_survive_verbatim(self):
        text = 'file under: ideas; tag "later" #priority'
        r = cap.capture(self.root, "x", instructions=text, now=_NOW)
        import json
        fm = _frontmatter(r.path)
        self.assertEqual(json.loads(fm["instructions"]), text)

    def test_an_exact_repeat_reinforces_the_note_already_home(self):
        r1 = cap.capture(self.root, "the same thought", now=_NOW)
        r2 = cap.capture(self.root, "the same thought", now=_NOW)
        self.assertTrue(r2.deduplicated)
        self.assertEqual(r1.path, r2.path)
        self.assertEqual(len(self._filed()), 1)


class TheIngestSweepReadsTheClassDirectories(_Vault):
    def test_a_class_filed_link_capture_is_found_and_staged(self):
        r = cap.capture(self.root, "worth reading", source_url="https://example.com/a", now=_NOW)
        self.assertIn(r.path, ingest_sweep._iter_inbox_candidates(self.root))
        html = "<html><head><title>An article</title></head><body><p>Body text here.</p></body></html>"
        with mock.patch.object(ingest_sweep.ingest, "fetch_url", return_value=html):
            result = ingest_sweep.run_ingest_sweep(self.root)
        self.assertEqual([Path(p) for p in result.fetched], [r.path], result)
        self.assertEqual(_frontmatter(r.path)["status"], "ingest_staged")

    def test_a_plain_unfiled_capture_is_walked_and_left_alone(self):
        # Not a link, not a clip, not an idea: the sweep walks it (the restamp
        # and the act step reach every unreviewed capture) and changes nothing.
        r = cap.capture(self.root, "just a thought", now=_NOW)
        self.assertIn(r.path, ingest_sweep._iter_inbox_candidates(self.root))
        before = r.path.read_text(encoding="utf-8")
        import os
        os.utime(r.path, (_NOW.timestamp(), _NOW.timestamp()))
        result = ingest_sweep.run_ingest_sweep(self.root, now=_NOW.timestamp())
        self.assertEqual(r.path.read_text(encoding="utf-8"), before)
        self.assertEqual(result.fetched + result.staged_clips + result.promoted, [])
        self.assertEqual(_frontmatter(r.path)["status"], "unfiled")

    def test_a_legacy_inbox_is_still_read_while_it_exists(self):
        inbox = self.root / "memory" / "_inbox"
        inbox.mkdir()
        p = inbox / "old-link.md"
        p.write_text("---\nkind: capture\nstatus: inbox\nslug: old-link\n"
                     "source_url: https://example.com/old\n---\n\nleft over\n", encoding="utf-8")
        self.assertIn(p, ingest_sweep._iter_inbox_candidates(self.root))


if __name__ == "__main__":
    unittest.main()
