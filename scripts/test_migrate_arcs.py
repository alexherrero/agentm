#!/usr/bin/env python3
"""migrate_arcs.py — the arc-as-metadata backfill (2026-07-18 convention).

Covers all three passes (stamp / archive-group / designs-move) in both their
dry-run (plan_*) and mutating (apply_*) forms — this is the highest-risk
module in the convention, since apply_* renames/moves real files and
rewrites frontmatter/content in place.

Run directly:
    cd scripts && python3 -m unittest test_migrate_arcs
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import migrate_arcs as ma  # noqa: E402


def _write(path: Path, *, arc: str | None = None, tags: str = "[]",
           group: str = "decisions", body: str = "body\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arc_line = f"arc: {arc}\n" if arc is not None else ""
    path.write_text(
        f"---\nkind: decision\nstatus: active\ncreated: 2026-07-01\n"
        f"updated: 2026-07-01\ntags: {tags}\n{arc_line}group: {group}\n"
        f"slug: {path.stem}\n---\n\n{body}",
        encoding="utf-8",
    )


class TestInsertArcLine(unittest.TestCase):
    def test_inserts_before_group(self):
        text = ("---\nkind: decision\nstatus: active\ncreated: 2026-07-01\n"
                "updated: 2026-07-01\ntags: []\ngroup: decisions\nslug: x\n"
                "---\n\nbody\n")
        out = ma._insert_arc_line(text, "wave-a")
        lines = out.splitlines()
        self.assertIn("arc: wave-a", lines)
        self.assertLess(lines.index("arc: wave-a"), lines.index("group: decisions"))

    def test_raises_without_frontmatter(self):
        with self.assertRaises(ValueError):
            ma._insert_arc_line("no frontmatter here\n", "wave-a")

    def test_raises_if_arc_already_present(self):
        text = ("---\nkind: decision\nstatus: active\ncreated: 2026-07-01\n"
                "updated: 2026-07-01\ntags: []\narc: v8\ngroup: decisions\nslug: x\n"
                "---\n\nbody\n")
        with self.assertRaises(ValueError):
            ma._insert_arc_line(text, "wave-a")


class TestInferArcFromTags(unittest.TestCase):
    def test_verbatim_tag_match_is_high(self):
        result = ma._infer_arc_from_tags(["wave-a", "other"])
        self.assertEqual(result, ("wave-a", "tag `wave-a` is a registered arc slug", "HIGH"))

    def test_version_tag_coarsened_is_medium(self):
        result = ma._infer_arc_from_tags(["v8-12", "other"])
        self.assertEqual(result[0], "v8")
        self.assertEqual(result[2], "MEDIUM")

    def test_no_match_returns_none(self):
        self.assertIsNone(ma._infer_arc_from_tags(["unrelated-tag"]))


class TestPlanStamp(unittest.TestCase):
    def test_high_confidence_tag_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "projects" / "agentm" / "decisions" / "a.md", tags="[wave-a]")
            plan = ma.plan_stamp(vault, "agentm")
            self.assertEqual(len(plan.rows), 1)
            self.assertEqual(plan.rows[0].confidence, "HIGH")
            self.assertEqual(plan.rows[0].proposed, "wave-a")

    def test_already_stamped_entry_is_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "projects" / "agentm" / "decisions" / "a.md", arc="v8")
            plan = ma.plan_stamp(vault, "agentm")
            self.assertEqual(plan.rows[0].confidence, "SKIP")
            self.assertEqual(plan.rows[0].proposed, "v8")

    def test_no_signal_is_unmatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "projects" / "agentm" / "decisions" / "a.md", tags="[unrelated]")
            plan = ma.plan_stamp(vault, "agentm")
            self.assertEqual(plan.rows[0].confidence, "UNMATCHED")

    def test_only_scans_decisions_and_designs(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "projects" / "agentm" / "other" / "a.md", tags="[wave-a]")
            plan = ma.plan_stamp(vault, "agentm")
            self.assertEqual(plan.rows, [])

    def test_apply_writes_only_high_and_medium(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            high = vault / "projects" / "agentm" / "decisions" / "a.md"
            unmatched = vault / "projects" / "agentm" / "decisions" / "b.md"
            _write(high, tags="[wave-a]")
            _write(unmatched, tags="[unrelated]")
            plan = ma.plan_stamp(vault, "agentm")
            ma.apply_stamp(vault, plan)
            self.assertIn("arc: wave-a", high.read_text(encoding="utf-8"))
            self.assertNotIn("arc:", unmatched.read_text(encoding="utf-8"))


class TestMatchRegistryPrefix(unittest.TestCase):
    def test_exact_and_prefix_match(self):
        self.assertEqual(ma._match_registry_prefix("wave-a"), "wave-a")
        self.assertEqual(ma._match_registry_prefix("wave-a-followup"), "wave-a")

    def test_alias_prefix_match(self):
        self.assertEqual(ma._match_registry_prefix("ag-phase2-agentm"), "architecture-governance")

    def test_no_match_returns_none(self):
        self.assertIsNone(ma._match_registry_prefix("completely-unrelated-slug"))

    def test_longest_registered_slug_wins(self):
        # v5 and v5-1-storage-seam are both registered; a slug that matches
        # the longer one should not be mis-bucketed under the shorter v5.
        self.assertEqual(ma._match_registry_prefix("v5-1-storage-seam-part2"), "v5-1-storage-seam")


class TestPlanAndApplyArchiveGroup(unittest.TestCase):
    def test_no_archive_dir_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            plan = ma.plan_archive_group(vault, "agentm")
            self.assertTrue(plan.errors)
            self.assertEqual(plan.rows, [])

    def test_matched_file_proposed_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            archive = vault / "projects" / "agentm" / "_harness" / "archive"
            f = archive / "PLAN.archive.20260718-wave-a-something.md"
            f.parent.mkdir(parents=True)
            f.write_text("stub\n", encoding="utf-8")
            plan = ma.plan_archive_group(vault, "agentm")
            self.assertEqual(len(plan.rows), 1)
            row = plan.rows[0]
            self.assertEqual(row.confidence, "HIGH")
            self.assertTrue(row.proposed.endswith("archive/wave-a/PLAN.archive.20260718-wave-a-something.md"))

    def test_unmatched_slug_not_moved_by_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            archive = vault / "projects" / "agentm" / "_harness" / "archive"
            f = archive / "PLAN.archive.20260718-totally-unregistered-thing.md"
            f.parent.mkdir(parents=True)
            f.write_text("stub\n", encoding="utf-8")
            plan = ma.plan_archive_group(vault, "agentm")
            self.assertEqual(plan.rows[0].confidence, "UNMATCHED")
            ma.apply_archive_group(vault, plan)
            self.assertTrue(f.is_file())  # untouched — still at its original path

    def test_apply_actually_moves_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            archive = vault / "projects" / "agentm" / "_harness" / "archive"
            f = archive / "PLAN.archive.20260718-wave-a-something.md"
            f.parent.mkdir(parents=True)
            f.write_text("stub content\n", encoding="utf-8")
            plan = ma.plan_archive_group(vault, "agentm")
            ma.apply_archive_group(vault, plan)
            self.assertFalse(f.is_file())
            dest = archive / "wave-a" / "PLAN.archive.20260718-wave-a-something.md"
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_text(encoding="utf-8"), "stub content\n")

    def test_no_date_prefix_slug_is_unmatched_not_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            archive = vault / "projects" / "agentm" / "_harness" / "archive"
            f = archive / "PLAN.archive..md"  # degenerate: empty slug after date-prefix strip
            f.parent.mkdir(parents=True)
            f.write_text("stub\n", encoding="utf-8")
            plan = ma.plan_archive_group(vault, "agentm")
            self.assertEqual(plan.rows[0].confidence, "UNMATCHED")


class TestPlanAndApplyDesignsMove(unittest.TestCase):
    def _seed_design_folder(self, vault: Path, project: str, arc: str) -> Path:
        src = vault / "projects" / project / "_harness" / "designs" / arc
        (src / "notes.md").parent.mkdir(parents=True, exist_ok=True)
        (src / "notes.md").write_text("design content\n", encoding="utf-8")
        return src

    def test_missing_folder_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            plan = ma.plan_designs_move(vault, "agentm", "wave-a")
            self.assertTrue(plan.errors)

    def test_existing_destination_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            self._seed_design_folder(vault, "agentm", "wave-a")
            dest = vault / "projects" / "agentm" / "_harness" / "archive" / "designs" / "wave-a"
            dest.mkdir(parents=True)
            plan = ma.plan_designs_move(vault, "agentm", "wave-a")
            self.assertTrue(plan.errors)

    def test_finds_vault_wide_link_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            self._seed_design_folder(vault, "agentm", "wave-a")
            referrer = vault / "projects" / "agentm" / "decisions" / "linker.md"
            _write(referrer, body="see [_harness/designs/wave-a/notes.md] for detail\n")
            plan = ma.plan_designs_move(vault, "agentm", "wave-a")
            paths = [r.path for r in plan.rows]
            self.assertIn("projects/agentm/decisions/linker.md", paths)

    def test_apply_moves_folder_and_rewrites_external_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            src = self._seed_design_folder(vault, "agentm", "wave-a")
            referrer = vault / "projects" / "agentm" / "decisions" / "linker.md"
            _write(referrer, body="see _harness/designs/wave-a/notes.md for detail\n")
            plan = ma.plan_designs_move(vault, "agentm", "wave-a")
            ma.apply_designs_move(vault, "agentm", "wave-a", plan)

            self.assertFalse(src.is_dir())
            dest = vault / "projects" / "agentm" / "_harness" / "archive" / "designs" / "wave-a"
            self.assertTrue((dest / "notes.md").is_file())
            self.assertEqual((dest / "notes.md").read_text(encoding="utf-8"), "design content\n")

            new_text = referrer.read_text(encoding="utf-8")
            self.assertIn("_harness/archive/designs/wave-a/notes.md", new_text)
            self.assertNotIn("_harness/designs/wave-a/notes.md", new_text)

    def test_apply_rewrites_reference_inside_the_moved_folder_itself(self):
        # A file that lived INSIDE the moved folder and referenced its own old
        # path must be remapped to read from its NEW location, not crash
        # looking for itself at the pre-move path.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            src = self._seed_design_folder(vault, "agentm", "wave-a")
            self_ref = src / "notes.md"
            self_ref.write_text("see _harness/designs/wave-a/notes.md (self)\n", encoding="utf-8")
            plan = ma.plan_designs_move(vault, "agentm", "wave-a")
            ma.apply_designs_move(vault, "agentm", "wave-a", plan)

            dest = vault / "projects" / "agentm" / "_harness" / "archive" / "designs" / "wave-a"
            new_text = (dest / "notes.md").read_text(encoding="utf-8")
            self.assertIn("_harness/archive/designs/wave-a/notes.md", new_text)


class TestResolveVault(unittest.TestCase):
    def test_explicit_arg_wins(self):
        self.assertEqual(ma._resolve_vault("/explicit/path"), Path("/explicit/path"))

    def test_falls_back_to_env(self):
        import os
        old = os.environ.get("MEMORY_VAULT_PATH")
        try:
            os.environ["MEMORY_VAULT_PATH"] = "/env/path"
            self.assertEqual(ma._resolve_vault(None), Path("/env/path"))
        finally:
            if old is None:
                os.environ.pop("MEMORY_VAULT_PATH", None)
            else:
                os.environ["MEMORY_VAULT_PATH"] = old

    def test_raises_when_neither_present(self):
        import os
        old = os.environ.pop("MEMORY_VAULT_PATH", None)
        try:
            with self.assertRaises(FileNotFoundError):
                ma._resolve_vault(None)
        finally:
            if old is not None:
                os.environ["MEMORY_VAULT_PATH"] = old


if __name__ == "__main__":
    unittest.main()
