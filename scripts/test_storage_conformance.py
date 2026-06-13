#!/usr/bin/env python3
"""Conformance suite runs — the backend-agnostic storage gate (V5-1 part 3/5).

The suite itself lives in ``storage_conformance`` (an importable kernel module);
this file *instantiates* it against concrete backends so the cases are
auto-discovered by ``python3 -m unittest discover -p 'test_*.py'`` and ride the
cross-OS ``[T]`` CI matrix.

  - **InMemoryConformance** (part 3 task 1 self-test) — runs the universal
    battery against the dict-backed reference backend and confirms the derived
    case *skips* when ``derived_maintenance`` is ``None``. Hermetic; no filesystem.

``DeviceLocalConformance`` (part 3 task 2) runs the same suite against the
``device-local`` backend on every CI OS; the negative/positive fixtures that
prove the suite *bites* live in ``test_storage_conformance_negative`` (task 3).

Run directly:

    python3 scripts/test_storage_conformance.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import storage_conformance as sc  # noqa: E402
from storage_conformance import ConformanceSuite, InMemoryBackend, run_conformance  # noqa: E402


class InMemoryConformance(ConformanceSuite, unittest.TestCase):
    """The universal battery over the dict-backed reference backend (the self-test).

    Proves the suite mechanics run end-to-end with no filesystem, and — since
    ``InMemoryBackend.derived_maintenance`` is ``None`` — that ``test_derived_rebuildable``
    correctly *skips*.
    """

    def make_backend(self) -> InMemoryBackend:
        return InMemoryBackend()


class SelfTestReport(unittest.TestCase):
    """``run_conformance`` over the reference backend: universal ran, derived skipped."""

    def test_run_conformance_reports_universal_ran_and_derived_skipped(self) -> None:
        report = run_conformance(InMemoryBackend)
        self.assertEqual(report["derived"], "skipped")
        # Every universal check ran, in order.
        self.assertEqual(report["universal"], [name for name, _ in sc.UNIVERSAL_CHECKS])
        self.assertIn("lf_exact_round_trip", report["universal"])

    def test_derived_layout_required_when_backend_is_derived_capable(self) -> None:
        # A backend that *claims* a derived layer but is run without a layout is a
        # caller-setup error, surfaced as ValueError (not a ConformanceFailure).
        class _Claimed(InMemoryBackend):
            @property
            def derived_maintenance(self):  # type: ignore[override]
                return object()  # non-None: claims a derived layer

        with self.assertRaises(ValueError):
            run_conformance(_Claimed)


if __name__ == "__main__":
    unittest.main()
