#!/usr/bin/env python3
"""test_supersession_migrate.py — one shape for the superseded relation (PLAN-superseded-vocabulary, task 4)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

import supersession_migrate as sm  # noqa: E402


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "memory" / "semantic").mkdir(parents=True)
        self._env = os.environ.get("AGENTM_STATE_DIR")
        os.environ["AGENTM_STATE_DIR"] = str(self.root / "state")
        self.addCleanup(lambda: os.environ.pop("AGENTM_STATE_DIR", None) if self._env is None else os.environ.__setitem__("AGENTM_STATE_DIR", self._env))

    def _w(self, name, fm):
        p = self.vault / "memory" / "semantic" / name
        p.write_text("---\n" + fm + "\n---\n\nbody\n", encoding="utf-8")
        return p

    def test_the_four_shapes(self):
        old = self._w("old-shape.md", "title: a\nstatus: superseded\nsupersedes: memory/semantic/winner.md")
        both = self._w("both.md", "title: b\nstatus: superseded\nlifecycle: superseded\nsuperseded_by: memory/semantic/winner.md\nsupersedes: memory/semantic/winner.md")
        done = self._w("done.md", "title: c\nstatus: active\nlifecycle: superseded\nsuperseded_by: memory/semantic/winner.md")
        lost = self._w("lost.md", "title: d\nstatus: superseded")
        self._w("winner.md", "title: w\nstatus: active\nsupersedes: memory/semantic/old-shape.md")
        rows = sm.plan(self.vault)
        by = {rel: (action, succ) for rel, action, succ in rows}
        self.assertEqual(by["memory/semantic/old-shape.md"], ("convert", "memory/semantic/winner.md"))
        self.assertEqual(by["memory/semantic/both.md"], ("convert", "memory/semantic/winner.md"))
        self.assertEqual(by["memory/semantic/lost.md"], ("unresolvable", ""))
        self.assertNotIn("memory/semantic/done.md", by, "already the contract's shape")
        self.assertNotIn("memory/semantic/winner.md", by, "a winner-side supersedes: is not a loser")
        n = sm.apply(self.vault, rows, today="2026-09-05", journal=self.root / "journal.jsonl")
        self.assertEqual(n, 2)
        for p in (old, both):
            t = p.read_text(encoding="utf-8")
            self.assertIn("status: active\n", t)
            self.assertIn("lifecycle: superseded\n", t)
            self.assertIn("superseded_by: memory/semantic/winner.md\n", t)
            self.assertIn("lifecycle_since: 2026-09-05\n", t)
            self.assertNotIn("supersedes:", t)
            self.assertTrue(t.endswith("\n\nbody\n"), "the body is untouched")
        self.assertIn("status: superseded", lost.read_text(encoding="utf-8"), "an unresolvable note is left alone")
        lines = (self.root / "journal.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn('"actor": "migration"', lines[0])
        # Idempotent: a second run finds nothing left to convert.
        self.assertEqual([r for r in sm.plan(self.vault) if r[1] == "convert"], [])

    def test_the_cli_reports_before_it_writes(self):
        self._w("x.md", "title: x\nstatus: superseded\nsupersedes: memory/semantic/y.md")
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = sm.main(["--vault", str(self.vault)])
        self.assertEqual(rc, 0)
        self.assertIn("1 to convert, 0 unresolvable", out.getvalue())
        self.assertIn("status: superseded", (self.vault / "memory" / "semantic" / "x.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
