#!/usr/bin/env python3
"""Contract tests for `resolve_active_plan` (V5-10 part 1, task 2).

`resolve_active_plan` picks the `(plan, progress)` filename pair a worker session
owns, with precedence **explicit arg → worktree-local `.harness/active-plan`
marker → legacy singleton `PLAN.md`**. The load-bearing guard (V5-10 Risk #7): a
*present* but unresolvable marker **raises** — it never silently degrades to the
singleton, which would mis-bind a worker to another worker's plan.

Run directly:

    python3 scripts/test_resolve_active_plan.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402

# Sandbox AGENTM_INSTALL_PREFIX module-wide so `_read_project_mode()`'s config
# fallback never reads the operator's real ~/.claude/.agentm-config.json (which
# could set state_mode=local and divert `read_state_file` off the vault). Mirrors
# test_harness_memory_named_plans.py's module-level sandbox.
_TEST_INSTALL_PREFIX = tempfile.mkdtemp(prefix="agentm-test-active-plan-prefix-")


def setUpModule() -> None:  # noqa: N802 — unittest convention
    os.environ["AGENTM_INSTALL_PREFIX"] = _TEST_INSTALL_PREFIX


def tearDownModule() -> None:  # noqa: N802
    os.environ.pop("AGENTM_INSTALL_PREFIX", None)
    shutil.rmtree(_TEST_INSTALL_PREFIX, ignore_errors=True)


_NAMED = ("PLAN-foo.md", "progress-foo.md")
_SINGLETON = ("PLAN.md", "progress.md")


class ResolveActivePlanPrecedence(unittest.TestCase):
    """Each precedence branch, plus the loud-error guard that makes a dangling
    marker fail instead of silently running the singleton."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-active-plan-")
        self.root = Path(self._tmp)
        self.vault = self.root / "vault"
        self.harness = self.vault / "_harness"
        self.harness.mkdir(parents=True)
        self.proj = self.root / "repo"
        (self.proj / ".harness").mkdir(parents=True)
        self.resolution = {
            "vault_path": self.vault,
            "project_root": self.proj,
            "slug": "fixture",
        }

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- fixture helpers ---

    def _write_plan(self, filename: str, body: str = "Status: in-progress\n") -> None:
        (self.harness / filename).write_text(body, encoding="utf-8")

    def _write_marker(self, text: str) -> Path:
        marker = self.proj / ".harness" / "active-plan"
        marker.write_text(text, encoding="utf-8")
        return marker

    # --- branch 1: explicit arg wins ---

    def test_explicit_arg_resolves_named_pair(self) -> None:
        self.assertEqual(
            hm.resolve_active_plan(self.resolution, plan_arg="foo"), _NAMED
        )

    def test_explicit_arg_accepts_filename_and_stem_forms(self) -> None:
        for arg in ("PLAN-foo.md", "PLAN-foo"):
            with self.subTest(arg=arg):
                self.assertEqual(
                    hm.resolve_active_plan(self.resolution, plan_arg=arg), _NAMED
                )

    def test_explicit_singleton_arg_resolves_singleton(self) -> None:
        for arg in ("", "PLAN", "PLAN.md"):
            with self.subTest(arg=arg):
                self.assertEqual(
                    hm.resolve_active_plan(self.resolution, plan_arg=arg), _SINGLETON
                )

    def test_explicit_arg_beats_marker(self) -> None:
        # A marker binds to bar (whose plan file is intentionally absent), but the
        # explicit arg names foo → foo wins AND the marker is never validated:
        # explicit precedence short-circuits before the marker branch.
        self._write_marker("bar")
        self.assertEqual(
            hm.resolve_active_plan(self.resolution, plan_arg="foo"), _NAMED
        )

    def test_explicit_arg_unsafe_raises_valueerror(self) -> None:
        for bad in ("../etc", "a/b", "..", "x\\y"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    hm.resolve_active_plan(self.resolution, plan_arg=bad)

    # --- branch 2: worktree-local marker, validated ---

    def test_valid_marker_resolves_named_pair(self) -> None:
        self._write_plan("PLAN-foo.md")
        self._write_marker("foo")
        self.assertEqual(hm.resolve_active_plan(self.resolution), _NAMED)

    def test_marker_filename_form_resolves(self) -> None:
        self._write_plan("PLAN-foo.md")
        self._write_marker("PLAN-foo.md")
        self.assertEqual(hm.resolve_active_plan(self.resolution), _NAMED)

    def test_marker_present_but_plan_absent_raises(self) -> None:
        self._write_marker("foo")  # no PLAN-foo.md in _harness/
        with self.assertRaises(hm.ActivePlanError):
            hm.resolve_active_plan(self.resolution)

    def test_marker_present_but_plan_empty_raises(self) -> None:
        self._write_plan("PLAN-foo.md", body="   \n")  # whitespace-only
        self._write_marker("foo")
        with self.assertRaises(hm.ActivePlanError):
            hm.resolve_active_plan(self.resolution)

    def test_marker_blank_raises(self) -> None:
        self._write_marker("   \n")  # present but empty → dangling binding
        with self.assertRaises(hm.ActivePlanError):
            hm.resolve_active_plan(self.resolution)

    def test_marker_unsafe_slug_raises(self) -> None:
        self._write_marker("../escape")
        with self.assertRaises(hm.ActivePlanError):
            hm.resolve_active_plan(self.resolution)

    # --- branch 3: legacy singleton default ---

    def test_no_arg_no_marker_resolves_singleton(self) -> None:
        self.assertEqual(hm.resolve_active_plan(self.resolution), _SINGLETON)

    def test_resolution_is_read_only(self) -> None:
        # Reader only — resolving the singleton must not create the marker file.
        hm.resolve_active_plan(self.resolution)
        self.assertFalse((self.proj / ".harness" / "active-plan").exists())


if __name__ == "__main__":
    unittest.main()
