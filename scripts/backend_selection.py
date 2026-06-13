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
per-plugin config model and capability-request *matching* are V5-7. The
**fail-loud guard** (part-5 task 3) refuses rather than demotes whenever the
configured backend can't be produced — an unregistered plugin
(`registry.get → None`), a `vault` selection with no `vault_path`, or an explicit
selection trapped in a corrupt/unreadable config — always raising
`StorageSelectionError`, never a silent fall-back to `device-local`.

Stdlib-only per ADR 0001.
"""
from __future__ import annotations

import json
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

__all__ = ["select_backend", "choose_protocol", "StorageSelectionError"]

_DEVICE_LOCAL = storage_device_local.PROTOCOL  # "device-local"
_VAULT = storage_vault.PROTOCOL  # "vault"


class StorageSelectionError(RuntimeError):
    """Selection refused: the configured backend can't be produced — fail loud, never demote.

    Raised when `storage.backend` names a backend whose plugin is not installed
    (`registry.get → None`, the part-1 resolve-as-absent signal), or when the
    `vault` backend is selected with no `vault_path` to seed it. This is the one
    failure the engine must NEVER paper over with a silent `device-local`
    fall-back: a silent demotion is exactly what mis-writes or orphans the vault.
    The caller (the future engine cutover; `doctor`, task 4) catches this to
    surface the install-the-plugin message rather than proceeding.
    """


def _install_plugin_message(protocol: str) -> str:
    """The fail-loud message naming the exact missing backend + how to resolve it.

    Reused by `doctor` (task 4) so its preview is byte-identical to what the guard
    raises. Names the currently-registered backends so the operator sees the valid
    alternatives.
    """
    installed = ", ".join(registry.protocols()) or "(none)"
    return (
        f"storage backend {protocol!r} is configured (storage.backend) but no "
        f"installed plugin registers it. Install the plugin that provides the "
        f"{protocol!r} backend, or set storage.backend to an installed backend "
        f"(currently registered: {installed})."
    )


def _configured_backend(install_prefix: Optional[Path] = None) -> Optional[str]:
    """Read the explicit `storage.backend` value from config — fail loud, never demote.

    Returns the stripped backend name when an explicit, valid selection is present;
    `None` only when there is *genuinely no* explicit selection. It deliberately
    does **not** reuse `agentm_config._read_config`, whose tolerant contract
    collapses "file missing" and "file present but unreadable" into the same `None`
    — that collapse is a silent-demotion hole the never-demote invariant forbids:
    an explicitly-configured backend trapped in a corrupt config would be dropped
    and the chain would fall back to `device-local`, mis-writing or orphaning the
    vault. Instead it distinguishes:

      - config file absent              → `None` (genuinely fresh — continue the chain)
      - file present but unparseable    → raise `StorageSelectionError` (never guess)
      - file present, key absent        → `None` (readable config, no explicit pick)
      - key present, valid non-empty str → the stripped name
      - key present but non-string/empty → raise `StorageSelectionError` (an explicit
                                           selection we can't honor, never demoted —
                                           the setter refuses empty and `--unset`
                                           removes the key, so neither is a
                                           legitimate write).
    """
    prefix = agentm_config._resolve_install_prefix(
        str(install_prefix) if install_prefix is not None else None
    )
    path = agentm_config._config_path(prefix)
    if not path.is_file():
        return None  # genuinely fresh — no config file to drop a selection from.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise StorageSelectionError(
            f"the install config at {path} exists but could not be read ({exc}); "
            f"refusing to guess a backend. Fix or remove the file — a silent "
            f"fall-back to device-local could mis-write or orphan the vault."
        )
    if not isinstance(data, dict):
        raise StorageSelectionError(
            f"the install config at {path} is not a JSON object; refusing to guess "
            f"a backend rather than risk a silent device-local fall-back."
        )
    if agentm_config._STORAGE_BACKEND_KEY not in data:
        return None  # readable config, no explicit selection — continue the chain.
    raw = data[agentm_config._STORAGE_BACKEND_KEY]
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raise StorageSelectionError(
        f"storage.backend in {path} is set but is not a non-empty string "
        f"(got {raw!r}); refusing to demote to device-local. Set a valid backend "
        f"name (agentm_config --storage-backend <name>) or remove it "
        f"(agentm_config --unset storage.backend)."
    )


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
        # installed (registry.get → None, the part-1 resolve-as-absent signal).
        # NOT a silent fall-back to device-local — that demotion is the single
        # failure that mis-writes or orphans the vault. (Bites in earnest after
        # V5-3 deletes the built-in vault backend; until then both built-ins
        # register at import, so this fires only for a genuinely-unregistered
        # configured name.)
        raise StorageSelectionError(_install_plugin_message(protocol))

    if protocol == _VAULT:
        if vault_root is None:
            # `vault` selected but no vault_path to seed it — a configuration
            # error, surfaced loudly rather than guessed at (same never-demote
            # family). V5-7 owns the full per-plugin config; V5-1 seeds vault
            # from vault_path() only.
            raise StorageSelectionError(
                "storage backend 'vault' is selected but no vault_path is "
                "configured to seed it — set vault_path (agentm_config "
                "--vault-path <dir>) or change storage.backend."
            )
        return backend_cls(vault_root, lock_root=vault_lock_root)
    if protocol == _DEVICE_LOCAL:
        return backend_cls(device_local_root)
    # An explicitly-configured, registered third-party backend: the V5-1 minimal
    # contract is a no-arg constructor (the backend owns its own defaults).
    # Rich per-plugin construction config is V5-7.
    return backend_cls()
