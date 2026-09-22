#!/usr/bin/env python3
"""The plan-state readers in the task layout (agentm-vault plans 09 and 15).

A project's state root is its own vault directory: its plans are the tasks under
`tasks/`, its machine files sit in `desk/`. Each live reader resolves from that
skeleton alone: the plan listing, the queue dashboard, the plan graph, the
doctor's `project.json` lookup, the session-start hook's plan block (bash and
PowerShell), and the exclusion rules that keep plan state out of the memory
linters, dreaming and the maps. A flat pair in a stray copy of the retired state
directory is never read; the flat pair lives only in a repo-local `.harness/`,
for a project with no vault.

Run directly:

    python3 scripts/test_projects_layout_readers.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
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
# The retired per-project state directory, spelled once: the tests below build a
# stray copy of it on purpose, to prove no reader looks inside.
_RETIRED = "_harness"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _Project(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agentm-layouts-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = self.root / "projects" / "demo"
        self.project.mkdir(parents=True)
        self.local = self.root / "repo" / ".harness"

    def _stray_flat(self, slug: str) -> None:
        _write(self.project / _RETIRED / f"PLAN-{slug}.md", f"# Plan: {slug}\n\n**Status:** planning\n")
        _write(self.project / _RETIRED / f"progress-{slug}.md", f"2026-09-12 10:00 /work — {slug} flat\n")

    def _local_flat(self, slug: str, status: str = "planning") -> None:
        _write(self.local / f"PLAN-{slug}.md", f"# Plan: {slug}\n\n**Status:** {status}\n\n- [x] one\n")
        _write(self.local / f"progress-{slug}.md", f"2026-09-12 10:00 /work — {slug} flat\n")

    def _task(self, slug: str, status: str = "in-progress") -> Path:
        task = self.project / "tasks" / slug
        _write(task / "plan.md", f"# Plan: {slug}\n\n**Status:** {status}\n\n- [x] one\n- [ ] two\n")
        _write(task / "progress.md", f"2026-09-12 11:00 /work — {slug} task\n")
        return task


class TheListing(_Project):
    def test_the_tasks_are_listed_from_the_project_directory(self) -> None:
        self._task("bar")
        self._task("baz")
        names = [p.relative_to(self.project).as_posix() for p in hm.list_plan_files(self.project)]
        self.assertEqual(names, ["tasks/bar/plan.md", "tasks/baz/plan.md"])

    def test_a_stray_flat_pair_is_not_listed(self) -> None:
        self._stray_flat("foo")
        self._task("bar")
        names = [p.relative_to(self.project).as_posix() for p in hm.list_plan_files(self.project)]
        self.assertEqual(names, ["tasks/bar/plan.md"])

    def test_a_device_local_harness_does_not_read_the_repos_tasks(self) -> None:
        repo = self.root / "repo"
        _write(repo / ".harness" / "PLAN-foo.md", "# Plan\n")
        _write(repo / "tasks" / "bar" / "plan.md", "# not plan state\n")
        self.assertEqual([p.name for p in hm.list_plan_files(repo / ".harness")], ["PLAN-foo.md"])


class TheListPlansVerb(unittest.TestCase):
    """`harness_memory.py list-plans` on a project in the vault: open tasks only.

    A finished plan used to leave the listing by moving into `archive/`; a task
    never moves, so its tracker is what says it is over. Before this the verb
    printed every task, and the session-start hook read 172 of them, 164 done,
    as "more than one active plan" (2026-09-17)."""

    def setUp(self) -> None:
        from storage_seam import Locator
        from vault_backend_stub import VaultBackend

        self.root = Path(tempfile.mkdtemp(prefix="agentm-list-plans-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.vault = self.root / "vault"
        self.repo = self.root / "repo"
        (self.repo / ".harness").mkdir(parents=True)
        self.tasks = self.vault / "projects" / "demo" / "tasks"
        self.tasks.mkdir(parents=True)
        self.resolution = {
            "backend": VaultBackend(root=self.vault, lock_root=self.root / "locks"),
            "project_locator": Locator("projects/demo"),
            "project_root": self.repo,
            "slug": "demo",
        }

    def _task(self, name: str, status=None) -> None:
        """A task, with its tracker walked to `status` through the schema's own
        transitions (None writes no tracker)."""
        task = self.tasks / name
        _write(task / "plan.md", f"# Plan: {name}\n")
        if status is None:
            return
        t = tk.new(title=name, project="demo", task=name, objective="Do it.",
                   next_step="Start.", today="2026-09-10")
        path = {"queued": (), "active": ("active",), "parked": ("active", "parked"),
                "done": ("active", "done"), "dropped": ("dropped",)}[status]
        for to in path:
            t = tk.transition(t, to, today="2026-09-11",
                              outcome="Finished." if to in tk.FINAL else None)
        tk.write(task / "tracker.md", t)

    def _listed(self) -> list:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), unittest.mock.patch.object(
                hm, "resolve_project", return_value=self.resolution):
            rc = hm.main(["list-plans", "--project-root", str(self.repo)])
        self.assertEqual(rc, 0)
        return [Path(line).parent.name for line in out.getvalue().splitlines() if line]

    def test_done_and_dropped_tasks_are_left_out(self) -> None:
        self._task("001-ship-it", "done")
        self._task("002-abandon-it", "dropped")
        self._task("003-build-it", "active")
        self._task("004-plan-it", "queued")
        self._task("005-pause-it", "parked")
        self.assertEqual(self._listed(), ["003-build-it", "004-plan-it", "005-pause-it"])

    def test_a_task_without_a_readable_tracker_is_still_listed(self) -> None:
        self._task("001-no-tracker")
        self._task("002-broken-tracker")
        _write(self.tasks / "002-broken-tracker" / "tracker.md", "not a tracker\n")
        self._task("003-ship-it", "done")
        self.assertEqual(self._listed(), ["001-no-tracker", "002-broken-tracker"])

    def test_the_listing_function_still_returns_every_task(self) -> None:
        # The dashboards read `list_plan_files` and show finished work; only the
        # verb leaves it out.
        self._task("001-ship-it", "done")
        self._task("002-build-it", "active")
        names = [p.parent.name for p in hm.list_plan_files(self.tasks.parent)]
        self.assertEqual(names, ["001-ship-it", "002-build-it"])

    def test_a_stray_flat_pair_beside_the_tasks_is_not_listed(self) -> None:
        self._task("001-build-it", "active")
        _write(self.tasks.parent / _RETIRED / "PLAN-stray.md", "# a stray plan\n")
        self.assertEqual(self._listed(), ["001-build-it"])


class TheQueueDashboard(_Project):
    def test_a_task_row_falls_back_to_its_status_line_without_a_tracker(self) -> None:
        self._task("bar", "in-progress")
        rows = [(r.plan_name, r.status, r.progress_name, r.progress_head)
                for r in qsl.collect_plan_statuses(self.project)]
        self.assertEqual(rows, [
            ("tasks/bar/plan.md", "in-progress", "tasks/bar/progress.md", "2026-09-12 11:00 /work — bar task"),
        ])

    def test_a_tracker_beside_a_task_carries_its_status(self) -> None:
        task = self._task("bar", "in-progress")
        t = tk.new(title="Bar", project="demo", task="bar", objective="Done.", next_step="Start.", today="2026-09-12")
        tk.write(task / "tracker.md", t)
        (row,) = qsl.collect_plan_statuses(self.project)
        self.assertEqual(row.status, "queued")

    def test_a_repo_local_pair_has_its_own_rows(self) -> None:
        self._local_flat("foo", "planning")
        rows = [(r.plan_name, r.status, r.progress_name, r.progress_head)
                for r in qsl.collect_plan_statuses(self.local)]
        self.assertEqual(rows, [
            ("PLAN-foo.md", "planning", "progress-foo.md", "2026-09-12 10:00 /work — foo flat"),
        ])


class ThePlanGraph(_Project):
    def test_a_task_builds_its_fields_from_its_own_directory(self) -> None:
        self._stray_flat("foo")
        self._task("bar")
        plans = {p.slug: p for p in pg.build_plan_graph(self.project)}
        self.assertEqual(set(plans), {"bar"})
        self.assertEqual(plans["bar"].filename, "tasks/bar/plan.md")
        self.assertEqual((plans["bar"].tasks_done, plans["bar"].tasks_total), (1, 2))
        self.assertIsNotNone(plans["bar"].last_touched, "the progress log beside the task was not read")

    def test_a_repo_local_pair_builds_the_same_fields(self) -> None:
        self._local_flat("foo")
        (plan,) = pg.build_plan_graph(self.local)
        self.assertEqual((plan.slug, plan.filename, plan.status), ("foo", "PLAN-foo.md", "planning"))
        self.assertIsNotNone(plan.last_touched)


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
    def test_the_desk_home_is_read_and_a_stray_retired_copy_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _write(repo / ".harness" / "project.json", json.dumps({"vault_project": "demo"}))
            mem = root / "mem"
            desk_cfg = mem / "desk" / "projects" / "demo" / "desk" / "project.json"
            _write(desk_cfg, json.dumps({"vault_project": "demo"}))
            stray_cfg = mem / "desk" / "projects" / "demo" / _RETIRED / "project.json"
            _write(stray_cfg, json.dumps({"vault_project": "demo"}))
            found = [(p.resolve(), label) for p, label in md.project_json_configs(repo, mem_root=mem)]
            self.assertIn((desk_cfg.resolve(), "vault"), found)
            self.assertNotIn(stray_cfg.resolve(), [p for p, _label in found])


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
        self._listing("/v/projects/demo/tasks/foo/plan.md", "/v/projects/demo/tasks/bar/plan.md", "active-binding=bar")
        out = self._run().stdout
        self.assertIn("Named-plan mode", out)
        self.assertRegex(out, r"\n  tasks/bar +/v/projects/demo/tasks/bar/plan\.md\n")
        self.assertIn("Active plan (.harness/active-plan -> bar): /v/projects/demo/tasks/bar/plan.md", out)
        self.assertNotIn("DANGLING", out)

    def test_a_repo_local_binding_still_binds(self) -> None:
        # A project with no vault keeps its named plans in the repo's .harness/.
        self._listing("/r/repo/.harness/PLAN-foo.md", "/r/repo/.harness/PLAN-bar.md", "active-binding=foo")
        out = self._run().stdout
        self.assertIn("Active plan (.harness/active-plan -> foo): /r/repo/.harness/PLAN-foo.md", out)

    def test_a_binding_to_neither_layout_is_dangling(self) -> None:
        self._listing("/v/projects/demo/tasks/bar/plan.md", "active-binding=ghost")
        out = self._run().stdout
        # The historic phrase stays, so a reader that matched it still does.
        self.assertIn("DANGLING - PLAN-ghost.md not found, nor tasks/ghost/plan.md", out)

    def test_a_lone_task_plan_is_not_mistaken_for_the_singleton(self) -> None:
        self._listing("/v/projects/demo/tasks/bar/plan.md")
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
