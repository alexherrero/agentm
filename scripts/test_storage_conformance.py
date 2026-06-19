#!/usr/bin/env python3
"""Conformance suite runs — the backend-agnostic storage gate (V5-1 part 3/5).

The suite itself lives in ``storage_conformance`` (an importable kernel module);
this file *instantiates* it against concrete backends so the cases are
auto-discovered by ``python3 -m unittest discover -p 'test_*.py'`` and ride the
cross-OS ``[T]`` CI matrix.

  - **InMemoryConformance** (part 3 task 1 self-test) — runs the universal
    battery against the dict-backed reference backend and confirms the derived
    case *skips* when ``derived_maintenance`` is ``None``. Hermetic; no filesystem.
  - **DeviceLocalConformance** (part 3 task 2) — runs the same universal battery
    against ``DeviceLocalBackend`` over a fresh temp root, on every CI OS. This is
    where the byte-identical LF-exact round-trip is proven on the Windows runner
    (the only place ``\\r\\n`` translation would surface).
  - **VaultConformance** (part 4 task 2) — runs the same universal battery against
    the transitional ``vault`` wrap over a fresh *scratch* vault (never the
    operator's live vault), shaped like the real per-project path
    (``<vault>/projects/<slug>``) with the ``vault_mutex`` lock base redirected
    into the scratch tree. It holds the wrap to the **identical** contract
    device-local passes — including the LF-exact round-trip on the Windows runner,
    now on the path that backs the operator's real store; the derived case is N/A
    (the vault exposes no derived layer). ``VaultRunConformanceReport`` additionally
    drives the importable one-call :func:`run_conformance` surface (the entry point
    the V5-2 vault *plugin* will self-test through) against the same backend.

The negative/positive fixtures that prove the suite *bites* live in
``test_storage_conformance_negative`` (part 3 task 3).

Run directly:

    python3 scripts/test_storage_conformance.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import storage_conformance as sc  # noqa: E402
import storage_device_local as sdl  # noqa: E402
import storage_vault as sv  # noqa: E402
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


class DeviceLocalConformance(ConformanceSuite, unittest.TestCase):
    """The universal battery over ``DeviceLocalBackend`` — runs on every CI OS.

    A fresh temp root per backend (never the operator's ``~/.agentm/memory``), so
    the run is hermetic and the factory hands every check a clean store. The
    LF-exact round-trip case here is the one that must execute on the **Windows**
    runner: device-local's bytes-mode I/O (``read_bytes`` + ``atomic_write``,
    which encodes utf-8 with no newline translation) is what keeps ``\\r\\n`` from
    being rewritten, and this is the gate that proves it. The derived case is N/A
    — device-local exposes no derived layer (``derived_maintenance`` is ``None``)
    — so ``test_derived_rebuildable`` skips.
    """

    def make_backend(self) -> "sdl.DeviceLocalBackend":
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return sdl.DeviceLocalBackend(root=Path(tmp.name) / "agentm-memory")


def _make_scratch_vault_backend(case: unittest.TestCase) -> "sv.VaultBackend":
    """A fresh ``VaultBackend`` over a throwaway scratch vault — never the live vault.

    A new temp tree per call (the factory contract: a clean root per check),
    shaped like the real per-project vault path (``<vault>/projects/<slug>``) so
    the suite exercises the layout the operator's vault actually uses. The
    ``vault_mutex`` lock base is redirected into the same scratch tree (an
    injected ``lock_root``) so the run never touches the real ``~/.cache`` lock
    base — and ``case.addCleanup`` removes the whole tree when the test finishes.
    """
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    base = Path(tmp.name)
    return sv.VaultBackend(
        root=base / "vault" / "projects" / "agentm",
        lock_root=base / "locks",
    )


class VaultConformance(ConformanceSuite, unittest.TestCase):
    """The universal battery over the wrapped ``vault`` backend — runs on every CI OS.

    Part 4's transitional vault wrap held to the *same* objective contract as
    device-local and the in-memory reference, over a fresh scratch vault per check
    (never the operator's live vault). The LF-exact round-trip case here proves
    the vault's byte-mode I/O (``read_bytes`` + ``atomic_write``, utf-8 with no
    newline translation) keeps ``\\r\\n`` intact on the **Windows** runner — the
    same proof device-local gets, now on the code path that backs the operator's
    real store. The derived case is N/A — the vault exposes no derived layer
    (``derived_maintenance`` is ``None``) — so ``test_derived_rebuildable`` skips.
    """

    def make_backend(self) -> "sv.VaultBackend":
        return _make_scratch_vault_backend(self)


class VaultRunConformanceReport(unittest.TestCase):
    """``run_conformance`` over the ``vault`` backend — the importable one-call surface.

    The mixin above rides the auto-discovered ``test_*`` path; this proves the
    *other* driver — the one-call :func:`run_conformance` the V5-2 vault plugin
    will import to self-test — also passes against the built-in wrap. Universal
    checks ran in order; the derived case skipped (no derived layer).
    """

    def test_run_conformance_over_vault_reports_universal_ran_and_derived_skipped(self) -> None:
        report = run_conformance(lambda: _make_scratch_vault_backend(self))
        self.assertEqual(report["derived"], "skipped")
        # Every universal check ran, in the declared order.
        self.assertEqual(report["universal"], [name for name, _ in sc.UNIVERSAL_CHECKS])
        self.assertIn("lf_exact_round_trip", report["universal"])


class RoutingConformanceReport(unittest.TestCase):
    """``run_conformance(include_routing=True)`` proves ``repo_registry`` on both backends.

    The routing-layer invariant (V5-6 LC-4): ``repo_registry`` operations produce
    identical semantic outcomes on every conforming backend. Exercises both the
    one-call importable driver (``include_routing=True``) and checks the report
    contains a ``routing`` key naming what ran.
    """

    def test_run_conformance_device_local_includes_routing(self) -> None:
        def make() -> "sdl.DeviceLocalBackend":
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            return sdl.DeviceLocalBackend(root=Path(tmp.name) / "agentm-memory")

        report = run_conformance(make, include_routing=True)
        self.assertIn("routing_repo_registry", report["routing"])

    def test_run_conformance_vault_includes_routing(self) -> None:
        report = run_conformance(
            lambda: _make_scratch_vault_backend(self),
            include_routing=True,
        )
        self.assertIn("routing_repo_registry", report["routing"])

    def test_run_conformance_routing_empty_when_not_requested(self) -> None:
        # Default (include_routing=False) must not run routing checks.
        report = run_conformance(InMemoryBackend)
        self.assertEqual(report["routing"], [])


if __name__ == "__main__":
    unittest.main()
