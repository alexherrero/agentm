#!/usr/bin/env python3
"""Unit tests for governs_resolver — the AG-track design-governance resolver.

Mirrors test_capability_resolver.py: temp-dir fixtures, no network, stdlib-only.
Auto-discovered by `python3 -m unittest discover -p 'test_*.py'` (the check-all
unit gate).
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import governs_resolver as gr


# ── fixtures ────────────────────────────────────────────────────────────────--

def _write_design(root: Path, name: str, *, status: str = "launched",
                  area: str | None = None, governs: list[str] | None = None,
                  frontmatter: bool = True) -> Path:
    """Write a minimal design .md under <root>/wiki/designs/<name>."""
    designs = root / "wiki" / "designs"
    designs.mkdir(parents=True, exist_ok=True)
    p = designs / name
    lines: list[str] = []
    if frontmatter:
        lines.append("---")
        lines.append(f"title: {name}")
        lines.append(f"status: {status}")
        lines.append("kind: design")
        lines.append("scope: arc")
        if area is not None:
            lines.append(f"area: {area}")
        if governs is not None:
            lines.append("governs:")
            for g in governs:
                lines.append(f"  - {g}")
        lines.append("---")
        lines.append("")
    lines.append(f"# {name}")
    lines.append("body")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# ── frontmatter parsing ─────────────────────────────────────────────────────--

class TestParseFrontmatter(unittest.TestCase):

    def test_scalar(self):
        fm = gr._parse_frontmatter("---\narea: memory\nstatus: launched\n---\nbody")
        self.assertEqual(fm["area"], "memory")
        self.assertEqual(fm["status"], "launched")

    def test_block_list(self):
        fm = gr._parse_frontmatter("---\ngoverns:\n  - a/b.py\n  - c\n---\n")
        self.assertEqual(fm["governs"], ["a/b.py", "c"])

    def test_inline_list(self):
        fm = gr._parse_frontmatter("---\ngoverns: [a/b.py, c]\n---\n")
        self.assertEqual(fm["governs"], ["a/b.py", "c"])

    def test_quoted_values_unquoted(self):
        fm = gr._parse_frontmatter('---\ntitle: "Quoted Title"\n---\n')
        self.assertEqual(fm["title"], "Quoted Title")

    def test_no_frontmatter_returns_empty(self):
        self.assertEqual(gr._parse_frontmatter("# Just a heading\n"), {})

    def test_unterminated_frontmatter_returns_empty(self):
        self.assertEqual(gr._parse_frontmatter("---\narea: x\nno close fence\n"), {})

    def test_block_list_then_scalar_resumes(self):
        fm = gr._parse_frontmatter(
            "---\ngoverns:\n  - a\n  - b\narea: memory\n---\n"
        )
        self.assertEqual(fm["governs"], ["a", "b"])
        self.assertEqual(fm["area"], "memory")


# ── build_index ─────────────────────────────────────────────────────────────--

class TestBuildIndex(unittest.TestCase):

    def test_launched_with_governs_indexed(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "a.md", area="memory", governs=["scripts/x.py"])
            entries = gr.build_index(root)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].pattern, "scripts/x.py")
            self.assertEqual(entries[0].design, "wiki/designs/a.md")
            self.assertEqual(entries[0].area, "memory")

    def test_design_with_neither_area_nor_governs_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            # no area: passed and no governs: → not a governance participant
            p = (root / "wiki" / "designs")
            p.mkdir(parents=True)
            (p / "a.md").write_text("---\nstatus: launched\n---\n# a\n", encoding="utf-8")
            self.assertEqual(gr.build_index(root), [])

    def test_area_only_design_indexed_with_empty_pattern(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "a.md", area="shared/foundations", governs=None)
            entries = gr.build_index(root)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].pattern, "")
            self.assertEqual(entries[0].area, "shared/foundations")

    def test_proposed_excluded_by_default(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "a.md", status="proposed", governs=["x"])
            self.assertEqual(gr.build_index(root), [])

    def test_proposed_included_with_flag(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "a.md", status="proposed", governs=["x"])
            entries = gr.build_index(root, include_proposed=True)
            self.assertEqual(len(entries), 1)

    def test_one_entry_per_pattern(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "a.md", governs=["scripts", "harness"])
            self.assertEqual(len(gr.build_index(root)), 2)

    def test_no_designs_dir_returns_empty(self):
        with TemporaryDirectory() as d:
            self.assertEqual(gr.build_index(Path(d)), [])

    def test_children_subdir_scanned(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            child = root / "wiki" / "designs" / "children"
            child.mkdir(parents=True)
            (child / "k.md").write_text(
                "---\nstatus: launched\narea: memory\ngoverns:\n  - scripts/k.py\n---\n#k\n",
                encoding="utf-8",
            )
            entries = gr.build_index(root)
            self.assertEqual(entries[0].design, "wiki/designs/children/k.md")


# ── resolution: path ────────────────────────────────────────────────────────--

class TestResolvePath(unittest.TestCase):

    def _root(self, d):
        root = Path(d)
        _write_design(root, "parent.md", area="agentm", governs=["scripts", "harness"])
        _write_design(root, "found.md", area="foundations",
                      governs=["harness/principles.md"])
        return root

    def test_dir_prefix_match(self):
        with TemporaryDirectory() as d:
            root = self._root(d)
            r = gr.resolve_governing_design("scripts/storage_seam.py", root=root)
            self.assertTrue(r["governed"])
            self.assertEqual(r["design"], "wiki/designs/parent.md")
            self.assertEqual(r["area"], "agentm")
            self.assertEqual(r["reason"], "governed")

    def test_most_specific_wins(self):
        with TemporaryDirectory() as d:
            root = self._root(d)
            r = gr.resolve_governing_design("harness/principles.md", root=root)
            # foundations' "harness/principles.md" beats parent's "harness"
            self.assertEqual(r["design"], "wiki/designs/found.md")
            self.assertEqual(r["area"], "foundations")

    def test_exact_file_match(self):
        with TemporaryDirectory() as d:
            root = self._root(d)
            r = gr.resolve_governing_design("harness/principles.md", root=root)
            self.assertTrue(r["governed"])

    def test_greenfield_no_match(self):
        with TemporaryDirectory() as d:
            root = self._root(d)
            r = gr.resolve_governing_design("README.md", root=root)
            self.assertFalse(r["governed"])
            self.assertIsNone(r["design"])
            self.assertEqual(r["reason"], "greenfield")

    def test_glob_pattern(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "g.md", area="memory", governs=["scripts/test_*.py"])
            r = gr.resolve_governing_design("scripts/test_foo.py", root=root)
            self.assertTrue(r["governed"])
            self.assertEqual(r["design"], "wiki/designs/g.md")

    def test_exact_tie_is_overlap_failloud(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "b.md", area="x", governs=["scripts"])
            _write_design(root, "a.md", area="y", governs=["scripts"])
            r = gr.resolve_governing_design("scripts/x.py", root=root)
            # equal specificity between two designs → fail-loud overlap, not a guess
            self.assertFalse(r["governed"])
            self.assertEqual(r["reason"], "overlap")
            self.assertIsNone(r["design"])

    def test_same_design_two_globs_is_not_overlap(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "a.md", area="x", governs=["scripts", "harness"])
            r = gr.resolve_governing_design("scripts/x.py", root=root)
            # two globs from the SAME design at equal spec → still governed (no overlap)
            self.assertTrue(r["governed"])
            self.assertEqual(r["design"], "wiki/designs/a.md")

    def test_double_star_glob_matches(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "a.md", area="agentm/architecture", governs=["scripts/**"])
            r = gr.resolve_governing_design("scripts/sub/deep.py", root=root)
            self.assertTrue(r["governed"])
            self.assertEqual(r["design"], "wiki/designs/a.md")

    def test_backslash_target_normalized(self):
        with TemporaryDirectory() as d:
            root = self._root(d)
            r = gr.resolve_governing_design("scripts\\storage_seam.py", root=root)
            self.assertTrue(r["governed"])


# ── resolution: area ────────────────────────────────────────────────────────--

class TestResolveArea(unittest.TestCase):

    def test_area_name_resolves(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "parent.md", area="agentm", governs=["scripts"])
            r = gr.resolve_governing_design("agentm", root=root)
            self.assertTrue(r["governed"])
            self.assertEqual(r["design"], "wiki/designs/parent.md")
            self.assertEqual(r["area"], "agentm")

    def test_area_only_design_reachable_by_area_not_by_file(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            # area-only root (no governs:), like shared/foundations
            _write_design(root, "found.md", area="shared/foundations", governs=None)
            _write_design(root, "arch.md", area="agentm/architecture", governs=["scripts/**"])
            # reachable by area name
            r = gr.resolve_governing_design("shared/foundations", root=root)
            self.assertTrue(r["governed"])
            self.assertEqual(r["design"], "wiki/designs/found.md")
            # listed by designs_in
            self.assertEqual(gr.designs_in("shared/foundations", root=root),
                             ["wiki/designs/found.md"])
            # but governs NO file (empty pattern never matches)
            r2 = gr.resolve_governing_design("harness/principles.md", root=root)
            self.assertFalse(r2["governed"])

    def test_unknown_area_is_greenfield(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "parent.md", area="agentm", governs=["scripts"])
            r = gr.resolve_governing_design("nonexistent-area", root=root)
            self.assertFalse(r["governed"])

    def test_designs_in_lists_area_members(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "seam.md", area="agentm/storage", governs=["scripts/storage_seam.py"])
            _write_design(root, "sel.md", area="agentm/storage", governs=["scripts/backend_selection.py"])
            _write_design(root, "arch.md", area="agentm/architecture", governs=["scripts/**"])
            got = gr.designs_in("agentm/storage", root=root)
            self.assertEqual(got, ["wiki/designs/seam.md", "wiki/designs/sel.md"])
            self.assertEqual(gr.designs_in("nope", root=root), [])


# ── fail-safe ───────────────────────────────────────────────────────────────--

class TestFailSafe(unittest.TestCase):

    def test_empty_target_greenfield(self):
        r = gr.resolve_governing_design("")
        self.assertFalse(r["governed"])
        self.assertEqual(r["reason"], "greenfield")

    def test_never_raises_on_missing_root(self):
        r = gr.resolve_governing_design("x", root=Path("/no/such/dir/anywhere"))
        self.assertFalse(r["governed"])

    def test_result_dict_has_all_keys(self):
        r = gr.resolve_governing_design("x", root=Path("/no/such/dir"))
        self.assertEqual(set(r), {"governed", "design", "area", "reason"})


# ── CLI exit codes ──────────────────────────────────────────────────────────--

class TestCLI(unittest.TestCase):

    def _root(self, d):
        root = Path(d)
        _write_design(root, "parent.md", area="agentm", governs=["scripts"])
        return root

    def test_exit_0_governed_prints_path(self):
        with TemporaryDirectory() as d:
            root = self._root(d)
            out = io.StringIO()
            with redirect_stdout(out):
                rc = gr._main(["governs_resolver.py", "--root", str(root),
                               "scripts/x.py"])
            self.assertEqual(rc, 0)
            self.assertEqual(out.getvalue().strip(), "wiki/designs/parent.md")

    def test_exit_1_greenfield(self):
        with TemporaryDirectory() as d:
            root = self._root(d)
            err = io.StringIO()
            with redirect_stderr(err):
                rc = gr._main(["governs_resolver.py", "--root", str(root),
                               "README.md"])
            self.assertEqual(rc, 1)
            self.assertIn("no design governs", err.getvalue())

    def test_exit_2_no_args(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = gr._main(["governs_resolver.py"])
        self.assertEqual(rc, 2)

    def test_json_flag_emits_dict(self):
        with TemporaryDirectory() as d:
            root = self._root(d)
            out = io.StringIO()
            with redirect_stdout(out):
                rc = gr._main(["governs_resolver.py", "--json", "--root", str(root),
                               "scripts/x.py"])
            self.assertEqual(rc, 0)
            self.assertIn('"governed": true', out.getvalue())
            self.assertIn('"design": "wiki/designs/parent.md"', out.getvalue())

    def test_include_proposed_flag(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _write_design(root, "p.md", status="proposed", area="x",
                          governs=["scripts"])
            # default: greenfield (proposed excluded)
            rc_default = gr._main(["governs_resolver.py", "--root", str(root),
                                   "scripts/x.py"])
            self.assertEqual(rc_default, 1)
            out = io.StringIO()
            with redirect_stdout(out):
                rc_incl = gr._main(["governs_resolver.py", "--include-proposed",
                                    "--root", str(root), "scripts/x.py"])
            self.assertEqual(rc_incl, 0)


if __name__ == "__main__":
    unittest.main()
