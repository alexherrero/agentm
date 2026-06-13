#!/usr/bin/env python3
"""The backend-agnostic storage conformance suite — the storage-track gate (V5-1 part 3/5).

One objective contract every storage backend is held to: *pass the suite or you
are not a valid backend.* This is the load-bearing deterministic gate for the
whole V5 storage track — the thing that lets the cutover (V5-3) delete the
built-in vault backend on **evidence** (the plugin is green on this suite), never
on assertion. Both V5-1 built-ins pass it here (``device-local`` now, the wrapped
vault at part 4); the V5-2 vault *plugin* runs this **same suite unchanged** by
importing this module; "green on the plugin" *is* V5-3's delete-after-proof.

The suite is two layers, parameterized over a backend **factory** (so each case
gets a clean backend / clean root — no case leaks state into the next):

  - **The universal contract — asserted on every backend.** The seven verb
    behaviors (``resolve``/``read``/``write``/``list``/``exists``/``info``/
    ``mkdir``), the **byte-identical, LF-exact markdown round-trip** (write a
    string with CRLF + non-ASCII, read it back unchanged — *no* newline
    translation), the **``list``-on-absent pin** (an absent or non-directory
    locator lists ``[]``, never raises — device-local's part-2 choice, promoted
    here to the cross-backend contract so part 4's vault must match), and the
    root-confinement guard (a ``..`` key raises :class:`InvalidLocatorError`).
  - **The derived-layer rebuildability invariant — gated.** delete the derived
    layer → ``reindex`` from source → byte-identical reads, asserted **only when
    the backend exposes a derived layer** (``derived_maintenance`` is non-``None``)
    and N/A otherwise. Neither V5-1 built-in exposes a derived layer (the V6
    vector index is the first), so this case is exercised in V5-1 only via the
    test-tree fixtures of part 3 task 3 — authored now so the V6 index slots onto
    a gate that already exists.

**Why this lives as an importable kernel module.** Mirroring V5-2 LC-3
(import-don't-vendor): the crickets vault plugin (V5-2) self-tests by *importing*
this module and calling :func:`run_conformance` against its own backend factory —
it never vendors a copy, and only ever runs under a present engine. That is the
opposite of the ``/memory save``+``evolve`` vendoring case (which couldn't import
across the three install scopes). It also keeps the suite out of the public
memory API (DC-7): it sits strictly below ``recall``/``reflect``/``save``/
``evolve`` and the five hooks, which stay byte-unchanged.

**Determinism is the point.** Every check takes a fresh backend from the factory
and asserts only deterministic properties (sizes, kinds, byte-exact content,
membership) — never a wall-clock ``mtime`` value — so running the suite twice on
the same backend yields identical results. LLM judgment plays no part: the
cutover decision (V5-3) reads this suite's exit code, not a review.

Two ways to drive it, both built on the same ``check_*`` functions:

  - :func:`run_conformance` — one call, raises :class:`ConformanceFailure` on the
    first violation (naming the broken contract). The entry point a non-unittest
    caller (the V5-2 plugin's self-test) imports.
  - :class:`ConformanceSuite` — a ``unittest.TestCase`` **mixin** (combine with
    ``unittest.TestCase``, implement ``make_backend``) so each contract is an
    auto-discovered ``test_*`` method riding the cross-OS ``[T]`` CI matrix. This
    is what proves the LF-exact round-trip on the Windows runner.
"""
from __future__ import annotations

from typing import Callable

import storage_seam as ss
from storage_seam import (
    Capabilities,
    Info,
    InvalidLocatorError,
    Locator,
    StorageBackend,
    Tier,
)

__all__ = [
    "ConformanceFailure",
    "InMemoryBackend",
    "check_resolve_and_locator",
    "check_write_read_round_trip",
    "check_lf_exact_round_trip",
    "check_exists_flips",
    "check_info",
    "check_mkdir_idempotent",
    "check_list_children",
    "check_list_on_absent",
    "check_invalid_locator_rejected",
    "check_rebuildable",
    "UNIVERSAL_CHECKS",
    "DERIVED_SEED",
    "run_conformance",
    "ConformanceSuite",
]

#: A factory that hands back a *fresh* backend (clean root). The suite calls it
#: once per check so no case can leak state into the next.
BackendFactory = Callable[[], StorageBackend]


class ConformanceFailure(AssertionError):
    """A backend violated the storage contract — raised naming the broken rule.

    An ``AssertionError`` subclass so ``unittest`` reports it as a *failure* (a
    backend that doesn't conform), distinct from an unexpected *error*. Every
    raise names the violated contract (LF-exactness, ``list``-on-absent,
    rebuildability, …) — a suite that fails silently is not a gate.
    """


# -----------------------------------------------------------------------------
# The reference in-memory backend — the suite's own self-test vehicle.
# -----------------------------------------------------------------------------
class InMemoryBackend(StorageBackend):
    """A dict-backed :class:`StorageBackend` — the contract with no filesystem at all.

    The suite's hermetic self-test backend (part 1 anticipated it: the seam's
    ``Locator`` is frozen+hashable "so a backend may use it as a dict key — the
    in-memory conformance fixture does"). Stores text verbatim, so it is trivially
    LF-exact and exposes no derived layer (``derived_maintenance`` inherits the
    ``None`` floor) — running the universal battery green and *skipping* the
    derived case is exactly the part-1 self-test. The part-3 negative fixtures
    (task 3) subclass it to inject a single contract violation.

    No ``pathlib.Path`` ever appears here — every verb returns the seam's own
    ``Locator``/``Info`` types — so this module stays clean under the
    ``check-storage-seam-no-path-leak`` gate (which scans ``storage_*.py``).
    """

    def __init__(self) -> None:
        self._files: dict[str, str] = {}
        self._dirs: set[str] = {""}  # the root always exists
        self._mtimes: dict[str, float] = {}
        self._clock = 0.0

    def _tick(self) -> float:
        # A monotonic counter, not a wall clock, so mtime is deterministic.
        self._clock += 1.0
        return self._clock

    def _register_ancestors(self, locator: Locator) -> None:
        parts = locator.parts
        for i in range(1, len(parts)):
            self._dirs.add("/".join(parts[:i]))

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(concurrent_writers=True)

    def resolve(self, *parts: str) -> Locator:
        return Locator("/".join(str(p) for p in parts))

    def read(self, locator: Locator) -> str:
        try:
            return self._files[locator.key]
        except KeyError:
            raise FileNotFoundError(locator.key)

    def write(self, locator: Locator, content: str) -> Locator:
        self._files[locator.key] = content
        self._mtimes[locator.key] = self._tick()
        self._register_ancestors(locator)
        return locator

    def list(self, locator: Locator) -> list[Locator]:
        if locator.key not in self._dirs:
            return []  # absent or a file: no children (the list-on-absent pin)
        prefix = locator.key + "/" if locator.key else ""
        children: set[str] = set()
        for k in (*self._files, *self._dirs):
            if not k or k == locator.key or not k.startswith(prefix):
                continue
            children.add(prefix + k[len(prefix):].split("/")[0])
        return [Locator(c) for c in sorted(children)]

    def exists(self, locator: Locator) -> bool:
        return locator.key in self._files or locator.key in self._dirs

    def info(self, locator: Locator) -> Info:
        k = locator.key
        if k in self._files:
            content = self._files[k]
            return Info(
                locator=locator,
                is_dir=False,
                size=len(content.encode("utf-8")),
                mtime=self._mtimes[k],
            )
        if k in self._dirs:
            return Info(
                locator=locator,
                is_dir=True,
                size=0,
                mtime=self._mtimes.get(k, 0.0),
            )
        raise FileNotFoundError(k)

    def mkdir(self, locator: Locator) -> Locator:
        self._dirs.add(locator.key)
        self._mtimes.setdefault(locator.key, self._tick())
        self._register_ancestors(locator)
        return locator


# -----------------------------------------------------------------------------
# The universal contract — one function per behavior, each over a fresh backend.
# -----------------------------------------------------------------------------
# Markdown samples that pin LF-exactness: LF-only, CRLF, mixed, a no-trailing
# newline, and non-ASCII (accented Latin + Greek + a cricket 🦗) crossed with
# CRLF. A backend that translates newlines on read (a text-mode open on Windows)
# fails the byte-identical case against at least the CRLF samples.
_LF_SAMPLES = (
    "unix line one\nunix line two\n",
    "windows line one\r\nwindows line two\r\n",
    "mixed\r\nlf\nbare-cr\rtail",
    "no trailing newline",
    "non-ascii café ναί \U0001f997\r\nsecond\r\n",
)


def check_resolve_and_locator(make_backend: BackendFactory) -> None:
    """``resolve`` produces the seam's ``Locator`` — root is empty, parts join."""
    b = make_backend()
    root = b.resolve()
    if not isinstance(root, Locator) or root.key != "":
        raise ConformanceFailure(
            f"resolve() with no parts must be the root Locator (key ''), got {root!r}"
        )
    loc = b.resolve("a", "b", "c")
    if not isinstance(loc, Locator) or loc.key != "a/b/c":
        raise ConformanceFailure(
            f"resolve('a','b','c') must be Locator('a/b/c'), got {loc!r}"
        )


def check_write_read_round_trip(make_backend: BackendFactory) -> None:
    """``write`` is durable and returns the locator; ``read`` returns what was written."""
    b = make_backend()
    loc = b.resolve("notes", "hello.md")
    content = "# Hello\n\nbody line\n"
    returned = b.write(loc, content)
    if returned != loc:
        raise ConformanceFailure(
            f"write must return the locator written: gave {loc!r}, got {returned!r}"
        )
    got = b.read(loc)
    if got != content:
        raise ConformanceFailure(
            f"write→read did not round-trip: wrote {content!r}, read {got!r}"
        )


def check_lf_exact_round_trip(make_backend: BackendFactory) -> None:
    """Byte-identical, LF-exact round-trip — **no** newline translation, ever.

    The V4 Windows-CI lesson as an asserted contract: a backend must store and
    return content byte-for-byte, including ``\\r\\n`` and non-ASCII. Asserted
    both at the text level (``read == write``) and the encoded-bytes level
    (utf-8), so a newline-translating backend cannot slip through.
    """
    b = make_backend()
    for i, content in enumerate(_LF_SAMPLES):
        loc = b.resolve("roundtrip", f"sample{i}.md")
        b.write(loc, content)
        got = b.read(loc)
        if got != content:
            raise ConformanceFailure(
                "LF-exact round-trip violated (newline translation is forbidden): "
                f"wrote {content!r}, read {got!r}"
            )
        if got.encode("utf-8") != content.encode("utf-8"):
            raise ConformanceFailure(
                "LF-exact round-trip violated at the byte level: "
                f"wrote {content.encode('utf-8')!r}, read {got.encode('utf-8')!r}"
            )


def check_exists_flips(make_backend: BackendFactory) -> None:
    """``exists`` is ``False`` before a write at a locator and ``True`` after."""
    b = make_backend()
    loc = b.resolve("maybe.md")
    if b.exists(loc):
        raise ConformanceFailure(f"exists must be False before anything is written: {loc!r}")
    b.write(loc, "now i exist\n")
    if not b.exists(loc):
        raise ConformanceFailure(f"exists must be True after a write: {loc!r}")


def check_info(make_backend: BackendFactory) -> None:
    """``info`` carries ``size``/``is_dir``/``mtime``; raises on an absent locator."""
    b = make_backend()
    f = b.resolve("doc.md")
    content = "café\r\nsecond\r\n"  # non-ASCII + CRLF: size is *byte* length
    b.write(f, content)
    fi = b.info(f)
    if fi.is_dir:
        raise ConformanceFailure(f"info on a file must report is_dir=False: {fi!r}")
    if fi.size != len(content.encode("utf-8")):
        raise ConformanceFailure(
            f"info.size must be the utf-8 byte length ({len(content.encode('utf-8'))}), "
            f"got {fi.size}"
        )
    if not isinstance(fi.mtime, (int, float)):
        raise ConformanceFailure(f"info.mtime must be a number, got {fi.mtime!r}")

    d = b.mkdir(b.resolve("adir"))
    di = b.info(d)
    if not di.is_dir:
        raise ConformanceFailure(f"info on a directory must report is_dir=True: {di!r}")
    if di.size != 0:
        raise ConformanceFailure(f"info on a directory must report size 0, got {di.size}")

    absent = b.resolve("nope", "missing.md")
    try:
        b.info(absent)
    except FileNotFoundError:
        pass
    else:
        raise ConformanceFailure(f"info on an absent locator must raise FileNotFoundError: {absent!r}")


def check_mkdir_idempotent(make_backend: BackendFactory) -> None:
    """``mkdir`` creates a directory and is idempotent (a second call is fine)."""
    b = make_backend()
    loc = b.resolve("x", "y")
    b.mkdir(loc)
    b.mkdir(loc)  # idempotent — no raise
    if not b.exists(loc):
        raise ConformanceFailure(f"mkdir must make the locator exist: {loc!r}")
    if not b.info(loc).is_dir:
        raise ConformanceFailure(f"mkdir must create a directory: {loc!r}")


def check_list_children(make_backend: BackendFactory) -> None:
    """``list`` returns the *immediate* children of a directory as locators."""
    b = make_backend()
    base = b.resolve("box")
    b.write(base.child("a.md"), "a\n")
    b.write(base.child("b.md"), "b\n")
    b.mkdir(base.child("sub"))
    b.write(base.child("sub", "deep.md"), "deep\n")  # NOT an immediate child of base

    children = b.list(base)
    for c in children:
        if not isinstance(c, Locator):
            raise ConformanceFailure(f"list must return Locators, got {c!r}")
    keys = {c.key for c in children}
    expected = {"box/a.md", "box/b.md", "box/sub"}
    if keys != expected:
        raise ConformanceFailure(
            f"list must return exactly the immediate children {expected}, got {keys}"
        )


def check_list_on_absent(make_backend: BackendFactory) -> None:
    """The ``list``-on-absent pin: an absent *or non-directory* locator lists ``[]``.

    Device-local's part-2 choice (a missing or file locator yields no children
    rather than raising), promoted here to the cross-backend contract — so part
    4's vault backend must match it.
    """
    b = make_backend()
    absent = b.resolve("never", "created")
    # The violation this check exists to catch *is* "list-on-absent raises", so a
    # raise must be converted to a contract-named failure — not allowed to escape
    # as an unexpected error (mirrors check_info's absent handling).
    try:
        got = b.list(absent)
    except Exception as exc:  # noqa: BLE001 - any raise is the violation
        raise ConformanceFailure(
            f"list on an absent locator must return [], not raise: {absent!r} raised {exc!r}"
        )
    if got != []:
        raise ConformanceFailure(f"list on an absent locator must return [], got {got!r}: {absent!r}")

    f = b.resolve("file.md")
    b.write(f, "i am a file\n")
    try:
        got = b.list(f)
    except Exception as exc:  # noqa: BLE001 - any raise is the violation
        raise ConformanceFailure(
            f"list on a non-directory (file) locator must return [], not raise: {f!r} raised {exc!r}"
        )
    if got != []:
        raise ConformanceFailure(f"list on a non-directory (file) locator must return []: {f!r} got {got!r}")


def check_invalid_locator_rejected(make_backend: BackendFactory) -> None:
    """A ``..`` segment is rejected (``InvalidLocatorError``) — root-confinement.

    The safety property: a locator can never traverse up out of the backend root.
    Asserted through ``resolve`` (the backend's own naming verb), so it holds for
    however a backend builds locators.
    """
    b = make_backend()
    try:
        b.resolve("..")
    except InvalidLocatorError:
        pass
    else:
        raise ConformanceFailure("resolve('..') must raise InvalidLocatorError (root-confinement)")
    try:
        b.resolve("a", "..", "b")
    except InvalidLocatorError:
        pass
    else:
        raise ConformanceFailure("resolve('a','..','b') must raise InvalidLocatorError (root-confinement)")


#: The universal layer, ordered. ``(name, fn)`` so :func:`run_conformance` can
#: report which ran and the mixin can name each ``test_*`` method.
UNIVERSAL_CHECKS: tuple[tuple[str, Callable[[BackendFactory], None]], ...] = (
    ("resolve_and_locator", check_resolve_and_locator),
    ("write_read_round_trip", check_write_read_round_trip),
    ("lf_exact_round_trip", check_lf_exact_round_trip),
    ("exists_flips", check_exists_flips),
    ("info", check_info),
    ("mkdir_idempotent", check_mkdir_idempotent),
    ("list_children", check_list_children),
    ("list_on_absent", check_list_on_absent),
    ("invalid_locator_rejected", check_invalid_locator_rejected),
)


# -----------------------------------------------------------------------------
# The gated derived-layer invariant — delete → reindex → byte-identical reads.
# -----------------------------------------------------------------------------
#: A deterministic seed for the rebuildability case: relative key → content,
#: with a CRLF + non-ASCII entry so a faithful reindex is proven byte-exact, not
#: just present. Flat (no nesting) so a fixture's ``reindex`` is a simple mirror.
DERIVED_SEED: tuple[tuple[str, str], ...] = (
    ("alpha.md", "alpha source\n"),
    ("beta.md", "beta source\r\n"),
    ("gamma.md", "café \U0001f997\r\n"),
)


def _assert_derived_mirrors_source(
    backend: StorageBackend,
    derived_root: Locator,
    expected: tuple[tuple[str, str], ...],
    when: str,
) -> None:
    """Every ``expected`` entry has a byte-identical derived counterpart under ``derived_root``."""
    for rel, content in expected:
        loc = derived_root.child(rel)
        try:
            got = backend.read(loc)
        except FileNotFoundError:
            raise ConformanceFailure(
                f"rebuildability violated {when}: derived entry {loc!r} is missing — "
                "reindex must reproduce every source entry"
            )
        if got != content or got.encode("utf-8") != content.encode("utf-8"):
            raise ConformanceFailure(
                f"rebuildability violated {when}: derived entry {loc!r} is not byte-identical "
                f"to source — source is {content!r}, reindex produced {got!r}"
            )


def check_rebuildable(
    make_backend: BackendFactory, *, source_root: Locator, derived_root: Locator
) -> None:
    """delete the derived layer → ``reindex`` from source → byte-identical reads.

    Three things are proven, in order:

      1. **The deleted-index precondition** — a fresh backend has nothing under
         ``derived_root`` (the post-delete state). (Assumes ``derived_root`` is
         disjoint from ``source_root`` — not an ancestor of it; the seam's
         ``TierLayout`` keeps the tier roots distinct, and the V5-1 fixtures use
         sibling roots.)
      2. **Rebuild from source** — after ``reindex`` the derived layer is
         byte-identical to source.
      3. **The rebuild actually reads source, not a constant** — mutate every
         source entry, ``reindex`` again, and require the derived layer to track
         the *new* source. This is what distinguishes a real rebuild from a
         ``reindex`` that hardcodes the seed shape (the cutover's whole proof
         rests on this: a faithful-looking-but-wrong backend must not pass).

    Only run against a backend whose ``derived_maintenance`` is non-``None`` —
    :func:`run_conformance` and the mixin gate on that; calling it on a
    derived-less backend is a caller error (``ValueError``).
    """
    b = make_backend()
    dm = b.derived_maintenance
    if dm is None:
        raise ValueError(
            "check_rebuildable requires a derived-capable backend "
            "(derived_maintenance is None); the suite gates this case on that"
        )
    # Seed the source tier.
    for rel, content in DERIVED_SEED:
        b.write(source_root.child(rel), content)
    # (1) The deleted-index precondition: nothing derived yet.
    pre = b.list(derived_root)
    if pre != []:
        raise ConformanceFailure(
            f"rebuildability precondition: derived root {derived_root!r} must be empty before "
            f"reindex (the deleted-index state), found {[str(p) for p in pre]}"
        )
    # (2) Rebuild from source — byte-identical.
    dm.reindex(Tier.LOCAL_INDEX)
    _assert_derived_mirrors_source(b, derived_root, DERIVED_SEED, when="after reindex")
    # (3) Prove the rebuild *reads source*: change every source entry, reindex,
    # and require the derived layer to reflect the new source (not a constant the
    # reindex happened to emit, and not the stale pre-change bytes).
    mutated = tuple((rel, content + " (rev2)") for rel, content in DERIVED_SEED)
    for rel, content in mutated:
        b.write(source_root.child(rel), content)
    dm.reindex(Tier.LOCAL_INDEX)
    _assert_derived_mirrors_source(
        b, derived_root, mutated, when="after a source change + reindex"
    )


# -----------------------------------------------------------------------------
# Drivers — one-call (importable) and a unittest mixin (auto-discovered, cross-OS).
# -----------------------------------------------------------------------------
def run_conformance(
    make_backend: BackendFactory,
    *,
    derived_layout: tuple[Locator, Locator] | None = None,
) -> dict[str, object]:
    """Run the whole suite against ``make_backend``; raise on the first violation.

    The entry point a non-unittest caller imports (the V5-2 vault plugin's
    self-test). Runs every universal check over a fresh backend, then — **iff**
    the backend exposes a derived layer — the gated rebuildability invariant
    (which requires ``derived_layout=(source_root, derived_root)``). Returns a
    report of what ran; raises :class:`ConformanceFailure` (naming the contract)
    the moment a check fails.
    """
    ran: list[str] = []
    for name, fn in UNIVERSAL_CHECKS:
        fn(make_backend)
        ran.append(name)

    derived_status = "skipped"
    if make_backend().derived_maintenance is not None:
        if derived_layout is None:
            raise ValueError(
                "backend exposes a derived layer (derived_maintenance is non-None) but no "
                "derived_layout=(source_root, derived_root) was supplied to run the rebuild case"
            )
        source_root, derived_root = derived_layout
        check_rebuildable(make_backend, source_root=source_root, derived_root=derived_root)
        derived_status = "passed"

    return {"universal": ran, "derived": derived_status}


class ConformanceSuite:
    """A ``unittest.TestCase`` **mixin** — one auto-discovered ``test_*`` per contract.

    Combine with ``unittest.TestCase`` and implement :meth:`make_backend` (return
    a *fresh* backend each call). Because the methods are auto-discovered by
    ``unittest``, a subclass rides the cross-OS ``[T]`` CI matrix unchanged —
    which is what proves the LF-exact round-trip on the Windows runner. The
    derived case skips unless the backend exposes a derived layer **and**
    :meth:`derived_layout` returns the source/derived roots.

    Not itself a ``TestCase`` (so ``unittest`` discovery does not try to run the
    abstract suite): only the concrete ``(ConformanceSuite, unittest.TestCase)``
    subclass is collected.
    """

    def make_backend(self) -> StorageBackend:  # pragma: no cover - overridden
        raise NotImplementedError("ConformanceSuite subclasses must implement make_backend()")

    def derived_layout(self) -> tuple[Locator, Locator] | None:
        """``(source_root, derived_root)`` for a derived-capable backend, else ``None``.

        Default ``None``: a backend with no derived layer (both V5-1 built-ins)
        skips the rebuildability case. A derived-capable fixture overrides this.
        """
        return None

    def test_resolve_and_locator(self) -> None:
        check_resolve_and_locator(self.make_backend)

    def test_write_read_round_trip(self) -> None:
        check_write_read_round_trip(self.make_backend)

    def test_lf_exact_round_trip(self) -> None:
        check_lf_exact_round_trip(self.make_backend)

    def test_exists_flips(self) -> None:
        check_exists_flips(self.make_backend)

    def test_info(self) -> None:
        check_info(self.make_backend)

    def test_mkdir_idempotent(self) -> None:
        check_mkdir_idempotent(self.make_backend)

    def test_list_children(self) -> None:
        check_list_children(self.make_backend)

    def test_list_on_absent(self) -> None:
        check_list_on_absent(self.make_backend)

    def test_invalid_locator_rejected(self) -> None:
        check_invalid_locator_rejected(self.make_backend)

    def test_derived_rebuildable(self) -> None:
        if self.make_backend().derived_maintenance is None:
            self.skipTest("backend exposes no derived layer (derived_maintenance is None)")
        layout = self.derived_layout()
        if layout is None:
            self.fail(
                "a derived-capable backend must override derived_layout() to return "
                "(source_root, derived_root)"
            )
        source_root, derived_root = layout
        check_rebuildable(self.make_backend, source_root=source_root, derived_root=derived_root)
