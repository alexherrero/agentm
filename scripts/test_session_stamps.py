#!/usr/bin/env python3
"""The trace and the miner stamp the session's binding (agentm-vault plan 09, task 3).

A session bound to a project — `.harness/project.json` names it and
`.harness/active-plan` names the task — writes a trace and a HIGH card that
carry `project:` and `task:`, in the card's order. The binding is read from the
directory the transcript records, flags win over it, and an unbound session
stamps neither field.

Run directly:

    python3 scripts/test_session_stamps.py
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_SKILL), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import card_shape as cs  # noqa: E402
import episodic_trace as et  # noqa: E402
import reflect  # noqa: E402
import session_binding as sb  # noqa: E402


def _repo(root: Path, name: str = "repo", *, project: str | None = "agentm",
          task: str | None = "build-the-brief") -> Path:
    repo = root / name
    (repo / ".harness").mkdir(parents=True)
    if project:
        (repo / ".harness" / "project.json").write_text(json.dumps({"vault_project": project}), encoding="utf-8")
    if task:
        (repo / ".harness" / "active-plan").write_text(task + "\n", encoding="utf-8")
    return repo


def _transcript(path: Path, cwd: Path) -> None:
    def line(kind, content, ts):
        role = "user" if kind == "user" else "assistant"
        return json.dumps({"type": kind, "cwd": str(cwd), "timestamp": ts,
                           "message": {"role": role, "content": content}})

    captured = "captured\n" + json.dumps({"path": "memory/semantic/archive-line-ruling.md",
                                          "slug": "archive-line-ruling"})
    rows = [
        line("user", [{"type": "text", "text": "Help me tune the archive thresholds."}], "2026-09-05T10:00:00Z"),
        line("assistant", [{"type": "tool_use", "id": "t2", "name": "mcp__agentmemory__memory_capture",
                            "input": {"text": "x"}}], "2026-09-05T10:05:00Z"),
        line("user", [{"type": "tool_result", "tool_use_id": "t2",
                       "content": [{"type": "text", "text": captured}]}], "2026-09-05T10:05:01Z"),
        line("assistant", [{"type": "text", "text": "Done."}], "2026-09-05T10:10:00Z"),
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


class TheTrace(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agentm-stamps-trace-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.vault = self.root / "vault"
        (self.vault / "memory" / "episodic").mkdir(parents=True)
        self.history = self.root / "recall-history.jsonl"
        self.history.write_text("", encoding="utf-8")
        self.transcript = self.root / "session.jsonl"

    def _written(self, cwd: Path, *flags: str) -> str:
        _transcript(self.transcript, cwd)
        rc = et.main([str(self.transcript), "--session", "d46db2c7-0000", "--vault-path", str(self.vault),
                      "--history", str(self.history), *flags])
        self.assertEqual(rc, 0)
        (trace,) = (self.vault / "memory" / "episodic").glob("*.md")
        return trace.read_text(encoding="utf-8")

    # The trace writes its scalars double-quoted, as its `project:` line did
    # before the task field joined it.
    def test_a_bound_session_stamps_its_project_and_task(self) -> None:
        text = self._written(_repo(self.root))
        self.assertIn('\nproject: "agentm"\ntask: "build-the-brief"\n', text)
        self.assertEqual(cs.order_findings(cs.keys(text)), [])

    def test_flags_win_over_the_binding(self) -> None:
        text = self._written(_repo(self.root), "--project", "crickets", "--task", "ship-it")
        self.assertIn('\nproject: "crickets"\ntask: "ship-it"\n', text)

    def test_a_project_without_a_task_stamps_the_project_only(self) -> None:
        text = self._written(_repo(self.root, task=None))
        self.assertIn('\nproject: "agentm"\n', text)
        self.assertNotIn("\ntask:", text)

    def test_an_unbound_session_stamps_neither(self) -> None:
        text = self._written(_repo(self.root, project=None))
        self.assertNotIn("\nproject:", text)
        self.assertNotIn("\ntask:", text)


class TheMiner(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agentm-stamps-miner-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "memory").mkdir(parents=True)

    def _route(self, binding) -> str:
        cand = reflect.Candidate(category="workflows", confidence="HIGH", slug="battery-first",
                                 title="Battery first", body="User stated: always run the battery first.",
                                 rationale="test", excerpts=[])
        stats = reflect.route_candidates([cand], [], vault=self.root, mode=reflect.ROUTE_MODE_AUTO,
                                         binding=binding, stdin=io.StringIO(), stdout=io.StringIO(),
                                         stderr=io.StringIO())
        self.assertEqual(stats["auto_saved"], 1, stats)
        return (self.root / "memory" / "procedural" / "battery-first.md").read_text(encoding="utf-8")

    def test_a_high_card_carries_the_binding_in_the_cards_order(self) -> None:
        text = self._route(sb.Binding("agentm", "build-the-brief"))
        self.assertIn("\nproject: agentm\n", text)
        self.assertIn("\ntask: build-the-brief\n", text)
        self.assertEqual(cs.order_findings(cs.keys(text)), [])

    def test_an_unbound_route_stamps_neither(self) -> None:
        for binding in (None, sb.UNBOUND):
            with self.subTest(binding=binding):
                shutil.rmtree(self.root / "memory")
                (self.root / "memory").mkdir()
                text = self._route(binding)
                self.assertNotIn("\nproject:", text)
                self.assertNotIn("\ntask:", text)


if __name__ == "__main__":
    unittest.main()
