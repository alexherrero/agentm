#!/usr/bin/env python3
"""Tests for scripts/plan_graph.py (V5-11 task 1).

`plan_graph` is the shared map engine used by all three team-coordinator
capability scripts.  These tests lock the load-bearing contracts:

  - active plans enumerated (singleton + named), queued plans from
    queued-plans/ subdir;
  - status + task counts extracted correctly;
  - last_touched parsed from progress log timestamps;
  - depends_on + touches read from YAML frontmatter (inline + block forms);
  - plans with no frontmatter default to empty depends_on/touches;
  - read-only: the fixture directory is byte-identical before and after;
  - output is deterministic (same input → same list).

Run directly::

    python3 scripts/test_plan_graph.py

Or via the standard test discovery::

    cd scripts && python3 -m unittest test_plan_graph
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import plan_graph as pg  # noqa: E402

_FIXTURE_ROOT = _HERE / "fixtures" / "plan_graph" / ".harness"


def _snapshot(d: Path) -> dict:
    return {
        p.relative_to(d).as_posix(): p.read_bytes()
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


class TestFixtureTree(unittest.TestCase):
    """Tests that run against the committed fixtures/plan_graph/ tree."""

    def test_enumerates_four_plans(self) -> None:
        plans = pg.build_plan_graph(_FIXTURE_ROOT)
        self.assertEqual(len(plans), 4)

    def test_singleton_is_first(self) -> None:
        plans = pg.build_plan_graph(_FIXTURE_ROOT)
        self.assertEqual(plans[0].filename, "PLAN.md")
        self.assertEqual(plans[0].slug, "")

    def test_active_plans_before_queued(self) -> None:
        plans = pg.build_plan_graph(_FIXTURE_ROOT)
        active = [p for p in plans if p.active]
        queued = [p for p in plans if not p.active]
        self.assertEqual(len(active), 3)
        self.assertEqual(len(queued), 1)
        # Every active plan comes before any queued plan in the list.
        active_idx = [plans.index(p) for p in active]
        queued_idx = [plans.index(p) for p in queued]
        self.assertLess(max(active_idx), min(queued_idx))

    def test_status_extracted(self) -> None:
        plans = {p.slug: p for p in pg.build_plan_graph(_FIXTURE_ROOT)}
        self.assertEqual(plans[""].status, "in-progress")
        self.assertEqual(plans["worker-a"].status, "done")
        self.assertEqual(plans["worker-b"].status, "in-progress")
        self.assertEqual(plans["worker-c"].status, "planning")

    def test_task_counts(self) -> None:
        plans = {p.slug: p for p in pg.build_plan_graph(_FIXTURE_ROOT)}
        self.assertEqual((plans[""].tasks_done, plans[""].tasks_total), (3, 5))
        self.assertEqual((plans["worker-a"].tasks_done, plans["worker-a"].tasks_total), (3, 3))
        self.assertEqual((plans["worker-b"].tasks_done, plans["worker-b"].tasks_total), (2, 4))
        self.assertEqual((plans["worker-c"].tasks_done, plans["worker-c"].tasks_total), (0, 3))

    def test_last_touched_parsed(self) -> None:
        plans = {p.slug: p for p in pg.build_plan_graph(_FIXTURE_ROOT)}
        # Singleton — most-recent line is 2026-06-12 16:45
        self.assertEqual(plans[""].last_touched, datetime(2026, 6, 12, 16, 45))
        # worker-a — 2026-06-05 09:30
        self.assertEqual(plans["worker-a"].last_touched, datetime(2026, 6, 5, 9, 30))
        # worker-c has no progress log
        self.assertIsNone(plans["worker-c"].last_touched)

    def test_depends_on_block_form(self) -> None:
        plans = {p.slug: p for p in pg.build_plan_graph(_FIXTURE_ROOT)}
        self.assertEqual(plans["worker-b"].depends_on, ["worker-a"])

    def test_touches_block_form(self) -> None:
        plans = {p.slug: p for p in pg.build_plan_graph(_FIXTURE_ROOT)}
        self.assertEqual(plans["worker-a"].touches, ["src/testing/**", "scripts/check-all.sh"])

    def test_no_frontmatter_defaults_empty(self) -> None:
        plans = {p.slug: p for p in pg.build_plan_graph(_FIXTURE_ROOT)}
        self.assertEqual(plans[""].depends_on, [])
        self.assertEqual(plans[""].touches, [])

    def test_queued_plan_has_both_fields(self) -> None:
        plans = {p.slug: p for p in pg.build_plan_graph(_FIXTURE_ROOT)}
        self.assertEqual(plans["worker-c"].depends_on, ["worker-b"])
        self.assertEqual(plans["worker-c"].touches, ["src/api/**"])

    def test_read_only(self) -> None:
        before = _snapshot(_FIXTURE_ROOT)
        pg.build_plan_graph(_FIXTURE_ROOT)
        after = _snapshot(_FIXTURE_ROOT)
        self.assertEqual(before, after)

    def test_deterministic(self) -> None:
        run1 = [(p.slug, p.status, p.tasks_done, p.tasks_total, p.active)
                for p in pg.build_plan_graph(_FIXTURE_ROOT)]
        run2 = [(p.slug, p.status, p.tasks_done, p.tasks_total, p.active)
                for p in pg.build_plan_graph(_FIXTURE_ROOT)]
        self.assertEqual(run1, run2)


class TestInlineFrontmatter(unittest.TestCase):
    """Inline-form ``depends_on: [a, b]`` and ``touches: [src/**]``."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-pg-inline-")
        self._h = Path(self._tmp) / ".harness"
        self._h.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, name: str, body: str) -> None:
        (self._h / name).write_text(body, encoding="utf-8")

    def test_inline_depends_on(self) -> None:
        self._write(
            "PLAN-alpha.md",
            "---\ndepends_on: [beta, gamma]\n---\n# Plan: alpha\n\n**Status:** planning\n",
        )
        plans = {p.slug: p for p in pg.build_plan_graph(self._h)}
        self.assertEqual(plans["alpha"].depends_on, ["beta", "gamma"])

    def test_inline_touches(self) -> None:
        self._write(
            "PLAN-alpha.md",
            "---\ntouches: [src/**, tests/**]\n---\n# Plan: alpha\n\n**Status:** planning\n",
        )
        plans = {p.slug: p for p in pg.build_plan_graph(self._h)}
        self.assertEqual(plans["alpha"].touches, ["src/**", "tests/**"])

    def test_empty_inline_list(self) -> None:
        self._write(
            "PLAN-alpha.md",
            "---\ndepends_on: []\ntouches: []\n---\n# Plan: alpha\n\n**Status:** planning\n",
        )
        plans = {p.slug: p for p in pg.build_plan_graph(self._h)}
        self.assertEqual(plans["alpha"].depends_on, [])
        self.assertEqual(plans["alpha"].touches, [])


class TestEmptyHarnessDir(unittest.TestCase):
    def test_empty_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-pg-empty-") as d:
            harness = Path(d) / ".harness"
            harness.mkdir()
            self.assertEqual(pg.build_plan_graph(harness), [])

    def test_missing_dir_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentm-pg-missing-") as d:
            harness = Path(d) / ".harness"
            # Do NOT create it — build_plan_graph must tolerate a missing dir.
            result = pg.build_plan_graph(harness)
            self.assertEqual(result, [])


def _task_fixture(project: Path, name: str, status: str, *, steps=(0, 0),
                  depends_on=(), touches=(), plan_status=None) -> Path:
    """One task in the vault layout (agentm-vault plan 15): `tasks/<name>/` with
    a plan, and a tracker carrying `status`. `plan_status` writes the retired
    `**Status:**` line into the plan, to prove the tracker wins over it."""
    import tracker as tk
    task = project / "tasks" / name
    task.mkdir(parents=True)
    fm = ""
    if depends_on or touches:
        fm = "---\n"
        if depends_on:
            fm += f"depends_on: [{', '.join(depends_on)}]\n"
        if touches:
            fm += f"touches: [{', '.join(touches)}]\n"
        fm += "---\n"
    body = f"{fm}# Plan: {name}\n\n" + (f"**Status:** {plan_status}\n" if plan_status else "")
    done, total = steps
    for i in range(1, total + 1):
        body += f"\n### {i}. Step {i}\n- [{'x' if i <= done else ' '}]\n"
    (task / "plan.md").write_text(body, encoding="utf-8")
    if status is not None:
        t = tk.Tracker(title=name, project="fixture", task=name, status=status,
                       opened="2026-09-01", updated="2026-09-20",
                       closed="2026-09-02" if status in tk.FINAL else None)
        (task / "tracker.md").write_text(tk.render(t), encoding="utf-8")
    return task


class TestTaskLayout(unittest.TestCase):
    """A project directory in the vault: every plan is a task, and its status is
    the tracker's (agentm-vault plan 15)."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-pg-tasks-")
        self.project = Path(self._tmp) / "projects" / "fixture"
        self.project.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _plans(self) -> dict:
        return {p.slug: p for p in pg.build_plan_graph(self.project)}

    def test_the_trackers_status_wins_over_the_status_line(self) -> None:
        _task_fixture(self.project, "001-build-it", "active", plan_status="planning")
        plan = self._plans()["001-build-it"]
        self.assertEqual(plan.status, "active")
        self.assertEqual(plan.filename, "tasks/001-build-it/plan.md")
        self.assertTrue(plan.active)
        self.assertFalse(plan.finished)

    def test_the_status_line_is_read_only_when_no_tracker_parses(self) -> None:
        task = _task_fixture(self.project, "001-build-it", None, plan_status="in-progress")
        self.assertEqual(self._plans()["001-build-it"].status, "in-progress")
        (task / "tracker.md").write_text("not a tracker\n", encoding="utf-8")
        self.assertEqual(self._plans()["001-build-it"].status, "in-progress")

    def test_a_queued_task_is_staged_and_a_final_one_is_finished(self) -> None:
        _task_fixture(self.project, "001-ship-it", "done")
        _task_fixture(self.project, "002-drop-it", "dropped")
        _task_fixture(self.project, "003-plan-it", "queued")
        plans = self._plans()
        self.assertEqual([p for p in plans], ["001-ship-it", "002-drop-it", "003-plan-it"])
        self.assertTrue(plans["001-ship-it"].finished and plans["002-drop-it"].finished)
        self.assertFalse(plans["001-ship-it"].active)
        self.assertFalse(plans["003-plan-it"].active)
        self.assertFalse(plans["003-plan-it"].finished)

    def test_the_progress_log_beside_the_task_dates_it(self) -> None:
        task = _task_fixture(self.project, "001-build-it", "active")
        (task / "progress.md").write_text("2026-09-21 14:05 /work — step 1\n", encoding="utf-8")
        self.assertEqual(self._plans()["001-build-it"].last_touched, datetime(2026, 9, 21, 14, 5))

    def test_a_flat_pair_in_a_project_directory_is_not_a_plan(self) -> None:
        _task_fixture(self.project, "001-build-it", "active")
        (self.project / "PLAN-stray.md").write_text("**Status:** planning\n", encoding="utf-8")
        self.assertEqual(list(self._plans()), ["001-build-it"])


if __name__ == "__main__":
    unittest.main()
