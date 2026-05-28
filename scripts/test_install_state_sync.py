#!/usr/bin/env python3
"""Unit tests for scripts/install_state_sync.py — stdlib unittest.

Run directly:

    python3 -m unittest scripts.test_install_state_sync

Or:

    python3 scripts/test_install_state_sync.py

Covers (per v4.5.1 task 1 verification):
  - TestConfigFileMigration:
    (a) legacy .agentm-install-state.json → _read_state() renames + returns
        dict (schema_version absent in raw read — caller defaults to 1)
    (b) _write_state() persists schema_version=2 + vault_path field
    (c) new file only (post-migration steady state) → reads without rename
    (d) neither file exists → returns None (silent graceful-skip)
    (e) idempotent — second read on already-migrated install is a no-op
        (no rename attempted; new file untouched)
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import install_state_sync as iss  # noqa: E402


class TestConfigFileMigration(unittest.TestCase):
    """v4.5.1 task 1: schema v2 + .agentm-install-state.json → .agentm-config.json."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-config-test-")
        self.prefix = Path(self.tmp)
        self.legacy_path = self.prefix / iss._LEGACY_FILENAME
        self.config_path = self.prefix / iss._CONFIG_FILENAME

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -----------------------------------------------------------------------
    # (a) legacy file → rename + read
    # -----------------------------------------------------------------------

    def test_a_legacy_file_renamed_on_first_read(self) -> None:
        """Given .agentm-install-state.json, _read_state() renames to
        .agentm-config.json + returns the parsed dict."""
        legacy_payload = {
            "version": 1,
            "mode": "source",
            "source_clones": {"agentm": "/srv/agentm"},
            "installed_at": "2026-05-27T18:00:00Z",
            "harness_version": "v4.5.0",
        }
        self.legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")
        self.assertTrue(self.legacy_path.is_file())
        self.assertFalse(self.config_path.is_file())

        state = iss._read_state(self.prefix)

        # Returned dict equals legacy contents (no munging on read).
        self.assertEqual(state, legacy_payload)
        # Legacy file gone, new file at new path.
        self.assertFalse(self.legacy_path.is_file())
        self.assertTrue(self.config_path.is_file())
        # Default-1 semantic for legacy reads (callers use this contract).
        self.assertEqual(state.get("schema_version", 1), 1)

    # -----------------------------------------------------------------------
    # (b) write persists schema_version=2 + vault_path field
    # -----------------------------------------------------------------------

    def test_b_write_persists_schema_v2_and_vault_path(self) -> None:
        """_write_state() forces schema_version=2 + adds vault_path:null when absent;
        drops the legacy `version` field."""
        state = {
            "version": 1,  # legacy field — should be dropped on write
            "mode": "release",
            "harness_version": "v4.5.0",
        }
        iss._write_state(self.prefix, state)

        on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["schema_version"], 2)
        self.assertIn("vault_path", on_disk)
        self.assertIsNone(on_disk["vault_path"])
        self.assertEqual(on_disk["mode"], "release")
        self.assertEqual(on_disk["harness_version"], "v4.5.0")
        # Legacy field dropped.
        self.assertNotIn("version", on_disk)

    def test_b2_write_preserves_existing_vault_path(self) -> None:
        """If the caller already set vault_path, _write_state() preserves it."""
        state = {"mode": "source", "vault_path": "/Users/alex/vault"}
        iss._write_state(self.prefix, state)

        on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["vault_path"], "/Users/alex/vault")
        self.assertEqual(on_disk["schema_version"], 2)

    def test_b3_write_does_not_mutate_caller_dict(self) -> None:
        """_write_state must operate on a copy; caller's dict stays untouched."""
        state = {"mode": "source", "version": 1}
        original_id = id(state)
        iss._write_state(self.prefix, state)
        # Caller's dict still has version field + no schema_version added.
        self.assertEqual(id(state), original_id)
        self.assertIn("version", state)
        self.assertNotIn("schema_version", state)
        self.assertNotIn("vault_path", state)

    # -----------------------------------------------------------------------
    # (c) new file only — reads without rename
    # -----------------------------------------------------------------------

    def test_c_new_file_only_reads_directly(self) -> None:
        """Post-migration steady state: only .agentm-config.json exists.
        _read_state() reads it directly without any rename attempt."""
        payload = {
            "schema_version": 2,
            "mode": "source",
            "harness_version": "v4.5.1",
            "vault_path": "/Users/alex/vault",
        }
        self.config_path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertFalse(self.legacy_path.is_file())

        state = iss._read_state(self.prefix)
        self.assertEqual(state, payload)
        # No legacy file appeared as a side effect.
        self.assertFalse(self.legacy_path.is_file())

    # -----------------------------------------------------------------------
    # (d) neither file exists — graceful-skip
    # -----------------------------------------------------------------------

    def test_d_neither_file_returns_none(self) -> None:
        """Fresh install / pre-V4 #30 install: _read_state() returns None silently."""
        self.assertFalse(self.legacy_path.is_file())
        self.assertFalse(self.config_path.is_file())

        state = iss._read_state(self.prefix)
        self.assertIsNone(state)

    # -----------------------------------------------------------------------
    # (e) idempotent — second read after migration is a no-op
    # -----------------------------------------------------------------------

    def test_e_idempotent_post_migration(self) -> None:
        """Second _read_state() after a successful migration is a pure read —
        no rename attempted, file untouched, content stable."""
        legacy_payload = {"version": 1, "mode": "source", "harness_version": "v4.5.0"}
        self.legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

        # First read migrates.
        first = iss._read_state(self.prefix)
        self.assertEqual(first, legacy_payload)
        first_mtime = self.config_path.stat().st_mtime_ns

        # Second read — no migration, same content.
        second = iss._read_state(self.prefix)
        self.assertEqual(second, legacy_payload)
        self.assertEqual(self.config_path.stat().st_mtime_ns, first_mtime)
        self.assertFalse(self.legacy_path.is_file())

    # -----------------------------------------------------------------------
    # Bonus: malformed legacy file still migrates path but returns None
    # -----------------------------------------------------------------------

    def test_f_malformed_legacy_migrates_path_returns_none(self) -> None:
        """If the legacy file is malformed JSON, _read_state() still renames
        it (so the next write doesn't ping-pong between filenames) but
        returns None because the content can't be parsed."""
        self.legacy_path.write_text("{not valid json", encoding="utf-8")

        state = iss._read_state(self.prefix)
        self.assertIsNone(state)
        # Rename happened — legacy gone, new path has the malformed content.
        self.assertFalse(self.legacy_path.is_file())
        self.assertTrue(self.config_path.is_file())

    # -----------------------------------------------------------------------
    # Bonus: round-trip read → write → read preserves fields
    # -----------------------------------------------------------------------

    def test_g_round_trip_preserves_fields(self) -> None:
        """Read legacy → mutate → write → re-read produces a coherent v2 dict
        with all original fields preserved + schema upgrade applied."""
        legacy_payload = {
            "version": 1,
            "mode": "source",
            "source_clones": {"agentm": "/srv/agentm", "crickets": "/srv/crickets"},
            "installed_at": "2026-05-27T18:00:00Z",
            "harness_version": "v4.5.0",
            "fragments": [{"path": "/x/y.json", "sha256": "abc"}],
        }
        self.legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

        state = iss._read_state(self.prefix)
        state["vault_path"] = "/Users/alex/vault"
        iss._write_state(self.prefix, state)

        rt = iss._read_state(self.prefix)
        self.assertEqual(rt["schema_version"], 2)
        self.assertEqual(rt["vault_path"], "/Users/alex/vault")
        self.assertEqual(rt["mode"], "source")
        self.assertEqual(rt["source_clones"], legacy_payload["source_clones"])
        self.assertEqual(rt["harness_version"], "v4.5.0")
        self.assertEqual(rt["installed_at"], "2026-05-27T18:00:00Z")
        self.assertEqual(rt["fragments"], legacy_payload["fragments"])
        # Legacy `version` field dropped.
        self.assertNotIn("version", rt)


if __name__ == "__main__":
    unittest.main()
