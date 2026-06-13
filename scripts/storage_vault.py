#!/usr/bin/env python3
"""The vault storage backend — today's Obsidian/GDrive vault, behind the seam (V5-1 part 4/5).

The **transitional** backend: it wraps-and-moves the existing vault write path
(``harness_memory.write_state_file``'s vault branch + the V5-0
``vault_mutex``/``content_hash`` CAS/``atomic_write`` routing) *behind* the part-1
seam, so the operator's live vault keeps working — byte-unchanged, no data moved —
now reached through ``resolve``/``read``/``write``/``list``/``exists``/``info``/
``mkdir`` instead of by direct file access. It registers under the ``vault``
protocol name in the seam's default registry (mirroring how ``device-local``
registers at import). It exists so the vault survives expand and parallel-run; it
is **deleted in V5-3** once the V5-2 vault *plugin* passes the same conformance
suite (part 3). Do not let it absorb new vault behavior.

Design calls this module encodes (see the parent design, ``Status: final``):

  - **Wrap, not rewrite.** Reuse the proven V5-0 write primitives rather than
    reimplementing write-safety on live-data code — the lowest-regression path at
    the most dangerous moment of V5-1 (touching the operator's real store).
  - **The write path composes the FULL V5-0 stack** — ``vault_mutex`` +
    content-hash CAS + ``atomic_write``. This is the load-bearing difference from
    ``device-local`` (part 2), whose ``write`` composes ``atomic_write`` **only**
    (single machine — nothing concurrent, nothing synced). The vault is the
    multi-writer / GDrive-synced case the full stack exists for:

      * ``vault_mutex`` — the one per-vault advisory mutex (on a LOCAL,
        non-synced lockdir) serializes the *agentm fleet* — N≥2 concurrent agent
        sessions — so two writers never collide on the shared ``<target>.tmp``.
      * **content-hash CAS** — guards against a *non-mutex* writer: GDrive's sync
        daemon (or another device's sync) landing a remote change on the file
        between our pre-write read and the rename. The mutex can't see that
        writer — it lives outside the fleet — so the CAS re-read right before the
        atomic land refuses to silently clobber it (``ConcurrentModificationError``).
      * ``atomic_write`` — the crash-safe temp→fsync→rename; never an
        open-and-truncate, so an interrupted write leaves prior bytes intact.

  - **A synced, multi-writer capability profile + the whole-file conflict policy.**
    Unlike device-local's all-False floor, the vault declares
    ``concurrent_writers`` (the mutex makes N writers safe), ``sync`` (GDrive
    replicates the tree), and ``conflict_files`` (DriveFS surfaces
    "(conflicted copy)" files the engine must tolerate). It overrides the seam's
    ``conflict_strategy`` floor (``"none"``) to ``"whole-file"`` — *naming* the
    GDrive whole-file reconcile (DriveFS conflict files surfaced for
    operator-by-hand resolution by the existing ``detect_conflict_files`` +
    ``conflict-merger`` machinery), the policy part 5's selection reads. It is a
    name, not an executor — the backend ships no merge machinery; device-local
    stays ``"none"``.

  - **Depends only on ``vault_lock``, never the engine.** The seam sits strictly
    *below* the public memory API (DC-7); a storage backend importing
    ``harness_memory`` (the engine module) would invert that layering. So the CAS
    is composed here from ``vault_lock``'s own primitives (``content_hash`` +
    ``atomic_write``, the same pieces ``harness_memory.safe_write_replace_style``
    is built from) rather than by importing that engine helper. The error
    vocabulary is single-sourced: ``ConcurrentModificationError`` is imported from
    its canonical home, ``vault_lock``.

Locators map to paths exactly as ``write_state_file`` maps them: the backend is
rooted at the resolved per-project vault path (``<vault>/projects/<slug>``), and a
locator such as ``_harness/PLAN.md`` joins under it to
``<vault>/projects/<slug>/_harness/PLAN.md``. ``Locator`` guarantees the key is
normalized and carries no ``..`` (it raises ``InvalidLocatorError`` at
construction), so a key can never escape the root. Internal ``pathlib.Path`` use
is an implementation detail — every verb returns the seam's ``Locator`` / ``Info``
types, never a ``Path`` (the ``check-storage-seam-no-path-leak`` gate enforces it
statically over this ``storage_*.py`` module).

Unlike ``device-local`` (whose root is the fixed ``~/.agentm/memory``), the vault
backend has no universal default root — the per-project vault path is resolved at
runtime — so ``root`` is a required constructor argument. Part 5's selection
supplies the resolved path; the part-3 conformance suite and the composition
tests supply a throwaway scratch root (never the operator's live vault).
"""
from __future__ import annotations

from pathlib import Path

from storage_seam import Capabilities, Info, Locator, StorageBackend, registry
from vault_lock import (
    ConcurrentModificationError,
    atomic_write,
    content_hash,
    vault_mutex,
)

__all__ = ["VaultBackend", "PROTOCOL"]

#: The protocol name this backend registers under — the transitional vault wrap.
PROTOCOL = "vault"


class VaultBackend(StorageBackend):
    """Today's Obsidian/GDrive vault, reached through the seam — the transitional wrap.

    Implements the seven seam verbs against the vault's filesystem layout, with
    ``write`` composing the full V5-0 stack (``vault_mutex`` + content-hash CAS +
    ``atomic_write``). The root is the resolved per-project vault path; it is
    created when the backend is constructed (its "first use", idempotent), so the
    root locator always resolves to a real directory. ``root`` is required (the
    vault has no universal default) and ``lock_root`` is injectable so tests never
    pollute the real ``~/.cache`` lock base nor touch the operator's live vault.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        lock_root: Path | str | None = None,
    ) -> None:
        self._root = Path(root)
        # The local lock base for vault_mutex. None → vault_lock's default
        # (~/.cache/agentm/locks); tests inject a temp dir so the real cache and
        # the operator's vault are never touched.
        self._lock_root = Path(lock_root) if lock_root is not None else None
        # Created on first use — constructing the backend is that first use.
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """The resolved vault root directory backing this instance."""
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
        # The synced, multi-writer profile — the positive contrast to
        # device-local's all-False floor. The mutex makes N writers safe
        # (concurrent_writers); GDrive replicates the tree (sync) and surfaces
        # "(conflicted copy)" files the engine must tolerate (conflict_files);
        # the backend does not encrypt at rest (encryption False).
        return Capabilities(
            concurrent_writers=True,
            conflict_files=True,
            encryption=False,
            sync=True,
        )

    @property
    def conflict_strategy(self) -> str:
        # Override the seam's "none" floor with the GDrive whole-file strategy —
        # the synced, multi-writer reality device-local never faces (it stays
        # "none": one machine, nothing to reconcile). When Drive sync diverges
        # (two devices write the same file while offline), DriveFS materializes a
        # "(conflicted copy)" sibling rather than losing a write; the
        # reconciliation unit is the *whole file*. The existing conflict
        # machinery — harness_memory.detect_conflict_files + the
        # `conflict-merger` SessionStart hook — surfaces each conflict/base pair
        # for operator-by-hand resolution; resolution stays operator judgment,
        # never a line-level auto-merge (that would be a future CRDT strategy).
        # This is a *named* policy the part-5 selection reads, NOT an executor:
        # the backend ships no merge machinery of its own.
        return "whole-file"

    # -- the seven verbs ------------------------------------------------------

    def resolve(self, *parts: str) -> Locator:
        return Locator("/".join(str(p) for p in parts))

    def read(self, locator: Locator) -> str:
        # read_bytes + utf-8 decode (not read_text) so content round-trips
        # byte-for-byte with atomic_write's byte-mode writer — no newline
        # translation. A missing path raises FileNotFoundError natively.
        return self._path(locator).read_bytes().decode("utf-8")

    def write(self, locator: Locator, content: str) -> Locator:
        # The FULL V5-0 stack (the load-bearing difference from device-local):
        # serialize fleet-local writers on the one per-vault advisory mutex, then
        # land via a CAS-guarded atomic_write. The mutex serializes agentm
        # sessions against each other; the CAS catches a *non-mutex* writer
        # (GDrive sync / another device) landing between the pre-write read and
        # the rename — the cross-device hazard device-local does not have.
        target = self._path(locator)
        with vault_mutex(self._root, lock_root=self._lock_root):
            expected = content_hash(target.read_bytes()) if target.exists() else None
            self._cas_atomic_write(target, content, expected_hash=expected)
        return locator

    def _cas_atomic_write(
        self, target: Path, content: str, *, expected_hash: str | None
    ) -> None:
        """Content-hash CAS + atomic_write — the V5-0 ``safe_write_replace_style``
        discipline, composed here from ``vault_lock`` primitives so the seam need
        not import the engine module.

        If ``expected_hash`` is set, re-read the target right before the land and
        refuse the write if its content no longer matches — a non-mutex writer
        (Drive sync / another device) wrote it since the pre-write read. Then land
        atomically (temp→fsync→rename), creating parent dirs if absent.
        """
        if expected_hash is not None:
            current = content_hash(target.read_bytes()) if target.exists() else None
            if current != expected_hash:
                raise ConcurrentModificationError(
                    f"{target.name} changed under the vault mutex since its "
                    f"pre-write read (expected hash={expected_hash[:12]}…, "
                    f"actual={(current or 'absent')[:12]}…) — a non-mutex writer "
                    f"(Drive sync or another device) wrote it. Re-read and retry."
                )
        atomic_write(target, content)

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
# instance (selection instantiates the chosen backend with a resolved root), so
# import touches no vault.
registry.register(PROTOCOL, VaultBackend)
