#!/usr/bin/env python3
"""Tests for memory_root() — the agent's tree vs the repository root.

Expected paths here are written out by hand rather than rebuilt with the same
join the implementation uses. A test that recomputes the answer with the code's
own formula only proves the formula equals itself; these pin the contract.

Background: on 2026-08-10 the vault path moved to the Obsidian root so the
corpus-write gate could see the git root. The Python stack read that same key as
its memory root and began writing memories to `<vault>/personal/` while the
daemon kept writing `<vault>/Agent/memory/`. Same slugs, different bodies.
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
        (self.vault / "Agent" / "memory").mkdir(parents=True)
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
            "daemon.spaces": {"memory": "memory"},
        })
        result = self.run_gate(cfg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside memory_root", result.stderr)

    def test_unset_memory_root_is_not_a_violation(self):
        cfg = self.write_config(**{"daemon.spaces": {"memory": "memory"}})
        self.assertEqual(self.run_gate(cfg).returncode, 0)


if __name__ == "__main__":
    unittest.main()


class TestSpaces(MemoryRootBase):
    """Space names, so moving a sub-tree is a config key rather than a sweep.

    Every expected path below is written out by hand. The point of the seam is
    that the mapping lives in one place; a test that asked the implementation
    what the mapping is would verify nothing about what it should be.
    """

    def test_defaults_are_the_four_space_layout(self):
        """The names the stage-2 migration settled on, pinned by hand."""
        self.write_config(**{"plugins.obsidian-vault.memory_root": "Agent"})
        self.assertEqual(hm.space("memory"), "memory")
        self.assertEqual(hm.space("projects"), "desk/projects")
        self.assertEqual(hm.space("briefs"), "desk/briefs")
        self.assertEqual(hm.space("scratch"), "desk/scratch")

    def test_config_overrides_a_single_space(self):
        self.write_config(**{
            "plugins.obsidian-vault.memory_root": "Agent",
            "plugins.obsidian-vault.spaces": {"memory": "elsewhere"},
        })
        self.assertEqual(hm.space("memory"), "elsewhere")
        # Untouched spaces keep their defaults rather than disappearing.
        self.assertEqual(hm.space("projects"), "desk/projects")

    def test_a_nested_space_path_is_honoured(self):
        self.write_config(**{
            "plugins.obsidian-vault.memory_root": "Agent",
            "plugins.obsidian-vault.spaces": {"desk/projects": "desk/projects"},
        })
        self.assertEqual(hm.space("projects"), "desk/projects")
        self.assertEqual(hm.space_dir("projects"),
                         Path(str(self.vault) + "/Agent/desk/projects"))

    def test_space_dir_joins_under_the_memory_root_not_the_vault_root(self):
        """The 2026-08-10 split in one assertion."""
        self.write_config(**{"plugins.obsidian-vault.memory_root": "Agent"})
        self.assertEqual(hm.space_dir("memory"),
                         Path(str(self.vault) + "/Agent/memory"))
        self.assertNotEqual(hm.space_dir("memory"),
                            Path(str(self.vault) + "/memory"))

    def test_an_unknown_space_resolves_to_its_own_name(self):
        """A space nobody has migrated yet still lands where it already is."""
        self.write_config(**{"plugins.obsidian-vault.memory_root": "Agent"})
        self.assertEqual(hm.space("_opinions"), "_opinions")

    def test_an_escaping_value_is_refused_rather_than_honoured(self):
        for bad in ("/etc", "C:/windows", "../../elsewhere", "", "   "):
            with self.subTest(bad=bad):
                self.write_config(**{
                    "plugins.obsidian-vault.memory_root": "Agent",
                    "plugins.obsidian-vault.spaces": {"memory": bad},
                })
                # Falls back to the default rather than escaping the tree.
                self.assertEqual(hm.space("memory"), "memory")

    def test_a_malformed_spaces_block_is_ignored(self):
        for bad in ("not-a-dict", 17, ["memory"], None):
            with self.subTest(bad=bad):
                self.write_config(**{
                    "plugins.obsidian-vault.memory_root": "Agent",
                    "plugins.obsidian-vault.spaces": bad,
                })
                self.assertEqual(hm.space("memory"), "memory")

    def test_space_dir_is_none_when_no_vault_resolves(self):
        """Same graceful-skip contract as memory_root(), so callers are unchanged."""
        (self.prefix / ".agentm-config.json").unlink(missing_ok=True)
        self.assertIsNone(hm.memory_root())
        self.assertIsNone(hm.space_dir("memory"))


class TestSpaceNameAgreement(MemoryRootBase):
    """Invariant 2: containment is not agreement.

    The stage-2 migration produced a fork the containment rule passed straight
    through — the daemon on `Agent/memory` and the Python stack on
    `Agent/personal`, both beneath memory_root `Agent`. Every expectation here
    is a hand-written path, not one rebuilt with the checker's own join.
    """

    def _run(self, cfg: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CONSISTENCY), "--config", str(cfg)],
                              capture_output=True, text=True)

    def test_matching_names_pass(self):
        cfg = self.write_config(**{
            "plugins.obsidian-vault.memory_root": "Agent",
            "daemon.spaces": {"memory": "Agent/memory"},
            "plugins.obsidian-vault.spaces": {"memory": "memory"},
        })
        res = self._run(cfg)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("name-matched", res.stdout)

    def test_the_stage_2_fork_is_caught(self):
        """Both beneath memory_root, both healthy-looking, different trees."""
        cfg = self.write_config(**{
            "plugins.obsidian-vault.memory_root": "Agent",
            "daemon.spaces": {"memory": "Agent/memory"},
            "plugins.obsidian-vault.spaces": {"memory": "personal"},
        })
        res = self._run(cfg)
        self.assertEqual(res.returncode, 1)
        self.assertIn("Agent/memory", res.stderr)
        self.assertIn("Agent/personal", res.stderr)

    def test_an_unnamed_space_is_not_compared(self):
        """Unset on the Python side means 'use the built-in default', not 'disagree'."""
        cfg = self.write_config(**{
            "plugins.obsidian-vault.memory_root": "Agent",
            "daemon.spaces": {"memory": "Agent/memory", "projects": "Agent/desk/projects"},
            "plugins.obsidian-vault.spaces": {"memory": "memory"},
        })
        res = self._run(cfg)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_containment_is_still_enforced(self):
        cfg = self.write_config(**{
            "plugins.obsidian-vault.memory_root": "Agent",
            "daemon.spaces": {"memory": "Elsewhere/memory"},
        })
        res = self._run(cfg)
        self.assertEqual(res.returncode, 1)
        self.assertIn("outside memory_root", res.stderr)
