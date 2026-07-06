#!/usr/bin/env python3
"""Unit tests for opinion_resolver — the request-by-name Opinion registry
(agentm-opinion-registry.md, AG Wave B leader 2/5).

Mirrors test_governs_resolver.py: temp-dir fixtures, no network, stdlib-only.
Auto-discovered by `python3 -m unittest discover -p 'test_*.py'` (check-all.sh).
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import opinion_resolver as orz


# ── fixtures ──────────────────────────────────────────────────────────────────

def _write_opinion(root: Path, name: str, *, question: str = "q?",
                    implements: str | None = None, composes: list[str] | None = None,
                    serves: list[str] | None = None, body: str = "The standard.",
                    kind: str = "opinion") -> Path:
    d = root / "opinions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    lines = ["---", f"name: {name}", f"kind: {kind}", f'question: "{question}"']
    if implements:
        lines.append(f"implements: {implements}")
    if composes is not None:
        lines.append(f"composes: [{', '.join(composes)}]")
    if serves is not None:
        lines.append(f"serves: [{', '.join(serves)}]")
    lines += ["---", body]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# ── frontmatter parsing ──────────────────────────────────────────────────────

class TestParseFrontmatter(unittest.TestCase):
    def test_scalar_and_body_split(self):
        fm, body = orz._parse_frontmatter("---\nname: good\nkind: opinion\n---\nThe body.")
        self.assertEqual(fm["name"], "good")
        self.assertEqual(body, "The body.")

    def test_inline_list(self):
        fm, _ = orz._parse_frontmatter("---\ncomposes: [a, b]\n---\n")
        self.assertEqual(fm["composes"], ["a", "b"])

    def test_no_frontmatter_returns_whole_text_as_body(self):
        fm, body = orz._parse_frontmatter("just a body, no frontmatter")
        self.assertEqual(fm, {})
        self.assertEqual(body, "just a body, no frontmatter")

    def test_malformed_never_raises(self):
        fm, body = orz._parse_frontmatter("---\nno closing fence")
        self.assertEqual(fm, {})


# ── build_index ───────────────────────────────────────────────────────────────

class TestBuildIndex(unittest.TestCase):
    def test_missing_opinions_dir_is_empty(self):
        with TemporaryDirectory() as td:
            self.assertEqual(orz.build_index(Path(td)), {})

    def test_indexes_valid_entries(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            _write_opinion(root, "good", question="survives a hostile read?",
                            implements="crickets/code-review")
            _write_opinion(root, "done", implements="crickets/development-lifecycle")
            index = orz.build_index(root)
            self.assertEqual(set(index), {"good", "done"})
            self.assertEqual(index["good"].implements, "crickets/code-review")
            self.assertEqual(index["good"].question, "survives a hostile read?")

    def test_non_opinion_kind_is_ignored(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            _write_opinion(root, "not-an-opinion", kind="design")
            self.assertEqual(orz.build_index(root), {})

    def test_duplicate_name_first_file_wins(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            _write_opinion(root, "aaa-good", body="first")
            (root / "opinions" / "aaa-good.md").write_text(
                (root / "opinions" / "aaa-good.md").read_text().replace("aaa-good", "good", 1),
                encoding="utf-8",
            )
            _write_opinion(root, "zzz-good", body="second")
            (root / "opinions" / "zzz-good.md").write_text(
                (root / "opinions" / "zzz-good.md").read_text().replace("zzz-good", "good", 1),
                encoding="utf-8",
            )
            index = orz.build_index(root)
            # Sorted-filename order: "aaa-good.md" is read before "zzz-good.md".
            self.assertEqual(index["good"].body, "first")


# ── opinion_resolve — the four reasons ───────────────────────────────────────

class TestOpinionResolve(unittest.TestCase):
    def test_served_when_a_supplement_exists(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            _write_opinion(root, "good", body="the coded base")
            supp_dir = root / "supplement"
            supp_dir.mkdir()
            (supp_dir / "good.md").write_text("---\n---\nthe learned supplement", encoding="utf-8")
            result = orz.opinion_resolve("good", root=root, supplement_dir=supp_dir)
            self.assertEqual(result["reason"], "served")
            self.assertEqual(result["base"], "the coded base")
            self.assertEqual(result["supplement"], "the learned supplement")

    def test_base_only_when_no_supplement(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            _write_opinion(root, "good")
            result = orz.opinion_resolve("good", root=root)
            self.assertEqual(result["reason"], "base-only")
            self.assertIsNone(result["supplement"])

    def test_no_opinion_for_unknown_name(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            _write_opinion(root, "good")
            result = orz.opinion_resolve("nonexistent", root=root)
            self.assertEqual(result["reason"], "no-opinion")
            self.assertIsNone(result["base"])

    def test_never_raises_on_a_missing_opinions_dir(self):
        with TemporaryDirectory() as td:
            result = orz.opinion_resolve("good", root=Path(td) / "nowhere")
            self.assertEqual(result["reason"], "no-opinion")

    def test_composes_and_question_carried_through(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            _write_opinion(root, "efficient", question="cheap enough?", composes=["tokens", "routing"])
            result = orz.opinion_resolve("efficient", root=root)
            self.assertEqual(result["question"], "cheap enough?")
            self.assertEqual(result["composes"], ["tokens", "routing"])


# ── the real nine-name catalog conforms ──────────────────────────────────────

class TestLiveCatalogConforms(unittest.TestCase):
    def test_all_nine_stubs_resolve_base_only(self):
        repo_root = Path(__file__).resolve().parent.parent
        for name in ("done", "good", "efficient", "how-we-engineer", "recoverable",
                     "private", "ready", "simple", "worth-knowing"):
            result = orz.opinion_resolve(name, root=repo_root)
            self.assertIn(result["reason"], ("served", "base-only"), (name, result))
            self.assertTrue(result["base"], name)


# ── CLI exit codes ────────────────────────────────────────────────────────────

class TestCLI(unittest.TestCase):
    def test_served_exits_0_and_prints_body(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            _write_opinion(root, "good", body="the base")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = orz.main(["--root", str(root), "good"])
            self.assertEqual(rc, 0)
            self.assertIn("the base", buf.getvalue())

    def test_no_opinion_exits_1(self):
        with TemporaryDirectory() as td:
            rc = orz.main(["--root", str(td), "nonexistent"])
            self.assertEqual(rc, 1)

    def test_json_flag(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            _write_opinion(root, "good")
            buf = io.StringIO()
            with redirect_stdout(buf):
                orz.main(["--root", str(root), "--json", "good"])
            self.assertIn('"reason"', buf.getvalue())


if __name__ == "__main__":
    unittest.main()
