#!/usr/bin/env python3
"""Tests for memory_root() — the agent's tree vs the repository root.

Expected paths here are written out by hand rather than rebuilt with the same
join the implementation uses. A test that recomputes the answer with the code's
own formula only proves the formula equals itself; these pin the contract.

Background: on 2026-08-10 the vault path moved to the Obsidian root so the
corpus-write gate could see the git root. The Python stack read that same key as
its memory root and began writing memories to `<vault>/personal/` while the
daemon kept writing `<vault>/Agent/personal/`. Same slugs, different bodies.
These tests are the regression floor for that split.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_memory as hm  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CONSISTENCY = REPO / "scripts" / "check-memory-root-consistency.py"


class MemoryRootBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.prefix = Path(self._tmp.name) / "prefix"
        self.prefix.mkdir()
        self.vault = Path(self._tmp.name) / "Vault"
        (self.vault / "Agent" / "personal").mkdir(parents=True)
        (self.vault / "Church").mkdir()
        self._env = dict(os.environ)
        os.environ["AGENTM_INSTALL_PREFIX"] = str(self.prefix)
        os.environ.pop("MEMORY_VAULT_PATH", None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()

    def write_config(self, **extra) -> Path:
        cfg = {"plugins.obsidian-vault.vault_path": str(self.vault)}
        cfg.update(extra)
        p = self.prefix / ".agentm-config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        return p


class TestResolution(MemoryRootBase):
    def test_unset_key_leaves_memory_root_at_the_vault_root(self):
        """The pre-cutover topology, and every install that never moved."""
        self.write_config()
        self.assertEqual(hm.vault_path(), self.vault)
        self.assertEqual(hm.memory_root(), self.vault)

    def test_set_key_puts_the_memory_root_one_level_down(self):
        self.write_config(**{"plugins.obsidian-vault.memory_root": "Agent"})
        # Hand-written, not self.vault / "Agent" — pin the contract.
        expected = Path(str(self.vault) + "/Agent")
        self.assertEqual(hm.memory_root(), expected)

    def test_vault_path_is_unmoved_by_the_key(self):
        """The gate resolves the repository from vault_path; it must not shift."""
        self.write_config(**{"plugins.obsidian-vault.memory_root": "Agent"})
        self.assertEqual(hm.vault_path(), self.vault)
        self.assertNotEqual(hm.vault_path(), hm.memory_root())

    def test_env_is_taken_as_the_memory_root_not_re_joined(self):
        """$MEMORY_VAULT_PATH already names a memory tree — joining again would
        address <vault>/Agent/Agent. This is what the hooks now export."""
        self.write_config(**{"plugins.obsidian-vault.memory_root": "Agent"})
        os.environ["MEMORY_VAULT_PATH"] = str(self.vault / "Agent")
        self.assertEqual(hm.memory_root(), Path(str(self.vault) + "/Agent"))

    def test_nested_prefix_resolves_every_segment(self):
        (self.vault / "a" / "b").mkdir(parents=True)
        self.write_config(**{"plugins.obsidian-vault.memory_root": "a/b"})
        self.assertEqual(hm.memory_root(), Path(str(self.vault) + "/a/b"))

    def test_absolute_and_traversal_values_are_refused_not_honoured(self):
        """A bad value must mean 'unchanged', never a guess that relocates the
        corpus or escapes the vault."""
        for bad in ("/etc", "../../etc", "Agent/../.."):
            with self.subTest(bad=bad):
                self.write_config(**{"plugins.obsidian-vault.memory_root": bad})
                self.assertEqual(hm.memory_root(), self.vault)

    def test_no_vault_yields_none_so_graceful_skips_still_fire(self):
        self.assertIsNone(hm.memory_root())


class TestConsistencyGate(MemoryRootBase):
    def run_gate(self, cfg: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CONSISTENCY), "--config", str(cfg)],
            capture_output=True, text=True,
        )

    def test_spaces_beneath_memory_root_pass(self):
        cfg = self.write_config(**{
            "plugins.obsidian-vault.memory_root": "Agent",
            "daemon.spaces": {"memory": "Agent/personal",
                              "projects": "Agent/projects"},
        })
        self.assertEqual(self.run_gate(cfg).returncode, 0)

    def test_the_2026_08_10_split_is_caught(self):
        """memory_root=Agent while the daemon writes memory to a root-level
        personal/ is exactly the configuration that forked the corpus."""
        cfg = self.write_config(**{
            "plugins.obsidian-vault.memory_root": "Agent",
            "daemon.spaces": {"memory": "personal"},
        })
        result = self.run_gate(cfg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside memory_root", result.stderr)

    def test_unset_memory_root_is_not_a_violation(self):
        cfg = self.write_config(**{"daemon.spaces": {"memory": "personal"}})
        self.assertEqual(self.run_gate(cfg).returncode, 0)


if __name__ == "__main__":
    unittest.main()
