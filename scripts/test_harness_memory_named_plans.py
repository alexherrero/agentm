#!/usr/bin/env python3
"""Named-plan contract tests for scripts/harness_memory.py (V5-10 part 1, task 1).

Locks the contract that the state resolver is **filename-agnostic**: a named
plan (`PLAN-<name>.md` / `progress-<name>.md`) round-trips, CAS-guards, and
conflict-detects *exactly* like the singleton `PLAN.md` / `progress.md`. The
resolver already takes an arbitrary `filename` (`vault_state_path` /
`read_state_file` / `write_state_file`) and `safe_write_replace_style` does
content-hash CAS on an arbitrary path keying on no literal "PLAN.md" — so named
plans are a naming convention, not a new code path. These tests *codify* that
so a later edit can't silently re-introduce a singleton assumption.

Run directly:

    python3 scripts/test_harness_memory_named_plans.py
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
# could set state_mode=local and divert writes out of the vault). Mirrors
# test_harness_memory.py's module-level sandbox.
_TEST_INSTALL_PREFIX = tempfile.mkdtemp(prefix="agentm-test-named-plans-prefix-")


def setUpModule() -> None:  # noqa: N802 — unittest convention
    os.environ["AGENTM_INSTALL_PREFIX"] = _TEST_INSTALL_PREFIX


def tearDownModule() -> None:  # noqa: N802
    os.environ.pop("AGENTM_INSTALL_PREFIX", None)
    shutil.rmtree(_TEST_INSTALL_PREFIX, ignore_errors=True)


# The two name pairs every round-trip / CAS assertion runs over: the singleton
# (today's only shape) and a named plan (V5-10). Running both is the whole point
# — the test fails the instant named plans diverge from the singleton.
_SINGLETON = ("PLAN.md", "progress.md")
_NAMED = ("PLAN-foo.md", "progress-foo.md")


class NamedPlanResolverContract(unittest.TestCase):
    """`PLAN-<name>.md` is a naming convention, not a new code path: the resolver
    handles it identically to `PLAN.md`."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-named-plans-")
        self.root = Path(self._tmp)
        self.vault = self.root / "vault"
        (self.vault / "_harness").mkdir(parents=True)
        self.proj = self.root / "repo"
        self.proj.mkdir()
        self.resolution = {
            "vault_path": self.vault,
            "project_root": self.proj,
            "slug": "fixture",
        }
        # Redirect vault_mutex's lock root off the real ~/.cache (R4 rule 1 /
        # test hygiene) — write_state_file acquires the per-vault mutex.
        self._prev_xdg = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = str(self.root / "cache")

    def tearDown(self) -> None:
        if self._prev_xdg is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self._prev_xdg
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- vault_state_path: pure path construction over an arbitrary filename ---

    def test_vault_state_path_handles_named_plan(self) -> None:
        for fname in ("PLAN.md", "PLAN-foo.md", "progress-foo.md", "PLAN-bar.md"):
            with self.subTest(fname=fname):
                self.assertEqual(
                    hm.vault_state_path(self.resolution, fname),
                    self.vault / "_harness" / fname,
                )

    def test_vault_state_path_none_without_vault(self) -> None:
        self.assertIsNone(
            hm.vault_state_path({"project_root": self.proj}, "PLAN-foo.md")
        )

    # --- write/read round-trip: identical for singleton and named ---

    def test_round_trip_singleton_and_named(self) -> None:
        for plan_name, prog_name in (_SINGLETON, _NAMED):
            with self.subTest(plan=plan_name):
                plan_body = f"# {plan_name}\nStatus: in-progress\n"
                prog_body = f"log entry for {prog_name}\n"
                wrote_plan = hm.write_state_file(self.resolution, plan_name, plan_body)
                wrote_prog = hm.write_state_file(self.resolution, prog_name, prog_body)
                self.assertEqual(wrote_plan, self.vault / "_harness" / plan_name)
                self.assertEqual(wrote_prog, self.vault / "_harness" / prog_name)
                self.assertEqual(
                    hm.read_state_file(self.resolution, plan_name), plan_body
                )
                self.assertEqual(
                    hm.read_state_file(self.resolution, prog_name), prog_body
                )

    def test_named_plans_are_independent_files(self) -> None:
        # Two workers' distinct named plans must not clobber each other — the
        # core of LC-1: per-worker distinct files → no inter-worker contention.
        hm.write_state_file(self.resolution, "PLAN-foo.md", "FOO\n")
        hm.write_state_file(self.resolution, "PLAN-bar.md", "BAR\n")
        hm.write_state_file(self.resolution, "PLAN.md", "SINGLETON\n")
        self.assertEqual(hm.read_state_file(self.resolution, "PLAN-foo.md"), "FOO\n")
        self.assertEqual(hm.read_state_file(self.resolution, "PLAN-bar.md"), "BAR\n")
        self.assertEqual(hm.read_state_file(self.resolution, "PLAN.md"), "SINGLETON\n")

    # --- content-hash CAS: identical for singleton and named ---

    def test_cas_raises_on_stale_hash(self) -> None:
        for plan_name, _prog in (_SINGLETON, _NAMED):
            with self.subTest(plan=plan_name):
                path = self.vault / "_harness" / plan_name
                hm.safe_write_replace_style(path, "v1\n")  # initial, no CAS
                stale_hash = hm.content_hash(path.read_bytes())
                # A concurrent writer lands between our read and our write.
                hm.safe_write_replace_style(path, "v2-other\n")
                with self.assertRaises(hm.ConcurrentModificationError):
                    hm.safe_write_replace_style(
                        path, "v3-mine\n", expected_hash=stale_hash
                    )

    def test_cas_succeeds_on_current_hash(self) -> None:
        for plan_name, _prog in (_SINGLETON, _NAMED):
            with self.subTest(plan=plan_name):
                path = self.vault / "_harness" / plan_name
                hm.safe_write_replace_style(path, "v1\n")
                current = hm.content_hash(path.read_bytes())
                hm.safe_write_replace_style(path, "v2\n", expected_hash=current)
                self.assertEqual(path.read_text(encoding="utf-8"), "v2\n")


class NamedPlanConflictJanitor(unittest.TestCase):
    """A GDrive `(conflicted copy)` of a *named* plan is detected exactly like
    one of the singleton `PLAN.md` — the janitor keys on the marker, not the
    base name."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-named-plans-janitor-")
        self.vault = Path(self._tmp)
        (self.vault / "_harness").mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_conflict_family_classifies_named_plan_conflict(self) -> None:
        self.assertEqual(
            hm._conflict_family("PLAN-foo (conflicted copy 2026-06-12).md"),
            "conflicted-copy",
        )

    def test_infer_base_strips_marker_to_named_plan(self) -> None:
        base = hm._infer_conflict_base_path(
            Path("/v/_harness/PLAN-foo (conflicted copy 2026-06-12) - Mac.md")
        )
        self.assertEqual(base.name, "PLAN-foo.md")

    def test_detect_conflict_files_finds_named_plan_conflict(self) -> None:
        harness = self.vault / "_harness"
        (harness / "PLAN-foo.md").write_text("base\n", encoding="utf-8")
        conflict = harness / "PLAN-foo (conflicted copy 2026-06-12).md"
        conflict.write_text("dupe\n", encoding="utf-8")
        found = hm.detect_conflict_files(self.vault)
        names = {f["conflict"].name for f in found}
        self.assertIn(conflict.name, names)
        match = next(f for f in found if f["conflict"].name == conflict.name)
        self.assertEqual(match["base"].name, "PLAN-foo.md")


if __name__ == "__main__":
    unittest.main()
