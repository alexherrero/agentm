#!/usr/bin/env python3
"""Unit tests for persona_compile.py — the per-host launch compile
(agentm-persona-activation.md, AG Wave B leader 4/5)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import persona_compile as pc


def _make_root(tmp: str) -> Path:
    root = Path(tmp)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "personas").mkdir(parents=True, exist_ok=True)
    return root


def _write_persona(root: Path, name: str, frontmatter_extra: str = "",
                    body: str = "The stance body.") -> None:
    (root / "personas" / f"{name}.md").write_text(
        f"---\nkind: persona\nname: {name}\nrequires: []\nenhances: []\n"
        + frontmatter_extra + f"\n---\n{body}\n",
        encoding="utf-8",
    )


class TestCompileClaudeCode(unittest.TestCase):
    def test_compiles_name_and_stance(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "reviewer", body="Adversarial critic.")
            out = pc.compile_claude_code("reviewer", root=root)
            self.assertIn("name: reviewer", out)
            self.assertIn("kind: agent", out)
            self.assertIn("Adversarial critic.", out)

    def test_triggers_become_description(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "reviewer", frontmatter_extra="triggers: [review-phase]")
            out = pc.compile_claude_code("reviewer", root=root)
            self.assertIn("description: Activates on: review-phase.", out)

    def test_no_triggers_gets_default_description(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "p")
            out = pc.compile_claude_code("p", root=root)
            self.assertIn("description: The p persona.", out)

    def test_tier_becomes_model(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "p", frontmatter_extra="tier: T2")
            out = pc.compile_claude_code("p", root=root)
            self.assertIn("model: strongest", out)

    def test_no_tier_omits_model_line(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "p")
            out = pc.compile_claude_code("p", root=root)
            self.assertNotIn("model:", out)

    def test_gate_failing_persona_raises(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "bad", frontmatter_extra="always_load: true")
            with self.assertRaises(pc.PersonaCompileError):
                pc.compile_claude_code("bad", root=root)

    def test_not_found_raises(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            with self.assertRaises(pc.PersonaCompileError):
                pc.compile_claude_code("nonexistent", root=root)

    def test_never_fabricates_a_tools_allowlist(self):
        """No per-capability tool manifest exists in this repo's
        capability_resolver — the compiler must not invent a tools: line."""
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "p", frontmatter_extra="enhances: [code-review]")
            out = pc.compile_claude_code("p", root=root)
            self.assertNotIn("tools:", out)


class TestCompileAntigravity(unittest.TestCase):
    def test_compiles_as_a_skill(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "reviewer", body="Adversarial critic.")
            out = pc.compile_antigravity("reviewer", root=root)
            self.assertIn("kind: skill", out)
            self.assertIn("supported_hosts: [antigravity]", out)
            self.assertIn("Adversarial critic.", out)

    def test_gate_failing_persona_raises(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "bad", frontmatter_extra="always_load: true")
            with self.assertRaises(pc.PersonaCompileError):
                pc.compile_antigravity("bad", root=root)


class TestCLI(unittest.TestCase):
    def test_cli_writes_the_output_file(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "reviewer", body="stance")
            out_path = root / ".claude" / "agents" / "reviewer.md"
            rc = pc._main(["--host", "claude-code", "--name", "reviewer",
                           "--root", str(root), "--out", str(out_path)])
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.is_file())
            self.assertIn("stance", out_path.read_text(encoding="utf-8"))

    def test_cli_exits_1_on_compile_error(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            out_path = root / "out.md"
            rc = pc._main(["--host", "claude-code", "--name", "nonexistent",
                           "--root", str(root), "--out", str(out_path)])
            self.assertEqual(rc, 1)
            self.assertFalse(out_path.exists())


if __name__ == "__main__":
    unittest.main()
