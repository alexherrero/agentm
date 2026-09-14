#!/usr/bin/env python3
"""The shipped retrieval eval translates a gold expectation pinned at its
pre-migration path through the corpus migration's own disposition reports —
exact, per note — and compares everything else as it is. A basename fold
would have called two different notes that shared a name in different month
buckets the same note (the pre-tag review's finding); the reports say which
file went where.

Run: python3 scripts/test_eval_retrieval_shipped_canon.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for p in (_HERE, _HERE / "health"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import eval_retrieval_shipped as ev  # noqa: E402


def _report(root: Path, run: str, rows: list) -> None:
    d = root / "diagnostics" / "migrations" / "corpus-migration-3" / run
    d.mkdir(parents=True)
    lines = ["path,population,disposition,dest"] + [",".join(r) for r in rows]
    (d / "dispositions.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


class MigratedPaths(unittest.TestCase):
    def _root(self, td: Path) -> Path:
        vault = td / "Vault"
        (vault / ".obsidian").mkdir(parents=True)
        root = vault / "agent"
        root.mkdir()
        return root

    def test_a_pinned_path_translates_to_the_destination_the_report_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(Path(td))
            _report(root, "20260903T1200-route", [
                ("memory/2026/08/eval-canary.md", "dated", "route", "memory/semantic/eval-canary.md"),
                ("memory/_inbox/workflow-bash-13.md", "inbox", "route", "memory/procedural/workflow-bash-13.md"),
            ])
            table = ev._disposition_map(root)
            self.assertEqual(ev._migrated("agent/memory/2026/08/eval-canary.md", table), "agent/memory/semantic/eval-canary.md")
            self.assertEqual(ev._migrated("agent/memory/_inbox/workflow-bash-13.md", table), "agent/memory/procedural/workflow-bash-13.md")

    def test_two_notes_that_shared_a_basename_stay_two_notes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(Path(td))
            _report(root, "20260903T1200-route", [
                ("memory/2026/08/meeting-notes.md", "dated", "route", "memory/semantic/meeting-notes.md"),
                ("memory/2026/03/meeting-notes.md", "dated", "route", "memory/semantic/meeting-notes~dup.md"),
            ])
            table = ev._disposition_map(root)
            a = ev._migrated("agent/memory/2026/08/meeting-notes.md", table)
            b = ev._migrated("agent/memory/2026/03/meeting-notes.md", table)
            self.assertNotEqual(a, b)
            self.assertEqual(b, "agent/memory/semantic/meeting-notes~dup.md")

    def test_a_later_run_overrides_an_earlier_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(Path(td))
            _report(root, "20260903T1200-route", [("memory/_inbox/x.md", "inbox", "route", "memory/semantic/x.md")])
            _report(root, "20260903T1500-route", [("memory/_inbox/x.md", "inbox", "route", "memory/semantic/x~dup.md")])
            table = ev._disposition_map(root)
            self.assertEqual(ev._migrated("agent/memory/_inbox/x.md", table), "agent/memory/semantic/x~dup.md")

    def test_paths_no_report_moved_compare_as_they_are(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(Path(td))
            table = ev._disposition_map(root)  # no reports at all
            self.assertEqual(table, {})
            for path in ("projects/agentm/_harness/PLAN.md", "agent/memory/semantic/a.md",
                         "agent/memory/2026/03/meeting-notes.md", ev.CANARY_PATH):
                self.assertEqual(ev._migrated(path, table), path)
            self.assertEqual(ev._migrated("anything", {}), "anything")

    def test_the_report_key_matches_as_a_suffix_on_a_path_boundary(self):
        table = {"memory/2026/08/x.md": "memory/semantic/x.md"}
        self.assertEqual(ev._migrated("agent/memory/2026/08/x.md", table), "agent/memory/semantic/x.md")
        self.assertEqual(ev._migrated("memory/2026/08/x.md", table), "memory/semantic/x.md")
        self.assertEqual(ev._migrated("agent/memory/2026/08/notx.md", table), "agent/memory/2026/08/notx.md")
        self.assertEqual(ev._migrated("agent/old-memory/2026/08/x.md", table), "agent/old-memory/2026/08/x.md")

    def test_the_operators_whole_tree_moves_are_prefix_remaps(self):
        """The gold set is frozen and spells the roots the way it was labeled;
        the merge remap keeps that spelling, and the casing is folded last."""
        self.assertEqual(ev._remap_merged("Agent/external/primos/decisions/x.md"), "Projects/primos/decisions/x.md")  # root-casing: the gold set's spelling
        self.assertEqual(ev._remap_merged("Agent/_vault-archive/ag-design-history/a.md"),  # root-casing: the gold set's spelling
                         "Projects/agentm/_harness/archive/designs/ag-design-history/a.md")  # root-casing: the gold set's spelling
        self.assertEqual(ev._remap_merged("Agent/memory/semantic/a.md"), "Agent/memory/semantic/a.md")  # root-casing: the gold set's spelling

    def test_the_casing_is_folded_last_and_only_on_the_first_segment(self):
        """agentm-vault plan 08: the roots are lowercase on disk, the gold set
        keeps `agent/...`, and the fold is at score time after the other
        corrections. A path inside a space keeps its own casing."""
        self.assertEqual(ev._remap_casing("Agent/memory/semantic/a.md"), "agent/memory/semantic/a.md")  # root-casing: the gold set's spelling
        self.assertEqual(ev._remap_casing("Projects/agentm/Personal/x.md"), "projects/agentm/Personal/x.md")  # root-casing: the gold set's spelling
        self.assertEqual(ev._remap_casing("standards/voice/a.md"), "standards/voice/a.md")
        self.assertEqual(ev._remap_casing("agent/memory/a.md"), "agent/memory/a.md")
        merged = ev._remap_merged("Agent/external/primos/decisions/x.md")  # root-casing: the gold set's spelling
        self.assertEqual(ev._remap_casing(merged), "projects/primos/decisions/x.md")
        self.assertEqual(ev.CANARY_PATH, ev._remap_casing(ev.CANARY_PATH))

    def test_the_trims_remaps_fire_only_where_the_vault_has_moved(self):
        """agentm-vault plan 05: the migration runs at deploy time, so the
        gold set's voice-rule and settings paths follow the move only on a
        vault that holds the destination, and stay pre-trims elsewhere. The
        paths are the gold set's, spelled as it was labeled."""
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            old = "Projects/_global/wiki-style/2026-07-05-docs-prose-style.md"  # root-casing: the gold set's spelling
            self.assertEqual(ev._remap_trims(old, vault), old)
            (vault / "standards" / "voice").mkdir(parents=True)
            (vault / "standards" / "voice" / "2026-07-05-docs-prose-style.md").write_text("x", encoding="utf-8")
            self.assertEqual(ev._remap_trims(old, vault), "standards/voice/2026-07-05-docs-prose-style.md")
            self.assertEqual(ev._remap_trims("Agent/memory/trusted-sources.md", vault),  # root-casing: the gold set's spelling
                             "Agent/memory/trusted-sources.md")  # root-casing: the gold set's spelling
            (vault / "projects" / "agentm").mkdir(parents=True)
            (vault / "projects" / "agentm" / "trusted-sources.md").write_text("x", encoding="utf-8")
            self.assertEqual(ev._remap_casing(ev._remap_trims("Agent/memory/trusted-sources.md", vault)),  # root-casing: the gold set's spelling
                             "projects/agentm/trusted-sources.md")
            self.assertEqual(ev._remap_trims("agent/memory/semantic/a.md", vault), "agent/memory/semantic/a.md")
            self.assertEqual(ev._remap_trims(old, None), old, "no vault: the eval stays pre-trims")

    def test_purged_and_held_rows_do_not_enter_the_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(Path(td))
            _report(root, "20260903T1200-purge", [("memory/_inbox/junk.md", "inbox", "purge", ""),
                                                  ("memory/trusted-sources.md", "stray", "hold", "")])
            self.assertEqual(ev._disposition_map(root), {})


if __name__ == "__main__":
    unittest.main()
