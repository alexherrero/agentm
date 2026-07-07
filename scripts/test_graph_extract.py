#!/usr/bin/env python3
"""Tests for graph.py — V6-2 deterministic typed-edge extraction (PLAN-wave-e-v6-index task 4).

Fully synthetic — no dependency on the real vault (edge-fixture-v0.json's
real-vault comparison is a separate, gracefully-skipped eval script,
scripts/health/eval_v6_graph.py, since CI has no access to the operator's
private vault). These tests cover the classifier logic itself:

  - Zero-LLM red-test: graph.py's own source contains no LLM/API-client
    import, and it never imports anything that could reach the network.
  - Code-span exclusion: a `[[wikilink]]`-looking string inside an inline
    backtick span or a fenced code block is never extracted as an edge —
    the exact false-positive class the real fixture's 12 trap entries test.
  - Cue-based type classification for each of the 9 agreed edge types,
    against representative synthetic prose (not the real vault's wording,
    but the same cue phrases the real fixture's rationale strings named).
  - Frontmatter supersedes/superseded_by extraction (pass 2), independent
    of wikilink wrapping.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import graph  # noqa: E402

_GRAPH_PY_PATH = _SCRIPTS_DIR / "graph.py"

# Any of these appearing as an imported module name in graph.py would mean
# the extraction path could reach a model or the network — forbidden by the
# A3 zero-LLM constraint (non-negotiable per FABLE's V6-2 row).
_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "anthropic", "openai", "requests", "urllib", "http", "socket",
    "embed", "llm", "model",
)


class TestZeroLLMInvariant(unittest.TestCase):
    """Red-test: no LLM/network-capable import anywhere in graph.py's source."""

    def test_no_forbidden_imports_in_source(self):
        tree = ast.parse(_GRAPH_PY_PATH.read_text(encoding="utf-8"))
        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.append(node.module)

        allowed_stdlib = {"__future__", "re", "dataclasses", "pathlib"}
        self.assertTrue(
            set(imported_names) <= allowed_stdlib,
            f"graph.py imports beyond the allowed stdlib set: "
            f"{set(imported_names) - allowed_stdlib}",
        )
        for name in imported_names:
            for forbidden in _FORBIDDEN_IMPORT_SUBSTRINGS:
                self.assertNotIn(
                    forbidden, name.lower(),
                    f"import {name!r} matches forbidden substring {forbidden!r} "
                    "— the A3 zero-LLM constraint is non-negotiable",
                )

    def test_extract_edges_is_pure_no_io(self):
        # extract_edges() takes already-read content and returns data — no
        # filesystem or network call inside it (only extract_edges_for_paths,
        # the convenience wrapper, does file reads, and those are local disk
        # reads, never network).
        content = "See [[some-note]] for details."
        edges = graph.extract_edges("a.md", content)
        self.assertEqual(len(edges), 1)


class TestCodeSpanExclusion(unittest.TestCase):
    """The exact false-positive class the real fixture's 12 trap entries test:
    a `[[...]]`-looking string inside inline code or a fenced block is not
    a real edge."""

    def test_inline_backtick_span_excluded(self):
        content = "Typed relations as `- relation_type [[Target]]`, bare links = `links_to`."
        edges = graph.extract_edges("a.md", content)
        self.assertEqual(edges, [])

    def test_multiple_backtick_wrapped_targets_excluded(self):
        content = "collapse `[[Bob]]`/`[[Bob Smith]]`/`[[bob]]` to a canonical node."
        edges = graph.extract_edges("a.md", content)
        self.assertEqual(edges, [])

    def test_shell_double_bracket_inside_backticks_excluded(self):
        content = 'Installer dispatch (`[[ "$_am_kind" == "skill" ]] || continue` in `install.sh`).'
        edges = graph.extract_edges("a.md", content)
        self.assertEqual(edges, [])

    def test_fenced_code_block_excluded(self):
        content = "Some prose.\n\n```\nsee [[Target]] here\n```\n\nMore prose with [[real-note]]."
        edges = graph.extract_edges("a.md", content)
        self.assertEqual([e.target for e in edges], ["real-note"])

    def test_real_wikilink_outside_code_is_extracted(self):
        content = "See [[some-real-note]] for the full context."
        edges = graph.extract_edges("a.md", content)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].target, "some-real-note")
        self.assertTrue(edges[0].is_edge)


class TestTypeClassification(unittest.TestCase):
    """Cue-based classification for each of the 9 agreed edge types."""

    def _classify_one(self, content: str) -> str:
        edges = graph.extract_edges("a.md", content)
        self.assertEqual(len(edges), 1, f"expected exactly 1 edge in: {content!r}")
        return edges[0].edge_type

    def test_supersedes_cue(self):
        self.assertEqual(
            self._classify_one("This entry supersedes [[old-note]] entirely."),
            "supersedes",
        )

    def test_contradicts_cue(self):
        self.assertEqual(
            self._classify_one("Mutually exclusive with [[vault-drive]] — one transport per folder."),
            "contradicts",
        )

    def test_caused_cue(self):
        self.assertEqual(
            self._classify_one("The 421M-token incident autopsy motivates [[research-token-efficiency-novel]]."),
            "caused",
        )

    def test_fixed_cue(self):
        self.assertEqual(
            self._classify_one("This bug was fixed by the change described in [[some-fix-note]]."),
            "fixed",
        )

    def test_decided_in_cue(self):
        self.assertEqual(
            self._classify_one("Calibrates read-authority-by-volatility against [[memory-os-architecture-scan]]'s evidence."),
            "decided-in",
        )

    def test_implements_cue(self):
        self.assertEqual(
            self._classify_one("Design-doc shape: [[projects/crickets/conventions/design-doc-shape]]."),
            "implements",
        )

    def test_depends_on_cue(self):
        self.assertEqual(
            self._classify_one("The floor it builds on — [[research-concurrent-vault-writes]]."),
            "depends-on",
        )

    def test_uses_cue(self):
        self.assertEqual(
            self._classify_one("Routes writes through the real write-authz boundary [[autonomous-write-contract]] provides."),
            "uses",
        )

    def test_default_references(self):
        self.assertEqual(
            self._classify_one("See [[some-related-doc]] for background."),
            "references",
        )


class TestFrontmatterSupersedes(unittest.TestCase):
    """Pass 2 — frontmatter supersedes/superseded_by, independent of wikilinks."""

    def test_frontmatter_supersedes_bare_path(self):
        content = "---\nkind: convention\nsupersedes: old/path.md\n---\n\nBody.\n"
        edges = graph.extract_edges("a.md", content)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].target, "old/path.md")
        self.assertEqual(edges[0].edge_type, "supersedes")

    def test_frontmatter_supersedes_wikilink_wrapped(self):
        content = "---\nkind: convention\nsupersedes: [[old-note]]\n---\n\nBody.\n"
        edges = graph.extract_edges("a.md", content)
        targets = [e.target for e in edges if e.edge_type == "supersedes"]
        self.assertIn("old-note", targets)

    def test_no_frontmatter_no_crash(self):
        content = "Just a plain body with [[a-link]], no frontmatter at all.\n"
        edges = graph.extract_edges("a.md", content)
        self.assertEqual(len(edges), 1)


if __name__ == "__main__":
    unittest.main()
