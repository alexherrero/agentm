#!/usr/bin/env python3
"""Unit tests for scripts/backend_selection.py — the V5-1 part-5 selection resolver.

Run directly:

    python3 -m unittest scripts.test_backend_selection

Covers (part-5 task 1 verification):
  - Fresh install (no config) resolves the `device-local` backend.
  - An install config carrying a `vault_path` (no explicit `storage.backend`)
    resolves the built-in `vault` backend, seeded byte-identically from
    `harness_memory.vault_path()`.
  - An explicit `storage.backend` value wins over a configured `vault_path`.

All filesystem roots are injected (temp dirs) so the operator's real
`~/.agentm/memory`, `~/.cache/agentm/locks`, and vault are never touched.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentm_config as ac  # noqa: E402
import backend_selection as bs  # noqa: E402
import harness_memory  # noqa: E402
import storage_device_local  # noqa: E402
import storage_vault  # noqa: E402


class _Env:
    """Save/restore env: set AGENTM_INSTALL_PREFIX, drop MEMORY_VAULT_PATH."""

    def __init__(self, prefix: Path):
        self._prefix = prefix
        self._keys = ("AGENTM_INSTALL_PREFIX", "MEMORY_VAULT_PATH")
        self._saved: dict[str, str | None] = {}

    def __enter__(self):
        for k in self._keys:
            self._saved[k] = os.environ.get(k)
        os.environ["AGENTM_INSTALL_PREFIX"] = str(self._prefix)
        os.environ.pop("MEMORY_VAULT_PATH", None)
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestSelectBackend(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-backend-selection-test-")
        self.prefix = Path(self.tmp) / "prefix"
        self.prefix.mkdir(parents=True, exist_ok=True)
        self.device_root = Path(self.tmp) / "device-local-root"
        self.lock_root = Path(self.tmp) / "locks"
        self.env = _Env(self.prefix)
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fresh install -------------------------------------------------------

    def test_fresh_install_resolves_device_local(self) -> None:
        # No config file at all → fresh install → device-local default.
        backend = bs.select_backend(device_local_root=self.device_root)
        self.assertIsInstance(backend, storage_device_local.DeviceLocalBackend)
        # Seeded with the injected root (operator's real ~/.agentm/memory untouched).
        self.assertEqual(backend.root, self.device_root)

    def test_choose_protocol_fresh_is_device_local(self) -> None:
        self.assertEqual(
            bs.choose_protocol(vault_root=None), storage_device_local.PROTOCOL
        )

    # -- existing operator's vault ------------------------------------------

    def test_vault_path_config_resolves_vault(self) -> None:
        vault = Path(self.tmp) / "vault"
        vault.mkdir()
        rc = ac.main(["--vault-path", str(vault)])
        self.assertEqual(rc, 0)

        expected_root = harness_memory.vault_path()
        self.assertIsNotNone(expected_root, "vault_path() should read the config")

        backend = bs.select_backend(vault_lock_root=self.lock_root)
        self.assertIsInstance(backend, storage_vault.VaultBackend)
        # Byte-identical: the vault backend is seeded from the configured path,
        # zero re-setup.
        self.assertEqual(backend.root, expected_root)

    def test_choose_protocol_with_vault_root_is_vault(self) -> None:
        self.assertEqual(
            bs.choose_protocol(vault_root=Path(self.tmp)), storage_vault.PROTOCOL
        )

    # -- explicit selection wins --------------------------------------------

    def test_explicit_storage_backend_wins_over_vault_path(self) -> None:
        # Configure BOTH a vault_path and an explicit device-local selection;
        # the explicit value must win.
        vault = Path(self.tmp) / "vault"
        vault.mkdir()
        self.assertEqual(ac.main(["--vault-path", str(vault)]), 0)
        self.assertEqual(ac.main(["--storage-backend", "device-local"]), 0)

        backend = bs.select_backend(device_local_root=self.device_root)
        self.assertIsInstance(backend, storage_device_local.DeviceLocalBackend)
        self.assertEqual(backend.root, self.device_root)

    def test_explicit_vault_selection_resolves_vault(self) -> None:
        vault = Path(self.tmp) / "vault"
        vault.mkdir()
        self.assertEqual(ac.main(["--vault-path", str(vault)]), 0)
        self.assertEqual(ac.main(["--storage-backend", "vault"]), 0)

        backend = bs.select_backend(vault_lock_root=self.lock_root)
        self.assertIsInstance(backend, storage_vault.VaultBackend)

    def test_choose_protocol_explicit_value_wins(self) -> None:
        self.assertEqual(ac.main(["--storage-backend", "some-future-backend"]), 0)
        # Even with a vault_root present, the explicit name is returned verbatim.
        self.assertEqual(
            bs.choose_protocol(vault_root=Path(self.tmp)), "some-future-backend"
        )


class TestCapabilitiesRead(unittest.TestCase):
    """Part-5 task 2: the kernel reads the (already-shipped) capability descriptor.

    The descriptor *shape* (`storage_seam.Capabilities`) shipped in part 1 and
    both built-ins declared their values in parts 2/4. This task is the **read
    side**: the task-1 resolver returns the selected backend, whose `.capabilities`
    exposes all four booleans. Capability-request *matching* stays V5-7 — this is
    read-and-surface, not match-and-decide.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-capabilities-read-test-")
        self.prefix = Path(self.tmp) / "prefix"
        self.prefix.mkdir(parents=True, exist_ok=True)
        self.device_root = Path(self.tmp) / "device-local-root"
        self.lock_root = Path(self.tmp) / "locks"
        self.env = _Env(self.prefix)
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_device_local_capabilities_read_all_false(self) -> None:
        # Fresh → device-local; read all four booleans through the selection surface.
        caps = bs.select_backend(device_local_root=self.device_root).capabilities
        self.assertFalse(caps.concurrent_writers)
        self.assertFalse(caps.conflict_files)
        self.assertFalse(caps.encryption)
        self.assertFalse(caps.sync)

    def test_vault_capabilities_read_synced_multiwriter(self) -> None:
        vault = Path(self.tmp) / "vault"
        vault.mkdir()
        self.assertEqual(ac.main(["--vault-path", str(vault)]), 0)
        caps = bs.select_backend(vault_lock_root=self.lock_root).capabilities
        # The synced vault: multi-writer-safe (mutex), GDrive replicates the tree
        # (sync) + surfaces conflict copies (conflict_files); not encrypted at rest.
        self.assertTrue(caps.concurrent_writers)
        self.assertTrue(caps.conflict_files)
        self.assertTrue(caps.sync)
        self.assertFalse(caps.encryption)

    def test_capabilities_shape_is_additive_only(self) -> None:
        # Pin the four V5-7-target keys as present + boolean. A *reshape* (rename
        # or remove an existing key) breaks this; additive growth (a brand-new
        # capability key) is allowed by design — so this is a superset check, not
        # an exact-equality one.
        import dataclasses

        from storage_seam import Capabilities

        names = {f.name for f in dataclasses.fields(Capabilities)}
        expected = {"concurrent_writers", "conflict_files", "encryption", "sync"}
        self.assertTrue(
            expected <= names,
            f"capability shape reshaped — missing {expected - names}",
        )
        # Each defaults to the conservative floor (False) and is a real bool.
        floor = Capabilities()
        for name in expected:
            value = getattr(floor, name)
            self.assertIsInstance(value, bool)
            self.assertFalse(value)


if __name__ == "__main__":
    unittest.main()
