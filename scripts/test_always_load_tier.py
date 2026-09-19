#!/usr/bin/env python3
"""The always-load tier as it reaches a session: the block strip and the ceiling.

Two changes, one subject (agentm-vault plan 12 task 2).

**The strip.** The filing contract is 22 KB and three fifths of it is a fenced
`storage-rules` block the Go daemon parses out of the file at runtime. Injecting
it spends eleven kilobytes of every session's first prompt on a machine surface
no reader reads. The loader strips it the way it strips frontmatter.

**The ceiling.** 40,000 tokens, the operator's number. The battery gate holds the
*packaged* set to it; the live tier gets a visible line instead, because the
files there are the operator's own and the ceiling is against creep rather than
against them. The line comes from the brief hook and not the loader, for the
reason the design names outright: the loader's output is large and the host
collapses it unread, so a warning printed there is a warning nobody sees.
"""
from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import recall  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


alb = _load("_alb", "scripts/check-always-load-budget.py")
pb = _load("_pb", "scripts/project_brief.py")


class TheMachineBlockIsStripped(unittest.TestCase):
    def test_a_storage_rules_fence_leaves_the_injection(self):
        body = ("Prose the reader wants.\n\n"
                "## The block\n\n"
                "```storage-rules\nclasses:\n  semantic: facts\n```\n\n"
                "More prose.\n")
        out = recall._strip_machine_blocks(body)
        self.assertNotIn("```storage-rules", out)
        self.assertNotIn("semantic: facts", out)
        self.assertIn("Prose the reader wants.", out)
        self.assertIn("More prose.", out)

    def test_the_absence_is_named_rather_than_silent(self):
        """A hole where two hundred lines were reads as a truncation bug. The
        line says the daemon reads the block from the file, which is true and
        is what a session needs to know."""
        out = recall._strip_machine_blocks("```storage-rules\nx: 1\n```\n")
        self.assertIn("storage-rules", out)
        self.assertIn("not injected", out)

    def test_the_heading_above_it_stays(self):
        out = recall._strip_machine_blocks("## The block\n\n```storage-rules\nx: 1\n```\n")
        self.assertIn("## The block", out)

    def test_an_ordinary_fence_is_left_alone(self):
        """Only a fence whose info string names a machine surface. A python
        example in a preferences file is prose the operator wrote."""
        body = "```python\nprint('hello')\n```\n"
        self.assertEqual(recall._strip_machine_blocks(body), body)
        body = "```\nplain\n```\n"
        self.assertEqual(recall._strip_machine_blocks(body), body)

    def test_an_unclosed_fence_is_left_alone(self):
        """A body with one is malformed, and guessing where it ended would cut
        prose out of the injection with nothing to get it back from."""
        body = "before\n```storage-rules\nx: 1\nno close\n"
        self.assertEqual(recall._strip_machine_blocks(body), body)

    def test_two_blocks_both_go(self):
        body = "a\n```storage-rules\n1\n```\nb\n```storage-rules\n2\n```\nc\n"
        out = recall._strip_machine_blocks(body)
        self.assertNotIn("```storage-rules", out)
        for kept in ("a", "b", "c"):
            self.assertIn(kept, out)

    def test_the_loader_injects_a_stripped_body(self):
        """End to end through `session_start`, because the strip existing is
        not the same claim as the loader calling it."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "standards").mkdir()
            (root / "standards" / "storage-rules.md").write_text(
                "---\nkind: reference\n---\nThe contract.\n\n"
                "```storage-rules\nclasses:\n  semantic: facts\n```\n",
                encoding="utf-8")
            buf = io.StringIO()
            recall.session_start(vault=root, stdout=buf, stderr=io.StringIO())
            out = buf.getvalue()
        self.assertIn("The contract.", out)
        self.assertNotIn("semantic: facts", out)


class TheCeiling(unittest.TestCase):
    def test_the_packaged_set_is_under_it(self):
        total, rows = alb.tier_tokens(alb.PACKAGED)
        self.assertTrue(rows, "the packaged tier resolved to no files")
        self.assertLessEqual(total, alb.CEILING_TOKENS)

    def test_the_gate_measures_the_injected_body_not_the_stored_file(self):
        """Counting the machine block would gate on bytes nobody is charged
        for, and would hide real creep behind a number that was already mostly
        fenced block."""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "contract.md"
            filler = "machine line\n" * 2_000
            f.write_text(f"prose\n\n```storage-rules\n{filler}```\n", encoding="utf-8")
            counted, _ = alb.tier_tokens([f])
            raw = recall._estimate_tokens(f.read_text(encoding="utf-8"))
        self.assertLess(counted, raw // 10)

    def test_the_gate_self_test_passes(self):
        self.assertEqual(alb.self_test(out=io.StringIO()), 0)

    def test_an_oversized_packaged_set_fails(self):
        with tempfile.TemporaryDirectory() as d:
            big = Path(d) / "huge.md"
            big.write_text("x " * (alb.CEILING_TOKENS * 3), encoding="utf-8")
            total, _ = alb.tier_tokens([big])
        self.assertGreater(total, alb.CEILING_TOKENS)


class TheVisibleLine(unittest.TestCase):
    def _with_root(self, root):
        import harness_memory as hm
        saved = hm.memory_root
        hm.memory_root = lambda: root
        self.addCleanup(lambda: setattr(hm, "memory_root", saved))

    def test_it_fires_when_the_live_tier_is_over(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "standards").mkdir()
            (root / "standards" / "huge.md").write_text(
                "x " * (alb.CEILING_TOKENS * 4), encoding="utf-8")
            self._with_root(root)
            line = pb.ceiling_line()
        self.assertIsNotNone(line)
        self.assertIn("over the", line)
        self.assertIn(f"{alb.CEILING_TOKENS:,}", line)

    def test_it_is_silent_when_the_tier_is_under(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "standards").mkdir()
            (root / "standards" / "small.md").write_text("a rule\n", encoding="utf-8")
            self._with_root(root)
            self.assertIsNone(pb.ceiling_line())

    def test_it_never_raises(self):
        """A session must start even when nothing about the tier can be
        measured. A missing number is not worth a blocked boot."""
        import harness_memory as hm
        saved = hm.memory_root

        def boom():
            raise RuntimeError("no root")

        hm.memory_root = boom
        self.addCleanup(lambda: setattr(hm, "memory_root", saved))
        self.assertIsNone(pb.ceiling_line())

    def test_the_brief_hook_prints_it_and_the_loader_does_not(self):
        """Which hook prints it is the design call, not an implementation
        detail: a large SessionStart output is collapsed unread, so the line
        has to come from the small hook."""
        hook = (_REPO / "harness" / "hooks" / "project-brief-session-start"
                / "project-brief-session-start.sh").read_text(encoding="utf-8")
        self.assertIn("--ceiling-line", hook)
        loader_hook = (_REPO / "harness" / "hooks" / "memory-recall-session-start")
        for f in loader_hook.glob("*.sh"):
            self.assertNotIn("--ceiling-line", f.read_text(encoding="utf-8"),
                             f"{f.name} prints the ceiling line; it is collapsed there")


if __name__ == "__main__":
    unittest.main()
