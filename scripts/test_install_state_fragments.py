#!/usr/bin/env python3
"""Unit tests for the V4 #39 `--fragments-file` flag on
lib/install/python/install_state.py's `persist` subcommand.

The function `persist_install_state()` already accepted a `fragments` param;
v4.6.1 exposes it via the CLI so install.sh can record the {path, sha256} of
each merged settings fragment (install-time metadata).

Driven as a subprocess to exercise the real CLI. `--agentm-path` is pointed
at an empty temp dir to force deterministic release-mode detection
(independent of the test host's real clone).

Run: python3 scripts/test_install_state_fragments.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_INSTALL_STATE = _HERE.parent / "lib" / "install" / "python" / "install_state.py"


class TestPersistFragments(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prefix = self.root / "prefix"
        self.prefix.mkdir()
        # Empty dir → not a source clone → deterministic release mode.
        self.fake_agentm = self.root / "no-agentm"
        self.fake_agentm.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _persist(self, *extra: str):
        return subprocess.run(
            [sys.executable, str(_INSTALL_STATE), "persist", str(self.prefix),
             "--harness-version", "v4.6.1",
             "--agentm-path", str(self.fake_agentm),
             *extra],
            capture_output=True, text=True,
        )

    def _config(self) -> dict:
        return json.loads((self.prefix / ".agentm-config.json").read_text(encoding="utf-8"))

    def test_fragments_file_written_to_config(self) -> None:
        frags = [
            {"path": "/home/u/.claude/hooks/x/settings-fragment-bash.json", "sha256": "abc123"},
            {"path": "/home/u/.claude/hooks/y/settings-fragment-bash.json", "sha256": "def456"},
        ]
        ff = self.root / "frags.json"
        ff.write_text(json.dumps(frags), encoding="utf-8")
        r = self._persist("--fragments-file", str(ff))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._config().get("fragments"), frags)

    def test_no_fragments_file_means_no_fragments_field(self) -> None:
        r = self._persist()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("fragments", self._config())

    def test_empty_fragments_list_writes_empty_array(self) -> None:
        ff = self.root / "frags.json"
        ff.write_text("[]", encoding="utf-8")
        r = self._persist("--fragments-file", str(ff))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._config().get("fragments"), [])

    def test_invalid_fragments_json_exits_2(self) -> None:
        ff = self.root / "frags.json"
        ff.write_text("{not a list", encoding="utf-8")
        r = self._persist("--fragments-file", str(ff))
        self.assertEqual(r.returncode, 2)

    def test_non_list_fragments_exits_2(self) -> None:
        ff = self.root / "frags.json"
        ff.write_text(json.dumps({"path": "x"}), encoding="utf-8")
        r = self._persist("--fragments-file", str(ff))
        self.assertEqual(r.returncode, 2)

    def test_vault_path_preserved_alongside_fragments(self) -> None:
        # Pre-existing config with a vault_path → persist preserves it + adds fragments.
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "mode": "release", "vault_path": "/v/x"}),
            encoding="utf-8",
        )
        ff = self.root / "frags.json"
        ff.write_text(json.dumps([{"path": "p", "sha256": "s"}]), encoding="utf-8")
        r = self._persist("--fragments-file", str(ff))
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = self._config()
        self.assertEqual(cfg.get("vault_path"), "/v/x")
        self.assertEqual(cfg.get("fragments"), [{"path": "p", "sha256": "s"}])


class TestPersistStateMode(unittest.TestCase):
    """Hardening I #44 task 3: the `--state-mode` flag on `install_state.py persist`
    writes/preserves the device-level `state_mode` in `.agentm-config.json` — the
    on-host source of truth for vault-vs-local run mode (DC-8)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prefix = self.root / "prefix"
        self.prefix.mkdir()
        self.fake_agentm = self.root / "no-agentm"
        self.fake_agentm.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _persist(self, *extra: str):
        return subprocess.run(
            [sys.executable, str(_INSTALL_STATE), "persist", str(self.prefix),
             "--harness-version", "v4.15.0",
             "--agentm-path", str(self.fake_agentm),
             *extra],
            capture_output=True, text=True,
        )

    def _config(self) -> dict:
        return json.loads((self.prefix / ".agentm-config.json").read_text(encoding="utf-8"))

    def test_state_mode_local_written(self) -> None:
        r = self._persist("--state-mode", "local")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._config().get("state_mode"), "local")

    def test_state_mode_vault_written(self) -> None:
        r = self._persist("--state-mode", "vault")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._config().get("state_mode"), "vault")

    def test_no_state_mode_means_field_absent(self) -> None:
        # Back-compat: absent ⇒ vault default; the key is not forced into the config.
        r = self._persist()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("state_mode", self._config())

    def test_state_mode_preserved_across_re_persist(self) -> None:
        self.assertEqual(self._persist("--state-mode", "local").returncode, 0)
        # Re-persist WITHOUT --state-mode (the agentm-update path) keeps it.
        r = self._persist()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._config().get("state_mode"), "local")

    def test_explicit_state_mode_overrides_preserved(self) -> None:
        self.assertEqual(self._persist("--state-mode", "local").returncode, 0)
        r = self._persist("--state-mode", "vault")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._config().get("state_mode"), "vault")

    def test_invalid_state_mode_rejected(self) -> None:
        r = self._persist("--state-mode", "bogus")
        self.assertNotEqual(r.returncode, 0)

    def test_corrupt_preserved_state_mode_self_heals(self) -> None:
        # A hand-corrupted prior value must NOT propagate across a re-persist that
        # omits --state-mode — it self-heals to absent (⇒ vault default).
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "mode": "release", "state_mode": "bogus"}),
            encoding="utf-8",
        )
        r = self._persist()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("state_mode", self._config())

    def test_state_mode_coexists_with_vault_path(self) -> None:
        # Pre-existing config with a vault_path → persist preserves it + adds state_mode.
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "mode": "release", "vault_path": "/v/x"}),
            encoding="utf-8",
        )
        r = self._persist("--state-mode", "local")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = self._config()
        self.assertEqual(cfg.get("vault_path"), "/v/x")
        self.assertEqual(cfg.get("state_mode"), "local")


if __name__ == "__main__":
    unittest.main()
