#!/usr/bin/env python3
"""Crickets' phase commands find a task the night has moved (task 177, step 3).

Two weeks after a task closes, its directory moves whole to the project's
`completed/tasks/`. Crickets' development-lifecycle derives no path of its own:
`resolve_plan.py` asks agentm's process seam, and `plan_tracker.py status`
reads the tracker it is handed. So crickets needs no change if agentm answers
the moved paths — and this runs crickets' own scripts, as a phase command
does, against a fixture vault holding one moved task to prove it.

It runs where it can: the crickets sibling checkout present, and a synced
vault backend selectable on this machine. Continuous integration has neither,
and skips; the in-process resolver contract is pinned in
`test_resolve_active_plan.py` whatever the machine.

Run directly:

    python3 scripts/test_crickets_finds_a_moved_task.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import sibling_repo_root  # noqa: E402

_TRACKER = """---
kind: tracker
title: "Closed thing"
project: fx
task: 002-closed-thing
status: done
opened: 2026-09-01
updated: 2026-09-02
closed: 2026-09-02
---

## Objective

A thing.

## State

Done.

## Next

Nothing.

## Outcome

It shipped.
"""


def _crickets_scripts() -> "Path | None":
    root = sibling_repo_root.sibling_layout_root(_HERE)
    if root is None:
        return None
    scripts = root / "crickets" / "src" / "development-lifecycle" / "scripts"
    if (scripts / "resolve_plan.py").is_file() and (scripts / "plan_tracker.py").is_file():
        return scripts
    return None


class CricketsFindsAMovedTask(unittest.TestCase):
    def setUp(self) -> None:
        self.crickets = _crickets_scripts()
        if self.crickets is None:
            self.skipTest("no crickets sibling checkout")
        self._tmp = tempfile.mkdtemp(prefix="agentm-moved-task-")
        root = Path(self._tmp)
        self.repo = root / "repo"
        (self.repo / ".harness").mkdir(parents=True)
        (self.repo / ".harness" / "project.json").write_text(
            '{"vault_project": "fx"}\n', encoding="utf-8")
        self.vault = root / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        (self.vault / "agent" / "memory").mkdir(parents=True)
        project = self.vault / "projects" / "fx"
        self.moved = project / "completed" / "tasks" / "002-closed-thing"
        self.moved.mkdir(parents=True)
        (self.moved / "plan.md").write_text("# plan\n", encoding="utf-8")
        (self.moved / "progress.md").write_text("log\n", encoding="utf-8")
        (self.moved / "tracker.md").write_text(_TRACKER, encoding="utf-8")
        self.open = project / "tasks" / "003-open-thing"
        self.open.mkdir(parents=True)
        (self.open / "plan.md").write_text("# plan\n", encoding="utf-8")
        self.env = dict(os.environ, MEMORY_ROOT=str(self.vault / "agent"),
                        AGENTM_SCRIPTS_DIR=str(_HERE))
        self.env.pop("MEMORY_VAULT_PATH", None)
        if not self._synced():
            self.skipTest("no synced vault backend selectable on this machine")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _synced(self) -> bool:
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); import harness_memory as hm; "
                 "print(hm.resolve_project({'cwd': sys.argv[2]}).get('layout'))")
        out = subprocess.run([sys.executable, "-c", probe, str(_HERE), str(self.repo)],
                             capture_output=True, text=True, env=self.env, cwd=self.repo)
        return out.returncode == 0 and out.stdout.strip() == "root"

    def _resolve(self, name: str) -> list:
        out = subprocess.run(
            [sys.executable, str(self.crickets / "resolve_plan.py"), name,
             "--project-root", str(self.repo)],
            capture_output=True, text=True, env=self.env, cwd=self.repo)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.rstrip("\n").split("\t")

    def test_resolve_plan_answers_the_completed_paths_by_either_name(self) -> None:
        want = [str(self.moved / f) for f in ("plan.md", "progress.md", "tracker.md")]
        for name in ("002-closed-thing", "closed-thing"):
            with self.subTest(name=name):
                self.assertEqual([str(Path(p).resolve()) for p in self._resolve(name)],
                                 [str(Path(p).resolve()) for p in want])

    def test_the_duplicate_guard_reads_a_moved_task_done(self) -> None:
        plan, _progress, tracker = self._resolve("closed-thing")
        out = subprocess.run(
            [sys.executable, str(self.crickets / "plan_tracker.py"), "status",
             "--plan", plan, "--tracker", tracker],
            capture_output=True, text=True, env=self.env, cwd=self.repo)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split("\t")[0], "done")

    def test_a_new_plan_is_numbered_past_the_moved_task(self) -> None:
        plan = Path(self._resolve("new-thing")[0])
        self.assertEqual((plan.parent.name, plan.parent.parent.name), ("004-new-thing", "tasks"))


if __name__ == "__main__":
    unittest.main()
