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
import os
from pathlib import Path
from typing import NamedTuple, Optional

import agentm_config
import harness_memory

# Importing the concrete backends self-registers them into the seam's default
# registry (their modules call ``registry.register`` at import). Selection is the
# wiring layer that depends on the backends; the seam itself never does.
import storage_device_local
import storage_vault
from storage_seam import StorageBackend, registry

__all__ = [
    "select_backend",
    "choose_protocol",
    "StorageSelectionError",
    "storage_preview",
    "StoragePreview",
]

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
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
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


# --- doctor preview (part-5 task 4) -----------------------------------------
#
# The `doctor` skill's storage check invokes this module as a script
# (`python3 scripts/backend_selection.py --doctor`) and maps the printed
# `[OK]`/`[WARN]`/`[FAIL]` row. Like the `check-worktree-slug` probe, it shares
# the resolver's own code path — `_install_plugin_message` + the resolution chain
# — so the preview and the runtime fail-loud refusal can never drift.


class StoragePreview(NamedTuple):
    """A read-only `doctor` snapshot of the selected storage backend — no mutation, no raise.

    `status` is one of `"ok"` / `"warn"` / `"fail"`; `line` is the doctor-format
    summary printed verbatim; `protocol` is the resolved backend name, or `None`
    when the config could not even be read.
    """

    status: str
    protocol: Optional[str]
    line: str


def _root_is_writable(root: Path) -> bool:
    """True if `root` (or its nearest existing ancestor) is writable — never creates it.

    The preview must not mutate: `DeviceLocalBackend` mkdirs its root on
    construction, so writability is probed by walking up to the nearest existing
    ancestor and testing `os.W_OK`, never by constructing the backend.
    """
    probe = root
    while not probe.exists():
        if probe.parent == probe:  # reached the filesystem root without finding one
            return False
        probe = probe.parent
    return os.access(probe, os.W_OK)


def storage_preview(
    *,
    install_prefix: Optional[Path] = None,
    device_local_root: Optional[Path | str] = None,
) -> StoragePreview:
    """Preview the selected storage backend for `doctor` — read-only, never raises, never mutates.

    Mirrors `select_backend`'s resolution but produces a *report* instead of an
    instance: it never constructs a backend (construction would mkdir / touch the
    operator's home) and it converts the fail-loud `StorageSelectionError` cases
    into a `"fail"` row carrying the **same** message the guard would raise (via
    the shared `_install_plugin_message`), so the preview and the runtime refusal
    can never drift. The `doctor` skill prints `.line` and maps `.status`.

    Status rows:
      - `"fail"` — config unreadable / non-string selection, the configured
        backend's plugin is not installed, or `vault` is selected with no
        `vault_path` (the engine will refuse at runtime — this previews it).
      - `"warn"` — `device-local` is selected but its root is not writable.
      - `"ok"`   — the selected backend is registered (+ device-local root writable).
    """
    # The explicit selection read can itself fail loud (corrupt config / non-string
    # value, part-5 task-3 hardening); surface it as a fail row rather than raising.
    try:
        explicit = _configured_backend(install_prefix)
    except StorageSelectionError as exc:
        return StoragePreview("fail", None, f"storage [FAIL] {exc}")

    vault_root = harness_memory.vault_path()
    if explicit:
        protocol, origin = explicit, "configured (storage.backend)"
    elif vault_root is not None:
        protocol, origin = _VAULT, "existing vault_path"
    else:
        protocol, origin = _DEVICE_LOCAL, "fresh-install default"

    if registry.get(protocol) is None:
        return StoragePreview(
            "fail", protocol, f"storage [FAIL] {_install_plugin_message(protocol)}"
        )

    if protocol == _VAULT:
        if vault_root is None:
            return StoragePreview(
                "fail",
                protocol,
                "storage [FAIL] storage backend 'vault' is selected but no "
                "vault_path is configured to seed it — set vault_path "
                "(agentm_config --vault-path <dir>) or change storage.backend.",
            )
        return StoragePreview(
            "ok",
            protocol,
            f"storage [OK] selected backend 'vault' ({origin}) — registered; "
            f"seeded from {vault_root}",
        )

    if protocol == _DEVICE_LOCAL:
        root = (
            Path(device_local_root)
            if device_local_root is not None
            else storage_device_local._default_root()
        )
        if not _root_is_writable(root):
            return StoragePreview(
                "warn",
                protocol,
                f"storage [WARN] selected backend 'device-local' ({origin}) — "
                f"registered, but root {root} is not writable",
            )
        return StoragePreview(
            "ok",
            protocol,
            f"storage [OK] selected backend 'device-local' ({origin}) — "
            f"registered; root {root} writable",
        )

    # A registered third-party backend (explicit, non-built-in): "registered" is
    # all the V5-1 preview asserts; rich per-plugin readiness is V5-7.
    return StoragePreview(
        "ok",
        protocol,
        f"storage [OK] selected backend {protocol!r} ({origin}) — registered",
    )


def _doctor_main(argv: Optional[list[str]] = None) -> int:
    """CLI entry for the `doctor` storage check: print the preview row, map status → exit code.

    Exit 1 only on a `"fail"` row (the engine will refuse at runtime); `"ok"` and
    `"warn"` exit 0 (a WARN is never a hard install failure). `doctor` reads the
    `[OK]`/`[WARN]`/`[FAIL]` token from the printed line for the per-row status.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="backend_selection.py",
        description="Read-only storage-backend preview for the doctor skill.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="print the selected-backend preview (doctor structural check)",
    )
    parser.parse_args(argv)  # `--doctor` is the only (default) mode
    preview = storage_preview()
    print(preview.line)
    return 1 if preview.status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(_doctor_main())
