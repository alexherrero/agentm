#!/usr/bin/env python3
"""recent-wiki-changes.{sh,ps1} read the repo registry from engine state.

The memory-root trims moved the registry to `engine_state_dir() / "repos.json"`,
where it resolves without a vault, but both scripts still refused to run when no
memory root resolved. Every case runs a script with no vault configured, under a
throwaway home, install prefix and engine state directory, so nothing reads or
writes the machine's own.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
_PWSH = shutil.which("pwsh")


class _RecentWikiChangesCases:
    """The cases both scripts must pass; each subclass supplies its command line."""

    def _command(self, *args: str) -> list[str]:
        raise NotImplementedError

    def _vault_path_args(self, path: str) -> tuple[str, str]:
        raise NotImplementedError

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for name in ("home", "prefix", "state", "repo/wiki/how-to"):
            (self.tmp / name).mkdir(parents=True)
        (self.tmp / "repo/wiki/how-to/page.md").write_text("# page\n", encoding="utf-8")

    def _register(self) -> None:
        registry = {"version": 1, "repos": [
            {"slug": "fixture-repo", "root_path": (self.tmp / "repo").as_posix()},
        ]}
        (self.tmp / "state/repos.json").write_text(json.dumps(registry), encoding="utf-8")

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("MEMORY_ROOT", "MEMORY_VAULT_PATH")}
        home = str(self.tmp / "home")
        env.update(HOME=home, USERPROFILE=home, PYTHONIOENCODING="utf-8",
                   AGENTM_INSTALL_PREFIX=str(self.tmp / "prefix"),
                   AGENTM_STATE_DIR=str(self.tmp / "state"))
        return subprocess.run(self._command(*args), env=env, capture_output=True,
                              text=True, timeout=120)

    def test_lists_the_engine_registry_with_no_vault(self):
        self._register()
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("fixture-repo", res.stdout)
        self.assertIn("page.md", res.stdout)

    def test_an_empty_registry_names_its_engine_state_home(self):
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("No repos registered in ~/.local/state/agentm/repos.json",
                      res.stdout + res.stderr)

    def test_a_memory_root_that_is_not_a_directory_still_skips(self):
        self._register()
        res = self._run(*self._vault_path_args(str(self.tmp / "missing")))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertTrue(json.loads(res.stdout)["skipped"])
        self.assertNotIn("fixture-repo", res.stdout)

    def test_relays_the_registry_skip_when_no_backend_can_be_selected(self):
        self._register()
        (self.tmp / "prefix/.agentm-config.json").write_text(
            json.dumps({"storage.backend": "no-such-backend"}), encoding="utf-8")
        res = self._run()
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        marker = json.loads(res.stdout)
        self.assertTrue(marker["skipped"])
        self.assertIn("storage backend is unavailable", marker["reason"])


@unittest.skipIf(os.name == "nt", "bash script — POSIX only")
class RecentWikiChangesShTest(_RecentWikiChangesCases, unittest.TestCase):
    def _command(self, *args: str) -> list[str]:
        return ["bash", str(SCRIPTS / "recent-wiki-changes.sh"), "--days", "1", *args]

    def _vault_path_args(self, path: str) -> tuple[str, str]:
        return ("--vault-path", path)


@unittest.skipIf(_PWSH is None, "pwsh not on PATH")
class RecentWikiChangesPs1Test(_RecentWikiChangesCases, unittest.TestCase):
    def _command(self, *args: str) -> list[str]:
        return [_PWSH, "-NoProfile", "-File", str(SCRIPTS / "recent-wiki-changes.ps1"),
                "-Days", "1", *args]

    def _vault_path_args(self, path: str) -> tuple[str, str]:
        return ("-VaultPath", path)


if __name__ == "__main__":
    unittest.main()
