#!/usr/bin/env python3
"""Unit tests for scripts/harness_memory.py — stdlib unittest, cross-platform.

Run directly:

    python3 scripts/test_harness_memory.py

Covers:
  - available exit codes (vault present / absent)
  - recall graceful-skip + fixture-vault content + budget cap + permanent-only
  - offer-save mode envelope (off / silent / ask) + confidence threshold edges
  - offer-save non-TTY skip default + toolkit-absent graceful path
  - plan-done-promotion: empty / first run / idempotent re-run / cursor advance
"""
from __future__ import annotations

import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402


# v4.5.1: sandbox AGENTM_INSTALL_PREFIX module-wide so vault_path()'s
# new config-file fallback doesn't read the operator's real
# `~/.claude/.agentm-config.json` during tests. Each test that wants a
# specific config writes one to this sandbox under setUp/tearDown.
_TEST_INSTALL_PREFIX = tempfile.mkdtemp(prefix="agentm-test-install-prefix-")


def setUpModule() -> None:  # noqa: N802 — unittest convention
    os.environ["AGENTM_INSTALL_PREFIX"] = _TEST_INSTALL_PREFIX


def tearDownModule() -> None:  # noqa: N802
    os.environ.pop("AGENTM_INSTALL_PREFIX", None)
    import shutil
    shutil.rmtree(_TEST_INSTALL_PREFIX, ignore_errors=True)


# -----------------------------------------------------------------------------
# Fixture helpers
# -----------------------------------------------------------------------------

def _make_vault(root: Path, *, project: str = "fixture-project") -> Path:
    """Build a minimal MemoryVault under `root`. Returns the vault path."""
    vault = root / "vault"
    (vault / "personal" / "_always-load").mkdir(parents=True)
    (vault / "personal" / "_always-load" / "coding-style.md").write_text(
        "# coding style\nuse stdlib; kebab-case slugs.\n",
        encoding="utf-8",
    )
    (vault / "personal-projects" / project / "decisions").mkdir(parents=True)
    (vault / "personal-projects" / project / "_index.md").write_text(
        f"# {project} index\ncurrent state: in-progress\n",
        encoding="utf-8",
    )
    (vault / "personal-projects" / project / "decisions" / "2026-05-20-pick-stdlib.md").write_text(
        "# pick stdlib\nrationale: no new deps per ADR 0007 D7.\n",
        encoding="utf-8",
    )
    (vault / "personal-projects" / project / "open-questions").mkdir(parents=True)
    (vault / "personal-projects" / project / "open-questions" / "2026-05-22-budget-tuning.md").write_text(
        "# budget tuning\nq: what's the right per-phase budget?\n",
        encoding="utf-8",
    )
    (vault / "personal-projects" / project / "known-issues").mkdir(parents=True)
    (vault / "personal-projects" / project / "known-issues" / "2026-05-15-crlf-windows.md").write_text(
        "# CRLF on windows\nfix: write_bytes instead of write_text.\n",
        encoding="utf-8",
    )
    return vault


def _make_toolkit_stub(root: Path, *, save_exit: int = 0, save_log: Path | None = None) -> Path:
    """Build a toolkit stub directory with a stub save.py.

    The stub script optionally writes a JSON log of args + stdin to `save_log`.
    """
    tk = root / "toolkit-stub"
    tk.mkdir(parents=True)
    log_arg = repr(str(save_log)) if save_log else "None"
    stub = textwrap.dedent(
        f"""
        import json, sys
        log_path = {log_arg}
        record = {{
            "argv": sys.argv[1:],
            "stdin": sys.stdin.read(),
        }}
        if log_path:
            with open(log_path, "w", encoding="utf-8") as fh:
                json.dump(record, fh)
        sys.exit({save_exit})
        """
    ).lstrip()
    (tk / "save.py").write_text(stub, encoding="utf-8")
    return tk


def _set_env(**kwargs: str | None) -> mock.patch.dict:
    """Context manager: temporarily set env vars (None = unset)."""
    to_set = {k: v for k, v in kwargs.items() if v is not None}
    to_unset = [k for k, v in kwargs.items() if v is None]
    patcher = mock.patch.dict(os.environ, to_set, clear=False)
    # We can't unset via patch.dict; emulate via setting empty + checking helpers.
    # Tests should explicitly use _ClearEnv when they want to remove a var.
    return patcher


class _ClearEnv:
    """Context manager: set env vars, also explicitly unset listed keys on enter."""

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


# -----------------------------------------------------------------------------
# is_available / vault_path
# -----------------------------------------------------------------------------

class TestAvailable(unittest.TestCase):

    def test_available_false_when_env_unset(self) -> None:
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            self.assertFalse(hm.is_available())
            self.assertIsNone(hm.vault_path())

    def test_available_false_when_dir_missing(self) -> None:
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": "/definitely/not/a/real/path"}):
            self.assertFalse(hm.is_available())

    def test_available_true_when_dir_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                self.assertTrue(hm.is_available())
                self.assertEqual(hm.vault_path(), vault)


# -----------------------------------------------------------------------------
# v4.5.1 task 2: vault_path() resolution order — env → config → None
# -----------------------------------------------------------------------------

class TestVaultPathResolutionOrder(unittest.TestCase):
    """Resolution ladder added in v4.5.1: MEMORY_VAULT_PATH env first, then
    on-device .agentm-config.json, then None. Env always wins — even when
    set to a broken path — per v4.5.1 locked DC-2."""

    def _write_config(self, prefix: Path, vault_path: Optional[str]) -> None:
        prefix.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 2, "mode": "source"}
        if vault_path is not None:
            payload["vault_path"] = vault_path
        (prefix / ".agentm-config.json").write_text(
            json.dumps(payload), encoding="utf-8",
        )

    # (a) env set + valid → env wins
    def test_a_env_set_valid_returns_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            prefix = Path(tmp) / "prefix"
            self._write_config(prefix, str(vault))  # config also points here
            with _ClearEnv(set_vars={
                "MEMORY_VAULT_PATH": str(vault),
                "AGENTM_INSTALL_PREFIX": str(prefix),
            }):
                self.assertEqual(hm.vault_path(), vault)

    # (b) env unset + config valid → config wins
    def test_b_env_unset_config_valid_returns_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            prefix = Path(tmp) / "prefix"
            self._write_config(prefix, str(vault))
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertEqual(hm.vault_path(), vault)

    # (c) env set BUT broken + config valid → returns None (env wins, even broken)
    def test_c_env_set_broken_returns_none_even_if_config_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            prefix = Path(tmp) / "prefix"
            self._write_config(prefix, str(vault))
            with _ClearEnv(set_vars={
                "MEMORY_VAULT_PATH": "/definitely/not/a/real/path",
                "AGENTM_INSTALL_PREFIX": str(prefix),
            }):
                self.assertIsNone(hm.vault_path())

    # (d) env unset + config missing → None
    def test_d_env_unset_config_missing_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "empty-prefix"
            prefix.mkdir()
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertIsNone(hm.vault_path())

    # (e) env unset + config present but no vault_path field → None
    def test_e_env_unset_config_lacks_vault_path_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            self._write_config(prefix, vault_path=None)  # no field
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertIsNone(hm.vault_path())

    # (f) env unset + config has vault_path but directory missing → None
    def test_f_env_unset_config_vault_path_dir_missing_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            self._write_config(prefix, "/no/such/dir/at/all")
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertIsNone(hm.vault_path())

    # Bonus: malformed config JSON → graceful-skip returns None
    def test_g_env_unset_config_malformed_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            (prefix / ".agentm-config.json").write_text(
                "{not valid json", encoding="utf-8",
            )
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertIsNone(hm.vault_path())

    # Bonus: non-UTF-8 config bytes (a Windows editor's UTF-16/BOM save) → the
    # "never raises / graceful-skip" contract must hold. read_text(utf-8) raises
    # UnicodeDecodeError (a ValueError, not an OSError/JSONDecodeError), so a guard
    # naming only those two would leak it and crash vault_path() instead of
    # falling through to the next resolution layer.
    def test_g2_env_unset_config_non_utf8_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            (prefix / ".agentm-config.json").write_bytes(
                b'\xff\xfe{"vault_path": "/v"}',
            )
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertIsNone(hm.vault_path())

    # Bonus: vault_path with ~/ expansion works.
    # Skipped on Windows: os.path.expanduser uses USERPROFILE on Windows, not
    # HOME, so the env-override pattern this test uses to fake a home dir is
    # POSIX-only. Production resolver uses os.path.expanduser() which handles
    # the platform difference correctly; the gap is only in test setup.
    @unittest.skipIf(os.name == "nt", "tilde-via-HOME override is POSIX-only test setup")
    def test_h_vault_path_tilde_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            prefix = Path(tmp) / "prefix"
            # Set HOME so ~ expands into tmp
            self._write_config(prefix, "~/v4-test-vault-shadow")
            # We didn't actually create the shadow path; should return None
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix), "HOME": tmp},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertIsNone(hm.vault_path())
            # Now create it + retest
            shadow = Path(tmp) / "v4-test-vault-shadow"
            shadow.mkdir()
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix), "HOME": tmp},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertEqual(hm.vault_path(), shadow)


# -----------------------------------------------------------------------------
# V5-7 config-plane: plugin-namespaced key + first-read migration
# -----------------------------------------------------------------------------

class TestPluginNamespacedVaultPath(unittest.TestCase):
    """V5-7: _read_config_vault_path() reads plugins.obsidian-vault.vault_path first,
    falls back to legacy flat vault_path, and migrates legacy installs on first read."""

    def setUp(self) -> None:
        hm._reset_warn_state()

    def tearDown(self) -> None:
        hm._reset_warn_state()

    def _write_config(self, prefix: Path, **fields) -> None:
        prefix.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 2}
        payload.update(fields)
        (prefix / ".agentm-config.json").write_text(json.dumps(payload), encoding="utf-8")

    def _read_config(self, prefix: Path) -> dict:
        cfg = prefix / ".agentm-config.json"
        return json.loads(cfg.read_text(encoding="utf-8"))

    # (i) New key read: plugin-namespaced key present → returned directly, no migration.
    def test_i_plugin_key_returns_path_no_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            prefix = Path(tmp) / "prefix"
            self._write_config(prefix, **{hm._PLUGIN_VAULT_PATH_KEY: str(vault)})
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertEqual(hm.vault_path(), vault)
            # No migration: legacy key not written.
            cfg = self._read_config(prefix)
            self.assertNotIn("vault_path", cfg)

    # (ii) Legacy key + migration: writes plugin key + storage.backend=vault.
    def test_ii_legacy_key_triggers_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            prefix = Path(tmp) / "prefix"
            self._write_config(prefix, vault_path=str(vault))
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                result = hm.vault_path()
            self.assertEqual(result, vault)
            # Migration must have written both new keys.
            cfg = self._read_config(prefix)
            self.assertEqual(cfg.get(hm._PLUGIN_VAULT_PATH_KEY), str(vault))
            self.assertEqual(cfg.get("storage.backend"), "vault")
            # Legacy key preserved (migration adds, doesn't remove).
            self.assertEqual(cfg.get("vault_path"), str(vault))

    # (iii) Both keys present: plugin key wins; no migration fired.
    def test_iii_both_keys_plugin_wins_no_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_vault = Path(tmp) / "plugin_vault"
            plugin_vault.mkdir()
            legacy_vault = Path(tmp) / "legacy_vault"
            legacy_vault.mkdir()
            prefix = Path(tmp) / "prefix"
            self._write_config(
                prefix,
                **{hm._PLUGIN_VAULT_PATH_KEY: str(plugin_vault), "vault_path": str(legacy_vault)},
            )
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                result = hm.vault_path()
            self.assertEqual(result, plugin_vault)
            # No migration: storage.backend not written by this call.
            cfg = self._read_config(prefix)
            self.assertNotIn("storage.backend", cfg)

    # (iv) Neither key present → None (no migration).
    def test_iv_neither_key_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            self._write_config(prefix)  # no vault keys
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertIsNone(hm.vault_path())

    # (v) Migration idempotent: second call with legacy key doesn't re-migrate.
    def test_v_migration_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            prefix = Path(tmp) / "prefix"
            self._write_config(prefix, vault_path=str(vault))
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                r1 = hm.vault_path()
                r2 = hm.vault_path()  # second call — plugin key now present; no re-migration
            self.assertEqual(r1, vault)
            self.assertEqual(r2, vault)
            # Config written exactly once — storage.backend=vault present.
            cfg = self._read_config(prefix)
            self.assertEqual(cfg.get("storage.backend"), "vault")

    # (vi) Full call chain: legacy key → migration → choose_protocol sees explicit backend.
    # This verifies that the migration write completes BEFORE choose_protocol() runs
    # in select_backend(), which is the load-bearing timing constraint from the plan.
    def test_vi_migration_write_visible_to_choose_protocol(self) -> None:
        import backend_selection as bs
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            prefix = Path(tmp) / "prefix"
            self._write_config(prefix, vault_path=str(vault))
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                # After vault_path() fires migration, _configured_backend() reads
                # the updated config and returns "vault" — so choose_protocol returns
                # "vault" even without the legacy vault_root parameter.
                _ = hm.vault_path()  # fires migration
                protocol = bs.choose_protocol(install_prefix=prefix)
            self.assertEqual(protocol, "vault")


# -----------------------------------------------------------------------------
# V5-3 fail-loud guard: vault_path() raises when storage.backend=vault + no vault
# -----------------------------------------------------------------------------

class TestVaultPathGuard(unittest.TestCase):
    """LC-6: vault_path() is the choke point — raises StorageBackendNotInstalledError
    when storage.backend=vault is configured but no vault path is accessible. The
    guard only fires on the config-file branch (not the env override branch, which is
    a per-session escape hatch)."""

    def _write_config(self, prefix: Path, **fields) -> None:
        config = prefix / ".agentm-config.json"
        config.write_text(json.dumps(fields), encoding="utf-8")

    def test_raises_when_storage_backend_vault_and_no_vault_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            self._write_config(prefix, **{"storage.backend": "vault"})
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                with self.assertRaises(hm.StorageBackendNotInstalledError):
                    hm.vault_path()

    def test_raises_when_storage_backend_vault_and_vault_path_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            self._write_config(
                prefix,
                **{"storage.backend": "vault", "vault_path": "/nonexistent/path/xyz"},
            )
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                with self.assertRaises(hm.StorageBackendNotInstalledError):
                    hm.vault_path()

    def test_does_not_raise_when_storage_backend_vault_and_vault_accessible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            vault = Path(tmp) / "vault"
            vault.mkdir()
            self._write_config(
                prefix,
                **{"storage.backend": "vault", "vault_path": str(vault)},
            )
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertEqual(hm.vault_path(), vault)

    def test_does_not_raise_when_no_storage_backend_set(self) -> None:
        # No storage.backend key → graceful-skip still fires (no guard).
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            self._write_config(prefix)  # empty config
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                self.assertIsNone(hm.vault_path())

    def test_env_override_skips_guard_even_with_storage_backend_vault(self) -> None:
        # MEMORY_VAULT_PATH set to a bad path: graceful-skip (not fail-loud).
        # The env branch is a per-session escape hatch; the guard only fires on
        # the config-file branch.
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            self._write_config(prefix, **{"storage.backend": "vault"})
            with _ClearEnv(
                set_vars={
                    "AGENTM_INSTALL_PREFIX": str(prefix),
                    "MEMORY_VAULT_PATH": "/nonexistent/env/vault",
                },
            ):
                # Must return None (graceful-skip), never raise.
                self.assertIsNone(hm.vault_path())

    def test_error_message_names_vault_and_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            self._write_config(prefix, **{"storage.backend": "vault"})
            with _ClearEnv(
                set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)},
                unset_keys=["MEMORY_VAULT_PATH"],
            ):
                with self.assertRaises(hm.StorageBackendNotInstalledError) as ctx:
                    hm.vault_path()
                msg = str(ctx.exception)
                self.assertIn("vault", msg)
                self.assertIn("vault_path", msg)


# -----------------------------------------------------------------------------
# recall
# -----------------------------------------------------------------------------

class TestRecall(unittest.TestCase):

    def test_recall_empty_when_vault_absent(self) -> None:
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            self.assertEqual(hm.phase_recall("plan", "any-slug"), "")

    def test_recall_always_empty_v5_3(self) -> None:
        """V5-3: vault backend removed — phase_recall always returns empty string."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                out = hm.phase_recall("plan", "agentm")
        self.assertEqual(out, "")

    def test_recall_unknown_phase_raises(self) -> None:
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": "/tmp"}):
            with self.assertRaises(ValueError):
                hm.phase_recall("bogus", "x")

    def test_recall_review_phase_empty_v5_3(self) -> None:
        """V5-3: all phases return empty (vault backend removed)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                out = hm.phase_recall("review", "agentm")
        self.assertEqual(out, "")


# -----------------------------------------------------------------------------
# offer-save: confidence / mode / non-TTY
# -----------------------------------------------------------------------------

class TestOfferSaveDecision(unittest.TestCase):
    """should_prompt() pure decision logic — no I/O."""

    def test_silent_mode_never_prompts(self) -> None:
        self.assertFalse(hm.should_prompt(0.1, mode="silent", threshold=0.8))
        self.assertFalse(hm.should_prompt(None, mode="silent", threshold=0.8))

    def test_off_mode_never_prompts(self) -> None:
        self.assertFalse(hm.should_prompt(0.99, mode="off", threshold=0.8))

    def test_ask_mode_high_confidence_skips_prompt(self) -> None:
        self.assertFalse(hm.should_prompt(0.9, mode="ask", threshold=0.8))

    def test_ask_mode_low_confidence_prompts(self) -> None:
        self.assertTrue(hm.should_prompt(0.5, mode="ask", threshold=0.8))

    def test_ask_mode_no_confidence_prompts(self) -> None:
        self.assertTrue(hm.should_prompt(None, mode="ask", threshold=0.8))

    def test_ask_mode_at_threshold_skips(self) -> None:
        self.assertFalse(hm.should_prompt(0.8, mode="ask", threshold=0.8))


class TestOfferSaveBehavior(unittest.TestCase):
    """offer_save() end-to-end with stub toolkit."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.vault = _make_vault(self.tmp_root, project="fixture-project")
        self.save_log = self.tmp_root / "save_log.json"
        self.toolkit = _make_toolkit_stub(self.tmp_root, save_log=self.save_log)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *, confidence=None, mode="ask", threshold=None, stdin_data=""):
        env = {
            "MEMORY_VAULT_PATH": str(self.vault),
            "HARNESS_AUTO_SAVE_MODE": mode,
            "HARNESS_MEMORY_TOOLKIT_PATH": str(self.toolkit),
        }
        if threshold is not None:
            env["HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD"] = str(threshold)
        stdout = io.StringIO()
        stderr = io.StringIO()
        # Non-TTY stdin (StringIO doesn't have isatty=True).
        stdin = io.StringIO(stdin_data)
        with _ClearEnv(set_vars=env):
            rc = hm.offer_save(
                phase="work",
                project="fixture-project",
                kind="decision",
                slug="example-call",
                body="this is the entry body\n",
                confidence=confidence,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_off_mode_no_save(self) -> None:
        rc, out, err = self._run(mode="off")
        self.assertEqual(rc, 0)
        self.assertIn("HARNESS_AUTO_SAVE_MODE=off", err)
        self.assertFalse(self.save_log.exists())

    def test_silent_mode_saves_without_prompt(self) -> None:
        rc, out, err = self._run(mode="silent")
        self.assertEqual(rc, 0)
        # No preview headers in silent mode.
        self.assertNotIn("offer-save preview", out)
        self.assertIn("silent save", err)
        self.assertTrue(self.save_log.exists())
        log = json.loads(self.save_log.read_text(encoding="utf-8"))
        self.assertIn("--group", log["argv"])
        idx = log["argv"].index("--group")
        self.assertEqual(log["argv"][idx + 1], "personal-projects/fixture-project")
        self.assertEqual(log["argv"][-2:], ["decision", "example-call"])
        self.assertIn("this is the entry body", log["stdin"])

    def test_ask_high_confidence_auto_saves(self) -> None:
        rc, out, err = self._run(mode="ask", confidence=0.9)
        self.assertEqual(rc, 0)
        self.assertNotIn("offer-save preview", out)
        self.assertIn("[auto-saved high-confidence]", err)
        self.assertTrue(self.save_log.exists())

    def test_ask_low_confidence_non_tty_skips(self) -> None:
        rc, out, err = self._run(mode="ask", confidence=0.5)
        self.assertEqual(rc, 0)
        self.assertIn("offer-save preview", out)
        self.assertIn("non-TTY", err)
        self.assertFalse(self.save_log.exists())

    def test_ask_no_confidence_non_tty_skips(self) -> None:
        rc, out, err = self._run(mode="ask", confidence=None)
        self.assertEqual(rc, 0)
        self.assertIn("offer-save preview", out)
        self.assertFalse(self.save_log.exists())

    def test_ask_high_confidence_but_higher_threshold_prompts(self) -> None:
        # confidence=0.9 with threshold=0.95 → still below → prompt fires
        # (non-TTY default skip).
        rc, out, err = self._run(mode="ask", confidence=0.9, threshold=0.95)
        self.assertEqual(rc, 0)
        self.assertIn("offer-save preview", out)
        self.assertFalse(self.save_log.exists())


class TestOfferSaveToolkitAbsent(unittest.TestCase):
    """When toolkit isn't installed, offer-save records intent + exits 0."""

    def test_toolkit_absent_graceful_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            # Point toolkit override at a non-existent dir.
            env = {
                "MEMORY_VAULT_PATH": str(vault),
                "HARNESS_AUTO_SAVE_MODE": "silent",
                "HARNESS_MEMORY_TOOLKIT_PATH": str(Path(tmp) / "missing-toolkit"),
            }
            stdout = io.StringIO()
            stderr = io.StringIO()
            with _ClearEnv(set_vars=env):
                rc = hm.offer_save(
                    phase="work",
                    project="fixture-project",
                    kind="decision",
                    slug="x",
                    body="body",
                    confidence=0.9,
                    stdin=io.StringIO(""),
                    stdout=stdout,
                    stderr=stderr,
                )
            self.assertEqual(rc, 0)
            self.assertIn("toolkit not installed", stderr.getvalue())


# -----------------------------------------------------------------------------
# plan-done-promotion: cursor + tail
# -----------------------------------------------------------------------------

class TestPlanDonePromotion(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vault = _make_vault(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_progress(self, content: str) -> None:
        h = self.root / ".harness"
        h.mkdir(parents=True, exist_ok=True)
        (h / "progress.md").write_bytes(content.encode("utf-8"))

    def test_no_progress_returns_empty(self) -> None:
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(self.vault)}):
            self.assertEqual(hm.plan_done_promotion(self.root), "")

    def test_vault_absent_returns_empty(self) -> None:
        self._write_progress("some content\n")
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            self.assertEqual(hm.plan_done_promotion(self.root), "")

    def test_first_run_returns_full_tail_and_advances_cursor(self) -> None:
        content = "entry A\n\nentry B\n\nentry C\n"
        self._write_progress(content)
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(self.vault)}):
            tail = hm.plan_done_promotion(self.root)
        self.assertEqual(tail, content)
        cursor = (self.root / ".harness" / ".promoted-progress-cursor").read_text(encoding="utf-8")
        self.assertEqual(int(cursor.strip()), len(content.encode("utf-8")))

    def test_idempotent_re_run_returns_empty(self) -> None:
        content = "entry A\n\nentry B\n"
        self._write_progress(content)
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(self.vault)}):
            first = hm.plan_done_promotion(self.root)
            second = hm.plan_done_promotion(self.root)
        self.assertEqual(first, content)
        self.assertEqual(second, "")

    def test_appended_content_after_cursor_returned_next_run(self) -> None:
        self._write_progress("entry A\n")
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(self.vault)}):
            hm.plan_done_promotion(self.root)
            # Now append more progress entries
            (self.root / ".harness" / "progress.md").write_bytes(
                b"entry A\nentry B\n"
            )
            second = hm.plan_done_promotion(self.root)
        self.assertEqual(second, "entry B\n")

    def test_dry_run_does_not_advance_cursor(self) -> None:
        self._write_progress("entry A\n")
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(self.vault)}):
            tail = hm.plan_done_promotion(self.root, advance_cursor=False)
            self.assertEqual(tail, "entry A\n")
            # Cursor file should not exist (no advance happened).
            self.assertFalse((self.root / ".harness" / ".promoted-progress-cursor").is_file())
            # Re-running (without dry-run) should still return the full tail.
            again = hm.plan_done_promotion(self.root)
            self.assertEqual(again, "entry A\n")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

class TestCLI(unittest.TestCase):

    def _run(self, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("MEMORY_VAULT_PATH", None)
        env.pop("HARNESS_AUTO_SAVE_MODE", None)
        env.pop("HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD", None)
        env.pop("HARNESS_MEMORY_TOOLKIT_PATH", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(_HERE / "harness_memory.py"), *args],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )

    def test_cli_available_no_vault_exits_1(self) -> None:
        result = self._run("available")
        self.assertEqual(result.returncode, 1)

    def test_cli_available_with_vault_exits_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            result = self._run("available", env_extra={"MEMORY_VAULT_PATH": str(vault)})
        self.assertEqual(result.returncode, 0)

    def test_cli_recall_no_vault_empty_zero(self) -> None:
        result = self._run("recall", "--phase", "work", "--project", "x")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_cli_recall_always_empty_v5_3(self) -> None:
        """V5-3: vault backend removed — recall returns empty for any phase/vault."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp), project="agentm")
            result = self._run(
                "recall", "--phase", "plan", "--project", "agentm",
                env_extra={"MEMORY_VAULT_PATH": str(vault)},
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_cli_plan_done_promotion_empty_when_no_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            result = self._run(
                "plan-done-promotion", "--project-root", tmp,
                env_extra={"MEMORY_VAULT_PATH": str(vault)},
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    # V4 #37 task 7 / V5-3: read-state and write-state are device-local only.

    def test_cli_read_state_returns_repo_local_content(self) -> None:
        """V5-3: vault-first read removed — read-state reads from repo .harness/."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            (project_root / ".harness" / "PLAN.md").write_text(
                "repo PLAN content\n", encoding="utf-8"
            )
            vault = _make_vault_new_layout(Path(tmp), project="fixture")
            result = self._run(
                "read-state", "PLAN.md",
                "--project-root", str(project_root),
                env_extra={"MEMORY_VAULT_PATH": str(vault)},
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "repo PLAN content\n")

    def test_cli_read_state_reads_repo_local(self) -> None:
        """V5-3: read-state reads from <project>/.harness/<file> (device-local)."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            (project_root / ".harness" / "PLAN.md").write_text(
                "repo PLAN content\n", encoding="utf-8"
            )
            result = self._run(
                "read-state", "PLAN.md",
                "--project-root", str(project_root),
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "repo PLAN content\n")

    def test_cli_write_state_writes_device_local(self) -> None:
        """V5-3: write-state writes to <project>/.harness/ (device-local)."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            content_file = Path(tmp) / "input.md"
            content_file.write_text("new content\n", encoding="utf-8")
            result = self._run(
                "write-state", "PLAN.md",
                "--project-root", str(project_root),
                "--content-file", str(content_file),
            )
            self.assertEqual(result.returncode, 0)
            target = project_root / ".harness" / "PLAN.md"
            self.assertEqual(result.stdout.strip(), str(target))
            self.assertEqual(target.read_text(encoding="utf-8"), "new content\n")

    def test_cli_write_then_read_state_local_mode_no_vault(self) -> None:
        """Hardening I task 2: with NO vault configured + a repo-local
        .project-mode=local marker, the write-state/read-state CLIs round-trip
        through <repo>/.harness/ (the first-class single-repo path)."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            (project_root / ".harness").mkdir(parents=True)
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            (project_root / ".harness" / ".project-mode").write_text(
                "local", encoding="utf-8"
            )
            content_file = Path(tmp) / "input.md"
            content_file.write_text("local CLI content\n", encoding="utf-8")
            # No MEMORY_VAULT_PATH → no vault; _run pops it + the module sandboxes
            # AGENTM_INSTALL_PREFIX so vault_path() resolves to None.
            wr = self._run(
                "write-state", "PLAN.md",
                "--project-root", str(project_root),
                "--content-file", str(content_file),
            )
            self.assertEqual(wr.returncode, 0, wr.stderr)
            target = project_root / ".harness" / "PLAN.md"
            self.assertEqual(wr.stdout.strip(), str(target))
            self.assertEqual(target.read_text(encoding="utf-8"), "local CLI content\n")

            rd = self._run(
                "read-state", "PLAN.md",
                "--project-root", str(project_root),
            )
            self.assertEqual(rd.returncode, 0, rd.stderr)
            self.assertEqual(rd.stdout, "local CLI content\n")
            # Local mode is the configured home — no migrate-to-vault nag.
            self.assertNotIn("migrate-harness-to-vault.sh", rd.stderr)


# -----------------------------------------------------------------------------
# resolve_project / _vault_projects_dir  (V4 #26)
# -----------------------------------------------------------------------------

def _make_vault_new_layout(root: Path, *, project: str = "fixture-project") -> Path:
    """Build a vault using the post-V4 #26 `projects/` layout (no legacy dir)."""
    vault = root / "vault"
    (vault / "personal" / "_always-load").mkdir(parents=True)
    (vault / "projects" / project / "decisions").mkdir(parents=True)
    (vault / "projects" / project / "_index.md").write_text(
        f"# {project} index\nv4.1.0+ layout\n",
        encoding="utf-8",
    )
    return vault


class TestVaultProjectsDir(unittest.TestCase):
    """Covers _vault_projects_dir — now takes a StorageBackend, returns Locator (V5-6)."""

    def _make_backend(self, root: Path):
        from storage_device_local import DeviceLocalBackend
        return DeviceLocalBackend(root=root)

    def test_prefers_new_projects_dir_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "projects").mkdir(parents=True)
            backend = self._make_backend(root)
            result = hm._vault_projects_dir(backend)
            from storage_seam import Locator
            self.assertEqual(result, Locator("projects"))
            self.assertEqual(result.name, "projects")

    def test_falls_back_to_legacy_personal_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "personal-projects").mkdir(parents=True)
            backend = self._make_backend(root)
            result = hm._vault_projects_dir(backend)
            from storage_seam import Locator
            self.assertEqual(result, Locator("personal-projects"))
            self.assertEqual(result.name, "personal-projects")

    def test_prefers_new_when_both_present(self) -> None:
        """Locked semantics: if both dirs exist, new layout wins (legacy is stale)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "projects").mkdir(parents=True)
            (root / "personal-projects").mkdir(parents=True)
            backend = self._make_backend(root)
            result = hm._vault_projects_dir(backend)
            from storage_seam import Locator
            self.assertEqual(result, Locator("projects"))

    def test_returns_new_locator_when_neither_present(self) -> None:
        """Empty backend: return the new Locator (so write callers target post-V4 layout)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = self._make_backend(root)
            result = hm._vault_projects_dir(backend)
            from storage_seam import Locator
            self.assertEqual(result, Locator("projects"))

    def test_parallel_run_vault_backend_maps_to_same_path(self) -> None:
        """LC-7 parallel-run: vault backend Locator maps to the same path as the old vault_path (V5-6)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "projects").mkdir(parents=True)
            from storage_vault import VaultBackend
            backend = VaultBackend(root=vault)
            loc = hm._vault_projects_dir(backend)
            # The Locator key is "projects" — on the vault backend this maps to
            # <vault>/projects, exactly the same directory the old Path returned.
            self.assertEqual(loc.key, "projects")
            # Confirm the vault backend maps it to the same physical path.
            expected_path = vault / "projects"
            actual_path = vault / Path(*loc.parts) if loc.parts else vault
            self.assertEqual(actual_path, expected_path)


class TestResolveProject(unittest.TestCase):
    """Covers resolve_project() → {slug, project_locator, backend, project_root, layout} (V5-6)."""

    def _make_vault_backend(self, vault_root: Path):
        """VaultBackend seeded from vault_root — direct import, bypasses plugin discovery."""
        from storage_vault import VaultBackend
        return VaultBackend(root=vault_root)

    def test_no_slug_returns_none_fields(self) -> None:
        """No git origin + no project.json = no slug → layout='none', locator=None."""
        with tempfile.TemporaryDirectory() as tmp:
            resolution = hm.resolve_project({"cwd": Path(tmp)})
        self.assertIsNone(resolution["slug"])
        self.assertIsNone(resolution["project_locator"])
        self.assertIsNone(resolution["backend"])
        self.assertEqual(resolution["layout"], "none")

    def test_slug_present_but_backend_unavailable(self) -> None:
        """Slug resolves but select_backend() raises → project_locator=None, layout='none'."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "my-project"}', encoding="utf-8"
            )
            with mock.patch("backend_selection.select_backend", side_effect=Exception("no backend")):
                resolution = hm.resolve_project({"cwd": project_root})
        self.assertEqual(resolution["slug"], "my-project")
        self.assertIsNone(resolution["project_locator"])
        self.assertIsNone(resolution["backend"])
        self.assertEqual(resolution["layout"], "none")

    def test_resolves_new_layout(self) -> None:
        """Vault backend has projects/<slug>/ → layout='new', project_locator.key='projects/fixture'."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            vault = _make_vault_new_layout(Path(tmp), project="fixture")
            backend = self._make_vault_backend(vault)
            with mock.patch("backend_selection.select_backend", return_value=backend):
                resolution = hm.resolve_project({"cwd": project_root})
        self.assertEqual(resolution["slug"], "fixture")
        from storage_seam import Locator
        self.assertEqual(resolution["project_locator"], Locator("projects/fixture"))
        self.assertIs(resolution["backend"], backend)
        self.assertEqual(resolution["layout"], "new")

    def test_resolves_legacy_layout(self) -> None:
        """Vault backend has personal-projects/<slug>/ (no projects/) → layout='legacy'."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            vault = _make_vault(Path(tmp), project="fixture")  # legacy layout
            backend = self._make_vault_backend(vault)
            with mock.patch("backend_selection.select_backend", return_value=backend):
                resolution = hm.resolve_project({"cwd": project_root})
        self.assertEqual(resolution["slug"], "fixture")
        from storage_seam import Locator
        self.assertEqual(resolution["project_locator"], Locator("personal-projects/fixture"))
        self.assertEqual(resolution["layout"], "legacy")

    def test_returns_new_locator_when_neither_layout_has_project(self) -> None:
        """Slug + backend present but no project dir → layout='new', locator=projects/<slug>."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "new-project"}', encoding="utf-8"
            )
            vault = Path(tmp) / "vault"
            vault.mkdir()
            backend = self._make_vault_backend(vault)
            with mock.patch("backend_selection.select_backend", return_value=backend):
                resolution = hm.resolve_project({"cwd": project_root})
        self.assertEqual(resolution["slug"], "new-project")
        from storage_seam import Locator
        self.assertEqual(resolution["project_locator"], Locator("projects/new-project"))
        self.assertEqual(resolution["layout"], "new")


# -----------------------------------------------------------------------------
# read_state_file / write_state_file / warn_once  (V4 #26 task 3 / V5-3)
# -----------------------------------------------------------------------------

class TestReadStateFile(unittest.TestCase):
    """Covers device-local read (V5-3: vault-first read removed)."""

    def setUp(self) -> None:
        hm._reset_warn_state()

    def test_returns_empty_when_neither_path_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolution = {
                "vault_path": Path(tmp) / "vault" / "projects" / "p",
                "project_root": Path(tmp) / "project",
            }
            (Path(tmp) / "project").mkdir()
            self.assertEqual(hm.read_state_file(resolution, "PLAN.md"), "")

    def test_reads_device_local_no_warn_v5_3(self) -> None:
        # V5-3: device-local .harness/ is the canonical location — no warning emitted.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / "PLAN.md").write_text("device-local content", encoding="utf-8")
            resolution = {
                "vault_path": Path(tmp) / "vault" / "projects" / "p",
                "project_root": project,
            }
            with io.StringIO() as buf:
                with mock.patch("sys.stderr", buf):
                    result = hm.read_state_file(resolution, "PLAN.md")
                self.assertEqual(result, "device-local content")
                self.assertEqual(buf.getvalue(), "")  # no legacy warning

    def test_multiple_reads_no_warn_v5_3(self) -> None:
        # V5-3: multiple reads of the same device-local file produce no warnings.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / "PLAN.md").write_text("content", encoding="utf-8")
            resolution = {"vault_path": None, "project_root": project}
            with io.StringIO() as buf:
                with mock.patch("sys.stderr", buf):
                    hm.read_state_file(resolution, "PLAN.md")
                    hm.read_state_file(resolution, "PLAN.md")
                    hm.read_state_file(resolution, "PLAN.md")
                self.assertEqual(buf.getvalue(), "")  # no warnings across all reads

    def test_different_files_no_warn_v5_3(self) -> None:
        # V5-3: reads of different device-local files produce no warnings.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / "PLAN.md").write_text("a", encoding="utf-8")
            (project / ".harness" / "progress.md").write_text("b", encoding="utf-8")
            resolution = {"vault_path": None, "project_root": project}
            with io.StringIO() as buf:
                with mock.patch("sys.stderr", buf):
                    result_plan = hm.read_state_file(resolution, "PLAN.md")
                    result_prog = hm.read_state_file(resolution, "progress.md")
                self.assertEqual(result_plan, "a")
                self.assertEqual(result_prog, "b")
                self.assertEqual(buf.getvalue(), "")  # no warnings

    def test_project_mode_local_bypasses_vault_read(self) -> None:
        """DC-8: device-level state_mode=local routes the read to the repo-local
        <repo>/.harness/, even with a vault present and vault content.

        (Hardening I task 3 — replaces the removed in-vault-marker mechanism;
        see test_in_vault_marker_ignored_on_read for the removal regression.)"""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            (prefix / ".agentm-config.json").write_text(
                json.dumps({"state_mode": "local"}), encoding="utf-8")
            vp = Path(tmp) / "vault" / "projects" / "p"
            (vp / "_harness").mkdir(parents=True)
            (vp / "_harness" / "PLAN.md").write_text("vault content", encoding="utf-8")
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / "PLAN.md").write_text("repo content", encoding="utf-8")
            resolution = {"vault_path": vp, "project_root": project}
            with _ClearEnv(set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)}):
                self.assertEqual(hm.read_state_file(resolution, "PLAN.md"), "repo content")

    def test_vault_content_not_read_v5_3(self) -> None:
        """V5-3: vault-first read removed. Even with vault_path set and a vault
        file present, read_state_file returns repo-local content (not vault)."""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()  # empty config dir → no device state_mode
            vp = Path(tmp) / "vault" / "projects" / "p"
            (vp / "_harness").mkdir(parents=True)
            (vp / "_harness" / "PLAN.md").write_text("vault content", encoding="utf-8")
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / "PLAN.md").write_text("repo content", encoding="utf-8")
            resolution = {"vault_path": vp, "project_root": project}
            with _ClearEnv(set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)}):
                self.assertEqual(hm.read_state_file(resolution, "PLAN.md"), "repo content")

    # Hardening I task 2: vault-less read path + repo-local marker (DC-2).

    def test_repo_local_marker_local_no_vault_reads_repo(self) -> None:
        """#44 core: no vault + repo-local .project-mode=local → read comes from
        <repo>/.harness/ (round-trips the local write path)."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
            (project / ".harness" / "PLAN.md").write_text("local content", encoding="utf-8")
            resolution = {"vault_path": None, "project_root": project}
            self.assertEqual(hm.read_state_file(resolution, "PLAN.md"), "local content")

    def test_local_mode_read_emits_no_migrate_warning(self) -> None:
        """Local mode is the configured home, not a legacy fallback — reading it
        must NOT print the migrate-to-vault deprecation warning."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
            (project / ".harness" / "PLAN.md").write_text("local content", encoding="utf-8")
            resolution = {"vault_path": None, "project_root": project}
            with io.StringIO() as buf:
                with mock.patch("sys.stderr", buf):
                    self.assertEqual(hm.read_state_file(resolution, "PLAN.md"), "local content")
                stderr = buf.getvalue()
            self.assertNotIn("migrate-harness-to-vault.sh", stderr)
            self.assertNotIn("from legacy", stderr)

    def test_repo_local_marker_honored_with_vault_present_on_read(self) -> None:
        """DC-2: with a vault present and vault content, a repo-local marker still
        routes the read to <repo>/.harness/."""
        with tempfile.TemporaryDirectory() as tmp:
            vp = Path(tmp) / "vault" / "projects" / "p"
            (vp / "_harness").mkdir(parents=True)
            (vp / "_harness" / "PLAN.md").write_text("vault content", encoding="utf-8")
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
            (project / ".harness" / "PLAN.md").write_text("repo content", encoding="utf-8")
            resolution = {"vault_path": vp, "project_root": project}
            self.assertEqual(hm.read_state_file(resolution, "PLAN.md"), "repo content")


class TestWriteStateFile(unittest.TestCase):
    """Covers device-local writes (V5-3: vault backend removed)."""

    def test_writes_device_local_regardless_of_vault_path(self) -> None:
        """V5-3: write_state_file writes to <project_root>/.harness/ always."""
        with tempfile.TemporaryDirectory() as tmp:
            vp = Path(tmp) / "vault" / "projects" / "p"
            project = Path(tmp) / "project"
            project.mkdir()
            resolution = {"vault_path": vp, "project_root": project}
            target = hm.write_state_file(resolution, "PLAN.md", "new content")
            self.assertEqual(target, project / ".harness" / "PLAN.md")
            self.assertEqual(target.read_text(encoding="utf-8"), "new content")
            # _harness/ dir created in project root.
            self.assertTrue((project / ".harness").is_dir())
            # Vault untouched.
            self.assertFalse((vp / "_harness" / "PLAN.md").exists())

    def test_no_vault_path_writes_device_local(self) -> None:
        """V5-3: no vault_path no longer raises — writes to project_root/.harness/."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            resolution = {"vault_path": None, "project_root": project}
            target = hm.write_state_file(resolution, "PLAN.md", "x")
            self.assertEqual(target, project / ".harness" / "PLAN.md")

    def test_atomic_write_no_tmp_remnant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            resolution = {"vault_path": None, "project_root": project}
            hm.write_state_file(resolution, "PLAN.md", "content")
            # No .tmp file left behind.
            self.assertEqual(
                list((project / ".harness").glob("PLAN.md.*")), []
            )

    def test_device_state_mode_local_writes_to_repo(self) -> None:
        """DC-8: device-level state_mode=local routes the write to <repo>/.harness/,
        leaving the vault untouched. (Hardening I task 3 — replaces the removed
        in-vault-marker mechanism.)"""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            (prefix / ".agentm-config.json").write_text(
                json.dumps({"state_mode": "local"}), encoding="utf-8")
            vp = Path(tmp) / "vault" / "projects" / "p"
            (vp / "_harness").mkdir(parents=True)
            project = Path(tmp) / "project"
            project.mkdir()
            resolution = {"vault_path": vp, "project_root": project}
            with _ClearEnv(set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)}):
                target = hm.write_state_file(resolution, "PLAN.md", "device local write")
            self.assertEqual(target, project / ".harness" / "PLAN.md")
            self.assertEqual(target.read_text(encoding="utf-8"), "device local write")
            # Vault path NOT written.
            self.assertFalse((vp / "_harness" / "PLAN.md").exists())

    # Hardening I task 2: vault-less write path + repo-local marker (DC-2).

    def test_repo_local_marker_local_no_vault_writes_to_repo(self) -> None:
        """#44 core: no vault + repo-local .project-mode=local → write lands in
        <repo>/.harness/ and does NOT raise ValueError."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
            resolution = {"vault_path": None, "project_root": project}
            target = hm.write_state_file(resolution, "PLAN.md", "local content")
            self.assertEqual(target, project / ".harness" / "PLAN.md")
            self.assertEqual(target.read_text(encoding="utf-8"), "local content")

    def test_repo_local_marker_local_honored_with_vault_present(self) -> None:
        """DC-2: a repo-local marker is authoritative even when a vault exists —
        the write goes repo-local, the vault is untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            vp = Path(tmp) / "vault" / "projects" / "p"
            (vp / "_harness").mkdir(parents=True)
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
            resolution = {"vault_path": vp, "project_root": project}
            target = hm.write_state_file(resolution, "PLAN.md", "repo wins")
            self.assertEqual(target, project / ".harness" / "PLAN.md")
            self.assertEqual(target.read_text(encoding="utf-8"), "repo wins")
            self.assertFalse((vp / "_harness" / "PLAN.md").exists())

    def test_all_modes_write_device_local_v5_3(self) -> None:
        """V5-3: vault backend removed — all mode configurations write device-locally."""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            (prefix / ".agentm-config.json").write_text(
                json.dumps({"state_mode": "local"}), encoding="utf-8")
            vp = Path(tmp) / "vault" / "projects" / "p"
            (vp / "_harness").mkdir(parents=True)
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            resolution = {"vault_path": vp, "project_root": project}
            with _ClearEnv(set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)}):
                target = hm.write_state_file(resolution, "PLAN.md", "device local")
            self.assertEqual(target, project / ".harness" / "PLAN.md")
            # Vault untouched.
            self.assertFalse((vp / "_harness" / "PLAN.md").exists())

    def test_no_vault_no_local_marker_writes_device_local_v5_3(self) -> None:
        """V5-3: no vault + no local marker no longer raises; writes device-locally."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            resolution = {"vault_path": None, "project_root": project}
            target = hm.write_state_file(resolution, "PLAN.md", "x")
            self.assertEqual(target, project / ".harness" / "PLAN.md")

    def test_local_write_tolerates_str_project_root(self) -> None:
        """read/write symmetry: the local read path wraps `project_root` in Path,
        so the write path must too — a str project_root must not crash."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "p"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / ".project-mode").write_text("local", encoding="utf-8")
            resolution = {"vault_path": None, "project_root": str(project)}  # str, not Path
            target = hm.write_state_file(resolution, "PLAN.md", "str-root content")
            self.assertEqual(Path(target), project / ".harness" / "PLAN.md")
            # Symmetric read with the same str input round-trips.
            self.assertEqual(hm.read_state_file(resolution, "PLAN.md"), "str-root content")

    def test_empty_repo_local_marker_falls_through_to_device(self) -> None:
        """A whitespace-only repo-local marker is treated as absent, so the
        device-level state_mode decides the mode."""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            (prefix / ".agentm-config.json").write_text(
                json.dumps({"state_mode": "local"}), encoding="utf-8")
            vp = Path(tmp) / "vault" / "projects" / "p"
            (vp / "_harness").mkdir(parents=True)
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / ".project-mode").write_text("   \n", encoding="utf-8")
            resolution = {"vault_path": vp, "project_root": project}
            with _ClearEnv(set_vars={"AGENTM_INSTALL_PREFIX": str(prefix)}):
                target = hm.write_state_file(resolution, "PLAN.md", "device decides")
            # empty repo marker → device state_mode=local → repo-local home.
            self.assertEqual(target, project / ".harness" / "PLAN.md")


class TestReadConfigStateMode(unittest.TestCase):
    """Covers _read_config_state_mode — the device-level on-host state_mode read
    (Hardening I task 3 / DC-8). Read vault-free from .agentm-config.json."""

    def _write_config(self, prefix: Path, payload) -> None:
        (prefix / ".agentm-config.json").write_text(payload, encoding="utf-8")

    def test_reads_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), json.dumps({"state_mode": "local"}))
            self.assertEqual(hm._read_config_state_mode(Path(tmp)), "local")

    def test_reads_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), json.dumps({"state_mode": "backend"}))
            self.assertEqual(hm._read_config_state_mode(Path(tmp)), "backend")

    def test_reads_vault_aliases_to_backend(self) -> None:
        """LC-5: legacy state_mode:vault aliases to 'backend' at read time (no migration)."""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), json.dumps({"state_mode": "vault"}))
            self.assertEqual(hm._read_config_state_mode(Path(tmp)), "backend")

    def test_vault_and_backend_produce_identical_resolution(self) -> None:
        """LC-5 alias test: state_mode:vault and state_mode:backend resolve identically."""
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            self._write_config(Path(tmp1), json.dumps({"state_mode": "vault"}))
            self._write_config(Path(tmp2), json.dumps({"state_mode": "backend"}))
            self.assertEqual(
                hm._read_config_state_mode(Path(tmp1)),
                hm._read_config_state_mode(Path(tmp2)),
            )

    def test_absent_field_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), json.dumps({"vault_path": "/v"}))
            self.assertIsNone(hm._read_config_state_mode(Path(tmp)))

    def test_no_config_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(hm._read_config_state_mode(Path(tmp)))

    def test_normalizes_case_and_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), json.dumps({"state_mode": " LOCAL \n"}))
            self.assertEqual(hm._read_config_state_mode(Path(tmp)), "local")

    def test_malformed_json_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), "{not json")
            self.assertIsNone(hm._read_config_state_mode(Path(tmp)))

    def test_non_string_value_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), json.dumps({"state_mode": 1}))
            self.assertIsNone(hm._read_config_state_mode(Path(tmp)))

    def test_non_utf8_returns_none(self) -> None:
        # UTF-16/BOM config from a Windows editor → graceful-skip, not a leaked
        # UnicodeDecodeError (mirrors the vault_path reader's contract).
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".agentm-config.json").write_bytes(
                b'\xff\xfe{"state_mode": "local"}',
            )
            self.assertIsNone(hm._read_config_state_mode(Path(tmp)))


# -----------------------------------------------------------------------------
# safe_write_replace_style  (V4 #26 task 4)
# (the detect_conflict_files / lost_and_found sweep re-homed to the crickets
#  obsidian-vault plugin in V5-2 task 2; its tests moved with it)
# -----------------------------------------------------------------------------

class TestSafeWriteReplaceStyle(unittest.TestCase):
    """Covers atomic-write with optional mtime concurrent-modification check."""

    def test_plain_write_no_mtime_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            result = hm.safe_write_replace_style(path, "content")
            self.assertEqual(result, path)
            self.assertEqual(path.read_text(), "content")

    def test_overwrites_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            path.write_text("old")
            hm.safe_write_replace_style(path, "new")
            self.assertEqual(path.read_text(), "new")

    def test_creates_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "deep" / "PLAN.md"
            hm.safe_write_replace_style(path, "x")
            self.assertTrue(path.is_file())

    def test_mtime_check_passes_when_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            path.write_text("initial")
            mtime = path.stat().st_mtime
            # Write with matching expected_mtime succeeds.
            hm.safe_write_replace_style(path, "updated", expected_mtime=mtime)
            self.assertEqual(path.read_text(), "updated")

    def test_mtime_check_raises_when_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            path.write_text("initial")
            # Simulate "I read it earlier" with a stale mtime.
            stale_mtime = path.stat().st_mtime - 1000.0  # an hour ago
            with self.assertRaises(hm.ConcurrentModificationError) as cm:
                hm.safe_write_replace_style(path, "x", expected_mtime=stale_mtime)
            self.assertIn("modified since read", str(cm.exception))
            # File contents unchanged.
            self.assertEqual(path.read_text(), "initial")

    def test_mtime_check_passes_when_file_absent_originally(self) -> None:
        """First-write case: expected_mtime is provided but file doesn't exist
        yet — check should pass (nothing to conflict with) and proceed."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            hm.safe_write_replace_style(path, "x", expected_mtime=12345.0)
            self.assertEqual(path.read_text(), "x")

    # V5-0: content-hash CAS is the preferred currency (R4 rule 4). The
    # mtime tests above stay as back-compat proof that the deprecated arg still
    # works; these prove the new path.
    def test_hash_check_passes_when_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            path.write_bytes(b"initial")
            h = hm.content_hash(path.read_bytes())
            hm.safe_write_replace_style(path, "updated", expected_hash=h)
            self.assertEqual(path.read_text(), "updated")

    def test_hash_check_raises_when_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            path.write_bytes(b"initial")
            stale_hash = hm.content_hash(b"content the file no longer has")
            with self.assertRaises(hm.ConcurrentModificationError) as cm:
                hm.safe_write_replace_style(path, "x", expected_hash=stale_hash)
            self.assertIn("modified since read", str(cm.exception))
            self.assertEqual(path.read_text(), "initial")  # untouched

    def test_hash_check_raises_when_deleted(self) -> None:
        """Held a hash (file existed at read) but it vanished — concurrent
        deletion is itself a conflict."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            stale_hash = hm.content_hash(b"once existed")
            with self.assertRaises(hm.ConcurrentModificationError) as cm:
                hm.safe_write_replace_style(path, "x", expected_hash=stale_hash)
            self.assertIn("deleted since read", str(cm.exception))

    def test_hash_takes_precedence_over_mtime(self) -> None:
        """Both args given: expected_hash is authoritative — a matching hash
        writes even with a deliberately stale mtime arg."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            path.write_bytes(b"initial")
            h = hm.content_hash(path.read_bytes())
            hm.safe_write_replace_style(path, "updated", expected_hash=h, expected_mtime=1.0)
            self.assertEqual(path.read_text(), "updated")

    def test_atomic_no_tmp_remnant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            hm.safe_write_replace_style(path, "x")
            self.assertEqual(list(Path(tmp).glob("PLAN.md.*")), [])


# -----------------------------------------------------------------------------
# vec-index drift-detection schema migration (V4 #37 task 2)
# -----------------------------------------------------------------------------

# Load vec_index directly via importlib so we can test the schema-migration
# logic without needing sqlite-vec installed in the test env (the migration
# operates on the regular `entry_meta` sqlite table — vec0 virtual table
# not required for these test paths).
import importlib.util
import sqlite3
_VEC_INDEX_PATH = _HERE.parent / "harness" / "skills" / "memory" / "scripts" / "vec_index.py"
_vec_spec = importlib.util.spec_from_file_location("vec_index", _VEC_INDEX_PATH)
vec_index = importlib.util.module_from_spec(_vec_spec)
# Register in sys.modules so lazy-importing modules (e.g. recall.py) resolve
# to the SAME module instance the tests patch via mock.patch.object().
sys.modules["vec_index"] = vec_index
_vec_spec.loader.exec_module(vec_index)


def _make_pre_v37_entry_meta(db_path: Path) -> sqlite3.Connection:
    """Build a pre-#37-shaped sqlite db with `entry_meta` lacking `indexed_at`."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE entry_meta ("
        "  rowid INTEGER PRIMARY KEY,"
        "  path TEXT UNIQUE NOT NULL,"
        "  updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "INSERT INTO entry_meta(rowid, path, updated_at) VALUES (1, 'preferences/old-entry.md', '2026-04-01T12:00:00Z')"
    )
    conn.commit()
    return conn


def _make_post_v37_entry_meta(db_path: Path) -> sqlite3.Connection:
    """Build a post-#37-shaped sqlite db with `indexed_at` already present."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE entry_meta ("
        "  rowid INTEGER PRIMARY KEY,"
        "  path TEXT UNIQUE NOT NULL,"
        "  updated_at TEXT NOT NULL,"
        "  indexed_at INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    conn.execute(
        "INSERT INTO entry_meta(rowid, path, updated_at, indexed_at) VALUES (1, 'preferences/new-entry.md', '2026-05-27T18:00:00Z', 1748376000)"
    )
    conn.commit()
    return conn


class TestVecIndexSchemaMigration(unittest.TestCase):
    """V4 #37 task 2: pre-#37 → v37 schema migration via ALTER TABLE ADD COLUMN."""

    def test_has_column_detects_present_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_post_v37_entry_meta(db)
            try:
                self.assertTrue(vec_index._has_column(conn, "entry_meta", "indexed_at"))
                self.assertTrue(vec_index._has_column(conn, "entry_meta", "path"))
                self.assertFalse(vec_index._has_column(conn, "entry_meta", "nonexistent_column"))
            finally:
                conn.close()

    def test_has_column_detects_absent_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_pre_v37_entry_meta(db)
            try:
                self.assertFalse(vec_index._has_column(conn, "entry_meta", "indexed_at"))
            finally:
                conn.close()

    def test_migrate_pre_v37_adds_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_pre_v37_entry_meta(db)
            try:
                # Pre-migration: column absent.
                self.assertFalse(vec_index._has_column(conn, "entry_meta", "indexed_at"))
                # Run migration.
                with mock.patch("sys.stderr"):
                    migrated = vec_index._migrate_pre_v37(conn)
                conn.commit()
                self.assertTrue(migrated, "migration should have run")
                # Post-migration: column present.
                self.assertTrue(vec_index._has_column(conn, "entry_meta", "indexed_at"))
            finally:
                conn.close()

    def test_migrate_pre_v37_preserves_existing_rows(self) -> None:
        """ALTER TABLE preserves rows; existing entries get indexed_at=0 (default)."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_pre_v37_entry_meta(db)
            try:
                with mock.patch("sys.stderr"):
                    vec_index._migrate_pre_v37(conn)
                conn.commit()
                cursor = conn.execute(
                    "SELECT path, updated_at, indexed_at FROM entry_meta WHERE rowid = 1"
                )
                row = cursor.fetchone()
                self.assertEqual(row[0], "preferences/old-entry.md")
                self.assertEqual(row[1], "2026-04-01T12:00:00Z")
                self.assertEqual(row[2], 0, "default value should be 0 (pre-#37 rows appear drifted)")
            finally:
                conn.close()

    def test_migrate_pre_v37_idempotent(self) -> None:
        """Re-running migration on already-migrated table is a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_post_v37_entry_meta(db)
            try:
                with mock.patch("sys.stderr"):
                    migrated = vec_index._migrate_pre_v37(conn)
                conn.commit()
                self.assertFalse(migrated, "should be no-op on already-migrated schema")
                # Row data unchanged.
                cursor = conn.execute("SELECT indexed_at FROM entry_meta WHERE rowid = 1")
                self.assertEqual(cursor.fetchone()[0], 1748376000)
            finally:
                conn.close()

    def test_migrate_pre_v37_emits_one_line_stderr_notice(self) -> None:
        """First migration emits a clear one-line stderr notice; re-runs do not."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_pre_v37_entry_meta(db)
            try:
                with io.StringIO() as buf:
                    with mock.patch("sys.stderr", buf):
                        vec_index._migrate_pre_v37(conn)
                    stderr = buf.getvalue()
                self.assertIn("migrated pre-v4.2 entry_meta schema to v37", stderr)
                self.assertIn("drift-detection enabled", stderr)
                # Re-run: no additional notice (already migrated).
                with io.StringIO() as buf:
                    with mock.patch("sys.stderr", buf):
                        vec_index._migrate_pre_v37(conn)
                    self.assertEqual(buf.getvalue(), "")
            finally:
                conn.close()


# -----------------------------------------------------------------------------
# Drift detection primitives (V4 #37 task 3)
# -----------------------------------------------------------------------------

def _seed_v37_index(db_path: Path, entries: dict[str, int]) -> None:
    """Build a v37-shaped sqlite db with seeded entry_meta rows.

    entries: {entry_relative_path: indexed_at_epoch}
    No vec0 virtual table — just the metadata side, which is what drift-
    detection reads.
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE entry_meta ("
        "  rowid INTEGER PRIMARY KEY,"
        "  path TEXT UNIQUE NOT NULL,"
        "  updated_at TEXT NOT NULL,"
        "  indexed_at INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    for i, (path, indexed_at) in enumerate(entries.items(), start=1):
        conn.execute(
            "INSERT INTO entry_meta(rowid, path, updated_at, indexed_at) VALUES (?, ?, '2026-05-27T18:00:00Z', ?)",
            (i, path, indexed_at),
        )
    conn.commit()
    conn.close()


class _MockConn:
    """Stand-in for sqlite_vec-loaded connection — exposes just the parts
    the drift-detection code touches (execute returns a real cursor).
    Used to bypass _open_index's sqlite-vec check + extension load."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()


class TestIsEntryDrifted(unittest.TestCase):
    """V4 #37 task 3: per-entry drift detection."""

    def _patch_open_index(self, db_path: Path):
        """Return a mock.patch context that makes _open_index return our test db.

        Uses side_effect (lazy) instead of return_value (eager) so the sqlite
        connection is only created when production code actually invokes
        _open_index(). Without this, early-return code paths (e.g. source-file
        missing) would never consume the pre-built connection, leaking a file
        handle that breaks tempdir cleanup on Windows (WinError 32).
        """
        return mock.patch.object(
            vec_index,
            "_open_index",
            side_effect=lambda *a, **kw: _MockConn(db_path),
        )

    def test_returns_true_when_entry_not_indexed(self) -> None:
        """Entry exists on disk but has no row in entry_meta → drifted (first-embed)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal" / "_always-load").mkdir(parents=True)
            entry = vault / "personal" / "_always-load" / "new-rule.md"
            entry.write_text("freshly authored", encoding="utf-8")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})  # empty index
            with self._patch_open_index(db):
                self.assertTrue(
                    vec_index.is_entry_drifted(vault, "personal/_always-load/new-rule.md")
                )

    def test_returns_false_when_indexed_and_unchanged(self) -> None:
        """Entry indexed AT or AFTER current mtime → not drifted."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal" / "_always-load").mkdir(parents=True)
            entry = vault / "personal" / "_always-load" / "stable.md"
            entry.write_text("indexed earlier", encoding="utf-8")
            future = int(entry.stat().st_mtime) + 60  # indexed 60s after mtime
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {"personal/_always-load/stable.md": future})
            with self._patch_open_index(db):
                self.assertFalse(
                    vec_index.is_entry_drifted(vault, "personal/_always-load/stable.md")
                )

    def test_returns_true_when_mtime_exceeds_indexed_at(self) -> None:
        """Entry's source mtime > indexed_at + tolerance → drifted."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal" / "_always-load").mkdir(parents=True)
            entry = vault / "personal" / "_always-load" / "stale-row.md"
            entry.write_text("freshly touched", encoding="utf-8")
            past = int(entry.stat().st_mtime) - 1000  # indexed 1000s before mtime
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {"personal/_always-load/stale-row.md": past})
            with self._patch_open_index(db):
                self.assertTrue(
                    vec_index.is_entry_drifted(vault, "personal/_always-load/stale-row.md")
                )

    def test_returns_true_when_source_file_missing(self) -> None:
        """Entry's source file is gone → drifted (caller handles delete)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {"deleted-entry.md": 12345})
            with self._patch_open_index(db):
                self.assertTrue(vec_index.is_entry_drifted(vault, "deleted-entry.md"))

    def test_returns_false_when_sqlite_vec_unavailable(self) -> None:
        """Graceful-skip: when index can't open, no signal → not drifted."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal" / "_always-load").mkdir(parents=True)
            entry = vault / "personal" / "_always-load" / "any.md"
            entry.write_text("x", encoding="utf-8")
            with mock.patch.object(vec_index, "_open_index", return_value=None):
                self.assertFalse(
                    vec_index.is_entry_drifted(vault, "personal/_always-load/any.md")
                )

    def test_tolerance_window_avoids_false_positive(self) -> None:
        """Sub-1-second mtime/indexed-at differences should NOT report drift."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal" / "_always-load").mkdir(parents=True)
            entry = vault / "personal" / "_always-load" / "same-second.md"
            entry.write_text("x", encoding="utf-8")
            # indexed_at = mtime - 0.5 (within tolerance window)
            mtime = entry.stat().st_mtime
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {"personal/_always-load/same-second.md": int(mtime)})
            with self._patch_open_index(db):
                # mtime == int(mtime) + (fractional); within 1s tolerance.
                self.assertFalse(
                    vec_index.is_entry_drifted(vault, "personal/_always-load/same-second.md")
                )


class TestFindDriftedEntries(unittest.TestCase):
    """V4 #37 task 3: vault-walk drift inventory."""

    def _patch_open_index(self, db_path: Path):
        return mock.patch.object(
            vec_index,
            "_open_index",
            return_value=_MockConn(db_path),
        )

    def test_returns_empty_for_nonexistent_vault(self) -> None:
        result = vec_index.find_drifted_entries(Path("/nonexistent/path"))
        self.assertEqual(result, {"drifted": [], "up_to_date": [], "not_indexed": []})

    def test_classifies_mixed_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal" / "_always-load").mkdir(parents=True)
            (vault / "projects" / "fixture").mkdir(parents=True)
            # 3 entries: 1 indexed-fresh, 1 indexed-stale, 1 not-indexed
            fresh = vault / "personal" / "_always-load" / "fresh.md"
            stale = vault / "personal" / "_always-load" / "stale.md"
            new_entry = vault / "projects" / "fixture" / "new.md"
            for f in (fresh, stale, new_entry):
                f.write_text("x", encoding="utf-8")
            fresh_indexed = int(fresh.stat().st_mtime) + 60
            stale_indexed = int(stale.stat().st_mtime) - 1000
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {
                "personal/_always-load/fresh.md": fresh_indexed,
                "personal/_always-load/stale.md": stale_indexed,
            })
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["up_to_date"], ["personal/_always-load/fresh.md"])
        self.assertEqual(result["drifted"], ["personal/_always-load/stale.md"])
        self.assertEqual(result["not_indexed"], ["projects/fixture/new.md"])

    def test_excludes_archive_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal" / "_archive").mkdir(parents=True)
            (vault / "personal" / "_archive" / "old.md").write_text("x")
            (vault / "personal" / "active.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["not_indexed"], ["personal/active.md"])

    def test_excludes_plan_archive_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "projects" / "agentm" / "_harness").mkdir(parents=True)
            (vault / "projects" / "agentm" / "_harness" / "PLAN.archive.20260420.md").write_text("x")
            (vault / "projects" / "agentm" / "_harness" / "PLAN.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["not_indexed"], ["projects/agentm/_harness/PLAN.md"])

    def test_excludes_meta_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "_meta").mkdir(parents=True)
            (vault / "_meta" / "seed-manifest.md").write_text("x")
            (vault / "personal").mkdir(parents=True)
            (vault / "personal" / "active.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["not_indexed"], ["personal/active.md"])

    def test_walks_idea_incubator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "_idea-incubator" / "foo").mkdir(parents=True)
            (vault / "_idea-incubator" / "foo" / "_index.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["not_indexed"], ["_idea-incubator/foo/_index.md"])

    def test_legacy_personal_projects_fallback(self) -> None:
        """When projects/ absent but personal-projects/ present, walk legacy layout."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-projects" / "fixture").mkdir(parents=True)
            (vault / "personal-projects" / "fixture" / "_index.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["not_indexed"], ["personal-projects/fixture/_index.md"])

    def test_returns_all_not_indexed_when_sqlite_vec_unavailable(self) -> None:
        """Graceful-skip: no index → all walkable entries appear not_indexed."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal").mkdir(parents=True)
            (vault / "personal" / "a.md").write_text("x")
            (vault / "personal" / "b.md").write_text("x")
            with mock.patch.object(vec_index, "_open_index", return_value=None):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(sorted(result["not_indexed"]), ["personal/a.md", "personal/b.md"])
        self.assertEqual(result["drifted"], [])
        self.assertEqual(result["up_to_date"], [])


# -----------------------------------------------------------------------------
# full_sync subcommand + embed-text extraction (V4 #37 task 4)
# -----------------------------------------------------------------------------

class TestExtractEmbedTextFromFile(unittest.TestCase):
    """V4 #37 task 4: extract `{slug} [tags]\\n\\n{first_para}` from a .md file."""

    def test_extracts_slug_and_tags_from_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "entry.md"
            f.write_text(
                "---\n"
                "slug: my-pref\n"
                "tags: [convention, status-report]\n"
                "kind: preference\n"
                "---\n"
                "\nUse bullet points for status reports.\n",
                encoding="utf-8",
            )
            text = vec_index._extract_embed_text_from_file(f)
        self.assertIn("my-pref", text)
        self.assertIn("convention, status-report", text)
        self.assertIn("Use bullet points", text)

    def test_falls_back_to_file_stem_when_no_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "fallback-slug.md"
            f.write_text("plain markdown no frontmatter", encoding="utf-8")
            text = vec_index._extract_embed_text_from_file(f)
        self.assertIn("fallback-slug", text)
        self.assertIn("plain markdown", text)

    def test_truncates_body_at_500_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "long.md"
            long_body = "A" * 1000
            f.write_text(f"---\nslug: long\n---\n{long_body}", encoding="utf-8")
            text = vec_index._extract_embed_text_from_file(f)
        # Body portion should be ≤500 chars; total text includes slug + tags prefix.
        body_portion = text.split("\n\n", 1)[1] if "\n\n" in text else text
        self.assertLessEqual(len(body_portion), 500)

    def test_returns_empty_when_file_missing(self) -> None:
        result = vec_index._extract_embed_text_from_file(Path("/nonexistent/path.md"))
        self.assertEqual(result, "")


class TestFullSync(unittest.TestCase):
    """V4 #37 task 4: full-sync subcommand (default report + --rebuild enqueue)."""

    def _patch_open_index(self, db_path: Path):
        return mock.patch.object(
            vec_index,
            "_open_index",
            return_value=_MockConn(db_path),
        )

    def test_default_returns_summary_without_enqueueing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal").mkdir(parents=True)
            (vault / "personal" / "a.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.full_sync(vault, rebuild=False)
            self.assertEqual(result["not_indexed_count"], 1)
            self.assertEqual(result["enqueued"], 0)
            # Queue file should NOT exist (no rebuild)
            self.assertFalse((vault / "_meta" / "embedding-queue.jsonl").exists())

    def test_rebuild_enqueues_drifted_and_not_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal" / "_always-load").mkdir(parents=True)
            stale = vault / "personal" / "_always-load" / "stale.md"
            stale.write_text("---\nslug: stale\n---\nstale body", encoding="utf-8")
            new_entry = vault / "personal" / "_always-load" / "new.md"
            new_entry.write_text("---\nslug: new\n---\nnew body", encoding="utf-8")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {
                "personal/_always-load/stale.md": int(stale.stat().st_mtime) - 1000,
            })
            with self._patch_open_index(db):
                result = vec_index.full_sync(vault, rebuild=True)
            self.assertEqual(result["drifted_count"], 1)
            self.assertEqual(result["not_indexed_count"], 1)
            self.assertEqual(result["enqueued"], 2)
            # Queue file should now exist with 2 records
            queue_path = vault / "_meta" / "embedding-queue.jsonl"
            self.assertTrue(queue_path.exists())
            lines = queue_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            # Each record is well-formed JSON with op=upsert + extracted text
            for ln in lines:
                rec = json.loads(ln)
                self.assertEqual(rec["op"], "upsert")
                self.assertIn(rec["path"], (
                    "personal/_always-load/stale.md",
                    "personal/_always-load/new.md",
                ))
                self.assertIn(rec["path"].split("/")[-1].rsplit(".", 1)[0], rec["text"])

    def test_rebuild_idempotent_on_clean_vault(self) -> None:
        """All entries up-to-date → no enqueueing."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal").mkdir(parents=True)
            fresh = vault / "personal" / "fresh.md"
            fresh.write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {
                "personal/fresh.md": int(fresh.stat().st_mtime) + 60,
            })
            with self._patch_open_index(db):
                result = vec_index.full_sync(vault, rebuild=True)
            self.assertEqual(result["drifted_count"], 0)
            self.assertEqual(result["not_indexed_count"], 0)
            self.assertEqual(result["up_to_date_count"], 1)
            self.assertEqual(result["enqueued"], 0)


# -----------------------------------------------------------------------------
# recall.py drift-check integration (V4 #37 task 5)
# -----------------------------------------------------------------------------

_RECALL_PATH = _HERE.parent / "harness" / "skills" / "memory" / "scripts" / "recall.py"
_recall_spec = importlib.util.spec_from_file_location("recall", _RECALL_PATH)
recall = importlib.util.module_from_spec(_recall_spec)
_recall_spec.loader.exec_module(recall)


class TestDriftCheckVecHits(unittest.TestCase):
    """V4 #37 task 5: per-hit drift check in the recall path."""

    def _patch_open_index(self, db_path: Path):
        return mock.patch.object(
            vec_index,
            "_open_index",
            return_value=_MockConn(db_path),
        )

    def test_empty_vec_results_returns_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = recall._drift_check_vec_hits(Path(tmp), {})
            self.assertEqual(result, {})

    def test_un_drifted_hits_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal").mkdir(parents=True)
            stable = vault / "personal" / "stable.md"
            stable.write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            # indexed AFTER mtime — not drifted
            _seed_v37_index(db, {"personal/stable.md": int(stable.stat().st_mtime) + 60})
            vec_results = {"personal/stable.md": 0.85}
            with self._patch_open_index(db):
                with io.StringIO() as buf:
                    fresh = recall._drift_check_vec_hits(vault, vec_results, stderr=buf)
                    self.assertEqual(fresh, vec_results)
                    self.assertNotIn("flagged for re-embed", buf.getvalue())

    def test_drifted_hits_dropped_and_enqueued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal").mkdir(parents=True)
            stale = vault / "personal" / "stale.md"
            stale.write_text("---\nslug: stale\n---\nstale content", encoding="utf-8")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            # indexed BEFORE mtime — drifted
            _seed_v37_index(db, {"personal/stale.md": int(stale.stat().st_mtime) - 1000})
            vec_results = {"personal/stale.md": 0.85}
            with self._patch_open_index(db):
                with io.StringIO() as buf:
                    fresh = recall._drift_check_vec_hits(vault, vec_results, stderr=buf)
                    stderr_text = buf.getvalue()
            # Drifted entry dropped from results.
            self.assertNotIn("personal/stale.md", fresh)
            self.assertEqual(fresh, {})
            # Transparency line emitted.
            self.assertIn("1 entries flagged for re-embed", stderr_text)
            # Enqueued to queue file.
            queue = vault / "_meta" / "embedding-queue.jsonl"
            self.assertTrue(queue.exists())
            line = queue.read_text(encoding="utf-8").strip().splitlines()[0]
            rec = json.loads(line)
            self.assertEqual(rec["op"], "upsert")
            self.assertEqual(rec["path"], "personal/stale.md")
            self.assertIn("stale", rec["text"])

    def test_mixed_drifted_and_clean_hits(self) -> None:
        """Drifted entries dropped; clean entries retained."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal").mkdir(parents=True)
            stale = vault / "personal" / "stale.md"
            stale.write_text("---\nslug: stale\n---\nx", encoding="utf-8")
            clean = vault / "personal" / "clean.md"
            clean.write_text("y")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {
                "personal/stale.md": int(stale.stat().st_mtime) - 1000,
                "personal/clean.md": int(clean.stat().st_mtime) + 60,
            })
            vec_results = {
                "personal/stale.md": 0.85,
                "personal/clean.md": 0.72,
            }
            with self._patch_open_index(db):
                with io.StringIO() as buf:
                    fresh = recall._drift_check_vec_hits(vault, vec_results, stderr=buf)
            self.assertNotIn("personal/stale.md", fresh)
            self.assertEqual(fresh, {"personal/clean.md": 0.72})

    def test_vec_index_import_failure_returns_unchanged(self) -> None:
        """Defensive: if vec_index can't be imported, return input dict unchanged."""
        vec_results = {"any.md": 0.5}
        # Patch the lazy import by inserting a sentinel into sys.modules.
        with mock.patch.dict("sys.modules", {"vec_index": None}):
            # Importing None raises ImportError; the helper catches + returns unchanged.
            result = recall._drift_check_vec_hits(Path("/tmp"), vec_results)
        self.assertEqual(result, vec_results)


# -----------------------------------------------------------------------------
# V4 #30 plan #22 task 2 — repo_registry primitive
# -----------------------------------------------------------------------------

# importlib-load repo_registry from scripts/ dir without touching test PYTHONPATH
import importlib.util as _ilu

_REPO_REGISTRY_PATH = _HERE / "repo_registry.py"
_spec_rr = _ilu.spec_from_file_location("repo_registry", _REPO_REGISTRY_PATH)
assert _spec_rr is not None and _spec_rr.loader is not None
repo_registry = _ilu.module_from_spec(_spec_rr)
sys.modules["repo_registry"] = repo_registry
_spec_rr.loader.exec_module(repo_registry)


class TestRepoRegistry(unittest.TestCase):
    """V4 #30 task 2 / V5-6: seam-backed registry primitives (now takes StorageBackend)."""

    def _make_backend(self, root: Path):
        from storage_device_local import DeviceLocalBackend
        return DeviceLocalBackend(root=root)

    def test_read_empty_returns_default_schema(self) -> None:
        """First-write semantics: missing registry file returns {version:1, repos:[]}."""
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._make_backend(Path(tmp))
            data = repo_registry.read_registry(backend)
            self.assertEqual(data, {"version": 1, "repos": []})
            # Read does NOT create the file (write_registry is responsible).
            self.assertFalse((Path(tmp) / "_meta" / "repos.json").exists())

    def test_register_creates_file_and_entry(self) -> None:
        """First register_repo populates _meta/repos.json in the backend root."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = self._make_backend(root)
            repo_registry.register_repo(
                backend, "agentm", "/tmp/fixture-agentm",
                wiki_path="/tmp/fixture-agentm/wiki",
            )
            path = root / "_meta" / "repos.json"
            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            self.assertEqual(len(data["repos"]), 1)
            entry = data["repos"][0]
            self.assertEqual(entry["slug"], "agentm")
            self.assertEqual(entry["root_path"], "/tmp/fixture-agentm")
            self.assertEqual(entry["wiki_path"], "/tmp/fixture-agentm/wiki")
            # DC-8: the registry is a pure index — no run-mode config.
            self.assertNotIn("harness_state_mode", entry)

    def test_register_upserts_existing_slug(self) -> None:
        """Re-registering the same slug updates the entry in-place (not appended)."""
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._make_backend(Path(tmp))
            repo_registry.register_repo(backend, "agentm", "/old/path")
            repo_registry.register_repo(backend, "agentm", "/new/path", wiki_path="/wiki")
            repos = repo_registry.list_repos(backend)
            self.assertEqual(len(repos), 1)
            self.assertEqual(repos[0]["root_path"], "/new/path")
            self.assertEqual(repos[0]["wiki_path"], "/wiki")

    def test_unregister_removes_existing(self) -> None:
        """unregister_repo removes the matching slug; idempotent on absent."""
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._make_backend(Path(tmp))
            repo_registry.register_repo(backend, "agentm", "/a")
            repo_registry.register_repo(backend, "sherwood", "/s")
            removed = repo_registry.unregister_repo(backend, "agentm")
            self.assertTrue(removed)
            repos = repo_registry.list_repos(backend)
            self.assertEqual(len(repos), 1)
            self.assertEqual(repos[0]["slug"], "sherwood")
            # Idempotent: unregister of already-absent slug returns False, no-op.
            removed_again = repo_registry.unregister_repo(backend, "agentm")
            self.assertFalse(removed_again)

    def test_list_preserves_insertion_order(self) -> None:
        """list_repos returns entries in the order they were first registered."""
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._make_backend(Path(tmp))
            for slug in ("agentm", "sherwood", "dev-setup"):
                repo_registry.register_repo(backend, slug, f"/path/{slug}")
            repos = repo_registry.list_repos(backend)
            self.assertEqual(
                [r["slug"] for r in repos],
                ["agentm", "sherwood", "dev-setup"],
            )

    def test_concurrent_modification_raises(self) -> None:
        """write_registry with an expected_hash mismatch raises ConcurrentModificationError."""
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._make_backend(Path(tmp))
            repo_registry.register_repo(backend, "agentm", "/a")
            loc = repo_registry.registry_locator(backend)
            content = backend.read(loc)
            stale_hash = hm.content_hash(content.encode("utf-8"))
            # Simulate another writer mutating the file via the backend.
            backend.write(loc, content + " ")
            data = repo_registry.read_registry(backend)
            with self.assertRaises(hm.ConcurrentModificationError):
                repo_registry.write_registry(backend, data, expected_hash=stale_hash)

    def test_register_repo_recovers_from_cas_race(self) -> None:
        """A concurrent (cross-machine) write during register_repo's CAS window
        is retried, not silently dropped: both the racing peer's entry and ours
        survive."""
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._make_backend(Path(tmp))
            repo_registry.register_repo(backend, "seed", "/seed")

            real_write = repo_registry.write_registry
            state = {"raced": False}

            def racing_write(b, data, *, expected_hash=None):
                if not state["raced"]:
                    state["raced"] = True
                    real_write(b, {
                        "version": 1,
                        "repos": [
                            {"slug": "seed", "root_path": "/seed"},
                            {"slug": "peer", "root_path": "/peer"},
                        ],
                    })
                    raise hm.ConcurrentModificationError("simulated cross-machine race")
                return real_write(b, data, expected_hash=expected_hash)

            repo_registry.write_registry = racing_write
            try:
                repo_registry.register_repo(backend, "mine", "/mine")
            finally:
                repo_registry.write_registry = real_write

            self.assertTrue(state["raced"], "the race path must have fired")
            slugs = {r["slug"] for r in repo_registry.list_repos(backend)}
            self.assertEqual(slugs, {"seed", "peer", "mine"})

    def test_register_repo_raises_after_exhausting_retries(self) -> None:
        """If the CAS never wins, register_repo raises after _MAX_REGISTRY_RETRIES."""
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._make_backend(Path(tmp))
            repo_registry.register_repo(backend, "seed", "/seed")

            real_write = repo_registry.write_registry
            attempts = {"n": 0}

            def always_racing(b, data, *, expected_hash=None):
                attempts["n"] += 1
                raise hm.ConcurrentModificationError("perpetual race")

            repo_registry.write_registry = always_racing
            try:
                with self.assertRaises(hm.ConcurrentModificationError):
                    repo_registry.register_repo(backend, "mine", "/mine")
            finally:
                repo_registry.write_registry = real_write
            self.assertEqual(attempts["n"], repo_registry._MAX_REGISTRY_RETRIES)

    def test_atomic_write_no_tmp_remnant(self) -> None:
        """After successful write, no <path>.tmp lingers."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = self._make_backend(root)
            repo_registry.register_repo(backend, "agentm", "/a")
            meta_dir = root / "_meta"
            tmp_files = list(meta_dir.glob("*.tmp"))
            self.assertEqual(tmp_files, [])

    def test_parallel_run_vault_backend_maps_to_same_path(self) -> None:
        """LC-7: VaultBackend registry_locator maps to <vault>/_meta/repos.json (behavior-preserving)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            from storage_vault import VaultBackend
            backend = VaultBackend(root=vault)
            repo_registry.register_repo(backend, "test-repo", "/some/path")
            loc = repo_registry.registry_locator(backend)
            self.assertEqual(loc.key, "_meta/repos.json")
            expected_path = vault / "_meta" / "repos.json"
            self.assertTrue(expected_path.is_file())
            data = json.loads(expected_path.read_text(encoding="utf-8"))
            slugs = {r["slug"] for r in data["repos"]}
            self.assertIn("test-repo", slugs)


class TestRepoRegistryCLI(unittest.TestCase):
    """V4 #30 task 2: CLI subcommands (list / register / unregister)."""

    def _run(self, *argv: str, env: Optional[dict] = None) -> subprocess.CompletedProcess:
        e = os.environ.copy()
        if env is not None:
            e.update(env)
            # Allow caller to delete by passing empty string sentinel.
            for k, v in list(env.items()):
                if v == "":
                    e.pop(k, None)
        return subprocess.run(
            [sys.executable, str(_REPO_REGISTRY_PATH), *argv],
            capture_output=True, text=True, env=e,
        )

    def test_list_skipped_when_backend_unavailable(self) -> None:
        """CLI exits 1 with skip JSON when select_backend() raises (backend plugin unavailable)."""
        buf = io.StringIO()
        with mock.patch("repo_registry._backend_or_none", return_value=None):
            with mock.patch("sys.stdout", buf):
                rc = repo_registry.main(["list"])
        self.assertEqual(rc, 1)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["skipped"])
        self.assertIn("select_backend", out["reason"])

    def test_register_then_list_via_cli(self) -> None:
        """Register two repos via CLI; list returns both with correct fields.

        Uses device-local backend via AGENTM_DEVICE_LOCAL_ROOT so the test
        works in CI without the obsidian-vault plugin (V5-6 de-vaulting).
        """
        with tempfile.TemporaryDirectory() as tmp:
            dl_root = Path(tmp) / "device_local"
            dl_root.mkdir()
            # Use device-local backend: clear vault selection, redirect root,
            # sandbox AGENTM_INSTALL_PREFIX so no storage.backend config leaks.
            env = {
                "MEMORY_VAULT_PATH": "",        # delete — no vault selection
                "AGENTM_DEVICE_LOCAL_ROOT": str(dl_root),
                "AGENTM_INSTALL_PREFIX": str(Path(tmp) / "install_prefix"),
            }

            reg1 = self._run(
                "register", "agentm",
                "--root", "/tmp/fixture-agentm",
                "--wiki", "/tmp/fixture-agentm/wiki",
                env=env,
            )
            self.assertEqual(reg1.returncode, 0, reg1.stderr)
            self.assertEqual(reg1.stdout.strip(), "agentm")

            reg2 = self._run(
                "register", "sherwood",
                "--root", "/tmp/fixture-sherwood",
                env=env,
            )
            self.assertEqual(reg2.returncode, 0, reg2.stderr)

            ls = self._run("list", env=env)
            self.assertEqual(ls.returncode, 0, ls.stderr)
            data = json.loads(ls.stdout)
            self.assertEqual(len(data["repos"]), 2)
            slugs = [r["slug"] for r in data["repos"]]
            self.assertEqual(slugs, ["agentm", "sherwood"])
            # First repo carries all fields; second carries only required ones.
            self.assertEqual(data["repos"][0]["wiki_path"], "/tmp/fixture-agentm/wiki")
            self.assertNotIn("harness_state_mode", data["repos"][0])
            self.assertNotIn("wiki_path", data["repos"][1])

    def test_unregister_via_cli(self) -> None:
        """Unregister an existing repo via CLI; re-running is a no-op.

        Uses device-local backend via AGENTM_DEVICE_LOCAL_ROOT so the test
        works in CI without the obsidian-vault plugin (V5-6 de-vaulting).
        """
        with tempfile.TemporaryDirectory() as tmp:
            dl_root = Path(tmp) / "device_local"
            dl_root.mkdir()
            env = {
                "MEMORY_VAULT_PATH": "",        # delete — no vault selection
                "AGENTM_DEVICE_LOCAL_ROOT": str(dl_root),
                "AGENTM_INSTALL_PREFIX": str(Path(tmp) / "install_prefix"),
            }

            self._run("register", "agentm", "--root", "/a", env=env)
            res = self._run("unregister", "agentm", env=env)
            self.assertEqual(res.returncode, 0)
            self.assertEqual(res.stdout.strip(), "removed")

            # Idempotent: second unregister is a no-op.
            res2 = self._run("unregister", "agentm", env=env)
            self.assertEqual(res2.returncode, 0)
            self.assertEqual(res2.stdout.strip(), "noop")

            ls = self._run("list", env=env)
            data = json.loads(ls.stdout)
            self.assertEqual(data["repos"], [])


# -----------------------------------------------------------------------------
# V4 #30 plan #22 task 3 — install_state probe + persist
# -----------------------------------------------------------------------------

_INSTALL_STATE_PATH = _HERE.parent / "lib" / "install" / "python" / "install_state.py"
_spec_is = _ilu.spec_from_file_location("install_state", _INSTALL_STATE_PATH)
assert _spec_is is not None and _spec_is.loader is not None
install_state = _ilu.module_from_spec(_spec_is)
sys.modules["install_state"] = install_state
_spec_is.loader.exec_module(install_state)


def _fake_agentm_clone(root: Path) -> Path:
    """Build a fixture directory shaped like an agentm source clone."""
    p = root / "agentm"
    p.mkdir(parents=True)
    (p / ".git").mkdir()
    (p / "harness").mkdir()
    return p


class TestDetectSourceClones(unittest.TestCase):
    """V4 #30 task 3: detect_source_clones probe (agentm-only since the crickets decouple)."""

    def test_neither_clone_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            clones = install_state.detect_source_clones(agentm_path=base / "agentm")
            self.assertEqual(clones, {})

    def test_agentm_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agentm = _fake_agentm_clone(base)
            clones = install_state.detect_source_clones(agentm_path=agentm)
            self.assertEqual(list(clones.keys()), ["agentm"])
            self.assertEqual(clones["agentm"], str(agentm))

    def test_clone_without_required_subdirs_not_detected(self) -> None:
        """Dir with .git but missing harness/ is NOT an agentm clone."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake = base / "agentm"
            fake.mkdir()
            (fake / ".git").mkdir()
            # Missing harness/ — should not be detected
            clones = install_state.detect_source_clones(agentm_path=fake)
            self.assertEqual(clones, {})


class TestDetectInstallMode(unittest.TestCase):
    """V4 #30 task 3: install-mode decision (source vs release)."""

    def test_neither_clone_yields_release_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mode, clones = install_state.detect_install_mode(agentm_path=base / "agentm")
            self.assertEqual(mode, "release")
            self.assertEqual(clones, {})

    def test_agentm_clone_yields_source_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _fake_agentm_clone(base)
            mode, clones = install_state.detect_install_mode(agentm_path=base / "agentm")
            self.assertEqual(mode, "source")
            self.assertEqual(list(clones.keys()), ["agentm"])


class TestPersistInstallState(unittest.TestCase):
    """V4 #30 task 3: persist + read primitives."""

    def test_persist_writes_expected_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            path = install_state.persist_install_state(
                prefix, "release", {}, "v4.3.0",
                installed_at="2026-05-27T18:00:00Z",
            )
            self.assertTrue(path.exists())
            self.assertTrue(str(path).endswith(".agentm-config.json"))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 2)
            self.assertEqual(data["mode"], "release")
            self.assertEqual(data["source_clones"], {})
            self.assertEqual(data["installed_at"], "2026-05-27T18:00:00Z")
            self.assertEqual(data["harness_version"], "v4.3.0")
            # v4.5.1 schema v2 always has vault_path field present (null when unset).
            self.assertIn("vault_path", data)
            self.assertIsNone(data["vault_path"])
            # Legacy `version` field NOT written by schema v2.
            self.assertNotIn("version", data)

    def test_persist_creates_missing_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deep_prefix = Path(tmp) / "a" / "b" / "c"
            install_state.persist_install_state(deep_prefix, "source", {"agentm": "/x"}, "v4.3.0")
            self.assertTrue(deep_prefix.is_dir())

    def test_persist_rejects_invalid_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            with self.assertRaises(ValueError):
                install_state.persist_install_state(prefix, "invalid-mode", {}, "v4.3.0")

    def test_read_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(install_state.read_install_state(Path(tmp)))

    def test_read_returns_none_on_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            install_state.state_path(prefix).write_text("not-json", encoding="utf-8")
            self.assertIsNone(install_state.read_install_state(prefix))

    def test_persist_then_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "p"
            install_state.persist_install_state(
                prefix, "source", {"agentm": "/x"}, "v4.3.0",
            )
            data = install_state.read_install_state(prefix)
            self.assertIsNotNone(data)
            self.assertEqual(data["mode"], "source")
            self.assertEqual(data["source_clones"], {"agentm": "/x"})

    def test_re_probe_flips_mode_when_clone_appears(self) -> None:
        """Smoke: install in release mode, then clone appears, re-probe flips to source."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            prefix = base / "prefix"

            # First run: no clones → release mode
            mode1, clones1 = install_state.detect_install_mode(agentm_path=base / "agentm")
            install_state.persist_install_state(prefix, mode1, clones1, "v4.3.0")
            d1 = install_state.read_install_state(prefix)
            self.assertEqual(d1["mode"], "release")

            # Clone appears
            _fake_agentm_clone(base)

            # Re-probe + persist → source mode
            mode2, clones2 = install_state.detect_install_mode(agentm_path=base / "agentm")
            install_state.persist_install_state(prefix, mode2, clones2, "v4.3.0")
            d2 = install_state.read_install_state(prefix)
            self.assertEqual(d2["mode"], "source")
            self.assertIn("agentm", d2["source_clones"])


class TestInstallStateCLI(unittest.TestCase):
    """V4 #30 task 3: install_state CLI subcommands."""

    def _run(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_INSTALL_STATE_PATH), *argv],
            capture_output=True, text=True,
        )

    def test_detect_neither_clone_emits_release_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            res = self._run(
                "detect",
                "--agentm-path", str(base / "no-agentm"),
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(res.stdout)
            self.assertEqual(data["mode"], "release")
            self.assertEqual(data["source_clones"], {})

    def test_persist_then_read_via_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            prefix = base / "prefix"
            _fake_agentm_clone(base)

            persist = self._run(
                "persist", str(prefix),
                "--harness-version", "v4.3.0",
                "--agentm-path", str(base / "agentm"),
            )
            self.assertEqual(persist.returncode, 0, persist.stderr)
            self.assertTrue(persist.stdout.strip().endswith(".agentm-config.json"))

            read = self._run("read", str(prefix))
            self.assertEqual(read.returncode, 0, read.stderr)
            data = json.loads(read.stdout)
            self.assertEqual(data["mode"], "source")
            self.assertIn("agentm", data["source_clones"])

    def test_read_emits_empty_object_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = self._run("read", tmp)
            self.assertEqual(res.returncode, 0)
            self.assertEqual(json.loads(res.stdout), {})


# -----------------------------------------------------------------------------
# V4 #30 plan #22 task 4 — install_symlinks (source-mode symlink primitive)
# -----------------------------------------------------------------------------

_INSTALL_SYMLINKS_PATH = _HERE.parent / "lib" / "install" / "python" / "install_symlinks.py"
_spec_isy = _ilu.spec_from_file_location("install_symlinks", _INSTALL_SYMLINKS_PATH)
assert _spec_isy is not None and _spec_isy.loader is not None
install_symlinks = _ilu.module_from_spec(_spec_isy)
sys.modules["install_symlinks"] = install_symlinks
_spec_isy.loader.exec_module(install_symlinks)


def _fake_agentm_layout(root: Path) -> Path:
    """Build an agentm-shaped fixture with sample customizations."""
    ag = root / "agentm"
    ag.mkdir(parents=True)
    (ag / ".git").mkdir()
    # skill bundle (dir)
    (ag / "harness" / "skills" / "memory").mkdir(parents=True)
    (ag / "harness" / "skills" / "memory" / "SKILL.md").write_text("# memory", encoding="utf-8")
    # hook bundle (dir)
    (ag / "harness" / "hooks" / "harness-context-session-start").mkdir(parents=True)
    (ag / "harness" / "hooks" / "harness-context-session-start" / "hook.md").write_text("# hook", encoding="utf-8")
    # agent file
    (ag / "harness" / "agents").mkdir(parents=True)
    (ag / "harness" / "agents" / "explorer.md").write_text("# explorer", encoding="utf-8")
    # command file
    (ag / "adapters" / "claude-code" / "commands").mkdir(parents=True)
    (ag / "adapters" / "claude-code" / "commands" / "plan.md").write_text("# plan", encoding="utf-8")
    return ag


class TestSymlinkCustomizations(unittest.TestCase):
    """V4 #30 task 4: symlink_customizations primitive (agentm-only since the crickets decouple)."""

    def test_creates_symlinks_from_agentm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agentm = _fake_agentm_layout(base)
            prefix = base / "claude"
            result = install_symlinks.symlink_customizations(
                {"agentm": str(agentm)}, prefix,
            )
            # 1 skill + 1 hook + 1 agent + 1 command = 4 created
            self.assertEqual(len(result["created"]), 4)
            self.assertIn("skills/memory", result["created"])
            self.assertIn("hooks/harness-context-session-start", result["created"])
            self.assertIn("agents/explorer.md", result["created"])
            self.assertIn("commands/plan.md", result["created"])
            # Verify they're real symlinks pointing at the source clone
            self.assertTrue((prefix / "skills" / "memory").is_symlink())
            self.assertTrue((prefix / "agents" / "explorer.md").is_symlink())

    def test_idempotent_on_already_symlinked(self) -> None:
        """Re-running on already-symlinked target classifies as 'skipped'."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agentm = _fake_agentm_layout(base)
            prefix = base / "claude"
            install_symlinks.symlink_customizations({"agentm": str(agentm)}, prefix)
            # Second run
            result = install_symlinks.symlink_customizations({"agentm": str(agentm)}, prefix)
            self.assertEqual(len(result["created"]), 0)
            self.assertEqual(len(result["skipped"]), 4)

    def test_repoints_when_symlink_target_differs(self) -> None:
        """Existing symlink pointing elsewhere → repointed to expected source."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agentm = _fake_agentm_layout(base)
            prefix = base / "claude"
            # Pre-existing symlink to a different source
            (prefix / "agents").mkdir(parents=True)
            wrong_target = base / "wrong-target.md"
            wrong_target.write_text("wrong", encoding="utf-8")
            os.symlink(wrong_target, prefix / "agents" / "explorer.md")

            result = install_symlinks.symlink_customizations(
                {"agentm": str(agentm)}, prefix,
            )
            self.assertIn("agents/explorer.md", result["repointed"])
            # Verify the symlink now points at the agentm source. Use
            # os.path.samefile to handle Windows UNC-prefix normalization
            # (//?/C:/... vs C:/... refer to the same file but Path.resolve()
            # returns different forms).
            link = prefix / "agents" / "explorer.md"
            expected = agentm / "harness" / "agents" / "explorer.md"
            self.assertTrue(os.path.samefile(link, expected))

    def test_repoints_broken_symlink(self) -> None:
        """Broken symlink (target gone) is treated as needing repoint."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agentm = _fake_agentm_layout(base)
            prefix = base / "claude"
            (prefix / "agents").mkdir(parents=True)
            os.symlink(base / "ghost.md", prefix / "agents" / "explorer.md")

            result = install_symlinks.symlink_customizations(
                {"agentm": str(agentm)}, prefix,
            )
            self.assertIn("agents/explorer.md", result["repointed"])

    def test_real_file_conflict_skipped_without_force(self) -> None:
        """Real file (not a symlink) at target path → conflict + skip."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agentm = _fake_agentm_layout(base)
            prefix = base / "claude"
            (prefix / "agents").mkdir(parents=True)
            (prefix / "agents" / "explorer.md").write_text("real file", encoding="utf-8")

            result = install_symlinks.symlink_customizations(
                {"agentm": str(agentm)}, prefix,
            )
            self.assertIn("agents/explorer.md", result["conflicts"])
            # File still in place — never clobbered
            self.assertEqual(
                (prefix / "agents" / "explorer.md").read_text(),
                "real file",
            )

    def test_real_file_conflict_replaced_with_force(self) -> None:
        """With --force, real file is replaced by the symlink."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agentm = _fake_agentm_layout(base)
            prefix = base / "claude"
            (prefix / "agents").mkdir(parents=True)
            (prefix / "agents" / "explorer.md").write_text("real file", encoding="utf-8")

            result = install_symlinks.symlink_customizations(
                {"agentm": str(agentm)}, prefix, force=True,
            )
            self.assertIn("agents/explorer.md", result["repointed"])
            self.assertTrue((prefix / "agents" / "explorer.md").is_symlink())

    def test_no_source_clones_returns_empty(self) -> None:
        """Empty source_clones dict → no symlinks; clean result (all categories empty)."""
        with tempfile.TemporaryDirectory() as tmp:
            result = install_symlinks.symlink_customizations({}, Path(tmp) / "claude")
            # Semantic intent: every result category is empty when there are no source
            # clones — nothing to create, repoint, skip, conflict, OR reap. Tracks the
            # `reaped` field added in 8c5af42 (orphan-symlink reaping) while preserving
            # the "all-empty" check the test name encodes.
            self.assertEqual(
                result,
                {"created": [], "repointed": [], "skipped": [], "conflicts": [], "reaped": []},
            )

    def test_missing_clone_dir_is_skipped(self) -> None:
        """source_clones with non-existent path → quietly skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            result = install_symlinks.symlink_customizations(
                {"agentm": str(Path(tmp) / "no-such-clone")},
                Path(tmp) / "claude",
            )
            self.assertEqual(result["created"], [])


class TestInstallSymlinksCLI(unittest.TestCase):
    """V4 #30 task 4: CLI smoke."""

    def _run(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_INSTALL_SYMLINKS_PATH), *argv],
            capture_output=True, text=True,
        )

    def test_no_clones_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = self._run(tmp)
            self.assertEqual(res.returncode, 1)
            self.assertIn("no source clones", res.stderr)

    def test_install_agentm_via_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agentm = _fake_agentm_layout(base)
            prefix = base / "claude"
            res = self._run(str(prefix), "--agentm", str(agentm))
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(res.stdout)
            self.assertEqual(len(data["created"]), 4)


# -----------------------------------------------------------------------------
# V4 #30 plan #22 task 5 — install_copy (release-mode copy primitive)
# -----------------------------------------------------------------------------

_INSTALL_COPY_PATH = _HERE.parent / "lib" / "install" / "python" / "install_copy.py"
_spec_ic = _ilu.spec_from_file_location("install_copy", _INSTALL_COPY_PATH)
assert _spec_ic is not None and _spec_ic.loader is not None
install_copy = _ilu.module_from_spec(_spec_ic)
sys.modules["install_copy"] = install_copy
_spec_ic.loader.exec_module(install_copy)


def _seed_source_release(root: Path) -> Path:
    """Build a fake release-extract tree with a few customizations."""
    src = root / "release"
    (src / "skills" / "foo").mkdir(parents=True)
    (src / "skills" / "foo" / "SKILL.md").write_text("v1 foo skill", encoding="utf-8")
    (src / "agents").mkdir(parents=True)
    (src / "agents" / "bar.md").write_text("v1 bar agent", encoding="utf-8")
    (src / "commands").mkdir(parents=True)
    (src / "commands" / "baz.md").write_text("v1 baz command", encoding="utf-8")
    return src


class TestCopyCustomizations(unittest.TestCase):
    """V4 #30 task 5: copy_customizations primitive (SHA256-aware)."""

    def test_fresh_install_creates_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = _seed_source_release(base)
            prefix = base / "claude"
            result = install_copy.copy_customizations(src, prefix)
            self.assertEqual(len(result["created"]), 3)
            self.assertEqual(result["updated"], [])
            self.assertEqual(result["skipped"], [])
            self.assertEqual(result["conflicts"], [])
            self.assertEqual(len(result["installed_shas"]), 3)
            self.assertTrue((prefix / "skills" / "foo" / "SKILL.md").is_file())

    def test_update_with_no_source_changes_skips_all(self) -> None:
        """Second install with unchanged source = all skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = _seed_source_release(base)
            prefix = base / "claude"
            first = install_copy.copy_customizations(src, prefix)
            # Re-run with the prior state
            prior_state = {"installed_shas": first["installed_shas"]}
            second = install_copy.copy_customizations(src, prefix, install_state=prior_state)
            self.assertEqual(second["created"], [])
            self.assertEqual(second["updated"], [])
            self.assertEqual(len(second["skipped"]), 3)
            self.assertEqual(second["conflicts"], [])

    def test_update_with_source_changes_updates_target(self) -> None:
        """Source file changes, target unchanged since last install = update."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = _seed_source_release(base)
            prefix = base / "claude"
            first = install_copy.copy_customizations(src, prefix)
            prior_state = {"installed_shas": first["installed_shas"]}
            # Modify the source
            (src / "agents" / "bar.md").write_text("v2 bar agent", encoding="utf-8")
            second = install_copy.copy_customizations(src, prefix, install_state=prior_state)
            self.assertEqual(second["created"], [])
            self.assertIn(str(Path("agents/bar.md")), second["updated"])
            self.assertEqual(second["conflicts"], [])
            # Target file now has the new content
            self.assertEqual(
                (prefix / "agents" / "bar.md").read_text(encoding="utf-8"),
                "v2 bar agent",
            )

    def test_local_divergence_skipped_without_force(self) -> None:
        """Target edited locally + source also changed = conflict, skip."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = _seed_source_release(base)
            prefix = base / "claude"
            first = install_copy.copy_customizations(src, prefix)
            prior_state = {"installed_shas": first["installed_shas"]}
            # Operator edits the target locally
            (prefix / "agents" / "bar.md").write_text("local-edited bar", encoding="utf-8")
            # Source also changed (new release)
            (src / "agents" / "bar.md").write_text("v2 bar agent", encoding="utf-8")
            result = install_copy.copy_customizations(src, prefix, install_state=prior_state)
            self.assertIn(str(Path("agents/bar.md")), result["conflicts"])
            # Local edit is preserved
            self.assertEqual(
                (prefix / "agents" / "bar.md").read_text(encoding="utf-8"),
                "local-edited bar",
            )

    def test_local_divergence_replaced_with_force(self) -> None:
        """--force overwrites operator-local edits."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = _seed_source_release(base)
            prefix = base / "claude"
            first = install_copy.copy_customizations(src, prefix)
            prior_state = {"installed_shas": first["installed_shas"]}
            (prefix / "agents" / "bar.md").write_text("local-edited bar", encoding="utf-8")
            (src / "agents" / "bar.md").write_text("v2 bar agent", encoding="utf-8")
            result = install_copy.copy_customizations(
                src, prefix, install_state=prior_state, force=True,
            )
            self.assertIn(str(Path("agents/bar.md")), result["updated"])
            self.assertEqual(
                (prefix / "agents" / "bar.md").read_text(encoding="utf-8"),
                "v2 bar agent",
            )

    def test_no_prior_state_first_install_unconditional(self) -> None:
        """Without prior state, target is always created/overwritten on first install."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = _seed_source_release(base)
            prefix = base / "claude"
            # Operator has pre-existing file at install target
            (prefix / "agents").mkdir(parents=True)
            (prefix / "agents" / "bar.md").write_text("pre-existing", encoding="utf-8")
            result = install_copy.copy_customizations(src, prefix)
            # No prior state → target is treated as divergent (conflict)
            # because we can't tell if the operator-edited file was previously
            # installed by us or pre-existed.
            self.assertIn(str(Path("agents/bar.md")), result["conflicts"])


class TestInstallCopyCLI(unittest.TestCase):
    """V4 #30 task 5: install_copy CLI smoke."""

    def _run(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_INSTALL_COPY_PATH), *argv],
            capture_output=True, text=True,
        )

    def test_fresh_install_via_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = _seed_source_release(base)
            prefix = base / "claude"
            res = self._run(str(src), str(prefix))
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(res.stdout)
            self.assertEqual(len(data["created"]), 3)


class TestInstallStateInstallerSourceField(unittest.TestCase):
    """V4 #30 task 5: install_state.persist now supports installer_source + installed_shas."""

    def test_persist_with_installer_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            install_state.persist_install_state(
                prefix, "release", {}, "v4.3.0",
                installer_source="/path/to/install.sh",
                installed_shas={"agents/bar.md": "abc123"},
            )
            data = install_state.read_install_state(prefix)
            self.assertEqual(data["installer_source"], "/path/to/install.sh")
            self.assertEqual(data["installed_shas"], {"agents/bar.md": "abc123"})

    def test_persist_without_optional_fields(self) -> None:
        """Backward compat: omitted optional fields don't appear in JSON."""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            install_state.persist_install_state(prefix, "release", {}, "v4.3.0")
            data = install_state.read_install_state(prefix)
            self.assertNotIn("installer_source", data)
            self.assertNotIn("installed_shas", data)


@unittest.skipIf(platform.system() == "Windows",
                 "bash-only launcher; Windows uses the .ps1 twin (separate tests)")
class TestAgentmUpdateLauncher(unittest.TestCase):
    """V4 #30 task 5: agentm-update bash launcher behavior."""

    _LAUNCHER = Path(__file__).resolve().parent.parent / "templates" / "bin" / "agentm-update"

    def test_launcher_exists_and_executable(self) -> None:
        self.assertTrue(self._LAUNCHER.is_file(), f"launcher missing at {self._LAUNCHER}")
        self.assertTrue(os.access(self._LAUNCHER, os.X_OK), "launcher not executable")

    def test_launcher_fails_when_no_install_state(self) -> None:
        """No install state at install-prefix → exit 1 with actionable message."""
        with tempfile.TemporaryDirectory() as tmp:
            res = subprocess.run(
                ["bash", str(self._LAUNCHER)],
                capture_output=True, text=True,
                env={**os.environ, "AGENTM_INSTALL_PREFIX": tmp, "HOME": tmp},
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("no install state", res.stderr.lower())

    def test_launcher_fails_when_installer_source_missing_field(self) -> None:
        """Install state present but no installer_source field → exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            install_state.persist_install_state(prefix, "release", {}, "v4.3.0")
            res = subprocess.run(
                ["bash", str(self._LAUNCHER)],
                capture_output=True, text=True,
                env={**os.environ, "AGENTM_INSTALL_PREFIX": str(prefix), "HOME": tmp},
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("installer_source", res.stderr.lower())


class TestEngineConcurrencyProof(unittest.TestCase):
    """V5-3: executable proof that N concurrent workers writing to DISTINCT
    device-local files (the named-plan model) each land a complete, un-torn
    payload with no `.tmp` remnants.

    After V5-3 the vault backend is gone and the concurrency model is
    writer-per-file: each worker owns its own `PLAN-<slug>.md` / `progress-<slug>.md`
    pair in `<project_root>/.harness/`. Contention between writers is eliminated
    by file isolation, not by a per-vault mutex. This test proves that
    `write_state_file` (→ `_write_repo_local_state_file` → `atomic_write`)
    lands a complete, un-torn payload for each of N concurrent writers when each
    writes to its OWN distinct file.
    """

    def test_concurrent_writers_to_distinct_files_never_tear_v5_3(self) -> None:
        n_writers = 8
        iterations = 5
        payload_len = 120_000  # large enough that an unserialized overwrite tears

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir(parents=True, exist_ok=True)

            chars = "0123456789abcdefghij"[:n_writers]
            # Each writer owns its own distinct file — the named-plan model.
            filenames = {c: f"PLAN-worker-{c}.md" for c in chars}
            payloads = {c: (c * payload_len) for c in chars}
            resolutions = {c: {"project_root": repo} for c in chars}

            errors: list[BaseException] = []
            barrier = threading.Barrier(n_writers)

            def writer(ch: str) -> None:
                try:
                    barrier.wait()  # release all writers together: max race
                    for _ in range(iterations):
                        hm.write_state_file(resolutions[ch], filenames[ch], payloads[ch])
                except BaseException as exc:  # noqa: BLE001 - surface any
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(c,)) for c in chars]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

            self.assertEqual(
                [t for t in threads if t.is_alive()], [],
                "a writer thread hung",
            )
            self.assertEqual(errors, [], f"writers raised: {errors!r}")

            harness_dir = repo / ".harness"
            for c in chars:
                target = harness_dir / filenames[c]
                self.assertTrue(target.is_file(), f"file missing for worker {c!r}")
                data = target.read_text(encoding="utf-8")
                # Each file must be exactly one writer's full payload — never torn.
                distinct = set(data)
                self.assertEqual(
                    len(distinct), 1,
                    f"worker {c!r}: torn write ({len(distinct)} distinct byte values)",
                )
                self.assertEqual(len(data), payload_len, f"worker {c!r}: truncated/concatenated")
                self.assertEqual(data, payloads[c], f"worker {c!r}: wrong content")

            # No `.tmp` remnant left behind.
            leftovers = list(harness_dir.glob("*.tmp"))
            self.assertEqual(leftovers, [], f".tmp remnant(s) left behind: {leftovers}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
