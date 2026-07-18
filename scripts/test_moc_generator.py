#!/usr/bin/env python3
"""moc_generator.py — V6-18's browse-first MOC generator (task 3).

Run directly:
    cd scripts && python3 -m unittest test_moc_generator
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

import moc_generator as mg  # noqa: E402


def _write_note(path: Path, kind: str, created: str, slug: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slug_line = f"slug: {slug}\n" if slug else f"slug: {path.stem}\n"
    path.write_text(
        f"---\nkind: {kind}\nstatus: active\ncreated: {created}\n"
        f"updated: {created}\ntags: []\ngroup: test\n{slug_line}---\n\nbody\n",
        encoding="utf-8",
    )


class TestBuildKindGroups(unittest.TestCase):
    def test_missing_vault_returns_empty(self):
        self.assertEqual(mg.build_kind_groups("/nonexistent/path/xyz"), {})

    def test_groups_by_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "a.md", "fix", "2026-07-01", "a")
            _write_note(vault / "personal" / "b.md", "fix", "2026-07-02", "b")
            _write_note(vault / "projects" / "p" / "c.md", "idea", "2026-07-01", "c")
            groups = mg.build_kind_groups(vault)
            self.assertEqual(set(groups.keys()), {"fix", "idea"})
            self.assertEqual(len(groups["fix"]), 2)
            self.assertEqual(len(groups["idea"]), 1)

    def test_newest_first_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "old.md", "fix", "2026-01-01", "old")
            _write_note(vault / "personal" / "new.md", "fix", "2026-07-10", "new")
            groups = mg.build_kind_groups(vault)
            slugs = [fm["slug"] for _rel, _created, fm in groups["fix"]]
            self.assertEqual(slugs, ["new", "old"])

    def test_includes_idea_incubator(self):
        # Unlike frontmatter_validator.py's DC-4 exemption, moc_generator
        # mirrors vec_index.py's full_sync walk, which DOES cover
        # _idea-incubator/ — browse-first MOCs should include it.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "_idea-incubator" / "x" / "_index.md", "idea-incubator", "2026-07-01", "x-index")
            groups = mg.build_kind_groups(vault)
            self.assertIn("idea-incubator", groups)

    def test_excludes_archive_and_own_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "_archive" / "old.md", "fix", "2026-01-01", "old")
            _write_note(vault / "personal" / "_moc" / "fix.md", "fix", "2026-01-01", "stale-moc")
            groups = mg.build_kind_groups(vault)
            self.assertEqual(groups, {})


class TestGenerate(unittest.TestCase):
    def test_writes_one_page_per_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "a.md", "fix", "2026-07-01", "a")
            _write_note(vault / "personal" / "b.md", "idea", "2026-07-01", "b")
            written = mg.generate(vault)
            self.assertEqual(set(written), {"fix", "idea"})
            self.assertTrue((vault / "_moc" / "fix.md").is_file())
            self.assertTrue((vault / "_moc" / "idea.md").is_file())

    def test_page_contains_wikilinks_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "old.md", "fix", "2026-01-01", "old-slug")
            _write_note(vault / "personal" / "new.md", "fix", "2026-07-10", "new-slug")
            mg.generate(vault)
            content = (vault / "_moc" / "fix.md").read_text(encoding="utf-8")
            self.assertIn("[[new-slug]]", content)
            self.assertIn("[[old-slug]]", content)
            self.assertLess(content.index("[[new-slug]]"), content.index("[[old-slug]]"))

    def test_page_contains_home_backlink(self):
        # CONS-1: each generated MOC page links back to the project's wiki
        # Home so an operator browsing the vault has a way back to the docs
        # entry point. (This comment previously claimed "the vault has no
        # Home note of its own" -- that was wrong; corrected 2026-07-11,
        # Consolidation arc exit-gate follow-up. The vault DOES have its own
        # Home.md root, which is exactly why the separate [[Home]] backlink
        # below exists -- this URL backlink is a different, still-valid
        # pointer to the project's GitHub-wiki docs, not the vault root.)
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "a.md", "fix", "2026-07-01", "a")
            mg.generate(vault)
            content = (vault / "_moc" / "fix.md").read_text(encoding="utf-8")
            self.assertIn(mg._HOME_BACKLINK_URL, content)
            self.assertIn(f"]({mg._HOME_BACKLINK_URL})", content)

    def test_page_contains_vault_home_wikilink(self):
        # Consolidation arc exit-gate follow-up (2026-07-11): E5's vault-
        # connectivity review found Home.md and _moc/ never cross-
        # referenced each other in either direction. Home.md now links into
        # _moc/ directly; this is the other direction -- every generated
        # MOC page links back to the vault's own [[Home]] root, distinct
        # from the pre-existing wiki-Home URL backlink above.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "a.md", "fix", "2026-07-01", "a")
            mg.generate(vault)
            content = (vault / "_moc" / "fix.md").read_text(encoding="utf-8")
            self.assertIn("[[Home]]", content)

    def test_idempotent_regeneration_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "a.md", "fix", "2026-07-01", "a")
            _write_note(vault / "personal" / "b.md", "fix", "2026-07-02", "b")
            mg.generate(vault)
            first = (vault / "_moc" / "fix.md").read_text(encoding="utf-8")
            mg.generate(vault)
            second = (vault / "_moc" / "fix.md").read_text(encoding="utf-8")
            self.assertEqual(first, second)

    def test_never_touches_source_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "personal" / "a.md"
            _write_note(note, "fix", "2026-07-01", "a")
            before = note.read_text(encoding="utf-8")
            mg.generate(vault)
            after = note.read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_malformed_kind_is_skipped_not_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "personal" / "a.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(
                "---\nkind: handoff-artifact (verdict memo)\nstatus: active\n"
                "created: 2026-07-01\nupdated: 2026-07-01\ntags: []\n"
                "group: test\nslug: a\n---\n\nbody\n",
                encoding="utf-8",
            )
            written = mg.generate(vault)
            self.assertEqual(written, [])
            self.assertFalse((vault / "_moc").exists())

    def test_unrecognized_kind_still_gets_a_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_note(vault / "personal" / "a.md", "made-up-kind", "2026-07-01", "a")
            written = mg.generate(vault)
            self.assertEqual(written, ["made-up-kind"])
            content = (vault / "_moc" / "made-up-kind.md").read_text(encoding="utf-8")
            self.assertIn("unrecognized kind", content)


def _write_project_note(path: Path, arc: str | None, created: str, slug: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slug_line = f"slug: {slug}\n" if slug else f"slug: {path.stem}\n"
    arc_line = f"arc: {arc}\n" if arc is not None else ""
    path.write_text(
        f"---\nkind: decision\nstatus: active\ncreated: {created}\n"
        f"updated: {created}\ntags: []\n{arc_line}group: decisions\n{slug_line}---\n\nbody\n",
        encoding="utf-8",
    )


class TestBuildArcGroups(unittest.TestCase):
    def test_missing_vault_returns_empty(self):
        self.assertEqual(mg.build_arc_groups("/nonexistent/path/xyz"), {})

    def test_groups_by_project_and_arc(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "projects" / "agentm" / "decisions" / "a.md", "wave-a", "2026-07-01")
            _write_project_note(vault / "projects" / "agentm" / "decisions" / "b.md", "wave-a", "2026-07-02")
            _write_project_note(vault / "projects" / "crickets" / "decisions" / "c.md", "v8", "2026-07-01")
            groups = mg.build_arc_groups(vault)
            self.assertEqual(set(groups.keys()), {("agentm", "wave-a"), ("crickets", "v8")})
            self.assertEqual(len(groups[("agentm", "wave-a")]), 2)

    def test_entries_without_arc_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "projects" / "agentm" / "decisions" / "a.md", None, "2026-07-01")
            self.assertEqual(mg.build_arc_groups(vault), {})

    def test_newest_first_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "projects" / "agentm" / "decisions" / "old.md", "wave-a", "2026-01-01", "old")
            _write_project_note(vault / "projects" / "agentm" / "decisions" / "new.md", "wave-a", "2026-07-10", "new")
            groups = mg.build_arc_groups(vault)
            slugs = [fm["slug"] for _rel, _created, fm in groups[("agentm", "wave-a")]]
            self.assertEqual(slugs, ["new", "old"])

    def test_excludes_harness_and_archive_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "projects" / "agentm" / "_harness" / "a.md", "wave-a", "2026-07-01")
            _write_project_note(vault / "projects" / "agentm" / "decisions" / "_archive" / "b.md", "wave-a", "2026-07-01")
            self.assertEqual(mg.build_arc_groups(vault), {})


class TestGenerateArcIndexes(unittest.TestCase):
    def test_writes_new_page_with_locked_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "projects" / "agentm" / "decisions" / "a.md", "wave-a", "2026-07-01", "a")
            written = mg.generate_arc_indexes(vault, today="2026-07-18")
            self.assertEqual(written, ["agentm/wave-a"])
            content = (vault / "projects" / "agentm" / "arcs" / "wave-a.md").read_text(encoding="utf-8")
            self.assertIn("kind: arc-index", content)
            self.assertIn("arc: wave-a", content)
            self.assertIn("slug: wave-a", content)
            self.assertIn("- [[a]]", content)

    def test_rerun_preserves_hand_written_header_above_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "projects" / "agentm" / "decisions" / "a.md", "wave-a", "2026-07-01", "a")
            mg.generate_arc_indexes(vault, today="2026-07-18")
            target = vault / "projects" / "agentm" / "arcs" / "wave-a.md"
            existing = target.read_text(encoding="utf-8")
            hand_written = existing.replace("# wave-a — arc index\n", "# wave-a — arc index\n\nHand-written intro.\n")
            target.write_text(hand_written, encoding="utf-8")

            _write_project_note(vault / "projects" / "agentm" / "decisions" / "b.md", "wave-a", "2026-07-02", "b")
            mg.generate_arc_indexes(vault, today="2026-07-19")
            after = target.read_text(encoding="utf-8")
            self.assertIn("Hand-written intro.", after)
            self.assertIn("- [[b]]", after)

    def test_cross_repo_arc_gets_pointer_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "projects" / "agentm" / "decisions" / "a.md", "v8", "2026-07-01", "a")
            _write_project_note(vault / "projects" / "crickets" / "decisions" / "c.md", "v8", "2026-07-01", "c")
            mg.generate_arc_indexes(vault, today="2026-07-18")
            agentm_page = (vault / "projects" / "agentm" / "arcs" / "v8.md").read_text(encoding="utf-8")
            self.assertIn("Also stamped `arc: v8` in", agentm_page)
            self.assertIn("`crickets`", agentm_page)


if __name__ == "__main__":
    unittest.main()
