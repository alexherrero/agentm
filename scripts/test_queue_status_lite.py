#!/usr/bin/env python3
"""Tests for scripts/queue_status_lite.py (V5-10 part 1, task 3).

`queue_status_lite` is the **read-only** coordinator's-glance dashboard: enumerate
every active plan — a project's unfinished tasks, or a repo-local `.harness/`'s
`PLAN.md` plus each named `PLAN-<name>.md` — and report each plan's status and
the head of its progress log. The load-bearing contracts these tests lock:

  - all active plans listed (singleton + named), archives and GDrive conflict
    copies excluded;
  - status + progress-head extracted correctly (bold and un-bold `Status:`);
  - **zero filesystem mutation** — the directory is byte-identical afterwards;
  - the CLI always exits 0 (a status read, never a gate);
  - a project's tasks are listed with their tracker's status, finished ones left out;
  - `state_dir` resolves the right directory per state mode.

Run directly:

    python3 scripts/test_queue_status_lite.py
"""
from __future__ import annotations

import contextlib
import io
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

import harness_memory as hm  # noqa: E402
import queue_status_lite as qsl  # noqa: E402

# Sandbox AGENTM_INSTALL_PREFIX module-wide so `state_dir`'s mode probe
# (`_read_project_mode` → `_read_config_state_mode`) never reads the operator's
# real ~/.claude/.agentm-config.json — a device default of state_mode=local would
# flip the vault-mode resolution test. Mirrors the sibling named-plan tests.
_TEST_INSTALL_PREFIX = tempfile.mkdtemp(prefix="agentm-test-queue-status-prefix-")


def setUpModule() -> None:  # noqa: N802 — unittest convention
    os.environ["AGENTM_INSTALL_PREFIX"] = _TEST_INSTALL_PREFIX


def tearDownModule() -> None:  # noqa: N802
    os.environ.pop("AGENTM_INSTALL_PREFIX", None)
    shutil.rmtree(_TEST_INSTALL_PREFIX, ignore_errors=True)


def _snapshot(d: Path) -> dict:
    """Map every file under `d` to its bytes — the before/after image used to
    prove the read is non-mutating."""
    return {
        p.relative_to(d).as_posix(): p.read_bytes()
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


class QueueStatusLiteFixture(unittest.TestCase):
    """A repo-local `.harness/` — a project with no vault — with three active
    plans, the singleton plus two named, each with a matching progress log,
    exercising enumeration + extraction."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-queue-status-")
        self.harness = Path(self._tmp) / ".harness"
        self.harness.mkdir(parents=True)
        # Singleton plan — bold Status: form.
        self._write("PLAN.md", "# Plan: legacy\n\n**Status:** in-progress\n")
        self._write(
            "progress.md",
            "2026-06-12 10:00 /work — completed task 1\n"
            "2026-06-12 11:00 /work — completed task 2\n",
        )
        # Named plan foo — bold Status: form.
        self._write("PLAN-foo.md", "# Plan: foo\n\n**Status:** planning\n")
        self._write("progress-foo.md", "2026-06-12 09:30 /plan — created plan foo\n")
        # Named plan bar — un-bold Status: form (the other extraction branch).
        self._write("PLAN-bar.md", "# Plan: bar\n\nStatus: done\n")
        self._write("progress-bar.md", "2026-06-12 08:00 /release — shipped bar\n")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, name: str, body: str) -> None:
        (self.harness / name).write_text(body, encoding="utf-8")

    # --- enumeration ---

    def test_lists_all_three_plans_sorted(self) -> None:
        names = [p.name for p in qsl.list_plan_files(self.harness)]
        # Singleton first, then named plans alphabetically — deterministic order.
        self.assertEqual(names, ["PLAN.md", "PLAN-bar.md", "PLAN-foo.md"])

    def test_excludes_archives_and_conflicts(self) -> None:
        # An archived plan (PLAN.archive.*.md) and a GDrive conflict copy must
        # never show up as active plans.
        self._write("PLAN.archive.20260101-old.md", "**Status:** done\n")
        self._write(
            "PLAN-foo (conflicted copy 2026-06-12).md", "**Status:** planning\n"
        )
        names = [p.name for p in qsl.list_plan_files(self.harness)]
        self.assertEqual(names, ["PLAN.md", "PLAN-bar.md", "PLAN-foo.md"])

    # --- extraction ---

    def test_status_and_progress_head(self) -> None:
        rows = {r.plan_name: r for r in qsl.collect_plan_statuses(self.harness)}
        self.assertEqual(rows["PLAN.md"].status, "in-progress")
        self.assertEqual(
            rows["PLAN.md"].progress_head,
            "2026-06-12 11:00 /work — completed task 2",
        )
        self.assertEqual(rows["PLAN-foo.md"].status, "planning")
        self.assertEqual(rows["PLAN-foo.md"].progress_name, "progress-foo.md")
        self.assertEqual(
            rows["PLAN-foo.md"].progress_head,
            "2026-06-12 09:30 /plan — created plan foo",
        )
        # Un-bold `Status: done` resolves identically to the bold form.
        self.assertEqual(rows["PLAN-bar.md"].status, "done")

    def test_missing_progress_file_is_placeholder(self) -> None:
        (self.harness / "progress-foo.md").unlink()
        rows = {r.plan_name: r for r in qsl.collect_plan_statuses(self.harness)}
        self.assertEqual(rows["PLAN-foo.md"].progress_head, "(no progress file)")

    def test_status_missing_is_emdash(self) -> None:
        self._write("PLAN-baz.md", "# Plan: baz\n\n(no status line yet)\n")
        self._write("progress-baz.md", "seed\n")
        rows = {r.plan_name: r for r in qsl.collect_plan_statuses(self.harness)}
        self.assertEqual(rows["PLAN-baz.md"].status, "—")

    def test_progress_head_truncates_long_line(self) -> None:
        long_line = "x" * 400
        self._write("progress-foo.md", long_line + "\n")
        rows = {r.plan_name: r for r in qsl.collect_plan_statuses(self.harness)}
        head = rows["PLAN-foo.md"].progress_head
        self.assertLessEqual(len(head), qsl._PROGRESS_HEAD_MAXLEN)
        self.assertTrue(head.endswith("…"))

    # --- read-only contract ---

    def test_read_only_zero_mutation(self) -> None:
        before = _snapshot(self.harness)
        qsl.collect_plan_statuses(self.harness)
        qsl.render(self.harness, qsl.collect_plan_statuses(self.harness))
        after = _snapshot(self.harness)
        self.assertEqual(before, after)

    # --- CLI ---

    def test_cli_exits_zero(self) -> None:
        script = _HERE / "queue_status_lite.py"
        proc = subprocess.run(
            [sys.executable, str(script), "--state-dir", str(self.harness)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PLAN-foo.md", proc.stdout)
        self.assertIn("PLAN-bar.md", proc.stdout)
        # Still non-mutating when driven through the real entrypoint.
        self.assertEqual(
            [p.name for p in qsl.list_plan_files(self.harness)],
            ["PLAN.md", "PLAN-bar.md", "PLAN-foo.md"],
        )

    def test_empty_dir_exits_zero(self) -> None:
        # `--harness-dir` stays as an alias of `--state-dir`.
        empty = Path(self._tmp) / "empty" / ".harness"
        empty.mkdir(parents=True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(qsl.main(["--harness-dir", str(empty)]), 0)

    def test_unresolved_dir_exits_zero(self) -> None:
        missing = Path(self._tmp) / "does_not_exist"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(qsl.main(["--harness-dir", str(missing)]), 0)


class TaskLayoutDashboard(unittest.TestCase):
    """A project directory in the vault (agentm-vault plan 15): its plans are the
    tasks under `tasks/`, each task's status is its tracker's, and a finished
    task is not an active plan."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-queue-status-tasks-")
        self.project = Path(self._tmp) / "projects" / "fixture"
        self._task("001-ship-the-old-thing", "done", "2026-09-01 10:00 /work — closed\n")
        self._task("002-build-the-brief", "active", "2026-09-20 09:00 /work — step 2\n",
                   plan="# Plan\n\n**Status:** in-progress\n")
        self._task("003-plan-the-next", "queued", "")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _task(self, name: str, status: str, progress: str, plan: str = "# Plan\n") -> None:
        import tracker as tk
        task = self.project / "tasks" / name
        task.mkdir(parents=True)
        (task / "plan.md").write_text(plan, encoding="utf-8")
        if progress:
            (task / "progress.md").write_text(progress, encoding="utf-8")
        body = tk.Tracker(title=name, project="fixture", task=name, status=status,
                          opened="2026-09-01", updated="2026-09-20",
                          closed="2026-09-02" if status == "done" else None)
        (task / "tracker.md").write_text(tk.render(body), encoding="utf-8")

    def test_the_unfinished_tasks_are_listed_by_name(self) -> None:
        labels = [qsl._plan_label(p) for p in qsl.list_plan_files(self.project)]
        self.assertEqual(labels, ["tasks/002-build-the-brief/plan.md",
                                  "tasks/003-plan-the-next/plan.md"])

    def test_status_is_the_trackers_and_progress_sits_beside_the_plan(self) -> None:
        rows = {r.plan_name: r for r in qsl.collect_plan_statuses(self.project)}
        brief = rows["tasks/002-build-the-brief/plan.md"]
        # The tracker says active; the plan's own Status line is not read.
        self.assertEqual(brief.status, "active")
        self.assertEqual(brief.progress_name, "tasks/002-build-the-brief/progress.md")
        self.assertEqual(brief.progress_head, "2026-09-20 09:00 /work — step 2")
        self.assertEqual(rows["tasks/003-plan-the-next/plan.md"].status, "queued")

    def test_a_flat_pair_beside_the_tasks_is_not_a_plan(self) -> None:
        # The flat layout lives only in a repo-local `.harness/`.
        (self.project / "PLAN-stray.md").write_text("**Status:** planning\n", encoding="utf-8")
        labels = [qsl._plan_label(p) for p in qsl.list_plan_files(self.project)]
        self.assertNotIn("PLAN-stray.md", labels)


class StateDirResolution(unittest.TestCase):
    """`state_dir` resolves where a project's plans live per the active backend
    (ADR 0020, amends ADR 0018 DC-1): a synced backend → the project's own vault
    directory; no synced backend (or a `.project-mode=local` opt-out) →
    device-local `<repo>/.harness/`."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-state-dir-")
        self.root = Path(self._tmp)
        self.vault = self.root / "vault"
        self.proj = self.root / "repo"
        (self.proj / ".harness").mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_no_backend_returns_device_local(self) -> None:
        # ADR 0020: a stale vault_path key with no synced backend → device-local.
        resolution = {"vault_path": self.vault, "project_root": self.proj}
        self.assertEqual(
            hm.state_dir(resolution), self.proj / ".harness"
        )

    def test_synced_backend_returns_vault(self) -> None:
        # A synced backend: the project directory itself is the state root.
        from vault_backend_stub import VaultBackend
        from storage_seam import Locator
        backend = VaultBackend(root=self.vault, lock_root=self.root / "locks")
        resolution = {
            "backend": backend,
            "project_locator": Locator("desk/projects/repo"),
            "project_root": self.proj,
        }
        self.assertEqual(
            hm.state_dir(resolution),
            self.vault / "desk/projects" / "repo",
        )

    def test_local_mode_dir(self) -> None:
        resolution = {"vault_path": self.vault, "project_root": self.proj}
        self.assertEqual(
            hm.state_dir(resolution), self.proj / ".harness"
        )

    def test_no_project_root_is_none(self) -> None:
        # No backend and no project_root → state_dir cannot resolve.
        self.assertIsNone(hm.state_dir({"vault_path": self.vault}))


if __name__ == "__main__":
    unittest.main()
