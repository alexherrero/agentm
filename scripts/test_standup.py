#!/usr/bin/env python3
"""Tests for scripts/standup.py (V5-11 task 3).

Contracts verified:

  - correct worker_state derivation for each of the three states
    (building / mergeable / idle);
  - output is deterministic (same input → same list, same order);
  - a plan with all tasks done → mergeable regardless of last_touched age;
  - idle threshold: plan touched > 2h ago with tasks remaining → idle;
  - plan touched recently with tasks remaining → building;
  - queued plans are excluded from the standup table;
  - no live vault required — all tests use fixture data.

Run directly::

    python3 scripts/test_standup.py
"""
from __future__ import annotations

import sys
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import standup as su  # noqa: E402

_FIXTURE_ROOT = _HERE / "fixtures" / "plan_graph" / "_harness"

# A fixed "now" so tests are deterministic.
_NOW = datetime(2026, 6, 16, 12, 0)


class TestWorkerStates(unittest.TestCase):
    """Verify that each worker state is derived correctly from fixture data."""

    def _rows(self, tmp_harness: Path) -> dict:
        rows = su.build_standup(tmp_harness, now=_NOW)
        return {r.slug: r for r in rows}

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-standup-test-")
        harness = Path(self._tmp) / "_harness"
        harness.mkdir(parents=True)
        self._harness = harness

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, name: str, body: str) -> None:
        (self._harness / name).write_text(body, encoding="utf-8")

    def _write_plan(self, slug: str, tasks_done: int, tasks_total: int) -> None:
        tasks = ""
        for i in range(1, tasks_total + 1):
            mark = "x" if i <= tasks_done else " "
            tasks += f"\n### {i}. Task {i}\n- **Status:** [{mark}]\n"
        self._write(
            f"PLAN-{slug}.md",
            f"# Plan: {slug}\n\n**Status:** in-progress\n{tasks}",
        )

    def test_building_state(self) -> None:
        """Plan with tasks remaining + recent touch → building."""
        self._write_plan("alpha", tasks_done=1, tasks_total=3)
        # touched 30 min ago — within the 2h threshold
        recent = (_NOW - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
        self._write("progress-alpha.md", f"{recent} /work — completed task 1\n")
        rows = su.build_standup(self._harness, now=_NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].worker_state, "building")

    def test_mergeable_state(self) -> None:
        """Plan with all tasks done → mergeable, regardless of last_touched age."""
        self._write_plan("alpha", tasks_done=3, tasks_total=3)
        # touched long ago — but all tasks done, so mergeable not idle
        old = (_NOW - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
        self._write("progress-alpha.md", f"{old} /work — completed task 3\n")
        rows = su.build_standup(self._harness, now=_NOW)
        self.assertEqual(rows[0].worker_state, "mergeable")

    def test_idle_state(self) -> None:
        """Plan with tasks remaining + touch > 2h ago → idle."""
        self._write_plan("alpha", tasks_done=1, tasks_total=3)
        old = (_NOW - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
        self._write("progress-alpha.md", f"{old} /work — completed task 1\n")
        rows = su.build_standup(self._harness, now=_NOW)
        self.assertEqual(rows[0].worker_state, "idle")

    def test_idle_no_progress_log(self) -> None:
        """Plan with no progress log and tasks remaining → idle."""
        self._write_plan("alpha", tasks_done=0, tasks_total=2)
        rows = su.build_standup(self._harness, now=_NOW)
        self.assertEqual(rows[0].worker_state, "idle")

    def test_threshold_boundary_just_within(self) -> None:
        """Touch exactly at threshold - 1 minute → building (strictly >)."""
        self._write_plan("alpha", tasks_done=1, tasks_total=3)
        touched = (_NOW - timedelta(hours=2, minutes=-1)).strftime("%Y-%m-%d %H:%M")
        self._write("progress-alpha.md", f"{touched} /work — task 1\n")
        rows = su.build_standup(self._harness, now=_NOW)
        self.assertEqual(rows[0].worker_state, "building")

    def test_threshold_boundary_just_over(self) -> None:
        """Touch exactly at threshold + 1 minute → idle."""
        self._write_plan("alpha", tasks_done=1, tasks_total=3)
        touched = (_NOW - timedelta(hours=2, minutes=1)).strftime("%Y-%m-%d %H:%M")
        self._write("progress-alpha.md", f"{touched} /work — task 1\n")
        rows = su.build_standup(self._harness, now=_NOW)
        self.assertEqual(rows[0].worker_state, "idle")


class TestExclusions(unittest.TestCase):
    """Queued plans are excluded; only active plans appear."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-standup-excl-")
        self._h = Path(self._tmp) / "_harness"
        (self._h / "queued-plans").mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_queued_plans_excluded(self) -> None:
        (self._h / "PLAN-active.md").write_text(
            "# Plan: active\n\n**Status:** in-progress\n\n### 1. Task\n- **Status:** [ ]\n",
            encoding="utf-8",
        )
        (self._h / "queued-plans" / "PLAN-queued.md").write_text(
            "# Plan: queued\n\n**Status:** planning\n\n### 1. Task\n- **Status:** [ ]\n",
            encoding="utf-8",
        )
        rows = su.build_standup(self._h, now=_NOW)
        slugs = [r.slug for r in rows]
        self.assertIn("active", slugs)
        self.assertNotIn("queued", slugs)


class TestDeterminism(unittest.TestCase):
    """Same input → same output, two consecutive calls."""

    def test_deterministic_fixture(self) -> None:
        run1 = [
            (r.slug, r.tasks_done, r.tasks_total, r.worker_state, r.last_touched)
            for r in su.build_standup(_FIXTURE_ROOT, now=_NOW)
        ]
        run2 = [
            (r.slug, r.tasks_done, r.tasks_total, r.worker_state, r.last_touched)
            for r in su.build_standup(_FIXTURE_ROOT, now=_NOW)
        ]
        self.assertEqual(run1, run2)

    def test_table_render_deterministic(self) -> None:
        rows = su.build_standup(_FIXTURE_ROOT, now=_NOW)
        self.assertEqual(su.render_table(rows), su.render_table(rows))


class TestFixtureStates(unittest.TestCase):
    """Check the fixture tree produces the expected states at _NOW."""

    def setUp(self) -> None:
        self._rows = {
            r.slug: r for r in su.build_standup(_FIXTURE_ROOT, now=_NOW)
        }

    def test_singleton_in_progress_is_idle(self) -> None:
        # Last touched 2026-06-12 16:45; _NOW is 2026-06-16 12:00 → > 2h → idle.
        r = self._rows["(singleton)"]
        self.assertEqual(r.worker_state, "idle")
        self.assertEqual(r.tasks_done, 3)
        self.assertEqual(r.tasks_total, 5)

    def test_worker_a_all_done_is_mergeable(self) -> None:
        r = self._rows["worker-a"]
        self.assertEqual(r.worker_state, "mergeable")
        self.assertEqual(r.tasks_done, 3)
        self.assertEqual(r.tasks_total, 3)

    def test_worker_b_partial_is_idle(self) -> None:
        # Last touched 2026-06-13 14:00; _NOW is 2026-06-16 → > 2h → idle.
        r = self._rows["worker-b"]
        self.assertEqual(r.worker_state, "idle")
        self.assertEqual(r.tasks_done, 2)
        self.assertEqual(r.tasks_total, 4)

    def test_worker_c_not_in_standup(self) -> None:
        # worker-c is queued — must not appear.
        self.assertNotIn("worker-c", self._rows)


if __name__ == "__main__":
    unittest.main()
