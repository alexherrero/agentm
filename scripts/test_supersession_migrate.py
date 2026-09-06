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

    def test_a_successor_that_is_not_in_the_vault_revives_the_note(self):
        dead = "/somewhere/else/Obsidian/Agent/personal/_inbox/gone-successor-1.md"
        gone = self._w("gone-successor.md", f"title: g\nstatus: superseded\nlifecycle: active\nsupersedes: {dead}")
        rows = sm.plan(self.vault)
        self.assertEqual(rows, [("memory/semantic/gone-successor.md", "revive", dead)])
        n = sm.apply(self.vault, rows, today="2026-09-05", journal=self.root / "journal.jsonl")
        self.assertEqual(n, 1)
        t = gone.read_text(encoding="utf-8")
        self.assertIn("status: active\n", t)
        self.assertIn("lifecycle: active\n", t)
        self.assertNotIn("supersedes:", t)
        self.assertNotIn("superseded_by:", t)
        self.assertNotIn("lifecycle_since:", t)
        self.assertTrue(t.endswith("\n\nbody\n"), "the body is untouched")
        line = (self.root / "journal.jsonl").read_text(encoding="utf-8").strip()
        self.assertIn('"actor": "migration"', line)
        self.assertIn("gone-successor-1.md", line, "the dead pointer survives in the journal")
        self.assertEqual(sm.plan(self.vault), [], "idempotent: a revived note is a plain live note")

    def test_the_successor_resolves_inside_the_vault(self):
        self._w("winner.md", "title: w\nstatus: active")
        (self.vault / "memory" / "procedural").mkdir()
        (self.vault / "memory" / "procedural" / "elsewhere.md").write_text(
            "---\ntitle: e\nslug: named-by-slug\nstatus: active\n---\n\nbody\n", encoding="utf-8")
        self._w("abs.md", f"title: a\nstatus: superseded\nsupersedes: {self.vault / 'memory' / 'semantic' / 'winner.md'}")
        self._w("stem.md", "title: s\nstatus: superseded\nsupersedes: winner")
        self._w("slug.md", "title: l\nstatus: superseded\nsupersedes: named-by-slug")
        self._w("source.md", "title: v\nstatus: superseded\nsuperseded_by: github-issues at 2026-09-01")
        by = {rel: (action, succ) for rel, action, succ in sm.plan(self.vault)}
        self.assertEqual(by["memory/semantic/abs.md"], ("convert", "memory/semantic/winner.md"), "an absolute path under the vault is written relative")
        self.assertEqual(by["memory/semantic/stem.md"], ("convert", "memory/semantic/winner.md"))
        self.assertEqual(by["memory/semantic/slug.md"], ("convert", "memory/procedural/elsewhere.md"))
        self.assertEqual(by["memory/semantic/source.md"], ("convert", "github-issues at 2026-09-01"), "a source version is kept verbatim")
        sm.apply(self.vault, sm.plan(self.vault), today="2026-09-05", journal=self.root / "journal.jsonl")
        t = (self.vault / "memory" / "semantic" / "abs.md").read_text(encoding="utf-8")
        self.assertIn("superseded_by: memory/semantic/winner.md\n", t)
        self.assertNotIn(str(self.vault), t, "no absolute path is written into the note")

    def test_every_note_in_the_vault_is_in_scope(self):
        # The gate scans the whole vault, so the migration walks the same
        # population: a telemetry digest outside the memory classes is in
        # scope, a note under a dot-directory is not.
        self._w("winner.md", "title: w\nstatus: active")
        (self.vault / "diagnostics" / "digests").mkdir(parents=True)
        (self.vault / ".trash").mkdir()
        (self.vault / "diagnostics" / "digests" / "old-digest.md").write_text(
            "---\nkind: telemetry\nstatus: superseded\nsupersedes: /old/root/personal/_inbox/older-digest.md\n---\n\nSpend.\n", encoding="utf-8")
        (self.vault / "diagnostics" / "note.md").write_text(
            "---\nkind: telemetry\nstatus: superseded\nsupersedes: memory/semantic/winner.md\n---\n\nA report.\n", encoding="utf-8")
        (self.vault / ".trash" / "binned.md").write_text(
            "---\nstatus: superseded\nsupersedes: memory/semantic/winner.md\n---\n\nBinned.\n", encoding="utf-8")
        by = {rel: (action, succ) for rel, action, succ in sm.plan(self.vault)}
        self.assertEqual(by["diagnostics/digests/old-digest.md"], ("revive", "/old/root/personal/_inbox/older-digest.md"))
        self.assertEqual(by["diagnostics/note.md"], ("convert", "memory/semantic/winner.md"))
        self.assertNotIn(".trash/binned.md", by)
        self.assertEqual(sm.apply(self.vault, sm.plan(self.vault), today="2026-09-05", journal=self.root / "journal.jsonl"), 2)
        self.assertIn("status: superseded", (self.vault / ".trash" / "binned.md").read_text(encoding="utf-8"))

    def test_the_cli_reports_before_it_writes(self):
        self._w("y.md", "title: y\nstatus: active")
        self._w("x.md", "title: x\nstatus: superseded\nsupersedes: memory/semantic/y.md")
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = sm.main(["--vault", str(self.vault)])
        self.assertEqual(rc, 0)
        self.assertIn("1 to convert, 0 to revive (successor not in the vault), 0 unresolvable", out.getvalue())
        self.assertIn("status: superseded", (self.vault / "memory" / "semantic" / "x.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
