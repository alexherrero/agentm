#!/usr/bin/env python3
"""Regression tests for harness/hooks/conflict-merger-session-start/conflict-merger-session-start.sh.

Bug (#dogfood-5): the hook graceful-skipped whenever the MEMORY_VAULT_PATH env
var was unset — but Claude Code does NOT inject that env var into the hook
environment on user-scope installs, so the hook silently exited 0 on every real
session boot and never called detect_conflict_files(). The cross-agent /
cross-device conflict detection (V4 #26) was therefore functionally inert.

Fix: port the _resolve_vault_path() config fallback from
memory-recall-session-start.sh (env -> .agentm-config.json::vault_path -> none).

These tests drive the bash hook as a subprocess with a synthetic SessionStart
event JSON on stdin + a fixture vault, asserting the resolve / detect / skip
behaviors. Hermetic: a fake HOME carries
  - .claude/.agentm-config.json  (so vault_path resolves from config), and
  - Antigravity/agentm           (symlink to THIS repo, so the hook's
                                  harness_memory.py candidate resolves).

The load-bearing regression assertion is test_resolves_vault_from_config_when_
env_unset: it FAILS against the pre-fix hook (silent exit 0, empty stderr) and
PASSES once the config fallback lands.

Run: python3 scripts/test_conflict_merger_hook.py
Skipped on non-POSIX (bash hook; the pwsh twin mirrors the fix but has no test
harness, matching the repo's existing hook-test posture).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_HOOK = _REPO / "harness" / "hooks" / "conflict-merger-session-start" / "conflict-merger-session-start.sh"

_CONFLICT_NAME = "PLAN (conflicted copy 2026-05-27) - Mac.md"


@unittest.skipIf(os.name == "nt", "bash hook — POSIX only")
class TestConflictMergerHook(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # Fixture vault with a Google Drive conflict file in it.
        self.vault = self.root / "vault"
        (self.vault / "projects" / "demo" / "_harness").mkdir(parents=True)
        self.conflict = self.vault / "projects" / "demo" / "_harness" / _CONFLICT_NAME
        self.conflict.write_text("# conflicted copy\n", encoding="utf-8")

        # Neutral cwd for the hook subprocess (keeps the hook's relative
        # harness_memory.py candidates — ../agentm, ../../agentm — from
        # accidentally resolving against the real machine layout).
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()

        # Fake HOME: config carries vault_path (the fallback under test) and a
        # symlink so the hook's "$HOME/Antigravity/agentm/scripts/harness_memory.py"
        # candidate resolves to THIS repo regardless of the host machine.
        self.fake_home = self.root / "home"
        (self.fake_home / ".claude").mkdir(parents=True)
        (self.fake_home / "Antigravity").mkdir(parents=True)
        os.symlink(_REPO, self.fake_home / "Antigravity" / "agentm")
        self._write_config(vault_path=str(self.vault))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_config(self, *, vault_path: str | None) -> None:
        cfg = {"schema_version": 2, "source_clones": {"agentm": str(_REPO)}}
        if vault_path is not None:
            cfg["vault_path"] = vault_path
        (self.fake_home / ".claude" / ".agentm-config.json").write_text(
            json.dumps(cfg), encoding="utf-8",
        )

    def _env(self, **over) -> dict:
        env = {**os.environ, "HOME": str(self.fake_home)}
        # Start from a clean slate: env var must be UNSET to exercise the
        # config fallback. AGENTM_INSTALL_PREFIX unset so config resolves under
        # the fake HOME's ~/.claude.
        env.pop("MEMORY_VAULT_PATH", None)
        env.pop("AGENTM_INSTALL_PREFIX", None)
        env.update(over)
        return env

    def _run(self, env: dict):
        payload = json.dumps({"session_id": "doctor-probe", "cwd": str(self.cwd)})
        return subprocess.run(
            ["bash", str(_HOOK)], input=payload, env=env,
            cwd=str(self.cwd), capture_output=True, text=True,
        )

    # ── The regression ─────────────────────────────────────────────────────
    def test_resolves_vault_from_config_when_env_unset(self) -> None:
        """MEMORY_VAULT_PATH unset → resolve vault_path from .agentm-config.json
        and still detect the conflict file. FAILS pre-fix (silent exit 0)."""
        r = self._run(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[conflict-merger]", r.stderr,
                      f"expected conflict notice on stderr; got: {r.stderr!r}")
        self.assertIn(_CONFLICT_NAME, r.stderr)

    # ── Companion behaviors (must keep passing) ────────────────────────────
    def test_env_var_still_wins_when_set(self) -> None:
        """Explicit env override keeps working even with config present."""
        r = self._run(self._env(MEMORY_VAULT_PATH=str(self.vault)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[conflict-merger]", r.stderr)
        self.assertIn(_CONFLICT_NAME, r.stderr)

    def test_graceful_skip_when_no_vault_anywhere(self) -> None:
        """No env var and no vault_path in config → silent exit 0, no notice."""
        self._write_config(vault_path=None)
        r = self._run(self._env())
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("[conflict-merger]", r.stderr)

    def test_no_notice_when_vault_clean(self) -> None:
        """Vault resolves from config but holds no conflict files → no notice."""
        self.conflict.unlink()
        r = self._run(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("[conflict-merger]", r.stderr)

    def test_mode_off_short_circuits(self) -> None:
        """HARNESS_CONFLICT_MERGER_MODE=off skips before any resolution."""
        r = self._run(self._env(HARNESS_CONFLICT_MERGER_MODE="off"))
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("[conflict-merger]", r.stderr)


if __name__ == "__main__":
    unittest.main()
