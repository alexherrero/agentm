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


# A marker stand-in for the crickets obsidian-vault plugin's storage_vault.py.
# Subclassing the kernel built-in keeps the real write/lock behavior (so a
# round-trip flows through the genuine imported vault_lock) while giving a
# *distinct* class/module — which is how a test proves selection resolved to the
# plugin, not the shadowed built-in, with no crickets checkout in agentm CI. It
# self-registers under `vault` exactly as the real plugin does, exercising the
# loader's free-the-slot-then-restore dance (the kernel built-in still holds the
# slot through V5-2, so the registry's duplicate guard would raise otherwise).
_PLUGIN_FIXTURE_SRC = '''\
"""Test fixture: stand-in for the crickets obsidian-vault plugin backend."""
from storage_seam import registry
from storage_vault import PROTOCOL, VaultBackend as _BuiltinVaultBackend


class VaultBackend(_BuiltinVaultBackend):
    is_obsidian_vault_plugin_fixture = True


registry.register(PROTOCOL, VaultBackend)
'''


def _install_plugin_fixture(parent: Path) -> Path:
    """Write the plugin-backend fixture into a fresh `scripts/` dir and return it."""
    scripts = parent / "obsidian-vault-plugin" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "storage_vault.py").write_text(_PLUGIN_FIXTURE_SRC, encoding="utf-8")
    return scripts


class _Env:
    """Save/restore env: set AGENTM_INSTALL_PREFIX, drop MEMORY_VAULT_PATH + OBSIDIAN_VAULT_SCRIPTS."""

    def __init__(self, prefix: Path):
        self._prefix = prefix
        self._keys = (
            "AGENTM_INSTALL_PREFIX",
            "MEMORY_VAULT_PATH",
            "OBSIDIAN_VAULT_SCRIPTS",
        )
        self._saved: dict[str, str | None] = {}

    def __enter__(self):
        for k in self._keys:
            self._saved[k] = os.environ.get(k)
        os.environ["AGENTM_INSTALL_PREFIX"] = str(self._prefix)
        # Drop the discovery env overrides so a stray operator value can't leak
        # into the resolver; the vault tests inject `vault_plugin_scripts` directly.
        os.environ.pop("MEMORY_VAULT_PATH", None)
        os.environ.pop("OBSIDIAN_VAULT_SCRIPTS", None)
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
            bs.choose_protocol(), storage_device_local.PROTOCOL
        )

    # -- existing operator's vault ------------------------------------------

    def test_vault_path_config_resolves_vault(self) -> None:
        vault = Path(self.tmp) / "vault"
        vault.mkdir()
        rc = ac.main(["--vault-path", str(vault)])
        self.assertEqual(rc, 0)

        expected_root = harness_memory.vault_path()
        self.assertIsNotNone(expected_root, "vault_path() should read the config")

        plugin_scripts = _install_plugin_fixture(Path(self.tmp))
        backend = bs.select_backend(
            vault_lock_root=self.lock_root, vault_plugin_scripts=plugin_scripts
        )
        # Re-homed (V5-2 task 3): selection resolves to the *plugin* backend — a
        # distinct class from the shadowed kernel built-in, not device-local.
        from storage_seam import StorageBackend

        self.assertIsInstance(backend, StorageBackend)
        self.assertIsNot(type(backend), storage_vault.VaultBackend)
        self.assertTrue(getattr(backend, "is_obsidian_vault_plugin_fixture", False))
        # Byte-identical: the plugin backend is seeded from the configured path,
        # zero re-setup.
        self.assertEqual(backend.root, expected_root)

    def test_choose_protocol_explicit_vault_backend_is_vault(self) -> None:
        # V5-7: vault selection is explicit only — choose_protocol() returns vault
        # iff storage.backend=vault is configured; vault_root alone is not enough.
        self.assertEqual(ac.main(["--storage-backend", "vault"]), 0)
        self.assertEqual(bs.choose_protocol(), storage_vault.PROTOCOL)

    def test_choose_protocol_memory_vault_path_env_is_vault(self) -> None:
        # $MEMORY_VAULT_PATH env var set → vault (the env-based escape hatch).
        # Regression test: V5-7 implicit-inference removal must not break the env path.
        old = os.environ.pop("MEMORY_VAULT_PATH", None)
        os.environ["MEMORY_VAULT_PATH"] = "/tmp/fake-vault-for-protocol-test"
        try:
            self.assertEqual(bs.choose_protocol(), storage_vault.PROTOCOL)
        finally:
            os.environ.pop("MEMORY_VAULT_PATH", None)
            if old is not None:
                os.environ["MEMORY_VAULT_PATH"] = old

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

        plugin_scripts = _install_plugin_fixture(Path(self.tmp))
        backend = bs.select_backend(
            vault_lock_root=self.lock_root, vault_plugin_scripts=plugin_scripts
        )
        # Explicit `vault` resolves to the re-homed plugin backend (V5-2 task 3),
        # not the shadowed built-in.
        self.assertTrue(getattr(backend, "is_obsidian_vault_plugin_fixture", False))
        self.assertIsNot(type(backend), storage_vault.VaultBackend)

    def test_choose_protocol_explicit_value_wins(self) -> None:
        self.assertEqual(ac.main(["--storage-backend", "some-future-backend"]), 0)
        # Explicit storage.backend is returned verbatim regardless of vault_path.
        self.assertEqual(bs.choose_protocol(), "some-future-backend")


class TestVaultPluginDiscovery(unittest.TestCase):
    """V5-2 task 3: the vault backend is re-homed into the obsidian-vault plugin.

    Selecting `storage.backend=vault` now *discovers* the plugin and returns ITS
    backend — not the shadowed kernel built-in, never a silent device-local
    demotion. A marker-subclass fixture stands in for the plugin so these run
    deterministically without a crickets checkout; the real plugin's write
    round-trip through the imported `vault_lock` is proven crickets-side.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-vault-discovery-test-")
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

    def _configure_vault(self) -> Path:
        vault = Path(self.tmp) / "vault"
        vault.mkdir()
        self.assertEqual(ac.main(["--vault-path", str(vault)]), 0)
        return vault

    def test_vault_resolves_to_plugin_not_builtin(self) -> None:
        # Plugin discoverable → selection returns the PLUGIN backend (distinct class
        # + marker attr), seeded byte-identically from the configured vault_path.
        self._configure_vault()
        plugin_scripts = _install_plugin_fixture(Path(self.tmp))
        backend = bs.select_backend(
            vault_lock_root=self.lock_root, vault_plugin_scripts=plugin_scripts
        )
        self.assertTrue(getattr(backend, "is_obsidian_vault_plugin_fixture", False))
        self.assertIsNot(
            type(backend),
            storage_vault.VaultBackend,
            "selection returned the shadowed built-in, not the plugin backend",
        )
        self.assertEqual(backend.root, harness_memory.vault_path())

    def test_plugin_absent_raises_install_message_not_demote(self) -> None:
        # The load-bearing negative: vault selected, plugin NOT discoverable → fail
        # loud with the install-the-obsidian-vault message, and it must NOT demote
        # to device-local (the usable device_local_root stays uncreated — the
        # backend was never constructed).
        self._configure_vault()
        missing = Path(self.tmp) / "no-such-plugin" / "scripts"
        with self.assertRaises(bs.StorageSelectionError) as ctx:
            bs.select_backend(
                device_local_root=self.device_root,
                vault_lock_root=self.lock_root,
                vault_plugin_scripts=missing,
            )
        msg = str(ctx.exception)
        self.assertIn("obsidian-vault", msg)
        self.assertIn("install", msg.lower())
        self.assertFalse(
            self.device_root.exists(),
            "silent device-local demotion when the obsidian-vault plugin is absent",
        )

    def test_selection_leaves_registry_unmutated(self) -> None:
        # Discovery frees the `vault` slot so the plugin can self-register, then
        # restores the built-in: a select_backend call must leave the global
        # registry exactly as it found it (the built-in stays reachable for the
        # task-5 parallel-run by direct import / registry.get).
        from storage_seam import registry

        self._configure_vault()
        plugin_scripts = _install_plugin_fixture(Path(self.tmp))
        self.assertIs(registry.get(storage_vault.PROTOCOL), storage_vault.VaultBackend)
        bs.select_backend(
            vault_lock_root=self.lock_root, vault_plugin_scripts=plugin_scripts
        )
        self.assertIs(
            registry.get(storage_vault.PROTOCOL),
            storage_vault.VaultBackend,
            "select_backend left the plugin registered — the built-in must be restored",
        )

    def test_plugin_backend_carries_injected_lock_root(self) -> None:
        # The lock edge: the resolved plugin backend is constructed with the
        # injected lock_root, so its `write` composes the imported kernel
        # vault_lock against that base (the real FS round-trip is proven
        # crickets-side; here we assert the lock wiring is threaded through).
        self._configure_vault()
        plugin_scripts = _install_plugin_fixture(Path(self.tmp))
        backend = bs.select_backend(
            vault_lock_root=self.lock_root, vault_plugin_scripts=plugin_scripts
        )
        self.assertEqual(backend._lock_root, self.lock_root)

    def test_preview_plugin_absent_is_fail_row(self) -> None:
        # Doctor's vault preview mirrors the runtime refusal: plugin not
        # discoverable → a fail row carrying the same install-the-plugin message,
        # and the probe is read-only (no device-local construction).
        self._configure_vault()
        missing = Path(self.tmp) / "no-such-plugin" / "scripts"
        prev = bs.storage_preview(
            device_local_root=self.device_root, vault_plugin_scripts=missing
        )
        self.assertEqual(prev.status, "fail")
        self.assertEqual(prev.protocol, storage_vault.PROTOCOL)
        self.assertIn("[FAIL]", prev.line)
        self.assertIn("obsidian-vault", prev.line)
        self.assertFalse(self.device_root.exists())

    def test_loader_leaves_empty_slot_unmutated(self) -> None:
        # Regression (review DEFECT 1): when the `vault` slot is EMPTY at loader
        # entry (`prior is None`), the plugin's import-time self-register must not
        # leak — the loader has to leave the registry exactly as it found it, i.e.
        # empty. The shipped `test_selection_leaves_registry_unmutated` only covers
        # the prior-non-None branch; this one drives the prior-None branch the loader
        # docstring's unconditional "no lingering mutation" promise also has to honor.
        from storage_seam import registry

        plugin_scripts = _install_plugin_fixture(Path(self.tmp))
        prior = registry._backends.pop(storage_vault.PROTOCOL, None)
        try:
            self.assertNotIn(
                storage_vault.PROTOCOL, registry._backends, "slot not empty at entry"
            )
            cls = bs._load_vault_plugin_backend(plugin_scripts=plugin_scripts)
            self.assertIsNotNone(cls, "the loader did not discover the plugin backend")
            self.assertNotIn(
                storage_vault.PROTOCOL,
                registry._backends,
                "loader leaked the plugin self-registration into an empty vault slot",
            )
        finally:
            if prior is not None:
                registry._backends[storage_vault.PROTOCOL] = prior

    def test_preview_and_select_agree_on_present_but_unloadable_plugin(self) -> None:
        # Regression (review DEFECT 2): a plugin dir that is PRESENT (a
        # `storage_vault.py` exists) but exports no `VaultBackend` (a partial /
        # wrong-version install) must not preview "ok" while selection refuses.
        # Doctor's preview now uses the SAME load-discovery the runtime guard uses,
        # so the two can't drift on a present-but-unloadable install.
        self._configure_vault()
        broken = Path(self.tmp) / "broken-plugin" / "scripts"
        broken.mkdir(parents=True)
        (broken / "storage_vault.py").write_text(
            '"""Present, but exports no VaultBackend (partial install)."""\n'
            "VAULT_BACKEND_MISSING = True\n",
            encoding="utf-8",
        )
        # Runtime selection refuses (fail-loud) and constructs no device-local backend.
        with self.assertRaises(bs.StorageSelectionError):
            bs.select_backend(
                device_local_root=self.device_root,
                vault_lock_root=self.lock_root,
                vault_plugin_scripts=broken,
            )
        self.assertFalse(self.device_root.exists())
        # Doctor's preview must AGREE — a fail row, not a misleading "ok" — and stay
        # read-only (never raises, constructs nothing).
        prev = bs.storage_preview(
            device_local_root=self.device_root, vault_plugin_scripts=broken
        )
        self.assertEqual(
            prev.status,
            "fail",
            "doctor previewed OK for a present-but-unloadable plugin the engine refuses",
        )
        self.assertIn("obsidian-vault", prev.line)
        self.assertFalse(self.device_root.exists())

    def test_preview_and_select_agree_on_throws_on_import_plugin(self) -> None:
        # Regression (Verify-phase review, follow-on to DEFECT 2): the OTHER
        # present-but-unloadable variant — a `storage_vault.py` that is present and
        # parses but RAISES at import time (a broken / wrong-version install). Before
        # the follow-on fix the doctor preview wrapped the loader in try/except
        # (clean "fail" + install message) while `select_backend` called the loader
        # UNGUARDED — so a throws-on-import plugin made the engine die with a raw
        # traceback (not StorageSelectionError, not the actionable install message)
        # while doctor previewed a clean refusal: exactly the preview/select drift
        # DEFECT 2 was chartered to eliminate, left open for this variant and newly
        # *documented* as closed. Both paths must collapse the import failure to the
        # SAME fail-loud refusal — and never demote to device-local.
        self._configure_vault()
        exploding = Path(self.tmp) / "exploding-plugin" / "scripts"
        exploding.mkdir(parents=True)
        (exploding / "storage_vault.py").write_text(
            '"""Present, parses, but explodes at import (broken install)."""\n'
            "raise RuntimeError('obsidian-vault plugin import explodes')\n",
            encoding="utf-8",
        )
        # Runtime selection refuses with the fail-loud StorageSelectionError — never
        # a raw RuntimeError (assertRaises(StorageSelectionError) would not catch a
        # RuntimeError, so this errors against the pre-fix code), never a demotion.
        with self.assertRaises(bs.StorageSelectionError) as ctx:
            bs.select_backend(
                device_local_root=self.device_root,
                vault_lock_root=self.lock_root,
                vault_plugin_scripts=exploding,
            )
        # The underlying import failure is chained (`from exc`), not discarded —
        # the actionable install message leads, the real cause stays attached.
        self.assertIsInstance(
            ctx.exception.__cause__,
            RuntimeError,
            "select must chain the underlying import failure, not swallow it",
        )
        self.assertFalse(self.device_root.exists())
        # Doctor's preview AGREES — same fail row + install message, still read-only.
        prev = bs.storage_preview(
            device_local_root=self.device_root, vault_plugin_scripts=exploding
        )
        self.assertEqual(
            prev.status,
            "fail",
            "doctor drifted from select on a throws-on-import plugin",
        )
        self.assertIn("obsidian-vault", prev.line)
        self.assertFalse(self.device_root.exists())

    def test_preview_and_select_agree_on_non_class_vaultbackend_export(self) -> None:
        # Regression (Verify-phase review, follow-on #2 to DEFECT 2): the THIRD
        # present-but-unloadable variant — `storage_vault.py` execs cleanly and DOES
        # export `VaultBackend`, but as something non-instantiable (`VaultBackend =
        # 42`, a stray value shadowing the class, a wrong-version misdefinition).
        # `getattr(module, "VaultBackend", None)` returns the non-None junk, so it
        # slips past the loader's None-gate: before the loader-side type check, the
        # preview reported "ok" while `select_backend` detonated with a RAW TypeError
        # at `plugin_cls(...)` (construction is outside the load try/except by
        # design). The loader now validates the export is a real StorageBackend
        # subclass and returns None otherwise, so BOTH paths refuse identically.
        self._configure_vault()
        bad = Path(self.tmp) / "non-class-export-plugin" / "scripts"
        bad.mkdir(parents=True)
        (bad / "storage_vault.py").write_text(
            '"""Present, execs clean, but VaultBackend is not a class."""\n'
            "VaultBackend = 42\n",
            encoding="utf-8",
        )
        # The loader treats a non-StorageBackend export as absent — None, not junk.
        self.assertIsNone(
            bs._load_vault_plugin_backend(plugin_scripts=bad),
            "loader handed back a non-StorageBackend export instead of None",
        )
        # Runtime selection refuses (fail-loud) — never a raw TypeError, never demote.
        with self.assertRaises(bs.StorageSelectionError):
            bs.select_backend(
                device_local_root=self.device_root,
                vault_lock_root=self.lock_root,
                vault_plugin_scripts=bad,
            )
        self.assertFalse(self.device_root.exists())
        # Doctor's preview AGREES — a fail row, not a misleading "ok".
        prev = bs.storage_preview(
            device_local_root=self.device_root, vault_plugin_scripts=bad
        )
        self.assertEqual(
            prev.status,
            "fail",
            "doctor previewed OK for a non-class VaultBackend export the engine refuses",
        )
        self.assertIn("obsidian-vault", prev.line)
        self.assertFalse(self.device_root.exists())

    def test_loadable_plugin_with_failing_constructor_is_a_bounded_boundary(self) -> None:
        # Bounded-contract PIN (Verify-phase review, variant (h)): a plugin that
        # loads to a *valid* StorageBackend subclass but whose __init__ raises (a
        # version-skewed constructor signature, or an environmental error like a
        # read-only vault root) is NOT a loadability failure. The loader returns it;
        # doctor's read-only preview certifies loadability ("ok") because it
        # deliberately never constructs (construction mkdirs the vault root, which
        # would break read-only). select DOES construct, so it surfaces the failure
        # RAW — true cause preserved, never mislabeled "install the plugin" — but it
        # MUST NOT demote to device-local. This pins that deliberate boundary and
        # guards the never-demote invariant on the construction-failure path; the
        # one place preview and select intentionally differ. Closing the residual
        # gap needs a read-only plugin-API conformance check (deferred to V5-8), not
        # a behavior change here — if a future change makes preview return "fail"
        # for this case, that is a conscious decision and this pin should be updated.
        self._configure_vault()
        broken_ctor = Path(self.tmp) / "broken-ctor-plugin" / "scripts"
        broken_ctor.mkdir(parents=True)
        (broken_ctor / "storage_vault.py").write_text(
            '"""Loads to a valid StorageBackend subclass, but __init__ raises."""\n'
            "from storage_seam import registry\n"
            "from storage_vault import PROTOCOL, VaultBackend as _Builtin\n"
            "\n\n"
            "class VaultBackend(_Builtin):\n"
            "    def __init__(self, *a, **k):\n"
            "        raise RuntimeError('version-skewed constructor')\n"
            "\n\n"
            "registry.register(PROTOCOL, VaultBackend)\n",
            encoding="utf-8",
        )
        # It genuinely LOADS — this is variant (h), not a load failure.
        self.assertIsNotNone(
            bs._load_vault_plugin_backend(plugin_scripts=broken_ctor),
            "the broken-constructor plugin should still LOAD to a usable class",
        )
        # select surfaces the construction failure RAW (its true cause, RuntimeError)
        # — NOT StorageSelectionError (it is not an install problem), NOT a demotion.
        with self.assertRaises(RuntimeError):
            bs.select_backend(
                device_local_root=self.device_root,
                vault_lock_root=self.lock_root,
                vault_plugin_scripts=broken_ctor,
            )
        self.assertFalse(
            self.device_root.exists(),
            "vault selection demoted to device-local on a construction failure",
        )
        # Doctor's preview certifies LOADABILITY ("ok") — the documented boundary: a
        # read-only preview can't verify side-effecting construction without mkdir'ing
        # the vault root. This is the deliberate select/preview difference.
        prev = bs.storage_preview(
            device_local_root=self.device_root, vault_plugin_scripts=broken_ctor
        )
        self.assertEqual(
            prev.status, "ok", "preview should certify loadability for variant (h)"
        )
        self.assertFalse(self.device_root.exists())


class TestFailLoud(unittest.TestCase):
    """Part-5 task 3: the fail-loud guard — refuse, never demote.

    A config naming a backend whose plugin is not installed (`registry.get → None`)
    must raise a clear install-the-plugin error and refuse the memory operation —
    NEVER silently fall back to `device-local`. The negative test
    (`test_no_silent_device_local_fallback`) is the load-bearing one: a silent
    demotion is the single failure that mis-writes or orphans the vault.
    """

    SENTINEL = "never-registered-sentinel-backend"

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-fail-loud-test-")
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

    def test_sentinel_is_genuinely_unregistered(self) -> None:
        # The premise: this name really does resolve to absent. (Both real
        # built-ins register at import, so a negative test needs a name that
        # registry.get genuinely doesn't resolve.)
        from storage_seam import registry
        self.assertNotIn(self.SENTINEL, registry)
        self.assertIsNone(registry.get(self.SENTINEL))

    def test_unregistered_backend_raises_install_the_plugin(self) -> None:
        self.assertEqual(ac.main(["--storage-backend", self.SENTINEL]), 0)
        with self.assertRaises(bs.StorageSelectionError) as ctx:
            bs.select_backend(device_local_root=self.device_root)
        msg = str(ctx.exception)
        # The error names the exact missing backend.
        self.assertIn(self.SENTINEL, msg)
        # …and points at the fix.
        self.assertIn("install", msg.lower())

    def test_no_silent_device_local_fallback(self) -> None:
        # The load-bearing negative test. Configure an unregistered backend AND
        # hand select_backend a usable device_local_root — so device-local COULD
        # be produced. It must NOT be: the resolver raises, it does not return a
        # DeviceLocalBackend, and it never even constructs one (the root stays
        # uncreated — DeviceLocalBackend mkdirs its root on construction).
        self.assertEqual(ac.main(["--storage-backend", self.SENTINEL]), 0)
        with self.assertRaises(bs.StorageSelectionError):
            bs.select_backend(device_local_root=self.device_root)
        self.assertFalse(
            self.device_root.exists(),
            "silent device-local fall-back: a backend was constructed despite "
            "the configured backend being unregistered",
        )

    def test_install_message_lists_registered_alternatives(self) -> None:
        self.assertEqual(ac.main(["--storage-backend", self.SENTINEL]), 0)
        with self.assertRaises(bs.StorageSelectionError) as ctx:
            bs.select_backend(device_local_root=self.device_root)
        msg = str(ctx.exception)
        # The two built-ins are registered today → both named as alternatives.
        self.assertIn(storage_device_local.PROTOCOL, msg)
        self.assertIn(storage_vault.PROTOCOL, msg)

    def test_vault_selected_without_vault_path_raises(self) -> None:
        # Explicit `vault` with no vault_path to seed it → fail-loud, never demote.
        # V5-3: the choke point moved upstream — vault_path() raises
        # StorageBackendNotInstalledError before select_backend()'s vault-root
        # guard even fires. Same never-demote contract, earlier detection.
        self.assertEqual(ac.main(["--storage-backend", "vault"]), 0)
        with self.assertRaises(harness_memory.StorageBackendNotInstalledError) as ctx:
            bs.select_backend(vault_lock_root=self.lock_root)
        self.assertIn("vault_path", str(ctx.exception))

    def test_unparseable_config_with_explicit_backend_raises(self) -> None:
        # A config file that EXISTS and explicitly names a backend but is malformed
        # JSON (a partial write, a hand-edit slip, disk corruption). The tolerant
        # agentm_config._read_config collapses "file missing" and "file unreadable"
        # into the same None, so naively the chain would treat this as a fresh
        # install and demote to device-local — dropping the operator's explicit
        # selection and orphaning the vault. The resolver must refuse instead, and
        # must NOT construct a device-local backend (root stays uncreated).
        config_path = ac._config_path(self.prefix)
        config_path.write_text(
            '{ "storage.backend": "' + self.SENTINEL + '", }',  # trailing comma → invalid JSON
            encoding="utf-8",
        )
        with self.assertRaises(bs.StorageSelectionError) as ctx:
            bs.select_backend(device_local_root=self.device_root)
        # The error points the operator at the unreadable file.
        self.assertIn(str(config_path), str(ctx.exception))
        self.assertFalse(
            self.device_root.exists(),
            "silent device-local fall-back on an unreadable config",
        )

    def test_non_string_storage_backend_value_raises(self) -> None:
        # Valid JSON, but storage.backend is the wrong type (a list — corruption or
        # a stray hand-edit; the setter only ever writes a non-empty string and
        # --unset removes the key, so a non-string value is never a legitimate
        # write). An explicit-but-unhonorable selection: refuse, never demote.
        config_path = ac._config_path(self.prefix)
        config_path.write_text('{"storage.backend": ["x"]}', encoding="utf-8")
        with self.assertRaises(bs.StorageSelectionError):
            bs.select_backend(device_local_root=self.device_root)
        self.assertFalse(
            self.device_root.exists(),
            "silent device-local fall-back on a non-string storage.backend value",
        )

    def test_non_utf8_config_with_explicit_backend_raises(self) -> None:
        # A config that EXISTS but is not valid UTF-8 — a Windows editor saved it
        # UTF-16/BOM (the \xff\xfe byte-order mark) or disk corruption flipped a
        # byte. Path.read_text(encoding="utf-8") raises UnicodeDecodeError, which
        # is a ValueError — NOT an OSError and NOT a JSONDecodeError — so it slips
        # past a guard that only names those two and leaks an uncaught traceback,
        # breaking the docstring's "file present but unparseable → raise" contract.
        # It must be caught by the same fail-loud guard as malformed JSON: refuse,
        # never demote, and never construct a device-local backend.
        config_path = ac._config_path(self.prefix)
        config_path.write_bytes(b'\xff\xfe{"storage.backend": "' + self.SENTINEL.encode() + b'"}')
        with self.assertRaises(bs.StorageSelectionError) as ctx:
            bs.select_backend(device_local_root=self.device_root)
        self.assertIn(str(config_path), str(ctx.exception))
        self.assertFalse(
            self.device_root.exists(),
            "silent device-local fall-back on a non-UTF-8 config",
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
        plugin_scripts = _install_plugin_fixture(Path(self.tmp))
        caps = bs.select_backend(
            vault_lock_root=self.lock_root, vault_plugin_scripts=plugin_scripts
        ).capabilities
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


class TestStoragePreview(unittest.TestCase):
    """Part-5 task 4: the `doctor` storage preview — read-only, never mutates, never drifts.

    `storage_preview` mirrors `select_backend`'s resolution but *reports* instead
    of instantiating. The load-bearing assertions: it reuses
    `_install_plugin_message` (so doctor's preview is byte-identical to the
    runtime guard, mirroring the `check-worktree-slug` probe/gate shared-resolver
    pattern) and it NEVER constructs a backend (the device-local root stays
    uncreated — doctor must not mutate the operator's home).
    """

    SENTINEL = "never-registered-sentinel-backend"

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-storage-preview-test-")
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

    def test_fresh_install_previews_device_local_ok(self) -> None:
        prev = bs.storage_preview(device_local_root=self.device_root)
        self.assertEqual(prev.status, "ok")
        self.assertEqual(prev.protocol, storage_device_local.PROTOCOL)
        self.assertIn("[OK]", prev.line)
        self.assertIn("device-local", prev.line)
        # Read-only: the preview probed writability WITHOUT creating the root.
        self.assertFalse(
            self.device_root.exists(),
            "doctor preview mutated the operator's home (constructed a backend)",
        )

    def test_vault_path_previews_vault_ok(self) -> None:
        vault = Path(self.tmp) / "vault"
        vault.mkdir()
        self.assertEqual(ac.main(["--vault-path", str(vault)]), 0)
        # The preview's vault row is now gated on the obsidian-vault plugin being
        # discoverable (V5-2 task 3) — a pure presence probe, no module exec.
        plugin_scripts = _install_plugin_fixture(Path(self.tmp))
        prev = bs.storage_preview(vault_plugin_scripts=plugin_scripts)
        self.assertEqual(prev.status, "ok")
        self.assertEqual(prev.protocol, storage_vault.PROTOCOL)
        self.assertIn("[OK]", prev.line)
        self.assertIn("vault", prev.line)
        self.assertIn("obsidian-vault plugin discoverable", prev.line)

    def test_unregistered_backend_previews_install_message_verbatim(self) -> None:
        # The byte-identical-to-the-guard assertion: the preview embeds the EXACT
        # _install_plugin_message the task-3 guard raises, so doctor's preview and
        # the runtime refusal can never drift.
        self.assertEqual(ac.main(["--storage-backend", self.SENTINEL]), 0)
        prev = bs.storage_preview(device_local_root=self.device_root)
        self.assertEqual(prev.status, "fail")
        self.assertEqual(prev.protocol, self.SENTINEL)
        self.assertIn(bs._install_plugin_message(self.SENTINEL), prev.line)
        # Still read-only: no device-local backend was constructed on the fail path.
        self.assertFalse(self.device_root.exists())

    def test_corrupt_config_previews_fail_not_crash(self) -> None:
        # The task-3 hardening surfaces a corrupt config as a fail-loud raise; the
        # preview must CATCH that and report a fail row, never propagate / crash.
        config_path = ac._config_path(self.prefix)
        config_path.write_text('{ "storage.backend": "x", }', encoding="utf-8")  # invalid JSON
        prev = bs.storage_preview(device_local_root=self.device_root)
        self.assertEqual(prev.status, "fail")
        self.assertIsNone(prev.protocol)
        self.assertIn("[FAIL]", prev.line)
        self.assertFalse(self.device_root.exists())

    def test_non_utf8_config_previews_fail_not_crash(self) -> None:
        # A non-UTF-8 config (UTF-16/BOM from a Windows editor) raises
        # UnicodeDecodeError deep in the resolver. The preview promises to never
        # propagate / crash — it must catch the fail-loud raise and report a fail
        # row, exactly as it does for malformed JSON above.
        config_path = ac._config_path(self.prefix)
        config_path.write_bytes(b'\xff\xfe{"storage.backend": "x"}')
        prev = bs.storage_preview(device_local_root=self.device_root)
        self.assertEqual(prev.status, "fail")
        self.assertIsNone(prev.protocol)
        self.assertIn("[FAIL]", prev.line)
        self.assertFalse(self.device_root.exists())

    def test_device_local_non_writable_root_previews_warn(self) -> None:
        ro = Path(self.tmp) / "ro"
        ro.mkdir()
        os.chmod(ro, 0o555)
        try:
            if os.access(ro, os.W_OK):
                self.skipTest("cannot make a dir read-only (running as root?)")
            prev = bs.storage_preview(device_local_root=ro / "memory")
            self.assertEqual(prev.status, "warn")
            self.assertIn("[WARN]", prev.line)
            self.assertIn("not writable", prev.line)
        finally:
            os.chmod(ro, 0o755)  # restore so tearDown's rmtree can clean up

    def test_doctor_main_exit_code_fail_on_unregistered(self) -> None:
        # The CLI maps a fail row → exit 1 (the engine will refuse at runtime).
        self.assertEqual(ac.main(["--storage-backend", self.SENTINEL]), 0)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rc = bs._doctor_main(["--doctor"])
        self.assertEqual(rc, 1)


class TestDoctorRequires(unittest.TestCase):
    """V5-7: _doctor_main --requires flag — pre-flight capability check.

    Tests call ``_doctor_main()`` directly (not via subprocess) with injected
    roots so the operator's real home / vault are never touched. The unknown-field
    validation test exercises the path that fires before any backend is resolved;
    the FAIL test uses device-local (all caps False) + a True requirement; the PASS
    test uses the vault plugin fixture (concurrent_writers=True).
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-doctor-requires-test-")
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

    def _run(self, *args, **kwargs):
        """Call _doctor_main, capturing stdout + stderr; return (rc, stdout, stderr)."""
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = bs._doctor_main(list(args), **kwargs)
        return rc, out.getvalue(), err.getvalue()

    def test_unknown_field_exits_nonzero_before_backend(self) -> None:
        # Validation fires before any backend is resolved — device root stays uncreated.
        rc, _out, err = self._run(
            "--doctor", "--requires", "not_a_real_capability",
            device_local_root=self.device_root,
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("unknown", err)
        self.assertIn("not_a_real_capability", err)
        self.assertFalse(self.device_root.exists(), "backend was constructed despite unknown field")

    def test_mismatched_capability_prints_fail_and_exits_1(self) -> None:
        # Device-local has concurrent_writers=False; requiring True → FAIL, exit 1.
        rc, out, _err = self._run(
            "--doctor", "--requires", "concurrent_writers",
            device_local_root=self.device_root,
        )
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out)
        self.assertIn("concurrent_writers", out)

    def test_satisfied_capability_prints_pass_and_exits_0(self) -> None:
        # Vault backend (concurrent_writers=True) + --requires concurrent_writers → PASS.
        vault = Path(self.tmp) / "vault"
        vault.mkdir()
        import agentm_config as ac
        self.assertEqual(ac.main(["--vault-path", str(vault)]), 0)
        plugin_scripts = _install_plugin_fixture(Path(self.tmp))
        rc, out, _err = self._run(
            "--doctor", "--requires", "concurrent_writers",
            vault_lock_root=self.lock_root,
            vault_plugin_scripts=plugin_scripts,
        )
        self.assertEqual(rc, 0)
        self.assertIn("PASS", out)
        self.assertIn("concurrent_writers", out)

    def test_multiple_unknown_fields_all_named(self) -> None:
        rc, _out, err = self._run(
            "--doctor", "--requires", "not_real,also_fake",
            device_local_root=self.device_root,
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("not_real", err)
        self.assertIn("also_fake", err)

    def test_no_requires_flag_preserves_existing_behavior(self) -> None:
        # Without --requires, doctor prints the standard preview row.
        rc, out, _err = self._run("--doctor", device_local_root=self.device_root)
        self.assertEqual(rc, 0)
        self.assertIn("[OK]", out)
        self.assertIn("device-local", out)


class TestCapabilityMatching(unittest.TestCase):
    """V5-7: select_backend(required=...) subset check + CapabilityMismatchError.

    All tests run against the device-local backend (fresh-install default) because
    its capabilities are all-False — the conservative floor — which makes both the
    pass cases (required=None / all-False required) and the fail cases (any True
    requirement) deterministic and vault-plugin-free.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="agentm-capability-matching-test-")
        self.prefix = Path(self.tmp) / "prefix"
        self.prefix.mkdir(parents=True, exist_ok=True)
        self.device_root = Path(self.tmp) / "device-local-root"
        self.env = _Env(self.prefix)
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- pass cases -----------------------------------------------------------

    def test_required_none_no_check(self) -> None:
        # required=None (default) → no check, existing behavior unchanged.
        from storage_seam import Capabilities
        backend = bs.select_backend(
            device_local_root=self.device_root, required=None
        )
        self.assertIsInstance(backend, storage_device_local.DeviceLocalBackend)

    def test_all_false_required_passes(self) -> None:
        # required=Capabilities() — all False — is vacuously satisfied by any backend.
        from storage_seam import Capabilities
        backend = bs.select_backend(
            device_local_root=self.device_root, required=Capabilities()
        )
        self.assertIsInstance(backend, storage_device_local.DeviceLocalBackend)

    def test_matching_capabilities_passes(self) -> None:
        # Device-local has all capabilities False; requiring all False passes.
        from storage_seam import Capabilities
        backend = bs.select_backend(
            device_local_root=self.device_root,
            required=Capabilities(
                concurrent_writers=False,
                conflict_files=False,
                encryption=False,
                sync=False,
            ),
        )
        self.assertIsInstance(backend, storage_device_local.DeviceLocalBackend)

    # -- fail cases -----------------------------------------------------------

    def test_single_mismatched_field_raises(self) -> None:
        # Device-local has concurrent_writers=False; requiring True → mismatch.
        from storage_seam import Capabilities
        with self.assertRaises(bs.CapabilityMismatchError) as ctx:
            bs.select_backend(
                device_local_root=self.device_root,
                required=Capabilities(concurrent_writers=True),
            )
        msg = str(ctx.exception)
        self.assertIn("concurrent_writers", msg)
        self.assertIn("device-local", msg)

    def test_multiple_mismatched_fields_all_named(self) -> None:
        # Two mismatched fields → both named in the error message.
        from storage_seam import Capabilities
        with self.assertRaises(bs.CapabilityMismatchError) as ctx:
            bs.select_backend(
                device_local_root=self.device_root,
                required=Capabilities(concurrent_writers=True, encryption=True),
            )
        msg = str(ctx.exception)
        self.assertIn("concurrent_writers", msg)
        self.assertIn("encryption", msg)

    def test_error_is_subclass_of_storage_selection_error(self) -> None:
        # CapabilityMismatchError is a StorageSelectionError so existing catch
        # sites cover it automatically.
        from storage_seam import Capabilities
        with self.assertRaises(bs.StorageSelectionError):
            bs.select_backend(
                device_local_root=self.device_root,
                required=Capabilities(sync=True),
            )

    def test_str_contains_protocol_and_field(self) -> None:
        # str(e) names the backend protocol and the unsatisfied field.
        from storage_seam import Capabilities
        try:
            bs.select_backend(
                device_local_root=self.device_root,
                required=Capabilities(conflict_files=True),
            )
            self.fail("expected CapabilityMismatchError")
        except bs.CapabilityMismatchError as e:
            self.assertIn("device-local", str(e))
            self.assertIn("conflict_files", str(e))


if __name__ == "__main__":
    unittest.main()
