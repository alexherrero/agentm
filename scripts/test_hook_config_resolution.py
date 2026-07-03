#!/usr/bin/env python3
"""Unit tests proving `_resolve_vault_path` reads the plugin-namespaced vault
key (R0.1 / agentmEngine#0).

Pre-fix, all four memory hooks' `_resolve_vault_path` read only the legacy flat
`vault_path` key from `.agentm-config.json`. On any install whose config was
written by the canonical `agentm_config.py --vault-path` setter (post-V5-7),
that setter writes ONLY `plugins.obsidian-vault.vault_path` — so the hooks
silently fail to resolve the vault and `MEMORY_VAULT_PATH` stays unset.

Extracts the `_resolve_vault_path` function body verbatim from each hook
script (no `MEMORY_VAULT_PATH` env, no legacy `vault_path` key) and invokes it
directly via `bash -c`, asserting the plugin-namespaced key resolves.

Run: python3 scripts/test_hook_config_resolution.py
Skipped on non-POSIX (bash hooks).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent

_HOOKS = [
    _REPO / "harness/hooks/memory-recall-session-start/memory-recall-session-start.sh",
    _REPO / "harness/hooks/memory-recall-prompt-submit/memory-recall-prompt-submit.sh",
    _REPO / "harness/hooks/memory-reflect-stop/memory-reflect-stop.sh",
    _REPO / "harness/hooks/memory-reflect-idle/memory-reflect-idle.sh",
]

_FUNC_RE = re.compile(r"_resolve_vault_path\(\)\s*\{.*?\n\}\n", re.DOTALL)


def _extract_function(hook: Path) -> str:
    text = hook.read_text(encoding="utf-8")
    m = _FUNC_RE.search(text)
    if not m:
        raise AssertionError(f"_resolve_vault_path not found in {hook}")
    return m.group(0)


@unittest.skipIf(os.name == "nt", "bash hook — POSIX only")
class TestHookConfigResolution(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root / ".agentm-config.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, hook: Path, config: dict) -> str:
        self.cfg.write_text(json.dumps(config), encoding="utf-8")
        func = _extract_function(hook)
        script = f"{func}\n_resolve_vault_path\n"
        env = {**os.environ, "AGENTM_INSTALL_PREFIX": str(self.root)}
        env.pop("MEMORY_VAULT_PATH", None)
        proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
        return proc.stdout.strip()

    def test_plugin_namespaced_key_resolves_on_every_hook(self) -> None:
        for hook in _HOOKS:
            with self.subTest(hook=hook.name):
                out = self._run(hook, {"plugins.obsidian-vault.vault_path": "/tmp/example-vault"})
                self.assertEqual(out, "/tmp/example-vault")

    def test_legacy_flat_key_still_resolves(self) -> None:
        for hook in _HOOKS:
            with self.subTest(hook=hook.name):
                out = self._run(hook, {"vault_path": "/tmp/legacy-vault"})
                self.assertEqual(out, "/tmp/legacy-vault")

    def test_plugin_key_wins_over_legacy_when_both_present(self) -> None:
        for hook in _HOOKS:
            with self.subTest(hook=hook.name):
                out = self._run(hook, {
                    "plugins.obsidian-vault.vault_path": "/tmp/plugin-vault",
                    "vault_path": "/tmp/legacy-vault",
                })
                self.assertEqual(out, "/tmp/plugin-vault")


if __name__ == "__main__":
    unittest.main()
