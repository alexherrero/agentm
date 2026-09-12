#!/usr/bin/env python3
"""test_memory_root_trims_migration.py — the vault-side moves of agentm-vault
plan 05 on a fixture vault in the pre-trims shape.

Three properties the migration invariants demand: the dry run touches
nothing; an apply produces the trimmed shape the gate enforces; a second
apply changes nothing (resumable, idempotent). Plus the two rulings the
script carries for the operator: nothing in the pen but the kernel is
folded, and a collision leaves the vault's copy in place."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
_MIGRATE = _HERE / "migrate"
for _p in (str(_HERE), str(_TOOLKIT), str(_MIGRATE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine_state_isolation as esi  # noqa: E402
import memory_root_trims as mrt  # noqa: E402


def _load_gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_memory_root_shape", _HERE / "check-memory-root-shape.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


shape = _load_gate()

KERNEL = "---\ntype: convention\nstatus: active\n---\n\nSound like the operator.\n"


def _snapshot(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_text(encoding="utf-8", errors="replace")
        else:
            out[str(p.relative_to(root)) + "/"] = ""
    return out


def build_pre_trims_vault(td: Path) -> Path:
    """`<td>/Vault` in the 2026-09-07 shape the draft measured."""
    vault = td / "Vault"
    (vault / ".obsidian").mkdir(parents=True)
    root = vault / "Agent"
    for d in ("memory/semantic", "memory/procedural", "memory/episodic", "memory/entities",
              "memory/crystallized", "memory/mocs", "diagnostics/health",
              "memory/_always-load", "memory/_watchlist/anthropic-research",
              "memory/_skill-watchlist/some-source", "_meta", "_dream/insights",
              "desk/tasks", "desk/projects", "desk/scratch", "desk/briefs", "desk/diagnostics",
              "memory/memory"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "memory" / "_always-load" / "voice-kernel.md").write_text(KERNEL, encoding="utf-8")
    (root / "memory" / "_watchlist" / "anthropic-research" / "one.md").write_text(
        "---\nkind: idea\nstatus: pending-review\n---\n\nan entry\n", encoding="utf-8")
    for name in ("auto-orchestration-config.md", "skill-discovery-sources.md", "trusted-sources.md"):
        (root / "memory" / name).write_text(f"# {name}\n", encoding="utf-8")
    (root / "memory" / "semantic" / "a-card.md").write_text(
        "---\ntype: reference\nstatus: active\n---\n\na card\n", encoding="utf-8")
    (root / "diagnostics" / "health" / "latest_health_scorecard.md").write_text("# ok\n", encoding="utf-8")
    (root / ".heat.json").write_text(json.dumps({"version": 1, "entries": {}}), encoding="utf-8")
    (root / ".lifecycle.json").write_text(json.dumps({"version": 1, "entries": {}}), encoding="utf-8")
    (root / "_meta" / "repos.json").write_text(json.dumps({"version": 1, "repos": []}), encoding="utf-8")
    (root / "_meta" / "how-to-use-agentmemory.md").write_text("# twin\n", encoding="utf-8")
    (root / "_dream" / "insights" / "20260905-run.md").write_text("---\nkind: insight\n---\n", encoding="utf-8")
    (root / "desk" / "tasks" / ".DS_Store").write_bytes(b"\x00")
    (root / "Home.md").write_text("# home\n", encoding="utf-8")
    (vault / "standards").mkdir()
    (vault / "standards" / "storage-rules.md").write_text("---\nkind: reference\n---\n\n# rules\n", encoding="utf-8")
    (vault / "standards" / "forward-learning-sources.json").write_text('{"sources": []}', encoding="utf-8")
    (vault / "Projects" / "_global" / "wiki-style").mkdir(parents=True)
    (vault / "Projects" / "_global" / "wiki-style" / "2026-07-05-docs-prose-style.md").write_text(
        "---\ntrigger: docs\n---\n\nplain prose\n", encoding="utf-8")
    (vault / "Projects" / "agentm").mkdir()
    (vault / "index.md").write_text("---\ntitle: index\n---\n\n# Vault\n\nthe map\n", encoding="utf-8")
    return root


class MigrationTests(unittest.TestCase):
    def setUp(self):
        cm = esi.isolated_engine_state()
        self.engine = Path(cm.__enter__())
        self.addCleanup(cm.__exit__, None, None, None)
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = build_pre_trims_vault(Path(self._td.name))
        self.vault = self.root.parent

    def _run(self, apply: bool) -> mrt.Trims:
        t = mrt.Trims(self.root, self.engine, apply=apply, out=io.StringIO())
        t.run()
        return t

    def test_the_dry_run_touches_nothing_and_names_every_move(self):
        before = _snapshot(self.vault)
        t = self._run(apply=False)
        self.assertEqual(_snapshot(self.vault), before)
        self.assertFalse(list(self.engine.iterdir()))
        joined = "\n".join(t.pending)
        for needle in ("user-preferences.md", "security-and-secret-governance.md",
                       "standards/voice/2026-07-05-docs-prose-style.md", "moc-standards.md",
                       "Projects/agentm/_watchlist", "Projects/agentm/forward-learning-sources.json",
                       ".heat.json", ".lifecycle.json", "repos.json", "dream-insights",
                       "index.md", "how-to-use-agentmemory.md", "desk", "memory/memory"):
            self.assertIn(needle, joined, needle)
        self.assertEqual(t.left, [])

    def test_the_apply_produces_the_trimmed_shape(self):
        self._run(apply=True)
        self.assertEqual(shape.check(self.root, out=io.StringIO()), 0)
        s = self.vault / "standards"
        prefs = (s / "user-preferences.md").read_text(encoding="utf-8")
        self.assertIn("Sound like the operator.", prefs, "the pen's kernel is the voice section")
        self.assertIn("kind: reference", prefs)
        self.assertTrue((s / "security-and-secret-governance.md").is_file())
        self.assertTrue((s / "voice" / "2026-07-05-docs-prose-style.md").is_file())
        moc = (s / "moc-standards.md").read_text(encoding="utf-8")
        self.assertIn("[[user-preferences]]", moc)
        self.assertIn("[[2026-07-05-docs-prose-style]]", moc)
        feature = self.vault / "Projects" / "agentm"
        self.assertTrue((feature / "_watchlist" / "anthropic-research" / "one.md").is_file())
        self.assertTrue((feature / "_skill-watchlist").is_dir())
        for name in ("auto-orchestration-config.md", "skill-discovery-sources.md",
                     "trusted-sources.md", "forward-learning-sources.json"):
            self.assertTrue((feature / name).is_file(), name)
        for name in (".heat.json", ".lifecycle.json", "repos.json"):
            self.assertTrue((self.engine / name).is_file(), name)
        self.assertTrue((self.engine / "dream-insights" / "20260905-run.md").is_file())
        for gone in ("memory/_always-load", "_meta", "_dream", "desk", "memory/memory",
                     ".heat.json", ".lifecycle.json"):
            self.assertFalse((self.root / gone).exists(), gone)
        self.assertFalse((self.vault / "Projects" / "_global").exists())
        index = (self.vault / "index.md").read_text(encoding="utf-8")
        self.assertIn("## How to read this vault", index)
        self.assertIn("`standards/storage-rules.md` decides where a capture goes", index)
        self.assertTrue((self.root / "Home.md").is_file(), "plan 07's file is not this plan's")
        self.assertTrue((self.root / "memory" / "semantic" / "a-card.md").is_file())

    def test_a_second_apply_changes_nothing(self):
        self._run(apply=True)
        before = _snapshot(self.vault)
        engine_before = _snapshot(self.engine)
        t = self._run(apply=True)
        self.assertEqual(_snapshot(self.vault), before)
        self.assertEqual(_snapshot(self.engine), engine_before)
        moved = [d for d in t.done if d.startswith("move ")]
        self.assertEqual(moved, [])

    def test_an_unfolded_pen_entry_keeps_the_pen_and_is_named(self):
        (self.root / "memory" / "_always-load" / "another.md").write_text("---\n---\nkeep me\n", encoding="utf-8")
        t = self._run(apply=True)
        self.assertTrue((self.root / "memory" / "_always-load" / "another.md").is_file())
        self.assertFalse((self.root / "memory" / "_always-load" / "voice-kernel.md").exists())
        self.assertTrue(any("did not fold" in x for x in t.left))
        self.assertEqual(shape.check(self.root, out=io.StringIO()), 1, "the gate names the residue")

    def test_a_collision_leaves_the_vault_copy(self):
        feature = self.vault / "Projects" / "agentm"
        (feature / "trusted-sources.md").write_text("the project's own\n", encoding="utf-8")
        t = self._run(apply=True)
        self.assertEqual((feature / "trusted-sources.md").read_text(encoding="utf-8"), "the project's own\n")
        self.assertTrue((self.root / "memory" / "trusted-sources.md").is_file())
        self.assertTrue(any("not moved" in x for x in t.left))

    def test_an_existing_user_preferences_file_is_yours_and_untouched(self):
        (self.vault / "standards" / "user-preferences.md").write_text("mine\n", encoding="utf-8")
        self._run(apply=True)
        self.assertEqual((self.vault / "standards" / "user-preferences.md").read_text(encoding="utf-8"), "mine\n")
        self.assertFalse((self.root / "memory" / "_always-load").exists(), "the pen still retires")

    def test_the_cli_dry_runs_by_default(self):
        before = _snapshot(self.vault)
        r = subprocess.run([sys.executable, str(_MIGRATE / "memory_root_trims.py"),
                            "--memory-root", str(self.root), "--engine-dir", str(self.engine)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry run", r.stdout)
        self.assertEqual(_snapshot(self.vault), before)


if __name__ == "__main__":
    unittest.main()
