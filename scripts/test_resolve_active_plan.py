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

import contextlib
import io
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


class ResolveActivePlanCLI(unittest.TestCase):
    """The `resolve-active-plan` CLI verb (V5-10 part 1) — the bash-reachable
    wrapper the crickets developer-workflows bridge shells to so phase specs can
    target named plans without reimplementing resolution.

    Hermetic via **local mode** (`.harness/.project-mode = local`): resolution
    never touches a real vault, and `harness_state_dir` returns the repo-local
    `.harness/`, so the emitted paths are deterministic. The vault-mode dir
    branch is `harness_state_dir`'s own contract (covered in test_harness_memory).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-rap-cli-")
        self.proj = Path(self._tmp) / "repo"
        self.harness = self.proj / ".harness"
        self.harness.mkdir(parents=True)
        # Local mode: the repo-local .harness/ is the canonical state home, so
        # resolution is vault-free + hermetic (DC-2). harness_state_dir → here.
        (self.harness / ".project-mode").write_text("local\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, *cli_args: str, root: Path | None = None) -> tuple[int, str, str]:
        """Invoke `main()` for the verb with stdout/stderr captured."""
        out, err = io.StringIO(), io.StringIO()
        argv = [
            "resolve-active-plan",
            "--project-root", str(root or self.proj),
            *cli_args,
        ]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = hm.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def _pair(self, *names: str) -> str:
        return "\t".join(str(self.harness / n) for n in names)

    # --- happy paths: the tab-separated pair (LC-3) ---

    def test_bare_emits_singleton_pair(self) -> None:
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), self._pair("PLAN.md", "progress.md"))

    def test_named_emits_named_pair(self) -> None:
        rc, out, _ = self._run("--plan", "foo")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), self._pair("PLAN-foo.md", "progress-foo.md"))

    def test_named_accepts_filename_form(self) -> None:
        rc, out, _ = self._run("--plan", "PLAN-foo.md")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), self._pair("PLAN-foo.md", "progress-foo.md"))

    def test_valid_marker_emits_named_pair(self) -> None:
        # No --plan → the worktree active-plan marker binds the pair.
        (self.harness / "PLAN-foo.md").write_text(
            "Status: in-progress\n", encoding="utf-8"
        )
        (self.harness / "active-plan").write_text("foo\n", encoding="utf-8")
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), self._pair("PLAN-foo.md", "progress-foo.md"))

    # --- loud errors: exit 2, never a silent singleton fallback (Risk #7) ---

    def test_dangling_marker_exits_loud_no_singleton_fallback(self) -> None:
        # Marker binds to foo but PLAN-foo.md is absent → must NOT run PLAN.md.
        (self.harness / "active-plan").write_text("foo\n", encoding="utf-8")
        rc, out, err = self._run()
        self.assertEqual(rc, 2)
        self.assertEqual(out.strip(), "")          # emits no pair at all
        self.assertNotIn("PLAN.md", out)           # never the singleton
        self.assertIn("active-plan", err)

    def test_unsafe_plan_slug_exits_loud(self) -> None:
        rc, out, err = self._run("--plan", "../etc")
        self.assertEqual(rc, 2)
        self.assertEqual(out.strip(), "")
        self.assertIn("unsafe plan name", err)

    # --- graceful-skip: no resolvable _harness/ (vault-mode, no vault) → exit 1 ---

    def test_no_resolvable_harness_dir_exits_one(self) -> None:
        bare = Path(self._tmp) / "bare"
        (bare / ".harness").mkdir(parents=True)  # no .project-mode → vault mode
        rc, out, _ = self._run(root=bare)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    # --- reader only ---

    def test_resolve_is_read_only(self) -> None:
        self._run("--plan", "foo")
        self.assertFalse((self.harness / "active-plan").exists())


if __name__ == "__main__":
    unittest.main()
