#!/usr/bin/env python3
"""moc_generator.py — the standards map and the arc-index pages.

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


class TestCommandLine(unittest.TestCase):
    """The per-kind `_moc/` pages retired, and the function that wrote them went
    in agentm-vault plan 07: the mocs job writes the maps. The command runs the
    two generators it still has, and only when asked."""

    def test_the_command_writes_no_kind_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "standards").mkdir()
            (vault / "standards" / "storage-rules.md").write_text("# The rules\n", encoding="utf-8")
            (vault / "memory").mkdir()
            (vault / "memory" / "a.md").write_text("---\nkind: fix\nslug: a\n---\n\nbody\n", encoding="utf-8")
            self.assertEqual(mg.main(["--vault", str(vault), "--standards"]), 0)
            self.assertTrue((vault / "standards" / "moc-standards.md").is_file())
            self.assertFalse((vault / "_moc").exists(), "a per-kind page was written")

    def test_asking_for_nothing_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "memory").mkdir()
            (vault / "memory" / "a.md").write_text("---\nkind: fix\nslug: a\n---\n\nbody\n", encoding="utf-8")
            self.assertEqual(mg.main(["--vault", str(vault)]), 2)
            self.assertEqual(sorted(p.name for p in vault.iterdir()), ["memory"])


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
            _write_project_note(vault / "desk/projects" / "agentm" / "decisions" / "a.md", "wave-a", "2026-07-01")
            _write_project_note(vault / "desk/projects" / "agentm" / "decisions" / "b.md", "wave-a", "2026-07-02")
            _write_project_note(vault / "desk/projects" / "crickets" / "decisions" / "c.md", "v8", "2026-07-01")
            groups = mg.build_arc_groups(vault)
            self.assertEqual(set(groups.keys()), {("agentm", "wave-a"), ("crickets", "v8")})
            self.assertEqual(len(groups[("agentm", "wave-a")]), 2)

    def test_entries_without_arc_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "desk/projects" / "agentm" / "decisions" / "a.md", None, "2026-07-01")
            self.assertEqual(mg.build_arc_groups(vault), {})

    def test_newest_first_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "desk/projects" / "agentm" / "decisions" / "old.md", "wave-a", "2026-01-01", "old")
            _write_project_note(vault / "desk/projects" / "agentm" / "decisions" / "new.md", "wave-a", "2026-07-10", "new")
            groups = mg.build_arc_groups(vault)
            slugs = [fm["slug"] for _rel, _created, fm in groups[("agentm", "wave-a")]]
            self.assertEqual(slugs, ["new", "old"])

    def test_excludes_harness_and_archive_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "desk/projects" / "agentm" / "_harness" / "a.md", "wave-a", "2026-07-01")
            _write_project_note(vault / "desk/projects" / "agentm" / "decisions" / "_archive" / "b.md", "wave-a", "2026-07-01")
            self.assertEqual(mg.build_arc_groups(vault), {})


class TestGenerateArcIndexes(unittest.TestCase):
    def test_writes_new_page_with_locked_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "desk/projects" / "agentm" / "decisions" / "a.md", "wave-a", "2026-07-01", "a")
            written = mg.generate_arc_indexes(vault, today="2026-07-18")
            self.assertEqual(written, ["agentm/wave-a"])
            content = (vault / "desk/projects" / "agentm" / "arcs" / "wave-a.md").read_text(encoding="utf-8")
            self.assertIn("kind: arc-index", content)
            self.assertIn("arc: wave-a", content)
            self.assertIn("slug: wave-a", content)
            self.assertIn("- [[a]]", content)

    def test_rerun_preserves_hand_written_header_above_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "desk/projects" / "agentm" / "decisions" / "a.md", "wave-a", "2026-07-01", "a")
            mg.generate_arc_indexes(vault, today="2026-07-18")
            target = vault / "desk/projects" / "agentm" / "arcs" / "wave-a.md"
            existing = target.read_text(encoding="utf-8")
            hand_written = existing.replace("# wave-a — arc index\n", "# wave-a — arc index\n\nHand-written intro.\n")
            target.write_text(hand_written, encoding="utf-8")

            _write_project_note(vault / "desk/projects" / "agentm" / "decisions" / "b.md", "wave-a", "2026-07-02", "b")
            mg.generate_arc_indexes(vault, today="2026-07-19")
            after = target.read_text(encoding="utf-8")
            self.assertIn("Hand-written intro.", after)
            self.assertIn("- [[b]]", after)

    def test_cross_repo_arc_gets_pointer_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write_project_note(vault / "desk/projects" / "agentm" / "decisions" / "a.md", "v8", "2026-07-01", "a")
            _write_project_note(vault / "desk/projects" / "crickets" / "decisions" / "c.md", "v8", "2026-07-01", "c")
            mg.generate_arc_indexes(vault, today="2026-07-18")
            agentm_page = (vault / "desk/projects" / "agentm" / "arcs" / "v8.md").read_text(encoding="utf-8")
            self.assertIn("Also stamped `arc: v8` in", agentm_page)
            self.assertIn("`crickets`", agentm_page)


if __name__ == "__main__":
    unittest.main()
