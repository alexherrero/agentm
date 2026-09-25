#!/usr/bin/env python3
"""The opening brief (agentm-vault plan 09, task 6).

A bound session opens on its trackers' State and Next, the last three progress
lines and two counts, in at most twenty lines. With no tracker there is no brief
and the brief hook prints the plan block harness-context-session-start renders,
byte for byte. While the brief hook is registered the context hook leaves its
plan block to it, so the block never prints twice.

Run directly:

    python3 scripts/test_project_brief.py
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
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import project_brief as pb  # noqa: E402
import tracker as tk  # noqa: E402

_HOOKS = _REPO / "harness" / "hooks"
_BRIEF = _HOOKS / "project-brief-session-start" / "project-brief-session-start"
_CONTEXT = _HOOKS / "harness-context-session-start" / "harness-context-session-start"

# Sandbox the install prefix so the in-process resolver never reads the operator's
# real ~/.claude/.agentm-config.json (a state_mode there would move plan state).
_TEST_PREFIX = tempfile.mkdtemp(prefix="agentm-test-brief-prefix-")
_SAVED_ENV: dict = {}


def setUpModule() -> None:  # noqa: N802 — unittest convention
    for key in ("AGENTM_INSTALL_PREFIX", "MEMORY_ROOT", "MEMORY_VAULT_PATH"):
        _SAVED_ENV[key] = os.environ.pop(key, None)
    os.environ["AGENTM_INSTALL_PREFIX"] = _TEST_PREFIX


def tearDownModule() -> None:  # noqa: N802
    os.environ.pop("AGENTM_INSTALL_PREFIX", None)
    for key, value in _SAVED_ENV.items():
        if value is not None:
            os.environ[key] = value
    shutil.rmtree(_TEST_PREFIX, ignore_errors=True)


def _tracker(**over) -> tk.Tracker:
    fields = dict(title="Build the brief", project="agentm", task="build-the-brief", status="active",
                  opened="2026-09-10", updated="2026-09-12", objective="A session opens on twenty lines.",
                  state="The renderer exists.", next_steps="Write the hook.")
    fields.update(over)
    return tk.Tracker(**fields)


class TheRender(unittest.TestCase):
    def test_a_full_brief_fits_twenty_lines(self) -> None:
        many = "\n".join(f"state line {i}" for i in range(10))
        lines = pb.render(
            project="agentm", task="build-the-brief",
            project_tracker=_tracker(task=None, state=many, next_steps="first\nsecond"),
            task_tracker=_tracker(state=many, next_steps="\n".join(f"next {i}" for i in range(9))),
            progress=["p1", "p2", "p3"], open_followups=4, unfiled=2,
            plan_path=Path("/v/tasks/build-the-brief/plan.md"),
        )
        self.assertLessEqual(len(lines), pb.MAX_LINES)
        text = "\n".join(lines)
        for part in ("[agentm] agentm · task build-the-brief", "Project state: state line 0",
                     "Project next: first", "Task state (active): state line 0", "Task next: next 0",
                     "  next 2", "Recent progress:", "  p3",
                     "Open follow-ups: 4 · unfiled captures with project agentm: 2",
                     "Plan: /v/tasks/build-the-brief/plan.md"):
            self.assertIn(part, text)
        self.assertNotIn("state line 4", text, "the task's State is capped at four lines")
        self.assertNotIn("next 3", text, "the task's Next is capped at three lines")
        self.assertNotIn("Project next: second", text)

    def test_no_tracker_is_no_brief(self) -> None:
        self.assertIsNone(pb.render(project="agentm", task="x", project_tracker=None, task_tracker=None,
                                    progress=["p"], open_followups=0, unfiled=0, plan_path=None))

    def test_a_long_line_is_clipped(self) -> None:
        lines = pb.render(project="agentm", task=None, project_tracker=_tracker(task=None, state="x" * 500),
                          task_tracker=None, progress=[], open_followups=0, unfiled=0, plan_path=None)
        self.assertTrue(all(len(line) <= pb.LINE_WIDTH for line in lines))
        self.assertTrue(lines[1].endswith("…"))

    def test_the_plan_path_reads_with_forward_slashes_on_every_platform(self) -> None:
        from pathlib import PureWindowsPath
        lines = pb.render(project="agentm", task="build-the-brief", project_tracker=None,
                          task_tracker=_tracker(), progress=[], open_followups=0, unfiled=0,
                          plan_path=PureWindowsPath(r"C:\v\tasks\build-the-brief\plan.md"))
        self.assertIn("Plan: C:/v/tasks/build-the-brief/plan.md", lines)


class TheCounts(unittest.TestCase):
    def test_open_followups_are_unchecked_boxes_and_unfinished_rows(self) -> None:
        text = ("| Item | From | On | Status |\n|---|---|---|---|\n"
                "| A | x | y | ✅ done |\n| B | x | y | still-open |\n| C | x | y | ❓ awaiting |\n\n"
                "An entry's own table is content, not a list of items:\n\n"
                "| Face | Count |\n|---|---|\n| a row inside an entry | 3 |\n\n"
                "- [ ] an unchecked one\n- [x] a checked one\n")
        self.assertEqual(pb.count_open_followups(text), 3)

    def test_followups_are_read_from_docs_and_never_the_root(self) -> None:
        """The root holds five files (task 176): `followups.md` lives in
        `docs/`, and a stray copy at the root is not counted."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            self.assertIsNone(pb.followups_path(project))
            self.assertIsNone(pb.followups_path(None))
            (project / "followups.md").write_text("- [ ] a\n", encoding="utf-8")
            self.assertIsNone(pb.followups_path(project))
            (project / "docs").mkdir()
            (project / "docs" / "followups.md").write_text("- [ ] b\n", encoding="utf-8")
            self.assertEqual(pb.followups_path(project), project / "docs" / "followups.md")

    def test_unfiled_captures_are_counted_by_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def card(rel: str, status: str, project: str) -> None:
                path = root / "memory" / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"---\ntitle: T\nstatus: {status}\nproject: {project}\n---\n\nbody\n", encoding="utf-8")

            card("semantic/a.md", "unfiled", "agentm")
            card("semantic/b.md", "unfiled", "crickets")
            card("semantic/c.md", "active", "agentm")
            card("procedural/d.md", '"unfiled"', '"agentm"')
            card("mocs/e.md", "unfiled", "agentm")
            self.assertEqual(pb.count_unfiled(root, "agentm"), 2)
            self.assertEqual(pb.count_unfiled(None, "agentm"), 0)

    def test_a_card_saved_with_windows_line_endings_is_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "memory" / "semantic" / "a.md"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"---\r\ntitle: T\r\nstatus: unfiled\r\nproject: agentm\r\n---\r\n\r\nbody\r\n")
            self.assertEqual(pb.count_unfiled(root, "agentm"), 1)


class TheCommandLine(unittest.TestCase):
    """Device-local state: the task's tracker sits beside its flat pair in `.harness/`."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agentm-brief-cli-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repo = self.root / "repo"
        self.harness = self.repo / ".harness"
        self.harness.mkdir(parents=True)
        (self.harness / "project.json").write_text(json.dumps({"vault_project": "demo"}), encoding="utf-8")
        (self.harness / "active-plan").write_text("foo\n", encoding="utf-8")
        (self.harness / "PLAN-foo.md").write_text("# Plan: foo\n\n**Status:** in-progress\n", encoding="utf-8")
        (self.harness / "progress-foo.md").write_text(
            "2026-09-12 10:00 /work — one\n2026-09-12 11:00 /work — two\n", encoding="utf-8")

    def _main(self) -> tuple:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = pb.main(["--cwd", str(self.repo)])
        return rc, out.getvalue()

    def test_a_bound_task_with_a_tracker_prints_the_brief(self) -> None:
        tk.write(self.harness / "tracker-foo.md",
                 tk.new(title="Foo", project="demo", task="foo", objective="Done.",
                        next_step="Read the hook.", today="2026-09-12"))
        rc, out = self._main()
        self.assertEqual(rc, 0)
        self.assertIn("[agentm] demo · task foo", out)
        self.assertIn("Task state (queued): Not started.", out)
        self.assertIn("Task next: Read the hook.", out)
        self.assertIn("  2026-09-12 11:00 /work — two", out)
        self.assertLessEqual(len(out.splitlines()), pb.MAX_LINES)

    def test_no_tracker_prints_nothing_and_exits_three(self) -> None:
        self.assertEqual(self._main(), (pb.NO_BRIEF, ""))

    def test_an_unbound_directory_has_no_brief(self) -> None:
        (self.harness / "project.json").unlink()
        self.assertEqual(self._main(), (pb.NO_BRIEF, ""))


class _Hooks(unittest.TestCase):
    """Both hooks as subprocesses, against a fake HOME whose config resolves this
    repo's scripts, over device-local plan state."""

    suffix = ""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agentm-brief-hooks-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.proj = self.root / "proj"
        harness = self.proj / ".harness"
        harness.mkdir(parents=True)
        (harness / "project.json").write_text(json.dumps({"vault_project": "demo"}), encoding="utf-8")
        (harness / "active-plan").write_text("foo\n", encoding="utf-8")
        (harness / "PLAN-foo.md").write_text("# Plan: foo\n", encoding="utf-8")
        (harness / "progress-foo.md").write_text("2026-09-12 10:00 /work — one\n", encoding="utf-8")
        self.harness = harness
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "source_clones": {"agentm": str(_REPO)}}), encoding="utf-8")

    def _env(self, **over) -> dict:
        env = {**os.environ, "HOME": str(self.home)}
        for key in ("AGENTM_INSTALL_PREFIX", "MEMORY_ROOT", "MEMORY_VAULT_PATH", "AGENTM_PLAN_BLOCK_ONLY"):
            env.pop(key, None)
        env.update(over)
        return env

    def _command(self, script: Path) -> list:
        raise NotImplementedError

    def _run(self, script: Path, **env) -> subprocess.CompletedProcess:
        payload = json.dumps({"session_id": "brief", "cwd": str(self.proj)})
        # Read as UTF-8, the way a host reads a hook's output. The platform's locale
        # codec (cp1252 on a Windows runner) is not what any host reads with, and it
        # cannot read a correct brief's `·` back.
        return subprocess.run(self._command(script), input=payload, env=self._env(**env),
                              capture_output=True, encoding="utf-8")

    def test_with_a_tracker_the_session_opens_on_the_brief(self) -> None:
        tk.write(self.harness / "tracker-foo.md",
                 tk.new(title="Foo", project="demo", task="foo", objective="Done.",
                        next_step="Read the hook.", today="2026-09-12"))
        out = self._run(_BRIEF).stdout
        self.assertIn("[agentm] demo · task foo", out)
        self.assertNotIn("[agentm] Project state for this repo", out)
        self.assertLessEqual(len(out.splitlines()), 20)

    def test_without_a_tracker_the_fallback_is_the_context_hooks_own_block(self) -> None:
        brief = self._run(_BRIEF).stdout
        block = self._run(_CONTEXT, AGENTM_PLAN_BLOCK_ONLY="1").stdout
        self.assertIn("[agentm] Project state for this repo lives in .harness/", brief)
        self.assertEqual(brief, block)

    def test_the_context_hook_leaves_its_block_to_a_registered_brief(self) -> None:
        self.assertIn("[agentm] Project state for this repo", self._run(_CONTEXT).stdout)
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": {"SessionStart": [{"matcher": ".*", "hooks": [{"type": "command",
                "command": "bash ~/.claude/hooks/project-brief-session-start/project-brief-session-start.sh"}]}]}}),
            encoding="utf-8")
        self.assertNotIn("[agentm] Project state for this repo", self._run(_CONTEXT).stdout)


@unittest.skipIf(os.name == "nt", "bash hooks — POSIX only")
class TheBashHooks(_Hooks):
    def _command(self, script: Path) -> list:
        return ["bash", str(script) + ".sh"]


@unittest.skipIf(shutil.which("pwsh") is None, "pwsh not on PATH")
class ThePowerShellHooks(_Hooks):
    def _command(self, script: Path) -> list:
        return [shutil.which("pwsh"), "-NoProfile", "-File", str(script) + ".ps1"]


del _Hooks  # only its subclasses run


if __name__ == "__main__":
    unittest.main()
