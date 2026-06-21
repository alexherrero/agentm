#!/usr/bin/env python3
"""Standalone VaultBackend stub for tests — post-V5-3 replacement for kernel storage_vault.

The kernel `storage_vault.py` was deleted in V5-3 (v5.5.0); the real vault backend
now lives in the crickets `obsidian-vault` plugin.  Tests that need a VaultBackend
instance (routing checks, harness-memory tests, repo-registry tests, etc.) import
from here instead of the deleted kernel module.

Does NOT register itself in the seam registry (tests that need registry-based
discovery use the `_install_plugin_fixture` helper in `test_backend_selection.py`,
which writes a correctly-named `storage_vault.py` to a temp dir).
"""
from __future__ import annotations

from pathlib import Path

from storage_seam import Capabilities, Info, Locator, StorageBackend
from vault_lock import (
    ConcurrentModificationError,
    atomic_write,
    content_hash,
    vault_mutex,
)

PROTOCOL = "vault"


class VaultBackend(StorageBackend):
    """Minimal VaultBackend for tests — same contract as the deleted kernel module."""

    def __init__(
        self,
        root: Path | str,
        *,
        lock_root: Path | str | None = None,
    ) -> None:
        self._root = Path(root)
        self._lock_root = Path(lock_root) if lock_root is not None else None
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            concurrent_writers=True,
            conflict_files=True,
            encryption=False,
            sync=True,
        )

    @property
    def conflict_strategy(self) -> str:
        return "whole-file"

    def _path(self, locator: Locator) -> Path:
        return self._root.joinpath(*locator.parts)

    def resolve(self, *parts: str) -> Locator:
        return Locator("/".join(str(p) for p in parts))

    def read(self, locator: Locator) -> str:
        return self._path(locator).read_bytes().decode("utf-8")

    def write(self, locator: Locator, content: str) -> Locator:
        target = self._path(locator)
        with vault_mutex(self._root, lock_root=self._lock_root):
            expected = content_hash(target.read_bytes()) if target.exists() else None
            self._cas_atomic_write(target, content, expected_hash=expected)
        return locator

    def _cas_atomic_write(
        self, target: Path, content: str, *, expected_hash: str | None
    ) -> None:
        if expected_hash is not None:
            current = content_hash(target.read_bytes()) if target.exists() else None
            if current != expected_hash:
                raise ConcurrentModificationError(
                    f"{target.name} changed under the vault mutex"
                )
        atomic_write(target, content)

    def list(self, locator: Locator) -> list[Locator]:
        p = self._path(locator)
        if not p.is_dir():
            return []
        return sorted(
            (locator.child(child.name) for child in p.iterdir()),
            key=lambda loc: loc.key,
        )

    def exists(self, locator: Locator) -> bool:
        return self._path(locator).exists()

    def info(self, locator: Locator) -> Info:
        p = self._path(locator)
        st = p.stat()
        is_dir = p.is_dir()
        return Info(
            locator=locator,
            is_dir=is_dir,
            size=0 if is_dir else st.st_size,
            mtime=st.st_mtime,
        )

    def mkdir(self, locator: Locator) -> Locator:
        self._path(locator).mkdir(parents=True, exist_ok=True)
        return locator
