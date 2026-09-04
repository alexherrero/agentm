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
        root = vault / "Agent"
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
            self.assertEqual(ev._migrated("Agent/memory/2026/08/eval-canary.md", table), "Agent/memory/semantic/eval-canary.md")
            self.assertEqual(ev._migrated("Agent/memory/_inbox/workflow-bash-13.md", table), "Agent/memory/procedural/workflow-bash-13.md")

    def test_two_notes_that_shared_a_basename_stay_two_notes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(Path(td))
            _report(root, "20260903T1200-route", [
                ("memory/2026/08/meeting-notes.md", "dated", "route", "memory/semantic/meeting-notes.md"),
                ("memory/2026/03/meeting-notes.md", "dated", "route", "memory/semantic/meeting-notes~dup.md"),
            ])
            table = ev._disposition_map(root)
            a = ev._migrated("Agent/memory/2026/08/meeting-notes.md", table)
            b = ev._migrated("Agent/memory/2026/03/meeting-notes.md", table)
            self.assertNotEqual(a, b)
            self.assertEqual(b, "Agent/memory/semantic/meeting-notes~dup.md")

    def test_a_later_run_overrides_an_earlier_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(Path(td))
            _report(root, "20260903T1200-route", [("memory/_inbox/x.md", "inbox", "route", "memory/semantic/x.md")])
            _report(root, "20260903T1500-route", [("memory/_inbox/x.md", "inbox", "route", "memory/semantic/x~dup.md")])
            table = ev._disposition_map(root)
            self.assertEqual(ev._migrated("Agent/memory/_inbox/x.md", table), "Agent/memory/semantic/x~dup.md")

    def test_paths_no_report_moved_compare_as_they_are(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(Path(td))
            table = ev._disposition_map(root)  # no reports at all
            self.assertEqual(table, {})
            for path in ("Projects/agentm/_harness/PLAN.md", "Agent/memory/semantic/a.md",
                         "Agent/memory/2026/03/meeting-notes.md", ev.CANARY_PATH):
                self.assertEqual(ev._migrated(path, table), path)
            self.assertEqual(ev._migrated("anything", {}), "anything")

    def test_the_report_key_matches_as_a_suffix_on_a_path_boundary(self):
        table = {"memory/2026/08/x.md": "memory/semantic/x.md"}
        self.assertEqual(ev._migrated("Agent/memory/2026/08/x.md", table), "Agent/memory/semantic/x.md")
        self.assertEqual(ev._migrated("memory/2026/08/x.md", table), "memory/semantic/x.md")
        self.assertEqual(ev._migrated("Agent/memory/2026/08/notx.md", table), "Agent/memory/2026/08/notx.md")
        self.assertEqual(ev._migrated("Agent/old-memory/2026/08/x.md", table), "Agent/old-memory/2026/08/x.md")

    def test_the_operators_whole_tree_moves_are_prefix_remaps(self):
        self.assertEqual(ev._remap_merged("Agent/external/primos/decisions/x.md"), "Projects/primos/decisions/x.md")
        self.assertEqual(ev._remap_merged("Agent/_vault-archive/ag-design-history/a.md"),
                         "Projects/agentm/_harness/archive/designs/ag-design-history/a.md")
        self.assertEqual(ev._remap_merged("Agent/memory/semantic/a.md"), "Agent/memory/semantic/a.md")

    def test_purged_and_held_rows_do_not_enter_the_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(Path(td))
            _report(root, "20260903T1200-purge", [("memory/_inbox/junk.md", "inbox", "purge", ""),
                                                  ("memory/trusted-sources.md", "stray", "hold", "")])
            self.assertEqual(ev._disposition_map(root), {})


if __name__ == "__main__":
    unittest.main()
