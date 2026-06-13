#!/usr/bin/env python3
"""Negative + positive fixtures — proof the conformance suite *bites* (V5-1 part 3/5, task 3).

A suite that cannot fail is not a gate. These deliberately contract-violating
fixtures make `storage_conformance` fail **loudly, naming the broken contract**,
and a matching faithful fixture makes the gated case pass — so the rebuildability
invariant is proven to *run green when satisfied*, not merely skip (it would
otherwise be authored-but-never-exercised in V5-1, where neither built-in has a
derived layer).

  - **Non-LF-exact backend** — a backend whose `read` strips `\\r` (the classic
    Windows text-mode translation) ⇒ the byte-identical round-trip fails, naming
    LF-exactness.
  - **Non-rebuildable derived layer** — a `DerivedMaintenance` whose `reindex`
    drops an entry (and a sibling that garbles one) ⇒ the gated rebuildability
    invariant fires, naming rebuildability.
  - **Faithful (positive) derived layer** — a `reindex` that mirrors source
    byte-for-byte ⇒ the gated case passes, both called directly and run as an
    auto-discovered `ConformanceSuite` subclass (so the gated case is proven to
    execute, not skip).

These fixtures live in the test tree (`test_*.py`), so they are out of the
`check-storage-seam-no-path-leak` glob (`storage_*.py`) and never break the
battery — they assert *failure* in isolation.

Run directly:

    python3 scripts/test_storage_conformance_negative.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from storage_conformance import (  # noqa: E402
    ConformanceFailure,
    ConformanceSuite,
    InMemoryBackend,
    check_lf_exact_round_trip,
    check_rebuildable,
)
from storage_seam import DerivedMaintenance, Locator, Tier  # noqa: E402

# The tier roots the derived fixtures use — distinct (TierLayout's invariant), so
# the derived layer can never overwrite the source it rebuilds from.
_SOURCE_ROOT = Locator("source")
_DERIVED_ROOT = Locator("local-index")


# -----------------------------------------------------------------------------
# Negative fixture 1 — a backend that does not preserve bytes on read.
# -----------------------------------------------------------------------------
class _CrStrippingBackend(InMemoryBackend):
    """Stores verbatim but `read` strips `\\r` — a Windows text-mode read, simulated.

    The byte-identical round-trip must catch this against the CRLF samples: the
    write stored ``...\\r\\n...`` but the read returns ``...\\n...``.
    """

    def read(self, locator: Locator) -> str:
        return super().read(locator).replace("\r\n", "\n").replace("\r", "\n")


# -----------------------------------------------------------------------------
# Derived-layer fixtures — a DerivedMaintenance bound to its backend's storage.
# -----------------------------------------------------------------------------
class _MirrorMaintenance(DerivedMaintenance):
    """Rebuild the derived tier by mirroring every source entry into it.

    The faithful base: `reindex` copies each immediate child of the source root
    into the derived root byte-for-byte. `changed_since` is unused by the
    rebuildability case (returns ``[]``); it exists only to satisfy the abstract
    contract. Subclasses inject a single violation to prove the suite bites.
    """

    def __init__(self, backend, source_root: Locator, derived_root: Locator) -> None:
        self._backend = backend
        self._source_root = source_root
        self._derived_root = derived_root

    def _source_children(self) -> list[Locator]:
        return sorted(self._backend.list(self._source_root), key=lambda loc: loc.key)

    def reindex(self, tier: Tier) -> None:
        for child in self._source_children():
            self._backend.write(self._derived_root.child(child.name), self._backend.read(child))

    def changed_since(self, mtime: float) -> list[Locator]:
        return []


class _DroppingMaintenance(_MirrorMaintenance):
    """A `reindex` that **drops** the first source entry — not rebuildable."""

    def reindex(self, tier: Tier) -> None:
        for child in self._source_children()[1:]:  # drop the first
            self._backend.write(self._derived_root.child(child.name), self._backend.read(child))


class _GarblingMaintenance(_MirrorMaintenance):
    """A `reindex` that **garbles** the first source entry's bytes — not faithful."""

    def reindex(self, tier: Tier) -> None:
        children = self._source_children()
        for i, child in enumerate(children):
            content = self._backend.read(child)
            if i == 0:
                content = content + "GARBLED"
            self._backend.write(self._derived_root.child(child.name), content)


class _FaithfulDerivedBackend(InMemoryBackend):
    """An in-memory backend that *does* expose a faithful derived layer (the positive)."""

    @property
    def derived_maintenance(self) -> DerivedMaintenance:
        return _MirrorMaintenance(self, _SOURCE_ROOT, _DERIVED_ROOT)


class _DroppingDerivedBackend(InMemoryBackend):
    @property
    def derived_maintenance(self) -> DerivedMaintenance:
        return _DroppingMaintenance(self, _SOURCE_ROOT, _DERIVED_ROOT)


class _GarblingDerivedBackend(InMemoryBackend):
    @property
    def derived_maintenance(self) -> DerivedMaintenance:
        return _GarblingMaintenance(self, _SOURCE_ROOT, _DERIVED_ROOT)


# -----------------------------------------------------------------------------
# The proofs.
# -----------------------------------------------------------------------------
class SuiteBites(unittest.TestCase):
    """Each violating fixture fails the suite, naming the broken contract."""

    def test_non_lf_exact_backend_fails_round_trip(self) -> None:
        with self.assertRaises(ConformanceFailure) as cm:
            check_lf_exact_round_trip(_CrStrippingBackend)
        self.assertIn("LF-exact", str(cm.exception))

    def test_dropping_reindex_fails_rebuildable(self) -> None:
        with self.assertRaises(ConformanceFailure) as cm:
            check_rebuildable(
                _DroppingDerivedBackend, source_root=_SOURCE_ROOT, derived_root=_DERIVED_ROOT
            )
        msg = str(cm.exception)
        self.assertIn("rebuildability", msg)
        self.assertIn("missing", msg)  # the dropped entry is named

    def test_garbling_reindex_fails_rebuildable(self) -> None:
        with self.assertRaises(ConformanceFailure) as cm:
            check_rebuildable(
                _GarblingDerivedBackend, source_root=_SOURCE_ROOT, derived_root=_DERIVED_ROOT
            )
        msg = str(cm.exception)
        self.assertIn("rebuildability", msg)
        self.assertIn("byte-identical", msg)  # the garbled entry is named


class SuiteRunsGreenWhenSatisfied(unittest.TestCase):
    """The gated rebuildability case is proven to *run green*, not merely skip.

    Without this, the reindex invariant would be dead code in V5-1 (neither
    built-in exposes a derived layer, so the case would only ever skip).
    """

    def test_faithful_reindex_passes_rebuildable(self) -> None:
        # No raise: a faithful reindex reproduces byte-identical reads.
        check_rebuildable(
            _FaithfulDerivedBackend, source_root=_SOURCE_ROOT, derived_root=_DERIVED_ROOT
        )


class FaithfulDerivedConformance(ConformanceSuite, unittest.TestCase):
    """The full suite over a derived-capable backend — the gated case *executes* here.

    Because `derived_maintenance` is non-``None`` and `derived_layout` returns the
    roots, ``test_derived_rebuildable`` runs (it does not skip) and passes — the
    auto-discovered counterpart to :class:`SuiteRunsGreenWhenSatisfied`.
    """

    def make_backend(self) -> _FaithfulDerivedBackend:
        return _FaithfulDerivedBackend()

    def derived_layout(self) -> tuple[Locator, Locator]:
        return (_SOURCE_ROOT, _DERIVED_ROOT)


if __name__ == "__main__":
    unittest.main()
