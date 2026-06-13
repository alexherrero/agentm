#!/usr/bin/env python3
"""The memory↔storage seam — verbs, locator, backend contract (V5-1 part 1/5).

The small interface the memory engine calls *instead of touching files directly*.
A backend implements the verbs; the engine consumes the seam's own ``Locator``
type and so learns no filesystem assumption. This is the contract every
downstream storage item builds against — the device-local backend (part 2), the
conformance suite (part 3), the vault wrap (part 4), selection + fail-loud
(part 5) — which is why it ships small and high-care.

Scope of *this* part — **contract only**:

  - the seven verbs as an abstract ``StorageBackend`` (no concrete backend ships
    here: device-local is part 2, the vault wrap is part 4);
  - the seam's own ``Locator`` value type (opaque, backend-relative — never a
    ``pathlib.Path``, so an FS assumption can't cross the seam to the engine);
  - the ``Info`` metadata record (carries ``mtime`` — the ``changed-since``
    granularity, lean for v1) and the four-boolean ``Capabilities`` descriptor a
    backend declares.

Locked design calls this module encodes (see the parent design, ``Status:
final``):

  - **Verbs + a named registry, hand-rolled.** ``resolve``/``read``/``write``/
    ``list``/``exists`` are the five canonical verbs; ``info``/``mkdir`` the two
    ergonomic ones. The vocabulary mirrors fsspec's method names and its
    named-protocol registry pattern, but imports neither fsspec nor any DB —
    bare markdown is the floor. (The registry itself is task 2.)
  - **The write path composes V5-0.** A *filesystem* backend's ``write`` routes
    through ``vault_lock`` (``atomic_write`` + content-hash CAS + ``vault_mutex``)
    and never reinvents write-safety. The abstract contract here only declares
    the shape; the composition lands with the concrete backends (parts 2 / 4).
  - **The public API stays frozen (DC-7).** ``recall``/``reflect``/``save``/
    ``evolve`` and the five memory hooks are byte-unchanged; this seam sits
    strictly *below* that surface and never widens it.

Text is the v1 currency (``read`` → ``str``, ``write`` takes ``str``) because the
engine's state is markdown; a bytes channel is a deliberate future extension, not
a v1 obligation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

__all__ = [
    "InvalidLocatorError",
    "normalize_key",
    "Locator",
    "Info",
    "Capabilities",
    "StorageBackend",
]


class InvalidLocatorError(ValueError):
    """A locator key escaped or malformed its backend-relative namespace.

    A ``ValueError`` subclass — it signals a *caller bug* (an unsafe key), kept
    distinct from the absent-data degrade a backend reports for a missing read.
    """


def normalize_key(key: object) -> str:
    """Normalize a locator key to a backend-relative POSIX-style string.

    Splits on ``/``; drops empty and ``.`` segments; rejoins with ``/``. A leading
    slash is silently relativized (the empty leading segment is dropped) — a
    locator is *always* backend-relative, so an absolute-looking key can never
    address outside the backend root. A ``..`` segment is **rejected** outright
    (``InvalidLocatorError``): the seam has no upward-traversal semantics, which
    is the safety property that keeps a key from escaping the backend root. The
    root locator is the empty string.
    """
    out: list[str] = []
    for seg in str(key).split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise InvalidLocatorError(
                f"'..' is not allowed in a storage locator: {key!r}"
            )
        out.append(seg)
    return "/".join(out)


@dataclass(frozen=True)
class Locator:
    """A place in *some* backend's namespace — the seam's own opaque locator.

    Deliberately **not** a ``pathlib.Path``: it carries a normalized,
    backend-relative key and exposes only namespace operations (``child``,
    ``name``, ``parts``), never filesystem I/O. All reading/writing goes through
    the backend verbs, so the engine — which only ever holds ``Locator`` values —
    learns no filesystem assumption. Frozen and hashable, so a backend may use it
    as a dict key (the in-memory conformance fixture does).
    """

    key: str = ""

    def __post_init__(self) -> None:
        # Normalize at construction so every Locator — however built (direct,
        # via resolve, via child) — is canonical and root-confined.
        object.__setattr__(self, "key", normalize_key(self.key))

    @property
    def parts(self) -> tuple[str, ...]:
        """The key's segments; ``()`` for the root locator."""
        return tuple(self.key.split("/")) if self.key else ()

    @property
    def name(self) -> str:
        """The final segment; ``""`` for the root locator."""
        parts = self.parts
        return parts[-1] if parts else ""

    def child(self, *parts: str) -> "Locator":
        """Derive a sub-locator by appending ``parts`` (each normalized, no escape)."""
        return Locator("/".join((self.key, *(str(p) for p in parts))))

    def __str__(self) -> str:
        return self.key


@dataclass(frozen=True)
class Info:
    """Metadata about a locator — the ``info`` verb's return.

    ``mtime`` is the basis for ``changed-since`` (the incremental feed named in
    task 3): epoch seconds, the *lean* granularity chosen for v1 over a
    content-hash log. ``size`` is bytes (``0`` for a directory).
    """

    locator: Locator
    is_dir: bool
    size: int
    mtime: float


@dataclass(frozen=True)
class Capabilities:
    """What a backend can promise — the per-backend descriptor a backend declares.

    Four booleans, all defaulting to the conservative floor (``False``); a
    dataclass so the set can grow (the design's "four booleans + room to add").
    Selection and fail-loud (part 5) read these; the contract only defines them.

      - ``concurrent_writers`` — safe under more than one writer process.
      - ``conflict_files`` — the backend may surface conflict copies (e.g. a
        sync layer's "(conflicted copy)" files) the engine must tolerate.
      - ``encryption`` — content is encrypted at rest by the backend.
      - ``sync`` — the backend's tree is replicated by an external sync layer
        (the property that makes a SQLite index on it a corruption pattern —
        why the local index is designated never-sync in task 3).
    """

    concurrent_writers: bool = False
    conflict_files: bool = False
    encryption: bool = False
    sync: bool = False


class StorageBackend(ABC):
    """The storage interface the engine calls instead of touching files directly.

    A backend registers under a protocol name (``device-local``, ``vault`` —
    task 2) and implements these verbs. Concrete backends are out of scope for
    this part; this is the abstract contract they conform to. Verbs operate on
    and return the seam's own ``Locator`` — **never** ``pathlib.Path`` — which the
    ``check-storage-seam-no-path-leak`` gate enforces statically.
    """

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities:
        """What this backend promises — see :class:`Capabilities`."""

    @abstractmethod
    def resolve(self, *parts: str) -> Locator:
        """Make a backend-relative locator from path ``parts``.

        ``resolve()`` with no parts is the backend root. The naming verb: it
        produces the seam's locator type, the engine's only handle on storage.
        """

    @abstractmethod
    def read(self, locator: Locator) -> str:
        """Return the text content at ``locator``.

        Raises ``FileNotFoundError`` if nothing exists there — distinct from
        ``InvalidLocatorError`` (a malformed key, a caller bug).
        """

    @abstractmethod
    def write(self, locator: Locator, content: str) -> Locator:
        """Write text ``content`` at ``locator``; return the locator written.

        A filesystem backend composes V5-0 here (``atomic_write`` + content-hash
        CAS + ``vault_mutex``) rather than reinventing write-safety — see the
        module docstring. The contract is: the write is durable and atomic, and
        the returned locator round-trips through ``read``.
        """

    @abstractmethod
    def list(self, locator: Locator) -> list[Locator]:
        """List the immediate children of ``locator`` as locators.

        An empty directory lists ``[]``; a non-existent directory is the
        backend's choice of empty-or-raise, pinned by the conformance suite
        (part 3). Returns the seam's locators, never paths.
        """

    @abstractmethod
    def exists(self, locator: Locator) -> bool:
        """Whether anything (file or directory) is present at ``locator``."""

    @abstractmethod
    def info(self, locator: Locator) -> Info:
        """Return :class:`Info` for ``locator`` (raises if absent).

        Carries ``mtime`` — the granularity ``changed-since`` reads (task 3).
        """

    @abstractmethod
    def mkdir(self, locator: Locator) -> Locator:
        """Ensure a directory exists at ``locator``; return it. Idempotent."""
