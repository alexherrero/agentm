#!/usr/bin/env python3
"""Tests for scripts/migrate/agentkv_layout.py — the AgentKV layout moves (task 176)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "migrate"))
sys.path.insert(0, str(_HERE.parent / "harness" / "skills" / "memory" / "scripts"))

import agentkv_layout as kv  # noqa: E402


def _w(root: Path, rel: str, text: str = "x\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout


class _Vault(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.vault = base / "vault"
        self.state = base / "state"
        self.state.mkdir()
        v = self.vault
        _w(v, "projects/agentm/charter.md", "# agentm\n")
        _w(v, "projects/agentm/moc-agentm.md", "map\n")
        _w(v, "projects/agentm/followups.md", "# Followups\n\nSee [the roadmap](roadmap.md).\n")
        _w(v, "projects/agentm/roadmap.md", "# Roadmap\n")
        _w(v, "projects/agentm/trusted-sources.md", "- anthropics\n")
        _w(v, "projects/agentm/forward-learning-sources.json", '{"sources": []}\n')
        _w(v, "projects/house/charter.md", "# home\n")
        _w(v, "projects/house/home_config.py", "A = {}\n")
        _w(v, "projects/house/research/x.md", "# x\n")
        _w(v, "projects/agentm/research/sqlite/reference/bm25.md", "# bm25\n")
        _w(v, "projects/agentm/research/sqlite/notes.md", "# kept\n")
        _w(v, "projects/agentm/_watchlist/openai-research/paper.md", "# paper\n")
        _w(v, "agent/memory/semantic/home-server.md", "---\ntitle: Home server\n---\n\nBody.\n")
        _w(v, "agent/memory/semantic/nas-backup.md", "---\ntitle: NAS backup\n---\n\nBody.\n")
        # The linkers: a full path, a partial path, a basename, a markdown link
        # from the vault root, and one into a walled area that is never opened.
        _w(v, "agent/memory/semantic/linker.md",
           "---\ntitle: linker\nrelated: [\"[[projects/agentm/roadmap]]\"]\n---\n\n"
           "Full [[projects/agentm/followups]], partial [[agentm/followups#Open|open ones]], "
           "basename [[followups]], md [r](/projects/agentm/roadmap.md), "
           "card [[projects/agentm/research/sqlite/reference/bm25]].\n")
        _w(v, "personal/Home/Important Docs/codes.md", "[[projects/agentm/followups]]\n")
        _git(v, "init", "-q")
        _git(v, "config", "user.email", "t@t")
        _git(v, "config", "user.name", "t")
        _git(v, "add", "-A")
        _git(v, "commit", "-q", "-m", "fixture")
        walled = mock.patch.object(kv, "_walled", return_value=["personal/Home/Important Docs"])
        walled.start()
        self.addCleanup(walled.stop)

    def run_batch(self, batch: str) -> dict:
        plan = kv.build_plan(self.vault, batch)
        return kv.apply(self.vault, plan, len(plan["moves"]), self.state, check_writers=lambda: [])


class TestPlans(_Vault):
    def test_root_files_go_to_docs_or_desk_and_the_five_stay(self):
        moves = {m["src"]: m["dst"] for m in kv.build_plan(self.vault, "root-files")["moves"]}
        self.assertEqual(moves, {
            "projects/agentm/followups.md": "projects/agentm/docs/followups.md",
            "projects/agentm/roadmap.md": "projects/agentm/docs/roadmap.md",
            "projects/agentm/trusted-sources.md": "projects/agentm/desk/trusted-sources.md",
            "projects/agentm/forward-learning-sources.json": "projects/agentm/desk/forward-learning-sources.json",
            "projects/house/home_config.py": "projects/house/desk/home_config.py",
        })

    def test_a_root_file_with_no_rule_is_refused_not_guessed(self):
        _w(self.vault, "projects/house/photo.png", "png")
        with self.assertRaises(kv.Refused):
            kv.build_plan(self.vault, "root-files")

    def test_resources_drop_the_reference_level_and_leave_the_rest_of_research(self):
        moves = {m["src"]: m["dst"] for m in kv.build_plan(self.vault, "resources")["moves"]}
        self.assertEqual(moves, {
            "projects/agentm/research/sqlite/reference/bm25.md": "resources/topics/sqlite/bm25.md",
            "projects/agentm/_watchlist/openai-research/paper.md": "resources/watchlist/openai-research/paper.md",
        })

    def test_systems_put_the_overview_at_system_md(self):
        with mock.patch("harness_memory.memory_root", return_value=str(self.vault / "agent")):
            moves = {m["src"]: m["dst"] for m in kv.build_plan(self.vault, "systems")["moves"]}
        self.assertEqual(moves, {
            "agent/memory/semantic/home-server.md": "systems/homelab/system.md",
            "agent/memory/semantic/nas-backup.md": "systems/homelab/components/nas-backup.md",
        })

    def test_a_move_never_overwrites(self):
        _w(self.vault, "projects/agentm/docs/roadmap.md", "already\n")
        with self.assertRaises(kv.Refused):
            kv.build_plan(self.vault, "root-files")


class TestApply(_Vault):
    def test_path_links_follow_and_the_basename_link_is_untouched(self):
        self.run_batch("root-files")
        text = (self.vault / "agent/memory/semantic/linker.md").read_text()
        self.assertIn("[[projects/agentm/docs/followups|projects/agentm/followups]]", text)
        self.assertIn("[[projects/agentm/docs/followups#Open|open ones]]", text)
        self.assertIn("basename [[followups]]", text)
        self.assertIn("[r](/projects/agentm/docs/roadmap.md)", text)
        self.assertIn('"[[projects/agentm/docs/roadmap|projects/agentm/roadmap]]"', text)

    def test_a_relative_link_between_two_moved_notes_still_resolves(self):
        self.run_batch("root-files")
        text = (self.vault / "projects/agentm/docs/followups.md").read_text()
        self.assertIn("[the roadmap](roadmap.md)", text)

    def test_the_walled_area_is_never_rewritten(self):
        self.run_batch("root-files")
        self.assertEqual((self.vault / "personal/Home/Important Docs/codes.md").read_text(),
                         "[[projects/agentm/followups]]\n")

    def test_the_audit_reads_the_same_set_before_and_after(self):
        before = kv.audit(self.vault)
        self.run_batch("root-files")
        self.run_batch("resources")
        self.assertEqual(kv.audit(self.vault), before)

    def test_the_audit_sees_a_broken_path_link_the_basename_check_would_miss(self):
        _w(self.vault, "agent/memory/semantic/bad.md", "[[projects/agentm/nowhere/followups]]\n")
        self.assertIn(["agent/memory/semantic/bad.md", "projects/agentm/nowhere/followups"],
                      kv.audit(self.vault))

    def test_the_batch_is_one_commit_and_the_tree_is_clean(self):
        head = _git(self.vault, "rev-parse", "HEAD").strip()
        rec = self.run_batch("root-files")
        self.assertEqual(_git(self.vault, "rev-parse", "HEAD~1").strip(), head)
        self.assertEqual(_git(self.vault, "status", "--porcelain"), "")
        self.assertEqual(rec["commit"], _git(self.vault, "rev-parse", "HEAD").strip())

    def test_sidecars_follow_the_move(self):
        (self.state / ".heat.json").write_text(json.dumps(
            {"version": 2, "entries": {"projects/agentm/roadmap.md": {"hits": 3}}}))
        (self.state / ".lifecycle.json").write_text(json.dumps(
            {"version": 2, "entries": {"projects/agentm/roadmap.md": {"last_access": "2026-09-01",
                                                                        "fingerprint": "old"}}}))
        self.run_batch("root-files")
        heat = json.loads((self.state / ".heat.json").read_text())["entries"]
        life = json.loads((self.state / ".lifecycle.json").read_text())["entries"]
        self.assertEqual(heat, {"projects/agentm/docs/roadmap.md": {"hits": 3}})
        self.assertIn("projects/agentm/docs/roadmap.md", life)
        self.assertNotEqual(life["projects/agentm/docs/roadmap.md"]["fingerprint"], "old")

    def test_revert_puts_everything_back(self):
        before = {p: (self.vault / p).read_bytes() for p in kv.vault_files(self.vault)}
        rec = self.run_batch("root-files")
        kv.revert(self.vault, rec["run_id"], self.state, check_writers=lambda: [])
        after = {p: (self.vault / p).read_bytes() for p in kv.vault_files(self.vault)}
        self.assertEqual(after, before)


class TestRefusals(_Vault):
    def test_a_live_writer_refuses(self):
        plan = kv.build_plan(self.vault, "root-files")
        with self.assertRaises(kv.Refused):
            kv.apply(self.vault, plan, len(plan["moves"]), self.state,
                     check_writers=lambda: ["com.agentm.daemon is loaded"])

    def test_a_wrong_count_refuses(self):
        plan = kv.build_plan(self.vault, "root-files")
        with self.assertRaises(kv.Refused):
            kv.apply(self.vault, plan, 1, self.state, check_writers=lambda: [])

    def test_a_stale_plan_refuses(self):
        plan = kv.build_plan(self.vault, "root-files")
        _w(self.vault, "projects/agentm/new-root-note.md", "late\n")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", "late")
        with self.assertRaises(kv.Refused):
            kv.apply(self.vault, plan, len(plan["moves"]), self.state, check_writers=lambda: [])

    def test_a_dirty_tree_refuses(self):
        plan = kv.build_plan(self.vault, "root-files")
        (self.vault / "projects/agentm/charter.md").write_text("edited\n")
        with self.assertRaises(kv.Refused):
            kv.apply(self.vault, plan, len(plan["moves"]), self.state, check_writers=lambda: [])


if __name__ == "__main__":
    unittest.main()
