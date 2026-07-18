#!/usr/bin/env python3
"""Unit tests for scripts/agentm_config.py — stdlib unittest.

Run directly:

    python3 -m unittest scripts.test_agentm_config

Or:

    python3 scripts/test_agentm_config.py

Covers (per v4.5.1 task 3 verification):
  - --vault-path with existing directory writes the field + rc=0
  - --vault-path with nonexistent directory refuses + rc=2
  - --get vault_path after a successful write returns the path + rc=0
  - --list emits the full JSON
  - --unset vault_path clears the field + rc=0
  - --get on missing config returns rc=1 silently
  - atomic write contract — partial writes don't corrupt existing config
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentm_config as ac  # noqa: E402


class _ClearEnv:
    """Context manager: set + unset env vars cleanly across the test."""

    def __init__(self, set_vars: dict | None = None, unset_keys: list[str] | None = None):
        self.set_vars = set_vars or {}
        self.unset_keys = unset_keys or []
        self._saved: dict[str, str | None] = {}

    def __enter__(self):
        for k in list(self.set_vars.keys()) + self.unset_keys:
            self._saved[k] = os.environ.get(k)
        for k in self.unset_keys:
            os.environ.pop(k, None)
        for k, v in self.set_vars.items():
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestAgentmConfig(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-config-cli-test-")
        self.prefix = Path(self.tmp) / "prefix"
        self.prefix.mkdir(parents=True, exist_ok=True)
        # Sandbox AGENTM_INSTALL_PREFIX so we never touch the operator's
        # real ~/.claude/.
        self.env = _ClearEnv(set_vars={"AGENTM_INSTALL_PREFIX": str(self.prefix)})
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *argv: str) -> tuple[int, str, str]:
        """Invoke ac.main(argv) capturing stdout + stderr."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = ac.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    # -----------------------------------------------------------------------
    # --vault-path: happy path + validation + idempotency
    # -----------------------------------------------------------------------

    def test_set_vault_path_writes_field_rc0(self) -> None:
        vault = Path(self.tmp) / "my-vault"
        vault.mkdir()
        rc, out, err = self._run("--vault-path", str(vault))
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        # V5-7: written to plugin-namespaced key, not the legacy flat key.
        self.assertEqual(config[ac._PLUGIN_VAULT_PATH_KEY], str(vault.resolve()))
        self.assertEqual(config["storage.backend"], "vault")
        self.assertEqual(config["schema_version"], 2)
        # Legacy flat key must NOT be written by the new code path.
        self.assertNotIn("vault_path", config)

    def test_set_vault_path_refuses_nonexistent_dir(self) -> None:
        rc, out, err = self._run("--vault-path", "/no/such/dir/at/all")
        self.assertEqual(rc, 2)
        self.assertIn("not an existing directory", err)
        # Config file MUST NOT have been written on refusal.
        self.assertFalse((self.prefix / ".agentm-config.json").is_file())

    def test_set_vault_path_refuses_file_not_dir(self) -> None:
        file_path = Path(self.tmp) / "not-a-dir.txt"
        file_path.write_text("hi", encoding="utf-8")
        rc, out, err = self._run("--vault-path", str(file_path))
        self.assertEqual(rc, 2)
        self.assertIn("not an existing directory", err)

    def test_set_vault_path_idempotent_on_same_value(self) -> None:
        vault = Path(self.tmp) / "vault"
        vault.mkdir()
        rc1, _, _ = self._run("--vault-path", str(vault))
        # Capture mtime after first write
        mtime1 = (self.prefix / ".agentm-config.json").stat().st_mtime_ns
        # Re-run with same value
        rc2, _, _ = self._run("--vault-path", str(vault))
        mtime2 = (self.prefix / ".agentm-config.json").stat().st_mtime_ns
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        # mtime unchanged → no re-write happened
        self.assertEqual(mtime1, mtime2)

    # Skipped on Windows: os.path.expanduser uses USERPROFILE, not HOME, so
    # the env-override pattern is POSIX-only. Production --vault-path delegates
    # to os.path.expanduser() which handles the platform difference correctly.
    @unittest.skipIf(os.name == "nt", "tilde-via-HOME override is POSIX-only test setup")
    def test_set_vault_path_expands_tilde(self) -> None:
        vault = Path(self.tmp) / "tilde-vault"
        vault.mkdir()
        with _ClearEnv(set_vars={
            "AGENTM_INSTALL_PREFIX": str(self.prefix),
            "HOME": self.tmp,
        }):
            rc, _, _ = self._run("--vault-path", "~/tilde-vault")
        self.assertEqual(rc, 0)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        # V5-7: plugin-namespaced key, not legacy flat key.
        self.assertEqual(config[ac._PLUGIN_VAULT_PATH_KEY], str(vault.resolve()))

    # -----------------------------------------------------------------------
    # --get
    # -----------------------------------------------------------------------

    def test_get_after_write_returns_value(self) -> None:
        vault = Path(self.tmp) / "v"
        vault.mkdir()
        self._run("--vault-path", str(vault))
        rc, out, _ = self._run("--get", "vault_path")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), str(vault.resolve()))

    def test_get_missing_field_rc1_silent(self) -> None:
        # Write a config without vault_path
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "mode": "source"}), encoding="utf-8",
        )
        rc, out, err = self._run("--get", "vault_path")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_get_no_config_file_rc1_silent(self) -> None:
        rc, out, err = self._run("--get", "vault_path")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_read_config_non_utf8_returns_none(self) -> None:
        # A non-UTF-8 config (a Windows editor's UTF-16/BOM save) is "malformed"
        # per the _read_config contract. read_text(utf-8) raises UnicodeDecodeError
        # — a ValueError, not an OSError/JSONDecodeError — so a guard naming only
        # those two leaks it and crashes the CLI instead of staying rc1-silent.
        (self.prefix / ".agentm-config.json").write_bytes(
            b'\xff\xfe{"vault_path": "/v"}',
        )
        self.assertIsNone(ac._read_config(self.prefix))
        rc, out, err = self._run("--get", "vault_path")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    # -----------------------------------------------------------------------
    # --list
    # -----------------------------------------------------------------------

    def test_list_dumps_full_config(self) -> None:
        payload = {
            "schema_version": 2,
            "mode": "source",
            "vault_path": str(Path(self.tmp)),
            "harness_version": "v4.5.1",
        }
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps(payload), encoding="utf-8",
        )
        rc, out, _ = self._run("--list")
        self.assertEqual(rc, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed, payload)

    def test_list_no_config_rc1(self) -> None:
        rc, out, _ = self._run("--list")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    # -----------------------------------------------------------------------
    # --unset
    # -----------------------------------------------------------------------

    def test_unset_removes_field_rc0(self) -> None:
        vault = Path(self.tmp) / "v"
        vault.mkdir()
        self._run("--vault-path", str(vault))
        rc, _, _ = self._run("--unset", "vault_path")
        self.assertEqual(rc, 0)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        # V5-7: both the plugin-namespaced key and the legacy flat key must be absent.
        self.assertNotIn(ac._PLUGIN_VAULT_PATH_KEY, config)
        self.assertNotIn("vault_path", config)

    # V5-7 backward-compat: --get vault_path reads legacy flat key when plugin key absent.
    def test_get_vault_path_reads_legacy_flat_key(self) -> None:
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "vault_path": "/some/path"}), encoding="utf-8",
        )
        rc, out, _ = self._run("--get", "vault_path")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "/some/path")

    # V5-7 backward-compat: --unset vault_path also clears legacy flat key.
    def test_unset_vault_path_clears_both_keys(self) -> None:
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({
                "schema_version": 2,
                ac._PLUGIN_VAULT_PATH_KEY: "/p",
                "vault_path": "/l",
            }),
            encoding="utf-8",
        )
        rc, _, _ = self._run("--unset", "vault_path")
        self.assertEqual(rc, 0)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertNotIn(ac._PLUGIN_VAULT_PATH_KEY, config)
        self.assertNotIn("vault_path", config)

    # V5-7: --unset vault_path on legacy-only config returns rc=0 + clears it.
    def test_unset_vault_path_clears_legacy_only(self) -> None:
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "vault_path": "/legacy"}),
            encoding="utf-8",
        )
        rc, _, _ = self._run("--unset", "vault_path")
        self.assertEqual(rc, 0)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertNotIn("vault_path", config)
        self.assertNotIn(ac._PLUGIN_VAULT_PATH_KEY, config)

    def test_unset_missing_field_rc1_silent(self) -> None:
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2}), encoding="utf-8",
        )
        rc, out, err = self._run("--unset", "vault_path")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_unset_no_config_rc1_silent(self) -> None:
        rc, _, _ = self._run("--unset", "vault_path")
        self.assertEqual(rc, 1)

    def test_unset_schema_version_refused(self) -> None:
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "vault_path": "/v"}), encoding="utf-8",
        )
        rc, _, err = self._run("--unset", "schema_version")
        self.assertEqual(rc, 2)
        self.assertIn("structural field", err)

    # -----------------------------------------------------------------------
    # Install prefix resolution
    # -----------------------------------------------------------------------

    def test_install_prefix_cli_arg_overrides_env(self) -> None:
        other = Path(self.tmp) / "other-prefix"
        other.mkdir()
        vault = Path(self.tmp) / "v"
        vault.mkdir()
        rc, _, _ = self._run(
            "--install-prefix", str(other),
            "--vault-path", str(vault),
        )
        self.assertEqual(rc, 0)
        # Should write into the CLI-specified prefix, NOT the env-set one
        self.assertTrue((other / ".agentm-config.json").is_file())
        self.assertFalse((self.prefix / ".agentm-config.json").is_file())

    # -----------------------------------------------------------------------
    # Mutual exclusion
    # -----------------------------------------------------------------------

    def test_no_operation_required(self) -> None:
        # With required=True on the mutually-exclusive group, argparse exits 2
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                ac.main([])
        self.assertEqual(ctx.exception.code, 2)

    def test_mutually_exclusive_ops_refused(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                ac.main(["--get", "vault_path", "--list"])
        self.assertEqual(ctx.exception.code, 2)

    # -----------------------------------------------------------------------
    # Round-trip: write → get → list → unset → get
    # -----------------------------------------------------------------------

    def test_round_trip(self) -> None:
        vault = Path(self.tmp) / "rt-vault"
        vault.mkdir()
        rc, _, _ = self._run("--vault-path", str(vault))
        self.assertEqual(rc, 0)

        # --get vault_path reads the plugin-namespaced key (V5-7 backward-compat alias).
        rc, out, _ = self._run("--get", "vault_path")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), str(vault.resolve()))

        rc, out, _ = self._run("--list")
        self.assertEqual(rc, 0)
        parsed = json.loads(out)
        # V5-7: plugin-namespaced key in the config; legacy flat key absent.
        self.assertEqual(parsed[ac._PLUGIN_VAULT_PATH_KEY], str(vault.resolve()))
        self.assertNotIn("vault_path", parsed)
        self.assertEqual(parsed["schema_version"], 2)

        rc, _, _ = self._run("--unset", "vault_path")
        self.assertEqual(rc, 0)

        rc, _, _ = self._run("--get", "vault_path")
        self.assertEqual(rc, 1)


class TestStateMode(unittest.TestCase):
    """Hardening I #44 task 4: the `--state-mode` setter — the post-install /
    `/setup` way to opt a machine into repo-local (vault-less) harness state, or
    back to vault-resident, without re-running the installer (DC-8)."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-config-statemode-test-")
        self.prefix = Path(self.tmp) / "prefix"
        self.prefix.mkdir(parents=True, exist_ok=True)
        self.env = _ClearEnv(set_vars={"AGENTM_INSTALL_PREFIX": str(self.prefix)})
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = ac.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_set_state_mode_local_writes_field_rc0(self) -> None:
        rc, out, err = self._run("--state-mode", "local")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertEqual(config["state_mode"], "local")
        self.assertEqual(config["schema_version"], 2)
        self.assertEqual(out.strip(), "state_mode = local")

    def test_set_state_mode_backend_writes_field_rc0(self) -> None:
        rc, out, err = self._run("--state-mode", "backend")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertEqual(config["state_mode"], "backend")
        self.assertEqual(out.strip(), "state_mode = backend")

    def test_set_state_mode_vault_normalizes_to_backend(self) -> None:
        """LC-5: --state-mode vault is accepted but written as 'backend' (deprecated alias)."""
        rc, out, err = self._run("--state-mode", "vault")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertEqual(config["state_mode"], "backend")
        self.assertEqual(out.strip(), "state_mode = backend")

    def test_set_state_mode_rejects_invalid(self) -> None:
        # argparse `choices` rejects at parse time → SystemExit(2), no write.
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                ac.main(["--state-mode", "bogus"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertFalse((self.prefix / ".agentm-config.json").is_file())

    def test_set_state_mode_idempotent_on_same_value(self) -> None:
        rc1, _, _ = self._run("--state-mode", "local")
        mtime1 = (self.prefix / ".agentm-config.json").stat().st_mtime_ns
        rc2, _, _ = self._run("--state-mode", "local")
        mtime2 = (self.prefix / ".agentm-config.json").stat().st_mtime_ns
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertEqual(mtime1, mtime2)

    def test_set_state_mode_preserves_vault_path(self) -> None:
        # Setting state_mode must not clobber a pre-existing vault_path.
        vault = Path(self.tmp) / "coexist-vault"
        vault.mkdir()
        self._run("--vault-path", str(vault))
        rc, _, err = self._run("--state-mode", "local")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        # V5-7: plugin-namespaced key preserved by --state-mode.
        self.assertEqual(config[ac._PLUGIN_VAULT_PATH_KEY], str(vault.resolve()))
        self.assertEqual(config["state_mode"], "local")

    def test_state_mode_round_trips_via_get(self) -> None:
        self._run("--state-mode", "local")
        rc, out, _ = self._run("--get", "state_mode")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "local")

    def test_state_mode_mutually_exclusive_with_vault_path(self) -> None:
        vault = Path(self.tmp) / "v"
        vault.mkdir()
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                ac.main(["--state-mode", "local", "--vault-path", str(vault)])
        self.assertEqual(ctx.exception.code, 2)


class TestStorageBackend(unittest.TestCase):
    """V5-1 part 5 task 1: the `--storage-backend` setter — writes the literal flat
    `storage.backend` key the selection resolver reads. Validates non-empty only
    (NOT against the registry): fail-loud philosophy requires being able to
    configure an as-yet-uninstalled backend."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-config-storage-test-")
        self.prefix = Path(self.tmp) / "prefix"
        self.prefix.mkdir(parents=True, exist_ok=True)
        self.env = _ClearEnv(set_vars={"AGENTM_INSTALL_PREFIX": str(self.prefix)})
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = ac.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_set_storage_backend_writes_flat_key_rc0(self) -> None:
        rc, out, err = self._run("--storage-backend", "device-local")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        # Literal flat key, dot in the name — NOT a nested object.
        self.assertEqual(config["storage.backend"], "device-local")
        self.assertEqual(config["schema_version"], 2)
        self.assertEqual(out.strip(), "storage.backend = device-local")

    def test_set_storage_backend_accepts_uninstalled_name(self) -> None:
        # No registry validation — an as-yet-uninstalled backend is configurable.
        rc, _, err = self._run("--storage-backend", "some-future-plugin")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertEqual(config["storage.backend"], "some-future-plugin")

    def test_set_storage_backend_refuses_empty(self) -> None:
        rc, _, err = self._run("--storage-backend", "   ")
        self.assertEqual(rc, 2)
        self.assertIn("non-empty", err)
        self.assertFalse((self.prefix / ".agentm-config.json").is_file())

    def test_set_storage_backend_idempotent_on_same_value(self) -> None:
        rc1, _, _ = self._run("--storage-backend", "vault")
        mtime1 = (self.prefix / ".agentm-config.json").stat().st_mtime_ns
        rc2, _, _ = self._run("--storage-backend", "vault")
        mtime2 = (self.prefix / ".agentm-config.json").stat().st_mtime_ns
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertEqual(mtime1, mtime2)

    def test_set_storage_backend_preserves_vault_path(self) -> None:
        vault = Path(self.tmp) / "coexist-vault"
        vault.mkdir()
        self._run("--vault-path", str(vault))
        rc, _, err = self._run("--storage-backend", "vault")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        # V5-7: plugin-namespaced key preserved by --storage-backend.
        self.assertEqual(config[ac._PLUGIN_VAULT_PATH_KEY], str(vault.resolve()))
        self.assertEqual(config["storage.backend"], "vault")

    def test_storage_backend_round_trips_via_get(self) -> None:
        self._run("--storage-backend", "device-local")
        rc, out, _ = self._run("--get", "storage.backend")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "device-local")

    def test_storage_backend_unset_clears_field(self) -> None:
        self._run("--storage-backend", "device-local")
        rc, _, _ = self._run("--unset", "storage.backend")
        self.assertEqual(rc, 0)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertNotIn("storage.backend", config)

    def test_storage_backend_mutually_exclusive_with_get(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                ac.main(["--storage-backend", "vault", "--get", "vault_path"])
        self.assertEqual(ctx.exception.code, 2)


class TestAutonomyDeliveryConfig(unittest.TestCase):
    """FRIDAY feature 1 ("Reports that reach you") — the two opt-in delivery-
    channel keys: `--notify-enabled` (bool) and `--email-to` / `--email-smtp-url`
    (strings). Absent-by-default is the load-bearing contract: both channels
    graceful-skip until the operator explicitly opts in."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-config-autonomy-test-")
        self.prefix = Path(self.tmp) / "prefix"
        self.prefix.mkdir(parents=True, exist_ok=True)
        self.env = _ClearEnv(set_vars={"AGENTM_INSTALL_PREFIX": str(self.prefix)})
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = ac.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    # -- absent-by-default ----------------------------------------------------

    def test_notify_enabled_absent_by_default(self) -> None:
        rc, out, err = self._run("--get", "plugins.autonomy.notify_enabled")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_email_to_absent_by_default(self) -> None:
        rc, out, err = self._run("--get", "plugins.autonomy.email_to")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    # -- --notify-enabled ------------------------------------------------------

    def test_set_notify_enabled_true_writes_bool_rc0(self) -> None:
        rc, out, err = self._run("--notify-enabled", "true")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertIs(config[ac._AUTONOMY_NOTIFY_ENABLED_KEY], True)
        self.assertEqual(out.strip(), "plugins.autonomy.notify_enabled = True")

    def test_set_notify_enabled_false_writes_bool_rc0(self) -> None:
        rc, out, err = self._run("--notify-enabled", "false")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertIs(config[ac._AUTONOMY_NOTIFY_ENABLED_KEY], False)

    def test_set_notify_enabled_case_insensitive(self) -> None:
        rc, _, err = self._run("--notify-enabled", "TRUE")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertIs(config[ac._AUTONOMY_NOTIFY_ENABLED_KEY], True)

    def test_set_notify_enabled_rejects_invalid(self) -> None:
        rc, _, err = self._run("--notify-enabled", "yes")
        self.assertEqual(rc, 2)
        self.assertIn("'true' or 'false'", err)
        self.assertFalse((self.prefix / ".agentm-config.json").is_file())

    def test_set_notify_enabled_idempotent_on_same_value(self) -> None:
        rc1, _, _ = self._run("--notify-enabled", "true")
        mtime1 = (self.prefix / ".agentm-config.json").stat().st_mtime_ns
        rc2, _, _ = self._run("--notify-enabled", "true")
        mtime2 = (self.prefix / ".agentm-config.json").stat().st_mtime_ns
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertEqual(mtime1, mtime2)

    def test_notify_enabled_round_trips_via_get_and_unset(self) -> None:
        self._run("--notify-enabled", "true")
        rc, out, _ = self._run("--get", "plugins.autonomy.notify_enabled")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "True")
        rc, _, _ = self._run("--unset", "plugins.autonomy.notify_enabled")
        self.assertEqual(rc, 0)
        rc, _, _ = self._run("--get", "plugins.autonomy.notify_enabled")
        self.assertEqual(rc, 1)

    # -- --email-to --------------------------------------------------------

    def test_set_email_to_writes_field_rc0(self) -> None:
        rc, out, err = self._run("--email-to", "me@example.com")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertEqual(config[ac._AUTONOMY_EMAIL_TO_KEY], "me@example.com")
        self.assertEqual(out.strip(), "plugins.autonomy.email_to = me@example.com")

    def test_set_email_to_refuses_empty(self) -> None:
        rc, _, err = self._run("--email-to", "   ")
        self.assertEqual(rc, 2)
        self.assertIn("non-empty", err)
        self.assertFalse((self.prefix / ".agentm-config.json").is_file())

    def test_email_to_round_trips_via_get(self) -> None:
        self._run("--email-to", "me@example.com")
        rc, out, _ = self._run("--get", "plugins.autonomy.email_to")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "me@example.com")

    # -- --email-smtp-url ----------------------------------------------------

    def test_set_email_smtp_url_writes_field_rc0(self) -> None:
        rc, out, err = self._run("--email-smtp-url", "smtp://me@localhost:587")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertEqual(config[ac._AUTONOMY_EMAIL_SMTP_URL_KEY], "smtp://me@localhost:587")

    def test_set_email_smtp_url_refuses_empty(self) -> None:
        rc, _, err = self._run("--email-smtp-url", "  ")
        self.assertEqual(rc, 2)
        self.assertIn("non-empty", err)

    # -- coexistence with the vault-path key ---------------------------------

    def test_notify_enabled_preserves_vault_path(self) -> None:
        vault = Path(self.tmp) / "coexist-vault"
        vault.mkdir()
        self._run("--vault-path", str(vault))
        rc, _, err = self._run("--notify-enabled", "true")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertEqual(config[ac._PLUGIN_VAULT_PATH_KEY], str(vault.resolve()))
        self.assertIs(config[ac._AUTONOMY_NOTIFY_ENABLED_KEY], True)

    def test_notify_enabled_mutually_exclusive_with_email_to(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stderr(io.StringIO()):
                ac.main(["--notify-enabled", "true", "--email-to", "me@example.com"])
        self.assertEqual(ctx.exception.code, 2)

    # -- --email-from (optional verified sending address) --------------------

    def test_set_email_from_writes_field_rc0(self) -> None:
        rc, out, err = self._run("--email-from", "digest@example.com")
        self.assertEqual(rc, 0, err)
        config = json.loads((self.prefix / ".agentm-config.json").read_text())
        self.assertEqual(config[ac._AUTONOMY_EMAIL_FROM_KEY], "digest@example.com")
        self.assertEqual(out.strip(), "plugins.autonomy.email_from = digest@example.com")

    def test_set_email_from_refuses_empty(self) -> None:
        rc, _, err = self._run("--email-from", "   ")
        self.assertEqual(rc, 2)
        self.assertIn("non-empty", err)
        self.assertFalse((self.prefix / ".agentm-config.json").is_file())

    def test_email_from_absent_by_default(self) -> None:
        rc, out, err = self._run("--get", "plugins.autonomy.email_from")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_email_from_round_trips_via_get_and_unset(self) -> None:
        self._run("--email-from", "digest@example.com")
        rc, out, _ = self._run("--get", "plugins.autonomy.email_from")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "digest@example.com")
        rc, _, _ = self._run("--unset", "plugins.autonomy.email_from")
        self.assertEqual(rc, 0)
        rc, _, _ = self._run("--get", "plugins.autonomy.email_from")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
