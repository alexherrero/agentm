#!/usr/bin/env python3
"""Regression test for migrate-harness-to-vault.sh's `.project-mode` marker.

Hardening I #44 task 3 / locked DC-8: configuration is on-host only — the vault
holds data, never config. The migrate tool used to write the `.project-mode`
marker on the **vault side** (`<vault>/projects/<slug>/_harness/.project-mode`);
it now writes the **repo-local** marker (`<target>/.harness/.project-mode`) in
both directions, so the dispatcher's repo-local resolution layer sees it with no
vault. These tests pin that: the marker lands repo-local, and the vault side is
never written.

Driven as a subprocess against the real bash script. Hermetic (`mktemp`-style
temp dirs, an explicit `--vault-path`, a `.harness/project.json` slug). Skipped
on Windows (bash-native, mirrors the verify-v4 convention).

Run: python3 scripts/test_migrate_harness_marker.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPT = _HERE / "migrate-harness-to-vault.sh"


@unittest.skipIf(os.name == "nt", "bash-native script; POSIX-only (verify-v4 convention)")
class TestMigrateMarkerRepoLocal(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.target = self.root / "project"
        (self.target / ".harness").mkdir(parents=True)
        (self.target / ".harness" / "project.json").write_text(
            '{"vault_project": "fixture"}', encoding="utf-8"
        )
        self.vault = self.root / "vault"
        (self.vault / "projects").mkdir(parents=True)
        self.repo_marker = self.target / ".harness" / ".project-mode"
        self.vault_marker = self.vault / "projects" / "fixture" / "_harness" / ".project-mode"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("MEMORY_VAULT_PATH", None)
        return subprocess.run(
            ["bash", str(_SCRIPT), "--vault-path", str(self.vault), str(self.target), *extra],
            capture_output=True, text=True, timeout=30, env=env,
        )

    def test_rollback_writes_repo_local_marker(self) -> None:
        # Rollback's guard requires a vault-side _harness/ to exist (something to
        # roll back). Create it — but the marker must NOT be written there.
        (self.vault / "projects" / "fixture" / "_harness").mkdir(parents=True)
        r = self._run("--rollback")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.repo_marker.is_file(), "repo-local marker not written")
        self.assertEqual(self.repo_marker.read_text(encoding="utf-8").strip(), "local")
        self.assertFalse(self.vault_marker.exists(), "marker must NOT land in the vault")

    def test_forward_migrate_writes_repo_local_vault_marker(self) -> None:
        # Give the migration something to move; empty vault side ⇒ no conflicts.
        (self.target / ".harness" / "PLAN.md").write_text("# plan\n", encoding="utf-8")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.repo_marker.is_file(), "repo-local marker not written")
        self.assertEqual(self.repo_marker.read_text(encoding="utf-8").strip(), "vault")
        self.assertFalse(self.vault_marker.exists(), "marker must NOT land in the vault")

    def test_preview_writes_no_marker(self) -> None:
        (self.target / ".harness" / "PLAN.md").write_text("# plan\n", encoding="utf-8")
        r = self._run("--preview")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.repo_marker.exists(), "preview must not write the marker")
        self.assertFalse(self.vault_marker.exists())


if __name__ == "__main__":
    unittest.main()
