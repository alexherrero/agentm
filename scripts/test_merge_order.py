#!/usr/bin/env python3
"""Tests for scripts/merge_order.py (V5-11 task 5).

Required cases (from the plan):

  - ≥4 finished plans with a dep edge and a tie requiring size tie-break.
  - Determinism: two runs with identical input → byte-identical output.
  - The alphabetical fallback (--no-git / git unavailable) is tested
    explicitly so a missing git never causes non-deterministic output.
  - Dep cycle detection raises ValueError.
  - Plans with outstanding tasks are excluded.
  - Empty harness → empty result.

Run directly::

    python3 scripts/test_merge_order.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import merge_order as mo  # noqa: E402


def _make_harness(slugs_config: dict) -> Path:
    """Create a temporary _harness with the plans described by *slugs_config*.

    ``slugs_config`` maps slug → dict with optional keys:
        done: bool   (default False) — all tasks checked
        dep: str     (optional) — depends_on entry
    Returns the harness dir path; caller must clean up.
    """
    tmp = tempfile.mkdtemp(prefix="agentm-mo-")
    h = Path(tmp) / "_harness"
    h.mkdir(parents=True)

    for slug, cfg in slugs_config.items():
        done = cfg.get("done", False)
        dep = cfg.get("dep")
        fm = ""
        if dep:
            fm = f"---\ndepends_on: [{dep!r}]\n---\n"
        task_mark = "x" if done else " "
        text = (
            f"{fm}# Plan: {slug}\n\n"
            f"**Status:** {'done' if done else 'in-progress'}\n\n"
            f"### 1. Task\n- **Status:** [{task_mark}]\n"
        )
        (h / f"PLAN-{slug}.md").write_text(text, encoding="utf-8")

    return h


class TestBasicOrder(unittest.TestCase):
    """Four finished plans: two independent, one dep chain, one dep needing fallback."""

    def setUp(self) -> None:
        self._h = _make_harness({
            "alpha": {"done": True},
            "beta": {"done": True, "dep": "alpha"},  # beta depends on alpha
            "gamma": {"done": True},
            "delta": {"done": True},
        })

    def tearDown(self) -> None:
        shutil.rmtree(self._h.parent, ignore_errors=True)

    def test_alpha_before_beta(self) -> None:
        """alpha must appear before beta in the merge order (dep edge)."""
        order = mo.build_merge_order(self._h, use_git=False)
        slugs = [e["slug"] for e in order]
        self.assertIn("alpha", slugs)
        self.assertIn("beta", slugs)
        self.assertLess(slugs.index("alpha"), slugs.index("beta"))

    def test_all_four_included(self) -> None:
        order = mo.build_merge_order(self._h, use_git=False)
        self.assertEqual(len(order), 4)

    def test_deterministic_no_git(self) -> None:
        r1 = mo.build_merge_order(self._h, use_git=False)
        r2 = mo.build_merge_order(self._h, use_git=False)
        self.assertEqual(r1, r2)

    def test_reason_cites_dep(self) -> None:
        order = {e["slug"]: e for e in mo.build_merge_order(self._h, use_git=False)}
        self.assertIn("alpha", order["beta"]["reason"])

    def test_fallback_reason_mentions_git_unavailable(self) -> None:
        order = {e["slug"]: e for e in mo.build_merge_order(self._h, use_git=False)}
        # Independent plans with no git → alphabetical fallback reason.
        self.assertIn("git unavailable", order["gamma"]["reason"])
        self.assertIn("git unavailable", order["delta"]["reason"])


class TestAlphabeticalFallback(unittest.TestCase):
    """With use_git=False, independent plans are sorted by slug."""

    def setUp(self) -> None:
        self._h = _make_harness({
            "zoo": {"done": True},
            "aardvark": {"done": True},
            "monkey": {"done": True},
        })

    def tearDown(self) -> None:
        shutil.rmtree(self._h.parent, ignore_errors=True)

    def test_alphabetical_order(self) -> None:
        order = mo.build_merge_order(self._h, use_git=False)
        slugs = [e["slug"] for e in order]
        self.assertEqual(slugs, sorted(slugs))

    def test_deterministic_two_calls(self) -> None:
        r1 = mo.build_merge_order(self._h, use_git=False)
        r2 = mo.build_merge_order(self._h, use_git=False)
        self.assertEqual(r1, r2)


class TestExclusions(unittest.TestCase):
    """Plans with outstanding tasks must not appear in the merge order."""

    def setUp(self) -> None:
        self._h = _make_harness({
            "finished": {"done": True},
            "inprogress": {"done": False},
        })

    def tearDown(self) -> None:
        shutil.rmtree(self._h.parent, ignore_errors=True)

    def test_unfinished_excluded(self) -> None:
        order = mo.build_merge_order(self._h, use_git=False)
        slugs = [e["slug"] for e in order]
        self.assertIn("finished", slugs)
        self.assertNotIn("inprogress", slugs)


class TestCycleDetection(unittest.TestCase):
    """A dep cycle must raise ValueError (never produce output silently)."""

    def setUp(self) -> None:
        # Manually construct a cycle: a → b → a.
        self._tmp = tempfile.mkdtemp(prefix="agentm-mo-cycle-")
        self._h = Path(self._tmp) / "_harness"
        self._h.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, slug: str, dep: str) -> None:
        fm = f"---\ndepends_on: [{dep!r}]\n---\n"
        text = f"{fm}# Plan: {slug}\n\n**Status:** done\n\n### 1. Task\n- **Status:** [x]\n"
        (self._h / f"PLAN-{slug}.md").write_text(text, encoding="utf-8")

    def test_cycle_raises(self) -> None:
        self._write("a", "b")
        self._write("b", "a")
        with self.assertRaises(ValueError) as ctx:
            mo.build_merge_order(self._h, use_git=False)
        self.assertIn("cycle", str(ctx.exception).lower())


class TestEmptyHarness(unittest.TestCase):
    def test_empty_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-mo-empty-") as d:
            h = Path(d) / "_harness"
            h.mkdir()
            order = mo.build_merge_order(h, use_git=False)
            self.assertEqual(order, [])


class TestDepChain(unittest.TestCase):
    """Deep dep chain: a → b → c → d — must appear in that order."""

    def setUp(self) -> None:
        self._h = _make_harness({
            "a": {"done": True},
            "b": {"done": True, "dep": "a"},
            "c": {"done": True, "dep": "b"},
            "d": {"done": True, "dep": "c"},
        })

    def tearDown(self) -> None:
        shutil.rmtree(self._h.parent, ignore_errors=True)

    def test_chain_order(self) -> None:
        order = mo.build_merge_order(self._h, use_git=False)
        slugs = [e["slug"] for e in order]
        self.assertEqual(slugs, ["a", "b", "c", "d"])

    def test_deterministic(self) -> None:
        r1 = mo.build_merge_order(self._h, use_git=False)
        r2 = mo.build_merge_order(self._h, use_git=False)
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main()
