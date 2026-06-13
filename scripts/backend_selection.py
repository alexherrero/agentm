#!/usr/bin/env python3
"""backend_selection — choose the storage backend instance from install config (V5-1 part 5/5).

The *selection resolver*: maps the on-device `.agentm-config.json` to a concrete,
registered `StorageBackend` instance. It sits **above** the seam (DC-7) — it wires
the engine to a backend by reading `storage_seam.registry`,
`harness_memory.vault_path()`, and `agentm_config`. It is **not** a storage backend:
deliberately not named `storage_*.py` and using no seam-verb name
(`resolve`/`read`/`write`/`list`/`exists`/`info`/`mkdir`), so the
`check-storage-seam-no-path-leak` gate (which scans `storage_*.py` for verb-named
`Path` returns) never mistakes this path-handling resolver for a backend.

The resolution chain (first hit wins):

  1. An explicit `storage.backend` value in config → that protocol name.
  2. else an existing `vault_path` (env `$MEMORY_VAULT_PATH` or config) → the
     built-in `vault`, **seeded from `harness_memory.vault_path()`** — an existing
     operator's vault is selected with zero re-setup, byte-identical.
  3. else (fresh install, no vault) → `device-local`, the fresh-install default.

The chosen protocol is instantiated via `storage_seam.registry.get(<protocol>)`.
Importing this module guarantees the two built-ins are registered (their modules
self-register at import).

Minimal in V5-1: exactly one `storage.backend` key + this resolver. The full
per-plugin config model and capability-request *matching* are V5-7; the
**fail-loud guard** for an unregistered backend lands in part-5 task 3 (it
refines the bare `registry.get → None` guard below into a named
install-the-plugin error + a no-silent-fall-back negative test).

Stdlib-only per ADR 0001.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import agentm_config
import harness_memory

# Importing the concrete backends self-registers them into the seam's default
# registry (their modules call ``registry.register`` at import). Selection is the
# wiring layer that depends on the backends; the seam itself never does.
import storage_device_local
import storage_vault
from storage_seam import StorageBackend, registry

__all__ = ["select_backend", "choose_protocol"]

_DEVICE_LOCAL = storage_device_local.PROTOCOL  # "device-local"
_VAULT = storage_vault.PROTOCOL  # "vault"


def _configured_backend(install_prefix: Optional[Path] = None) -> Optional[str]:
    """Read the explicit `storage.backend` value from config, or None if unset.

    Reuses `agentm_config`'s prefix resolution + reader so the source of truth is
    the same file the `--storage-backend` setter writes. Returns the stripped name
    when present + non-empty; None otherwise (no explicit selection).
    """
    prefix = agentm_config._resolve_install_prefix(
        str(install_prefix) if install_prefix is not None else None
    )
    config = agentm_config._read_config(prefix) or {}
    raw = config.get(agentm_config._STORAGE_BACKEND_KEY)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def choose_protocol(
    *,
    install_prefix: Optional[Path] = None,
    vault_root: Optional[Path] = None,
) -> str:
    """Resolve the storage backend *protocol name* per the V5-1 chain (no instantiation).

    `vault_root` is the already-resolved `harness_memory.vault_path()` (passed in so
    the chain stays pure / testable). The name "choose" is deliberate — never a
    seam verb — so this resolver is never mistaken for a backend.
    """
    explicit = _configured_backend(install_prefix)
    if explicit:
        return explicit
    if vault_root is not None:
        return _VAULT
    return _DEVICE_LOCAL


def select_backend(
    *,
    install_prefix: Optional[Path] = None,
    device_local_root: Optional[Path | str] = None,
    vault_lock_root: Optional[Path | str] = None,
) -> StorageBackend:
    """Return the selected, instantiated `StorageBackend` for this install.

    The happy path (part-5 task 1): a fresh install resolves `device-local`; an
    install carrying a `vault_path` resolves the built-in `vault` seeded from
    `harness_memory.vault_path()` — byte-identical recall, zero re-setup.

    Injection points keep tests off the operator's real home / cache / vault:
    `device_local_root` (the device-local markdown root; None → `~/.agentm/memory`)
    and `vault_lock_root` (the `vault_mutex` lock base; None → `~/.cache/agentm/locks`).
    `install_prefix` overrides where the explicit `storage.backend` key is read from
    (None → `$AGENTM_INSTALL_PREFIX` → `~/.claude`).
    """
    vault_root = harness_memory.vault_path()
    protocol = choose_protocol(install_prefix=install_prefix, vault_root=vault_root)

    backend_cls = registry.get(protocol)
    if backend_cls is None:
        # Fail loud, never demote. The configured backend's plugin is not
        # installed. Part-5 task 3 refines this into the dedicated
        # StorageSelectionError naming the exact missing plugin, and adds the
        # negative test proving there is no silent device-local fall-back.
        raise RuntimeError(
            f"storage backend {protocol!r} is configured but not registered"
        )

    if protocol == _VAULT:
        if vault_root is None:
            # `vault` selected but no vault_path to seed it — a configuration
            # error, surfaced loudly rather than guessed at. (V5-7 owns the full
            # per-plugin config; V5-1 seeds vault from vault_path() only.)
            raise RuntimeError(
                "storage backend 'vault' is selected but no vault_path is "
                "configured to seed it"
            )
        return backend_cls(vault_root, lock_root=vault_lock_root)
    if protocol == _DEVICE_LOCAL:
        return backend_cls(device_local_root)
    # An explicitly-configured, registered third-party backend: the V5-1 minimal
    # contract is a no-arg constructor (the backend owns its own defaults).
    # Rich per-plugin construction config is V5-7.
    return backend_cls()
