#!/usr/bin/env python3
"""Tests for consolidate.py — V6-4 episodic->semantic consolidation
(PLAN-wave-e-v6-index task 7).

Fully synthetic fixture vaults (tmp dirs) — this module never touches the
real vault; actually running consolidation against real content is an
operator-invoked pass (matching the dreaming pipeline's own staged/
confirmed posture for corpus-mutating passes), not something this test
suite or the module itself does autonomously.

Covers:
  - find_recurring_targets(): deterministic recurrence grouping + threshold.
  - consolidate_target(): writes the shared crystallization schema with
    derived_from + lifecycle_tier: durable; raises below the recurrence
    floor; raises FileExistsError on a duplicate target.
  - THE RED-TEST this task's plan verification names explicitly: no
    consolidation write bypasses the revert-log — proven by reverting the
    run and asserting the consolidated file is gone (only possible if the
    write was genuinely journaled, not a direct save_entry/Path.write).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import consolidate  # noqa: E402
from revert_log import RevertLog  # noqa: E402
from crystallize import parse_digest, DIGEST_KIND as DIGEST_KIND_DIR  # noqa: E402


def _write_entry(vault: Path, rel: str, body: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nkind: insight\nstatus: active\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
        "tags: []\ngroup: personal\nslug: " + Path(rel).stem + "\nalways_load: false\n---\n\n"
        + body + "\n",
        encoding="utf-8",
    )


class TestFindRecurringTargets(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_target_below_recurrence_floor_not_returned(self):
        _write_entry(self.vault, "personal/insight/a.md", "See [[shared-topic]] for context.")
        _write_entry(self.vault, "personal/insight/b.md", "See [[shared-topic]] again.")
        recurring = consolidate.find_recurring_targets(
            self.vault, ["personal/insight/a.md", "personal/insight/b.md"], min_recurrence=3,
        )
        self.assertEqual(recurring, {})

    def test_target_at_recurrence_floor_is_returned(self):
        for name in ("a", "b", "c"):
            _write_entry(self.vault, f"personal/insight/{name}.md", "See [[shared-topic]] here.")
        paths = [f"personal/insight/{n}.md" for n in ("a", "b", "c")]
        recurring = consolidate.find_recurring_targets(self.vault, paths, min_recurrence=3)
        self.assertIn("shared-topic", recurring)
        self.assertEqual(recurring["shared-topic"], sorted(paths))

    def test_non_edge_matches_excluded_from_recurrence(self):
        # Code-block placeholders (graph.py's own trap-rejection) must not
        # count toward recurrence, even if they'd superficially repeat.
        for name in ("a", "b", "c"):
            _write_entry(self.vault, f"personal/insight/{name}.md", "`- relation_type [[Target]]`")
        paths = [f"personal/insight/{n}.md" for n in ("a", "b", "c")]
        recurring = consolidate.find_recurring_targets(self.vault, paths, min_recurrence=3)
        self.assertEqual(recurring, {})


class TestConsolidateTarget(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)
        self.log_root = Path(self._tmp.name) / "revert-log"
        self.revert_log = RevertLog(self.vault, log_root=self.log_root)
        self.sources = [f"personal/insight/{n}.md" for n in ("a", "b", "c")]
        for rel in self.sources:
            _write_entry(self.vault, rel, "See [[shared-topic]] here.")

    def tearDown(self):
        self._tmp.cleanup()

    def test_raises_below_recurrence_floor(self):
        with self.assertRaises(ValueError):
            consolidate.consolidate_target(
                self.vault, self.revert_log, "run1", "shared-topic", self.sources[:2],
            )

    def test_writes_consolidated_entry_with_provenance_and_durable_tier(self):
        consolidate.consolidate_target(
            self.vault, self.revert_log, "run1", "shared-topic", self.sources,
        )
        target_path = self.vault / "personal" / DIGEST_KIND_DIR / "consolidated-shared-topic.md"
        self.assertTrue(target_path.exists())
        content = target_path.read_text(encoding="utf-8")
        self.assertIn("lifecycle_tier: durable", content)
        self.assertIn("derived_from:", content)
        for rel in self.sources:
            self.assertIn(rel, content)

    def test_sources_never_deleted_or_modified(self):
        originals = {rel: (self.vault / rel).read_text(encoding="utf-8") for rel in self.sources}
        consolidate.consolidate_target(
            self.vault, self.revert_log, "run1", "shared-topic", self.sources,
        )
        for rel in self.sources:
            self.assertTrue((self.vault / rel).exists())
            self.assertEqual((self.vault / rel).read_text(encoding="utf-8"), originals[rel])

    def test_duplicate_target_raises_file_exists(self):
        consolidate.consolidate_target(
            self.vault, self.revert_log, "run1", "shared-topic", self.sources,
        )
        with self.assertRaises(FileExistsError):
            consolidate.consolidate_target(
                self.vault, self.revert_log, "run2", "shared-topic", self.sources,
            )

    def test_digest_round_trips_through_shared_schema(self):
        # Reuses crystallize.py's parse_digest — proves the shared schema
        # (not a redefined one) actually round-trips for a consolidation
        # entry, not just an "exploration" one.
        consolidate.consolidate_target(
            self.vault, self.revert_log, "run1", "shared-topic", self.sources,
        )
        target_path = self.vault / "personal" / DIGEST_KIND_DIR / "consolidated-shared-topic.md"
        digest = parse_digest(target_path)
        self.assertIn("shared-topic", digest.question)
        self.assertIn("3", digest.findings)

    def test_no_consolidation_write_bypasses_the_revert_log(self):
        """THE red-test this task's plan verification names explicitly."""
        entry_id = consolidate.consolidate_target(
            self.vault, self.revert_log, "run1", "shared-topic", self.sources,
        )
        target_path = self.vault / "personal" / DIGEST_KIND_DIR / "consolidated-shared-topic.md"
        self.assertTrue(target_path.exists())

        # If this write went through revert_log.record_and_apply (not a
        # direct save_entry/Path.write bypassing it), reverting THIS SAME
        # entry_id via THIS SAME RevertLog instance must remove the file —
        # a write that bypassed the journal would leave the file behind
        # (nothing was ever recorded to revert).
        self.revert_log.revert("run1", entry_id=entry_id)
        self.assertFalse(
            target_path.exists(),
            "consolidated entry survived a revert-log revert() call — "
            "the write must have bypassed record_and_apply's journal",
        )
        # Sources are untouched by the revert too (revert only undoes what
        # was journaled — the new file's creation, not the pre-existing
        # sources it was never asked to touch).
        for rel in self.sources:
            self.assertTrue((self.vault / rel).exists())


if __name__ == "__main__":
    unittest.main()
