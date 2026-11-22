#!/usr/bin/env python3
"""Unit tests for check-agent-tools.py.

Each case writes a fixture repo with one agent and asserts the gate's exit
code and message, so a test fails if the rule it names stops being enforced:
  - a read-only description with no `tools:` line fails (the adapt-evaluator
    shape before the fix);
  - a read-only agent whose `tools:` grants Write, Edit, MultiEdit,
    NotebookEdit or `*` fails;
  - a body self-declaration ("read-only by contract", "a read-only
    sub-agent") counts as a claim; a writer naming one tool read-only does not;
  - no `tools:` line fails unless the description says "Full tool access";
  - a malformed `tools:` value fails; a YAML list is accepted;
  - agents under adapters/*/agents/ are scanned too;
  - the real tree passes.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("check_agent_tools", _HERE / "check-agent-tools.py")
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore[union-attr]


def _agent(description: str, tools: str | None = None, body: str = "Does its job.\n") -> str:
    lines = ["---", "name: probe", f"description: {description}"]
    if tools is not None:
        lines.append(f"tools: {tools}")
    lines += ["kind: agent", "---", "", body]
    return "\n".join(lines)


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, content: str, rel: str = "harness/agents/probe.md") -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def run_gate(self) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = _mod.main(["--root", str(self.root)])
        return rc, out.getvalue() + err.getvalue()


class ReadOnlyClaims(_Fixture):
    def test_read_only_description_without_tools_fails(self):
        self.put(_agent("Read-only judge for candidates."))
        rc, msg = self.run_gate()
        self.assertEqual(rc, 1)
        self.assertIn("says it is read-only but has no 'tools:' line", msg)

    def test_read_only_with_each_write_tool_fails(self):
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "*"):
            with self.subTest(tool=tool):
                self.put(_agent("A read-only grader.", f"Read, Grep, {tool}"))
                rc, msg = self.run_gate()
                self.assertEqual(rc, 1)
                self.assertIn(f"grants {tool}", msg)

    def test_read_only_with_read_tools_passes(self):
        self.put(_agent("Read-only explorer.", "Read, Glob, Grep, Bash"))
        rc, msg = self.run_gate()
        self.assertEqual(rc, 0, msg)

    def test_body_read_only_by_contract_counts(self):
        self.put(_agent("Coordinator surface.", "Read, Write", "Read-only by contract.\n"))
        rc, msg = self.run_gate()
        self.assertEqual(rc, 1)
        self.assertIn("grants Write", msg)

    def test_body_a_read_only_sub_agent_counts(self):
        self.put(_agent("Judge.", "Read, Edit", "A read-only sub-agent that judges.\n"))
        rc, msg = self.run_gate()
        self.assertEqual(rc, 1)
        self.assertIn("grants Edit", msg)

    def test_writer_naming_one_tool_read_only_is_not_flagged(self):
        body = "Uses Bash (read-only: `git diff`, `git log`) and writes pages.\n"
        self.put(_agent("Maintains the wiki.", "Read, Write, Edit, Bash", body))
        rc, msg = self.run_gate()
        self.assertEqual(rc, 0, msg)


class MissingTools(_Fixture):
    def test_no_tools_and_no_full_access_statement_fails(self):
        self.put(_agent("Judges candidates and writes entries."))
        rc, msg = self.run_gate()
        self.assertEqual(rc, 1)
        self.assertIn("has no 'tools:' line, so Claude Code grants it every tool", msg)

    def test_full_tool_access_statement_passes_without_tools(self):
        self.put(_agent("Active executor role. Full tool access."))
        rc, msg = self.run_gate()
        self.assertEqual(rc, 0, msg)


class ToolsShape(_Fixture):
    def test_malformed_tools_value_fails(self):
        self.put(_agent("Judge.", "{Read: true}"))
        rc, msg = self.run_gate()
        self.assertEqual(rc, 1)
        self.assertIn("'tools' must be a comma-separated string or a list", msg)

    def test_empty_tools_value_fails(self):
        self.put(_agent("Judge.", '""'))
        rc, msg = self.run_gate()
        self.assertEqual(rc, 1)

    def test_yaml_list_is_read(self):
        self.put(_agent("Read-only judge.", "[Read, Write]"))
        rc, msg = self.run_gate()
        self.assertEqual(rc, 1)
        self.assertIn("grants Write", msg)

    def test_unparseable_frontmatter_is_a_setup_error(self):
        self.put("---\nname: [unclosed\n---\nbody\n")
        rc, msg = self.run_gate()
        self.assertEqual(rc, 2)
        self.assertIn("invalid YAML frontmatter", msg)


class Scope(_Fixture):
    def test_adapter_agents_are_scanned(self):
        self.put(_agent("Read-only judge."), "adapters/claude-code/agents/probe.md")
        rc, msg = self.run_gate()
        self.assertEqual(rc, 1)
        self.assertIn("adapters/claude-code/agents/probe.md", msg)

    def test_files_outside_agent_dirs_are_ignored(self):
        self.put(_agent("Read-only judge."), "harness/skills/probe.md")
        rc, msg = self.run_gate()
        self.assertEqual(rc, 0, msg)


class RealTree(unittest.TestCase):
    def test_shipped_agents_pass(self):
        violations, setup = _mod.check(_HERE.parent)
        self.assertEqual(setup, [])
        self.assertEqual(violations, [])
        self.assertTrue(_mod.agent_files(_HERE.parent), "no shipped agents found to check")

    def test_adapt_evaluator_writes_only_through_write(self):
        text = (_HERE.parent / "harness/agents/adapt-evaluator.md").read_text(encoding="utf-8")
        fm, _ = _mod.split_frontmatter(text)
        self.assertEqual(_mod.parse_tools(fm["tools"]), ["Read", "Glob", "Grep", "Write"])


if __name__ == "__main__":
    unittest.main()
