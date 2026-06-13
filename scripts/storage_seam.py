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
    backend declares;
  - the hand-rolled ``BackendRegistry`` (and the module-default ``registry``)
    backends register a protocol name with (``device-local``, ``vault``) and
    selection looks up — a miss reports *absent*, never raises (part 5 decides
    whether absence is fatal). This mirrors the seam's absent-vs-corrupt split:
    the registry reports absence; the caller fails loud on it;
  - the three-tier source/derived taxonomy (``Tier`` + ``TierLayout``): source
    (synced, authoritative) · shared-abstracts (derived, may sync) · local-index
    (derived, **never syncs**), and the two derived-tier ops ``reindex`` /
    ``changed_since`` (``DerivedMaintenance``) — **named here, built in V6**.
    This part designates the tiers and reserves the op names; it builds no index
    and promotes no abstract (the scope guard asserts as much).

Locked design calls this module encodes (see the parent design, ``Status:
final``):

  - **Verbs + a named registry, hand-rolled.** ``resolve``/``read``/``write``/
    ``list``/``exists`` are the five canonical verbs; ``info``/``mkdir`` the two
    ergonomic ones. The vocabulary mirrors fsspec's method names and its
    named-protocol registry pattern, but imports neither fsspec nor any DB —
    bare markdown is the floor.
  - **The write path composes V5-0.** A *filesystem* backend's ``write`` routes
    through ``vault_lock`` (``atomic_write`` + content-hash CAS + ``vault_mutex``)
    and never reinvents write-safety. The abstract contract here only declares
    the shape; the composition lands with the concrete backends (parts 2 / 4).
  - **Three tiers, one hard line.** source (synced, authoritative) · shared
    abstracts (derived, may sync) · local index (derived, **never syncs** — a
    replicated SQLite/vector index is a corruption pattern). All derived tiers
    are rebuildable from source; ``reindex``/``changed_since`` are *named now,
    built in V6*, so the V6 vector index lands on an affordance that already
    exists.
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
from enum import Enum

__all__ = [
    "InvalidLocatorError",
    "normalize_key",
    "Locator",
    "Info",
    "Capabilities",
    "StorageBackend",
    "ProtocolError",
    "BackendRegistry",
    "registry",
    "Tier",
    "TierLayout",
    "DerivedMaintenance",
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

    @property
    def conflict_strategy(self) -> str:
        """How this backend reconciles divergent concurrent writes — a *named* strategy.

        A **concrete** property (unlike ``capabilities``, which every backend must
        declare) defaulting to the conservative floor ``"none"`` — last write
        wins, because there is nothing to reconcile — mirroring how the
        :class:`Capabilities` booleans default to the safe floor. Distinct from
        those booleans: they describe *what the backend can promise*; this *names
        the policy* selection (part 5) reads to decide how to treat a conflict.

        The device-local backend (part 2) inherits ``"none"`` (single machine);
        the synced vault backend (part 4) overrides it to ``"whole-file"``; the
        slot is reserved for an iCloud numbered-suffix or a CRDT line-level
        strategy later (design §6). A trivial backend that does not override it
        inherits the floor, so the slot is always present and always answerable.
        """
        return "none"

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


class ProtocolError(ValueError):
    """A backend-registry protocol error — an empty or duplicate protocol name.

    A ``ValueError`` subclass: registering badly is a *programming* error,
    surfaced loudly. Distinct from a registry *miss*, which is not an error at
    all — a miss reports absence (``get`` → ``None``), and the caller decides
    whether that absence is fatal.
    """


class BackendRegistry:
    """A hand-rolled name→backend registry — the fsspec named-protocol pattern, mirrored.

    Backends register under a protocol name (``device-local``, ``vault``);
    selection (part 5) looks the name up to choose a backend. The contract that
    matters for fail-loud (part 5): **a miss is reported as absent, never
    raised** — ``get`` returns ``None`` and ``in`` returns ``False`` for an
    unregistered name. The registry reports absence; the caller turns absence
    into a loud failure if the configured backend doesn't exist. That split —
    absence here, fail-loud there — mirrors the seam's absent-vs-corrupt stance.

    Instances are independent (no shared global state), which is what lets the
    contract tests register into a fresh registry without leaking across tests;
    :data:`registry` is the process-wide default the real backends register into.
    """

    def __init__(self) -> None:
        self._backends: dict[str, type[StorageBackend]] = {}

    def register(
        self, protocol: str, backend: type[StorageBackend], *, clobber: bool = False
    ) -> None:
        """Register ``backend`` (a concrete ``StorageBackend`` subclass) under ``protocol``.

        Raises ``TypeError`` if ``backend`` is not a ``StorageBackend`` subclass
        (the abstract base itself is rejected — there's nothing to instantiate),
        and ``ProtocolError`` on an empty name or a duplicate registration
        (unless ``clobber=True``). Refusing a silent duplicate keeps one backend
        from shadowing another by accident.
        """
        if not (
            isinstance(backend, type)
            and issubclass(backend, StorageBackend)
            and backend is not StorageBackend
        ):
            raise TypeError(
                f"{backend!r} is not a concrete StorageBackend subclass"
            )
        if not protocol:
            raise ProtocolError("protocol name must be a non-empty string")
        if protocol in self._backends and not clobber:
            raise ProtocolError(
                f"protocol {protocol!r} is already registered "
                "(pass clobber=True to override)"
            )
        self._backends[protocol] = backend

    def get(self, protocol: str) -> type[StorageBackend] | None:
        """Return the backend registered under ``protocol``, or ``None`` if absent.

        The absent path is *not* an error — it is the signal part 5's selection
        reads to decide whether to fail loud.
        """
        return self._backends.get(protocol)

    def __contains__(self, protocol: object) -> bool:
        return protocol in self._backends

    def protocols(self) -> tuple[str, ...]:
        """The registered protocol names, sorted."""
        return tuple(sorted(self._backends))


#: The process-wide default registry the real backends register into (parts 2/4).
#: Contract tests use fresh ``BackendRegistry()`` instances to stay hermetic.
registry = BackendRegistry()


class Tier(Enum):
    """The three storage tiers — one hard line: the local index **never** syncs.

    The memory state splits across three tiers, distinguished by who owns the
    truth and whether an external sync layer may replicate the tree:

      - ``SOURCE`` — the synced, *authoritative* tier: the markdown the engine
        persists. Synced; never derived (it is the truth everything rebuilds from).
      - ``SHARED_ABSTRACTS`` — *derived* and portable: summaries/abstractions
        rebuilt from source that *may* sync between devices (they're useful
        everywhere, and rebuildable if a sync drops them).
      - ``LOCAL_INDEX`` — *derived* and **device-local**: the V6 vector/SQLite
        index. It must **never** sync — a replicated database file is a
        corruption pattern (see :class:`Capabilities` ``sync``), which is why it
        is pinned device-local here, before any index exists to mis-place.

    This taxonomy is *reserved* in this part: it designates the tiers and their
    sync policy so the V6 index lands on a contract that already exists. No
    index is built and no abstract is promoted here.
    """

    SOURCE = "source"
    SHARED_ABSTRACTS = "shared-abstracts"
    LOCAL_INDEX = "local-index"

    @property
    def syncs(self) -> bool:
        """Whether an external sync layer may replicate this tier's tree.

        ``True`` for ``SOURCE`` (the synced authority) and ``SHARED_ABSTRACTS``
        (derived but portable); **``False`` only for** ``LOCAL_INDEX`` — the one
        hard line. The never-sync property is what makes a device-local SQLite
        index safe.
        """
        return self is not Tier.LOCAL_INDEX

    @property
    def derived(self) -> bool:
        """Whether this tier is rebuildable from ``SOURCE`` (so it is never authoritative).

        ``True`` for both derived tiers; ``False`` only for ``SOURCE``, which is
        the truth ``reindex`` rebuilds the others from.
        """
        return self is not Tier.SOURCE


@dataclass(frozen=True)
class TierLayout:
    """Where each tier's root lives — three **distinct** roots, the local index pinned never-sync.

    Designates a root locator per tier; the defaults are tier-named placeholders
    a concrete backend (parts 2 / 4) overrides with its real roots — typically a
    synced-vault root for ``source``/``shared_abstracts`` and a device-local
    cache root for ``local_index``. The one invariant enforced here: the three
    roots are distinct (``source`` + ``shared_abstracts`` placement is separate
    from the ``local_index`` placement), so a derived tier can never overwrite
    the source it rebuilds from. No tree is created — this is placement, not I/O.
    """

    source: Locator = Locator("source")
    shared_abstracts: Locator = Locator("shared-abstracts")
    local_index: Locator = Locator("local-index")

    def __post_init__(self) -> None:
        roots = (self.source, self.shared_abstracts, self.local_index)
        if len({r.key for r in roots}) != len(roots):
            raise ValueError(
                "tier roots must be distinct (source/shared-abstracts/local-index): "
                + ", ".join(repr(str(r)) for r in roots)
            )

    @property
    def never_sync_root(self) -> Locator:
        """The local-index root — the one tree that must never be replicated by sync."""
        return self.local_index

    def root_for(self, tier: Tier) -> Locator:
        """The root locator designated for ``tier``."""
        return {
            Tier.SOURCE: self.source,
            Tier.SHARED_ABSTRACTS: self.shared_abstracts,
            Tier.LOCAL_INDEX: self.local_index,
        }[tier]


class DerivedMaintenance(ABC):
    """The two derived-tier operations — **named here, built in V6**.

    Maintaining a derived tier (``SHARED_ABSTRACTS`` or ``LOCAL_INDEX``) from
    ``SOURCE`` needs two ops; this part *reserves their names and shapes* so the
    V6 index plugs into a contract that already exists, but ships **no
    implementation** — there is deliberately no concrete subclass in this module
    (the scope guard asserts it). Both are abstract, so this class cannot be
    instantiated: the "named, not built" property is structural, not a comment.

      - ``reindex`` — rebuild a derived tier from ``SOURCE`` (the full rebuild).
      - ``changed_since`` — the *incremental feed*: the source locators changed
        after a watermark, keyed on :attr:`Info.mtime` (the lean v1 granularity
        locked in over a content-hash log), so an incremental reindex touches
        only what moved.
    """

    @abstractmethod
    def reindex(self, tier: Tier) -> None:
        """Rebuild ``tier`` (a derived tier) from ``SOURCE``. Built in V6."""

    @abstractmethod
    def changed_since(self, mtime: float) -> list[Locator]:
        """Source locators whose ``mtime`` is newer than ``mtime``. Built in V6."""
