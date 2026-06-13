#!/usr/bin/env python3
"""The device-local storage backend — plain markdown under ~/.agentm/memory (V5-1 part 2/5).

The fresh-install default: the moment no storage plugin is selected, the memory
engine's state lives on-device as plain markdown under ``~/.agentm/memory/`` —
user-owned, no vault, no Drive, no service. This is the first *concrete*
``StorageBackend``. It implements the part-1 seam verbs against the filesystem
and registers under the ``device-local`` protocol name in the seam's default
registry. It is the clean, reusable memory store the whole V5 cleave exists to
produce, and the first backend the conformance suite (part 3) will run against.

Design calls this module encodes (see the parent design, ``Status: final``):

  - **Bare markdown is the floor.** A user-owned directory of ``.md`` files — no
    service, daemon, or embedded database. A database on a synced path is a known
    corruption pattern; a database is something a *plugin* may offer, never the
    kernel default.
  - **The write path composes V5-0.** ``write`` routes through
    ``vault_lock.atomic_write`` (temp + fsync + rename) rather than reinventing
    crash-safety, and never open-and-truncates the target. Device-local is
    single-machine, so it needs none of the ``vault_mutex`` / content-hash CAS
    stack the synced vault backend (part 4) layers on — just the atomic file swap.
  - **Single-machine simple.** ``capabilities`` reports the conservative floor:
    no concurrent writers, no external sync, no conflict files, no encryption.
    The conflict strategy is the seam's inherited ``"none"`` — last write wins,
    because on one machine there is nothing to reconcile (part 4's vault overrides
    it to ``"whole-file"``).
  - **No sync / derived-index / conflict-merger machinery.** Those ride with the
    vault backend (part 4) and the V6 index — device-local has no conflicts by
    construction.

Locators map to paths by joining the backend-relative key under the root. The
``Locator`` type already guarantees the key is normalized and carries no ``..``
(it raises ``InvalidLocatorError`` at construction), so a key can never escape
the root. Internal ``pathlib.Path`` use is an implementation detail — every verb
returns the seam's ``Locator`` / ``Info`` types, never a ``Path`` (the
``check-storage-seam-no-path-leak`` gate enforces it statically).

The ``~/.agentm/memory/`` path is deliberate and load-bearing: it is the home the
operator-locked ``AgentMemory → Agent`` rename (V5-3) reconciles to, so the name
stays exactly as designed here.
"""
from __future__ import annotations

from pathlib import Path

from storage_seam import Capabilities, Info, Locator, StorageBackend, registry
from vault_lock import atomic_write

__all__ = ["DeviceLocalBackend", "PROTOCOL"]

#: The protocol name this backend registers under — the fresh-install default.
PROTOCOL = "device-local"

#: The on-device root, relative to the user's home: ``~/.agentm/memory``. Kept
#: exactly as designed so the V5-3 ``AgentMemory → Agent`` rename reconciles here.
_ROOT_PARTS = (".agentm", "memory")


def _default_root() -> Path:
    """``~/.agentm/memory`` — resolved against ``Path.home()`` at call time."""
    return Path.home().joinpath(*_ROOT_PARTS)


class DeviceLocalBackend(StorageBackend):
    """Plain-markdown storage under ``~/.agentm/memory`` — the fresh-install default.

    Implements the seven seam verbs against the local filesystem. The root is
    created when the backend is constructed (that is its "first use"), so the
    root locator always resolves to a real directory. ``root`` is injectable so
    tests never touch the operator's real home directory.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root is not None else _default_root()
        # Created on first use — constructing the backend is that first use.
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """The on-device root directory backing this instance."""
        return self._root

    def _path(self, locator: Locator) -> Path:
        # The Locator key is normalized and root-confined (no '..', no leading
        # slash — Locator raises InvalidLocatorError otherwise), so joining its
        # parts under the root can never escape the root. Internal Path use only;
        # never returned across the seam.
        return self._root.joinpath(*locator.parts)

    # -- capabilities ---------------------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        # The single-machine floor: nothing concurrent, nothing synced. A
        # positive design statement, not a fall-through to the defaults.
        return Capabilities(
            concurrent_writers=False,
            conflict_files=False,
            encryption=False,
            sync=False,
        )

    # -- the seven verbs ------------------------------------------------------

    def resolve(self, *parts: str) -> Locator:
        return Locator("/".join(str(p) for p in parts))

    def read(self, locator: Locator) -> str:
        # read_bytes + utf-8 decode (not read_text) so content round-trips
        # byte-for-byte with atomic_write's byte-mode writer — no newline
        # translation. A missing path raises FileNotFoundError natively.
        return self._path(locator).read_bytes().decode("utf-8")

    def write(self, locator: Locator, content: str) -> Locator:
        # Compose V5-0: temp + fsync + rename, parent dirs created if absent.
        # Never an open-and-truncate, so a crash can't leave a torn file.
        atomic_write(self._path(locator), content)
        return locator

    def list(self, locator: Locator) -> list[Locator]:
        p = self._path(locator)
        if not p.is_dir():
            return []  # absent or a file: no children (part 3 pins the contract)
        return sorted(
            (locator.child(child.name) for child in p.iterdir()),
            key=lambda loc: loc.key,
        )

    def exists(self, locator: Locator) -> bool:
        return self._path(locator).exists()

    def info(self, locator: Locator) -> Info:
        p = self._path(locator)
        st = p.stat()  # raises FileNotFoundError if absent — the contract
        is_dir = p.is_dir()
        return Info(
            locator=locator,
            is_dir=is_dir,
            size=0 if is_dir else st.st_size,
            mtime=st.st_mtime,
        )

    def mkdir(self, locator: Locator) -> Locator:
        self._path(locator).mkdir(parents=True, exist_ok=True)  # idempotent
        return locator


# Register into the seam's process-wide default registry under the protocol name
# selection (part 5) looks up. Runs once, on import — storing the class, not an
# instance (selection instantiates the chosen backend), so import touches no home.
registry.register(PROTOCOL, DeviceLocalBackend)
