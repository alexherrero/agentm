#!/usr/bin/env python3
"""test_vault_layout_toolkit.py — the toolkit's layout resolver (agentm-vault
plan 05, the memory-root trims).

One resolver, read and write alike: the newest home first, the retired one
as the fallback, the newest home when neither exists. These tests pin that
order for each family the trims moved, on both vault layouts (a memory root
nested inside an Obsidian vault, and a flat one)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine_state_isolation as esi  # noqa: E402
import vault_layout as vl  # noqa: E402


def _nested(td: Path) -> Path:
    """`<td>/Vault/Agent` inside an Obsidian vault at `<td>/Vault`."""
    (td / "Vault" / ".obsidian").mkdir(parents=True)
    root = td / "Vault" / "Agent"
    root.mkdir()
    return root


class VaultRootTests(unittest.TestCase):
    def test_nested_root_is_witnessed_by_obsidian(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            self.assertEqual(vl.vault_root_candidates(root)[0], root.parent)

    def test_nested_root_is_witnessed_by_a_standards_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "Agent"
            root.mkdir()
            (Path(td) / "standards").mkdir()
            self.assertEqual(vl.vault_root_candidates(root)[0], root.parent)

    def test_flat_root_is_its_own_vault(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "home" / "Vault"
            (root / ".obsidian").mkdir(parents=True)
            (Path(td) / "home" / "standards").mkdir()  # the operator's own folder, not the vault's
            self.assertEqual(vl.vault_root_candidates(root), [root])


class StandardsTests(unittest.TestCase):
    def test_standards_dir_is_the_sibling_on_a_nested_vault(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            self.assertEqual(vl.standards_dir(root), root.parent / "standards")

    def test_always_load_dirs_reads_standards_then_the_pen_while_it_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            (root.parent / "standards").mkdir()
            self.assertEqual(vl.always_load_dirs(root), [root.parent / "standards"])
            pen = root / "memory" / "_always-load"
            pen.mkdir(parents=True)
            self.assertEqual(vl.always_load_dirs(root), [root.parent / "standards", pen])

    def test_always_load_dirs_is_empty_when_neither_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            self.assertEqual(vl.always_load_dirs(root), [])

    def test_the_pen_is_never_conjured(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            pen = vl.legacy_pen_dir(root)
            self.assertEqual(pen, root / "memory" / "_always-load")
            self.assertFalse(pen.exists())


class VoiceTests(unittest.TestCase):
    def test_voice_dir_prefers_standards_voice(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            new = root.parent / "standards" / "voice"
            old = root.parent / "Projects" / "_global" / "wiki-style"
            old.mkdir(parents=True)
            self.assertEqual(vl.voice_dir(root), old)
            new.mkdir(parents=True)
            self.assertEqual(vl.voice_dir(root), new)

    def test_voice_dir_defaults_to_the_new_home_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            self.assertEqual(vl.voice_dir(root), root.parent / "standards" / "voice")


class FeatureStateTests(unittest.TestCase):
    def test_feature_state_prefers_the_project_then_the_retired_memory_spelling(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            old = root / "memory" / "_watchlist"
            new = root.parent / "Projects" / "agentm" / "_watchlist"
            self.assertEqual(vl.feature_state_path(root, "_watchlist"), new)  # nothing exists: the home
            old.mkdir(parents=True)
            self.assertEqual(vl.feature_state_path(root, "_watchlist"), old)
            new.mkdir(parents=True)
            self.assertEqual(vl.feature_state_path(root, "_watchlist"), new)

    def test_a_settings_file_resolves_the_same_way(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            (root / "memory").mkdir()
            (root / "memory" / "trusted-sources.md").write_text("x", encoding="utf-8")
            self.assertEqual(vl.feature_state_path(root, "trusted-sources.md"),
                             root / "memory" / "trusted-sources.md")

    def test_flat_vault_keeps_the_project_inside_the_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "Vault"
            (root / ".obsidian").mkdir(parents=True)
            self.assertEqual(vl.feature_state_dir(root), root / "Projects" / "agentm")


class SidecarTests(unittest.TestCase):
    def setUp(self):
        cm = esi.isolated_engine_state()
        self.engine = Path(cm.__enter__())
        self.addCleanup(cm.__exit__, None, None, None)

    def test_sidecar_is_created_in_the_engine_dir_and_read_from_the_root_while_it_is_there(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            self.assertEqual(vl.sidecar_path(root, ".heat.json"), self.engine / ".heat.json")
            (root / ".heat.json").write_text("{}", encoding="utf-8")
            self.assertEqual(vl.sidecar_path(root, ".heat.json"), root / ".heat.json")
            (self.engine / ".heat.json").write_text("{}", encoding="utf-8")
            self.assertEqual(vl.sidecar_path(root, ".heat.json"), self.engine / ".heat.json")

    def test_registry_candidates_engine_first(self):
        with tempfile.TemporaryDirectory() as td:
            root = _nested(Path(td))
            self.assertEqual(vl.registry_candidates(root),
                             [self.engine / "repos.json", root / "_meta" / "repos.json"])

    def test_dream_insights_dir_is_engine_state(self):
        self.assertEqual(vl.dream_insights_dir(), self.engine / "dream-insights")


class EnvTests(unittest.TestCase):
    def test_memory_root_env_then_its_alias(self):
        for var in ("MEMORY_ROOT", "MEMORY_VAULT_PATH"):
            self.addCleanup(os.environ.pop, var, None)
        os.environ.pop("MEMORY_ROOT", None)
        os.environ["MEMORY_VAULT_PATH"] = "/alias"
        self.assertEqual(vl.env_memory_root(), Path("/alias"))
        os.environ["MEMORY_ROOT"] = "/root"
        self.assertEqual(vl.env_memory_root(), Path("/root"))


if __name__ == "__main__":
    unittest.main()
