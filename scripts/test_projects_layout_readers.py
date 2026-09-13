#!/usr/bin/env python3
"""The `_harness` readers in both layouts (agentm-vault plan 09, task 5).

Until the migration moves every flat pair into `tasks/<slug>/`, each live reader
of plan state accepts both: the plan listing, the queue dashboard, the plan
graph, the doctor's `project.json` lookup, the session-start hook's plan block
(bash and PowerShell), and the exclusion rules that keep plan state out of the
memory linters, dreaming and the maps.

Run directly:

    python3 scripts/test_projects_layout_readers.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_SKILL = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_SKILL)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dream  # noqa: E402
import frontmatter_validator as fv  # noqa: E402
import harness_memory as hm  # noqa: E402
import machinery_doctor as md  # noqa: E402
import moc_generator as mg  # noqa: E402
import plan_graph as pg  # noqa: E402
import queue_status_lite as qsl  # noqa: E402
import tracker as tk  # noqa: E402
import vault_lint as vl  # noqa: E402

_HOOK_DIR = _REPO / "harness" / "hooks" / "harness-context-session-start"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _Project(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agentm-layouts-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = self.root / "Projects" / "demo"
        self.harness = self.project / "_harness"
        self.harness.mkdir(parents=True)

    def _flat(self, slug: str, status: str = "planning") -> None:
        _write(self.harness / f"PLAN-{slug}.md", f"# Plan: {slug}\n\n**Status:** {status}\n\n- [x] one\n")
        _write(self.harness / f"progress-{slug}.md", f"2026-09-12 10:00 /work — {slug} flat\n")

    def _task(self, slug: str, status: str = "in-progress") -> Path:
        task = self.project / "tasks" / slug
        _write(task / "plan.md", f"# Plan: {slug}\n\n**Status:** {status}\n\n- [x] one\n- [ ] two\n")
        _write(task / "progress.md", f"2026-09-12 11:00 /work — {slug} task\n")
        return task


class TheListing(_Project):
    def test_both_layouts_are_listed(self) -> None:
        self._flat("foo")
        self._task("bar")
        names = [p.relative_to(self.project).as_posix() for p in hm.list_plan_files(self.harness)]
        self.assertEqual(names, ["_harness/PLAN-foo.md", "tasks/bar/plan.md"])

    def test_a_device_local_harness_does_not_read_the_repos_tasks(self) -> None:
        repo = self.root / "repo"
        _write(repo / ".harness" / "PLAN-foo.md", "# Plan\n")
        _write(repo / "tasks" / "bar" / "plan.md", "# not plan state\n")
        self.assertEqual([p.name for p in hm.list_plan_files(repo / ".harness")], ["PLAN-foo.md"])


class TheQueueDashboard(_Project):
    def test_rows_in_both_layouts(self) -> None:
        self._flat("foo", "planning")
        self._task("bar", "in-progress")
        rows = [(r.plan_name, r.status, r.progress_name, r.progress_head)
                for r in qsl.collect_plan_statuses(self.harness)]
        self.assertEqual(rows, [
            ("PLAN-foo.md", "planning", "progress-foo.md", "2026-09-12 10:00 /work — foo flat"),
            ("tasks/bar/plan.md", "in-progress", "tasks/bar/progress.md", "2026-09-12 11:00 /work — bar task"),
        ])

    def test_a_tracker_beside_a_task_carries_its_status(self) -> None:
        task = self._task("bar", "in-progress")
        t = tk.new(title="Bar", project="demo", task="bar", objective="Done.", next_step="Start.", today="2026-09-12")
        tk.write(task / "tracker.md", t)
        (row,) = qsl.collect_plan_statuses(self.harness)
        self.assertEqual(row.status, "queued")


class ThePlanGraph(_Project):
    def test_both_layouts_build_the_same_fields(self) -> None:
        self._flat("foo")
        self._task("bar")
        plans = {p.slug: p for p in pg.build_plan_graph(self.harness)}
        self.assertEqual(set(plans), {"foo", "bar"})
        self.assertEqual(plans["bar"].filename, "tasks/bar/plan.md")
        self.assertEqual((plans["bar"].tasks_done, plans["bar"].tasks_total), (1, 2))
        self.assertIsNotNone(plans["bar"].last_touched, "the progress log beside the task was not read")
        self.assertEqual([p.slug for p in pg.build_plan_graph(self.harness)], ["foo", "bar"])


class TheExclusionRules(unittest.TestCase):
    def test_the_linters_and_dreaming_skip_a_projects_tasks(self) -> None:
        for name, dirs in (("vault_lint", vl._EXCLUDE_DIRS), ("dream", dream._EXCLUDE_DIRS),
                           ("frontmatter_validator", fv._EXCLUDE_DIRS)):
            with self.subTest(module=name):
                self.assertIn("tasks", dirs)

    def test_the_validator_passes_over_plan_state_in_a_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "desk/projects" / "some-repo" / "tasks" / "bar" / "plan.md", "# Plan\n\nno frontmatter here\n")
            self.assertEqual(fv.validate_vault(vault), {})
            # The control: the same file outside `tasks/` is a finding, so the skip is not vacuous.
            _write(vault / "desk/projects" / "some-repo" / "decisions" / "loose.md", "no frontmatter here\n")
            self.assertTrue(fv.validate_vault(vault))

    def test_the_maps_skip_a_projects_tasks(self) -> None:
        note = "---\ntitle: A\nkind: decision\narc: wave-a\ncreated: 2026-07-01\nslug: {slug}\n---\n\nbody\n"
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "desk/projects" / "agentm" / "tasks" / "bar" / "plan.md", note.format(slug="plan"))
            self.assertEqual(mg.build_arc_groups(vault), {})
            _write(vault / "desk/projects" / "agentm" / "decisions" / "a.md", note.format(slug="a"))
            self.assertIn(("agentm", "wave-a"), mg.build_arc_groups(vault), "the control did not group")


class TheDoctorsProjectJson(unittest.TestCase):
    def test_the_desk_home_is_read_when_harness_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _write(repo / ".harness" / "project.json", json.dumps({"vault_project": "demo"}))
            mem = root / "mem"
            desk_cfg = mem / "desk" / "projects" / "demo" / "desk" / "project.json"
            _write(desk_cfg, json.dumps({"vault_project": "demo"}))
            found = [(p.resolve(), label) for p, label in md.project_json_configs(repo, mem_root=mem)]
            self.assertIn((desk_cfg.resolve(), "vault"), found)
            harness_cfg = mem / "desk" / "projects" / "demo" / "_harness" / "project.json"
            _write(harness_cfg, json.dumps({"vault_project": "demo"}))
            found = [(p.resolve(), label) for p, label in md.project_json_configs(repo, mem_root=mem)]
            self.assertIn((harness_cfg.resolve(), "vault"), found, "the `_harness/` home still comes first")


class _HookStub(unittest.TestCase):
    """The hook reads plan paths from `harness_memory.py list-plans`; a stub clone
    prints a canned listing so the hook's own labelling and binding are tested."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agentm-hook-layouts-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.proj = self.root / "proj"
        self.proj.mkdir()
        self.home = self.root / "home"
        self.clone = self.root / "clone"
        _write(self.home / ".claude" / ".agentm-config.json",
               json.dumps({"schema_version": 2, "source_clones": {"agentm": str(self.clone)}}))

    def _listing(self, *lines: str) -> None:
        body = "".join(f"print({line!r})\n" for line in lines)
        _write(self.clone / "scripts" / "harness_memory.py", body)

    def _env(self) -> dict:
        env = {**os.environ, "HOME": str(self.home)}
        for key in ("AGENTM_INSTALL_PREFIX", "MEMORY_ROOT", "MEMORY_VAULT_PATH"):
            env.pop(key, None)
        return env

    def _run(self) -> subprocess.CompletedProcess:
        raise NotImplementedError

    def test_a_task_plan_is_labelled_and_binds(self) -> None:
        self._listing("/v/Projects/demo/_harness/PLAN-foo.md", "/v/Projects/demo/tasks/bar/plan.md", "active-binding=bar")
        out = self._run().stdout
        self.assertIn("Named-plan mode", out)
        self.assertRegex(out, r"\n  tasks/bar +/v/Projects/demo/tasks/bar/plan\.md\n")
        self.assertIn("Active plan (.harness/active-plan -> bar): /v/Projects/demo/tasks/bar/plan.md", out)
        self.assertNotIn("DANGLING", out)

    def test_a_flat_binding_still_binds(self) -> None:
        self._listing("/v/Projects/demo/_harness/PLAN-foo.md", "/v/Projects/demo/tasks/bar/plan.md", "active-binding=foo")
        out = self._run().stdout
        self.assertIn("Active plan (.harness/active-plan -> foo): /v/Projects/demo/_harness/PLAN-foo.md", out)

    def test_a_binding_to_neither_layout_is_dangling(self) -> None:
        self._listing("/v/Projects/demo/tasks/bar/plan.md", "active-binding=ghost")
        out = self._run().stdout
        # The historic phrase stays, so a reader that matched it still does.
        self.assertIn("DANGLING - PLAN-ghost.md not found, nor tasks/ghost/plan.md", out)

    def test_a_lone_task_plan_is_not_mistaken_for_the_singleton(self) -> None:
        self._listing("/v/Projects/demo/tasks/bar/plan.md")
        self.assertIn("Named-plan mode", self._run().stdout)


@unittest.skipIf(os.name == "nt", "bash hook — POSIX only")
class TheBashHook(_HookStub):
    def _run(self) -> subprocess.CompletedProcess:
        payload = json.dumps({"session_id": "layouts", "cwd": str(self.proj)})
        return subprocess.run(["bash", str(_HOOK_DIR / "harness-context-session-start.sh")],
                              input=payload, env=self._env(), capture_output=True, text=True)


@unittest.skipIf(shutil.which("pwsh") is None, "pwsh not on PATH")
class ThePowerShellHook(_HookStub):
    def _run(self) -> subprocess.CompletedProcess:
        payload = json.dumps({"session_id": "layouts", "cwd": str(self.proj)})
        return subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File",
                               str(_HOOK_DIR / "harness-context-session-start.ps1")],
                              input=payload, env=self._env(), capture_output=True, text=True)


del _HookStub  # only its subclasses run


if __name__ == "__main__":
    unittest.main()
