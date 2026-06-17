#!/usr/bin/env python3
"""Tests for scripts/readiness.py (V5-11 task 4).

All four required cases are tested (from the plan):

  (a) A plan held back because its dep isn't done.
  (b) Two ready plans with disjoint touches: → safe together.
  (c) Two ready plans with overlapping touches: → one held back.
  (d) A ready plan with no touches: → excluded, degrade warning emitted.

Plus:
  - loud-degrade message is the canonical string (quoted verbatim).
  - held_back reason cites the dep slug.
  - report is deterministic (same input → same output).
  - empty queued-plans → empty report.

Run directly::

    python3 scripts/test_readiness.py
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

import readiness as rd  # noqa: E402


def _make_harness(
    tmp: str,
    queued: dict | None = None,
    active: dict | None = None,
) -> Path:
    """Build a minimal _harness/ fixture and return its path.

    *queued* and *active* are dicts mapping slug → plan-text.
    """
    h = Path(tmp) / "_harness"
    qd = h / "queued-plans"
    qd.mkdir(parents=True)

    for slug, text in (active or {}).items():
        (h / f"PLAN-{slug}.md").write_text(text, encoding="utf-8")

    for slug, text in (queued or {}).items():
        (qd / f"PLAN-{slug}.md").write_text(text, encoding="utf-8")

    return h


def _plan(slug: str, *, status: str = "planning", depends_on=None, touches=None) -> str:
    """Generate a minimal plan file body."""
    fm_lines = []
    if depends_on is not None:
        items = ", ".join(f'"{s}"' for s in depends_on)
        fm_lines.append(f"depends_on: [{items}]")
    if touches is not None:
        items = ", ".join(f'"{s}"' for s in touches)
        fm_lines.append(f"touches: [{items}]")
    fm = ("---\n" + "\n".join(fm_lines) + "\n---\n") if fm_lines else ""
    return f"{fm}# Plan: {slug}\n\n**Status:** {status}\n\n### 1. Task\n- **Status:** [ ]\n"


def _done_plan(slug: str) -> str:
    return f"# Plan: {slug}\n\n**Status:** done\n\n### 1. Task\n- **Status:** [x]\n"


class TestCaseA(unittest.TestCase):
    """(a) A queued plan held back because its dep isn't done."""

    def test_held_back_when_dep_not_done(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-rd-a-") as tmp:
            h = _make_harness(
                tmp,
                active={"dep": "# Plan: dep\n\n**Status:** in-progress\n"},
                queued={"child": _plan("child", depends_on=["dep"])},
            )
            report = rd.build_readiness(h)
            self.assertEqual(report["ready"], [])
            self.assertEqual(len(report["held_back"]), 1)
            self.assertIn("dep", report["held_back"][0]["reason"])
            self.assertEqual(report["degrade_warnings"], [])

    def test_ready_when_dep_is_done(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-rd-a2-") as tmp:
            h = _make_harness(
                tmp,
                active={"dep": _done_plan("dep")},
                queued={"child": _plan("child", depends_on=["dep"])},
            )
            report = rd.build_readiness(h)
            self.assertIn("child", report["ready"])


class TestCaseB(unittest.TestCase):
    """(b) Two ready plans with disjoint touches: → safe together."""

    def test_disjoint_touches_safe_together(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-rd-b-") as tmp:
            h = _make_harness(
                tmp,
                queued={
                    "alpha": _plan("alpha", touches=["src/alpha/**"]),
                    "beta": _plan("beta", touches=["src/beta/**"]),
                },
            )
            report = rd.build_readiness(h)
            self.assertIn("alpha", report["ready"])
            self.assertIn("beta", report["ready"])
            safe = set(report["safe_together"])
            self.assertIn("alpha", safe)
            self.assertIn("beta", safe)
            self.assertEqual(report["held_back"], [])
            self.assertEqual(report["degrade_warnings"], [])


class TestCaseC(unittest.TestCase):
    """(c) Two ready plans with overlapping touches: → at least one held back."""

    def test_overlapping_touches_held_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-rd-c-") as tmp:
            h = _make_harness(
                tmp,
                queued={
                    "alpha": _plan("alpha", touches=["src/**"]),
                    "beta": _plan("beta", touches=["src/shared/**"]),
                },
            )
            report = rd.build_readiness(h)
            # Both are ready (no dep constraints).
            self.assertIn("alpha", report["ready"])
            self.assertIn("beta", report["ready"])
            # At least one must be held back due to overlap.
            held_slugs = {h["slug"] for h in report["held_back"]}
            self.assertTrue(
                held_slugs,
                "Expected at least one plan held back for overlap",
            )
            # The union of safe + held (for overlap) must cover both plans.
            covered = set(report["safe_together"]) | held_slugs
            self.assertLessEqual({"alpha", "beta"}, covered)


class TestCaseD(unittest.TestCase):
    """(d) A ready plan with no touches: → excluded, degrade warning emitted."""

    def test_no_touches_excluded_with_warning(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-rd-d-") as tmp:
            h = _make_harness(
                tmp,
                queued={"orphan": _plan("orphan")},  # no touches
            )
            report = rd.build_readiness(h)
            self.assertIn("orphan", report["ready"])
            self.assertNotIn("orphan", report["safe_together"])
            self.assertEqual(len(report["degrade_warnings"]), 1)
            self.assertIn("orphan", report["degrade_warnings"][0])
            self.assertIn("touches:", report["degrade_warnings"][0])

    def test_degrade_message_canonical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-rd-d2-") as tmp:
            h = _make_harness(
                tmp,
                queued={"myplan": _plan("myplan")},
            )
            report = rd.build_readiness(h)
            expected = (
                "plan 'myplan' excluded from safe-to-run-together check — "
                "touches: not declared; add it to get a file-overlap verdict"
            )
            self.assertIn(expected, report["degrade_warnings"])


class TestMixed(unittest.TestCase):
    """Mixed scenario: dep held, disjoint safe, no-touches degrades."""

    def test_three_queued_mixed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-rd-mix-") as tmp:
            h = _make_harness(
                tmp,
                active={"foundation": _done_plan("foundation")},
                queued={
                    # dep met → ready, but no touches → degrade
                    "a": _plan("a", depends_on=["foundation"]),
                    # no dep, has touches → safe
                    "b": _plan("b", touches=["src/b/**"]),
                    # dep NOT met → held
                    "c": _plan("c", depends_on=["not-yet-done"]),
                },
            )
            report = rd.build_readiness(h)
            ready = set(report["ready"])
            self.assertIn("a", ready)
            self.assertIn("b", ready)
            self.assertNotIn("c", ready)
            self.assertIn("b", report["safe_together"])
            self.assertEqual(len(report["degrade_warnings"]), 1)
            self.assertIn("a", report["degrade_warnings"][0])
            held_slugs = {h["slug"] for h in report["held_back"]}
            self.assertIn("c", held_slugs)


class TestDeterminism(unittest.TestCase):
    """Same input → same report, two consecutive calls."""

    def test_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-rd-det-") as tmp:
            h = _make_harness(
                tmp,
                queued={
                    "x": _plan("x", touches=["src/x/**"]),
                    "y": _plan("y", touches=["src/y/**"]),
                    "z": _plan("z"),  # no touches → degrade
                },
            )
            r1 = rd.build_readiness(h)
            r2 = rd.build_readiness(h)
            self.assertEqual(r1, r2)


class TestEmptyDir(unittest.TestCase):
    def test_empty_returns_empty_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-rd-empty-") as tmp:
            h = Path(tmp) / "_harness"
            h.mkdir()
            report = rd.build_readiness(h)
            self.assertEqual(report["ready"], [])
            self.assertEqual(report["safe_together"], [])
            self.assertEqual(report["held_back"], [])
            self.assertEqual(report["degrade_warnings"], [])


if __name__ == "__main__":
    unittest.main()
