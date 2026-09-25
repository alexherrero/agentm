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

    def test_the_casing_is_folded_last_to_the_spelling_the_vault_lists(self):
        """agentm-vault plan 08: the roots are lowercase on disk once the rename
        has run, the gold set keeps the old spelling, and the fold is at score
        time after the other corrections — to the name the vault root lists,
        so the gate reads true on either side of the data run, and to
        lowercase when no vault resolves. A path inside a space keeps its own
        casing."""
        renamed = {"agent": "agent", "calendar": "calendar", "personal": "personal", "projects": "projects", "standards": "standards"}
        old = {"agent": "Agent", "calendar": "Calendar", "personal": "Personal", "projects": "Projects", "standards": "standards"}  # root-casing: the spelling before the rename
        self.assertEqual(ev._remap_casing("Agent/memory/semantic/a.md", renamed), "agent/memory/semantic/a.md")  # root-casing: the gold set's spelling
        self.assertEqual(ev._remap_casing("Agent/memory/semantic/a.md", old), "Agent/memory/semantic/a.md")  # root-casing: the gold set's spelling
        self.assertEqual(ev._remap_casing("Agent/memory/semantic/a.md", {}), "agent/memory/semantic/a.md")  # root-casing: the gold set's spelling
        self.assertEqual(ev._remap_casing("agent/memory/a.md", old), "Agent/memory/a.md")  # root-casing: the vault's spelling before the rename
        self.assertEqual(ev._remap_casing("Projects/agentm/Personal/x.md", renamed), "projects/agentm/Personal/x.md")  # root-casing: the gold set's spelling
        self.assertEqual(ev._remap_casing("standards/voice/a.md", renamed), "standards/voice/a.md")
        merged = ev._remap_merged("Agent/external/primos/decisions/x.md")  # root-casing: the gold set's spelling
        self.assertEqual(ev._remap_casing(merged, renamed), "projects/primos/decisions/x.md")
        self.assertEqual(ev.CANARY_PATH, ev._remap_casing(ev.CANARY_PATH, renamed))
        self.assertEqual(ev._remap_casing(ev.CANARY_PATH, old).split("/")[0], "Agent")  # root-casing: the vault's spelling before the rename

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
            self.assertEqual(ev._remap_casing(ev._remap_trims("Agent/memory/trusted-sources.md", vault), {"projects": "projects"}),  # root-casing: the gold set's spelling
                             "projects/agentm/trusted-sources.md")
            self.assertEqual(ev._remap_trims("agent/memory/semantic/a.md", vault), "agent/memory/semantic/a.md")
            self.assertEqual(ev._remap_trims(old, None), old, "no vault: the eval stays pre-trims")

    def test_the_convergence_remaps_fire_only_where_the_vault_has_moved(self):
        """Task 176: root files, reference cards and homelab notes follow
        their move only on a vault that holds the destination. Keyed on the
        paths as the earlier folds leave them, lowercase."""
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            card = "projects/agentm/research/sqlite/reference/bm25-k1-b-constants.md"
            root_file = "projects/agentm/trusted-sources.md"
            note = "agent/memory/semantic/home-server.md"
            for path in (card, root_file, note):
                self.assertEqual(ev._remap_convergence(path, vault), path, "before the move")
            for rel in ("resources/topics/sqlite/bm25-k1-b-constants.md",
                        "projects/agentm/desk/trusted-sources.md", "systems/homelab/system.md"):
                (vault / rel).parent.mkdir(parents=True, exist_ok=True)
                (vault / rel).write_text("x", encoding="utf-8")
            self.assertEqual(ev._remap_convergence(card, vault), "resources/topics/sqlite/bm25-k1-b-constants.md")
            self.assertEqual(ev._remap_convergence(root_file, vault), "projects/agentm/desk/trusted-sources.md")
            self.assertEqual(ev._remap_convergence(note, vault), "systems/homelab/system.md")
            # A research note outside `reference/` never moved, and no vault
            # means no remap at all.
            self.assertEqual(ev._remap_convergence("projects/agentm/research/sqlite/notes.md", vault),
                             "projects/agentm/research/sqlite/notes.md")
            self.assertEqual(ev._remap_convergence(card, None), card)

    def test_a_trims_path_follows_the_file_on_to_desk(self):
        """The settings files moved twice: out of `memory/` into the project
        (the trims), then out of the project root into `desk/` (task 176).
        Once only the second home exists, the gold set's path still reaches it
        through both folds."""
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            gold = "Agent/memory/trusted-sources.md"  # root-casing: the gold set's spelling
            (vault / "projects" / "agentm" / "desk").mkdir(parents=True)
            (vault / "projects" / "agentm" / "desk" / "trusted-sources.md").write_text("x", encoding="utf-8")
            folded = ev._remap_casing(ev._remap_trims(gold, vault), {"projects": "projects"})
            self.assertEqual(folded, "projects/agentm/trusted-sources.md")
            self.assertEqual(ev._remap_convergence(folded, vault), "projects/agentm/desk/trusted-sources.md")

    def test_purged_and_held_rows_do_not_enter_the_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(Path(td))
            _report(root, "20260903T1200-purge", [("memory/_inbox/junk.md", "inbox", "purge", ""),
                                                  ("memory/trusted-sources.md", "stray", "hold", "")])
            self.assertEqual(ev._disposition_map(root), {})


if __name__ == "__main__":
    unittest.main()
