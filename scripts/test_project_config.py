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
import unittest.mock
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
            # The agentm SOURCE-repo self-marker (post V5 dev-loop slim) is the
            # durable memory-engine pair: the harness/ spec tree + the
            # scripts/harness_memory.py state resolver. Pre-V5 this keyed on
            # harness/phases/, which the slim removed — see
            # detect_project.rule_harness.
            (Path(td) / "harness").mkdir(parents=True)
            (Path(td) / "scripts").mkdir(parents=True)
            (Path(td) / "scripts" / "harness_memory.py").write_text("# resolver\n")
            bypass = dp.detect(Path(td))
            # Detection must short-circuit to bypass on the self-marker…
            self.assertEqual(bypass.verdict, "bypass")
            # …and build must refuse to write config for a bypass proposal.
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
            from storage_device_local import DeviceLocalBackend
            backend = DeviceLocalBackend(root=Path(td))
            repo_registry.register_repo(backend, "demo", "/some/repo")
            self.assertTrue(pc.is_registered({}, backend=backend, slug="demo"))
            self.assertFalse(pc.is_registered({}, backend=backend, slug="other"))


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
                # Patch select_backend to return the kernel VaultBackend so the
                # test works in CI without the obsidian-vault plugin (V5-6).
                from storage_vault import VaultBackend
                vault_backend = VaultBackend(root=vault)
                with unittest.mock.patch(
                    "backend_selection.select_backend", return_value=vault_backend
                ):
                    config = pc.register(repo, registered_via="auto-detect")
            finally:
                if old_env is None:
                    os.environ.pop("MEMORY_VAULT_PATH", None)
                else:
                    os.environ["MEMORY_VAULT_PATH"] = old_env
            # V5-3: project.json lands in device-local .harness/, not vault.
            vault_pj = repo / ".harness" / "project.json"
            self.assertTrue(vault_pj.is_file())
            data = json.loads(vault_pj.read_text(encoding="utf-8"))
            self.assertEqual(data["vault_project"], "demo")
            self.assertIn("skills", data)
            self.assertEqual(data["registered_via"], "auto-detect")
            # repo registered — read back via same VaultBackend.
            slugs = [r.get("slug") for r in repo_registry.list_repos(vault_backend)]
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


class TestRegisterNoVault(unittest.TestCase):
    """Hardening I #44 task 4: register() must complete with NO vault when the
    repo is in local state mode — the `--local-state` first-class entry point.
    The enablement block lands repo-local; the vault repo_registry step skips
    silently (no `ValueError`). Two local-mode signals, both on-host (DC-2/DC-8):
    the per-repo `.project-mode` marker and the device `state_mode` config."""

    def _make_repo(self, root: Path, slug: str) -> Path:
        repo = root / "repo"
        (repo / ".harness").mkdir(parents=True)
        (repo / ".harness" / "project.json").write_text(
            json.dumps({"vault_project": slug}), encoding="utf-8"
        )
        return repo

    def test_register_completes_with_repo_local_marker_no_vault(self) -> None:
        import harness_memory as hm
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._make_repo(root, "novault-marker")
            # Per-repo override marker signals local mode (DC-2) — no vault needed.
            (repo / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
            old_env = os.environ.get("MEMORY_VAULT_PATH")
            os.environ.pop("MEMORY_VAULT_PATH", None)
            hm._reset_warn_state()
            try:
                config = pc.register(repo, registered_via="auto-detect")
            finally:
                if old_env is not None:
                    os.environ["MEMORY_VAULT_PATH"] = old_env
            # Enablement block landed in the repo-local project.json (no ValueError).
            legacy = json.loads((repo / ".harness" / "project.json").read_text(encoding="utf-8"))
            self.assertIn("skills", legacy)
            self.assertEqual(legacy["vault_project"], "novault-marker")
            self.assertEqual(config["vault_project"], "novault-marker")

    def test_register_completes_with_device_state_mode_no_vault(self) -> None:
        # The actual `install.sh --local-state` flow: device-level state_mode in
        # .agentm-config.json (no per-repo marker), no vault → register succeeds.
        import harness_memory as hm
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._make_repo(root, "novault-device")
            prefix = root / "prefix"
            prefix.mkdir()
            (prefix / ".agentm-config.json").write_text(
                json.dumps({"schema_version": 2, "mode": "release", "state_mode": "local"}),
                encoding="utf-8",
            )
            old_vault = os.environ.get("MEMORY_VAULT_PATH")
            old_prefix = os.environ.get("AGENTM_INSTALL_PREFIX")
            os.environ.pop("MEMORY_VAULT_PATH", None)
            os.environ["AGENTM_INSTALL_PREFIX"] = str(prefix)
            hm._reset_warn_state()
            try:
                config = pc.register(repo, registered_via="auto-detect")
            finally:
                if old_vault is not None:
                    os.environ["MEMORY_VAULT_PATH"] = old_vault
                if old_prefix is None:
                    os.environ.pop("AGENTM_INSTALL_PREFIX", None)
                else:
                    os.environ["AGENTM_INSTALL_PREFIX"] = old_prefix
            legacy = json.loads((repo / ".harness" / "project.json").read_text(encoding="utf-8"))
            self.assertIn("skills", legacy)
            self.assertEqual(legacy["vault_project"], "novault-device")
            self.assertEqual(config["vault_project"], "novault-device")


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
