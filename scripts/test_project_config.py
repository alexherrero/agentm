#!/usr/bin/env python3
"""Unit tests for scripts/project_config.py — stdlib unittest, cross-platform.

Covers V4 #32 task 2: the enablement-block builder, the merge-writer that
preserves pre-existing project.json keys, operator-override recording,
is_registered, the write/load roundtrip, and the register() integration against
a fixture vault.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import detect_project as dp  # noqa: E402
import project_config as pc  # noqa: E402
import repo_registry  # noqa: E402


def _empty_proposal() -> dp.ProposedConfig:
    with tempfile.TemporaryDirectory() as td:
        return dp.detect(Path(td))  # empty dir -> propose, all default


class TestBuildAndMerge(unittest.TestCase):
    def test_build_block_shape(self):
        block = pc.build_enablement_block(_empty_proposal(), now="2026-05-29T00:00:00Z")
        self.assertEqual(block["type"], "coding")
        self.assertEqual(block["registered_via"], "auto-detect")
        self.assertEqual(block["registered_at"], "2026-05-29T00:00:00Z")
        self.assertEqual(block["operator_overrides"], [])
        self.assertIsNone(block["last_redetect_at"])
        # Every skill entry has the expected fields.
        for name, entry in block["skills"].items():
            self.assertEqual(set(entry), {"enabled", "auto_detected", "rationale", "rule_id", "operator_action"})
            self.assertTrue(entry["enabled"])

    def test_build_block_rejects_bypass(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "harness" / "phases").mkdir(parents=True)
            bypass = dp.detect(Path(td))
            with self.assertRaises(ValueError):
                pc.build_enablement_block(bypass)

    def test_merge_preserves_existing_keys(self):
        pj = {
            "vault_project": "demo",
            "github": {"owner": "x", "number": 9},
            "env": {"MEMORY_VAULT_PATH": "/v"},
        }
        block = pc.build_enablement_block(_empty_proposal())
        merged = pc.merge_enablement(pj, block)
        # Pre-existing keys survive verbatim.
        self.assertEqual(merged["vault_project"], "demo")
        self.assertEqual(merged["github"], {"owner": "x", "number": 9})
        self.assertEqual(merged["env"], {"MEMORY_VAULT_PATH": "/v"})
        # Enablement keys added.
        self.assertIn("skills", merged)
        self.assertEqual(merged["type"], "coding")
        # Input not mutated.
        self.assertNotIn("skills", pj)


class TestApplyOverride(unittest.TestCase):
    def test_disable_skill_records_override(self):
        block = pc.build_enablement_block(_empty_proposal())
        out = pc.apply_override(block, kind="skill", target="design", reason="not needed")
        self.assertFalse(out["skills"]["design"]["enabled"])
        self.assertEqual(out["skills"]["design"]["operator_action"], "disabled-at-registration")
        self.assertEqual(len(out["operator_overrides"]), 1)
        ov = out["operator_overrides"][0]
        self.assertEqual(ov["skill_or_hook"], "design")
        self.assertEqual(ov["reason"], "not needed")
        # Input not mutated.
        self.assertTrue(block["skills"]["design"]["enabled"])

    def test_disable_unknown_target_raises(self):
        block = pc.build_enablement_block(_empty_proposal())
        with self.assertRaises(KeyError):
            pc.apply_override(block, kind="skill", target="nonexistent-skill")


class TestIsRegistered(unittest.TestCase):
    def test_skills_block_means_registered(self):
        self.assertTrue(pc.is_registered({"skills": {"memory": {"enabled": True}}}))

    def test_empty_skills_not_registered(self):
        self.assertFalse(pc.is_registered({"skills": {}}))
        self.assertFalse(pc.is_registered({}))
        self.assertFalse(pc.is_registered(None))

    def test_registry_hit_means_registered(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            repo_registry.register_repo(vault, "demo", "/some/repo")
            self.assertTrue(pc.is_registered({}, vault_path=vault, slug="demo"))
            self.assertFalse(pc.is_registered({}, vault_path=vault, slug="other"))


class TestWriteLoadRoundtrip(unittest.TestCase):
    def test_write_then_load_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            vp = Path(td) / "projects" / "demo"
            resolution = {"slug": "demo", "vault_path": vp, "project_root": Path(td), "layout": "new"}
            pj = {"vault_project": "demo", "github": {"number": 2}}
            block = pc.build_enablement_block(_empty_proposal(), now="2026-05-29T00:00:00Z")
            config = pc.merge_enablement(pj, block)
            path = pc.write_config(resolution, config)
            self.assertTrue(path.is_file())
            loaded = pc.load_project_json(resolution)
            self.assertEqual(loaded["vault_project"], "demo")
            self.assertIn("skills", loaded)
            # Second write byte-identical.
            before = path.read_bytes()
            pc.write_config(resolution, config)
            self.assertEqual(path.read_bytes(), before)

    def test_load_absent_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            vp = Path(td) / "projects" / "demo"
            resolution = {"slug": "demo", "vault_path": vp, "project_root": Path(td), "layout": "new"}
            self.assertEqual(pc.load_project_json(resolution), {})


class TestRegisterIntegration(unittest.TestCase):
    def test_register_writes_block_and_registry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vault = root / "vault"
            (vault / "projects" / "demo").mkdir(parents=True)
            repo = root / "repo"
            (repo / ".harness").mkdir(parents=True)
            (repo / ".harness" / "project.json").write_text(
                json.dumps({"vault_project": "demo"}), encoding="utf-8"
            )
            old_env = os.environ.get("MEMORY_VAULT_PATH")
            os.environ["MEMORY_VAULT_PATH"] = str(vault)
            try:
                config = pc.register(repo, registered_via="auto-detect")
            finally:
                if old_env is None:
                    os.environ.pop("MEMORY_VAULT_PATH", None)
                else:
                    os.environ["MEMORY_VAULT_PATH"] = old_env
            # vault project.json now carries the enablement block + preserved slug.
            vault_pj = vault / "projects" / "demo" / "_harness" / "project.json"
            self.assertTrue(vault_pj.is_file())
            data = json.loads(vault_pj.read_text(encoding="utf-8"))
            self.assertEqual(data["vault_project"], "demo")
            self.assertIn("skills", data)
            self.assertEqual(data["registered_via"], "auto-detect")
            # repo registered.
            slugs = [r.get("slug") for r in repo_registry.list_repos(vault)]
            self.assertIn("demo", slugs)
            # returned config matches.
            self.assertEqual(config["vault_project"], "demo")

    def test_register_does_not_drop_github_env_under_local_mode(self):
        # Regression (adversarial review 2026-05-29): read_state_file honors
        # .project-mode=local (reads legacy), so write_config MUST write legacy
        # too — else it clobbers the vault file, dropping github/env.
        import harness_memory as hm
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vault = root / "vault"
            (vault / "projects" / "demo" / "_harness").mkdir(parents=True)
            vault_pj = vault / "projects" / "demo" / "_harness" / "project.json"
            vault_pj.write_text(
                json.dumps({
                    "vault_project": "demo",
                    "github": {"owner": "acme", "repo": "acme/demo", "number": 42},
                    "env": {"SECRET": "keep-me"},
                }),
                encoding="utf-8",
            )
            repo = root / "repo"
            (repo / ".harness").mkdir(parents=True)
            (repo / ".harness" / "project.json").write_text(
                json.dumps({"vault_project": "demo"}), encoding="utf-8"
            )
            # Signal local mode via the per-repo override marker (DC-2/DC-8 — the
            # in-vault `.project-mode` marker was removed in Hardening I task 3).
            (repo / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
            old_env = os.environ.get("MEMORY_VAULT_PATH")
            os.environ["MEMORY_VAULT_PATH"] = str(vault)
            hm._reset_warn_state()
            try:
                pc.register(repo, registered_via="auto-detect")
            finally:
                if old_env is None:
                    os.environ.pop("MEMORY_VAULT_PATH", None)
                else:
                    os.environ["MEMORY_VAULT_PATH"] = old_env
            # The vault file (with github/env) must be intact — local-mode writes
            # to legacy, not over the vault.
            vault_data = json.loads(vault_pj.read_text(encoding="utf-8"))
            self.assertEqual(vault_data.get("github"), {"owner": "acme", "repo": "acme/demo", "number": 42})
            self.assertEqual(vault_data.get("env"), {"SECRET": "keep-me"})
            # The enablement block landed in the legacy file (the local-mode target).
            legacy_data = json.loads((repo / ".harness" / "project.json").read_text(encoding="utf-8"))
            self.assertIn("skills", legacy_data)
            self.assertEqual(legacy_data["vault_project"], "demo")


class TestShouldNudgeGit(unittest.TestCase):
    def test_dotgit_file_worktree_counts_as_git(self):
        # A git worktree/submodule has `.git` as a FILE (`gitdir: …`), not a dir.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n", encoding="utf-8")
            # Not a harness source, no marker, no vault registration -> nudge.
            old_env = os.environ.get("MEMORY_VAULT_PATH")
            os.environ.pop("MEMORY_VAULT_PATH", None)
            try:
                rc = pc.main(["should-nudge", str(repo)])
            finally:
                if old_env is not None:
                    os.environ["MEMORY_VAULT_PATH"] = old_env
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
