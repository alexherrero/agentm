#!/usr/bin/env python3
"""Tests for harness/skills/memory/scripts/orchestration_phase.py
(V4 #23 task 5 — phase-integration auto-dispatch).

Covers both dispatches' contract: the enable_phase_integration toggle, the
phase_reflect cooldown (+ separate phase_release chain), dry-run (plan-only, no
state), and — for post-work — staging the session's new slice for the night's
lesson compile through `lesson_compile.py collect` (task 199): the session is
found in the host's transcript folder for the project root, never through a
`.harness` marker, and nothing is mined or filed. The injectable runner is the
seam.

Run: python3 scripts/test_orchestration_phase.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import auto_orchestration as ao  # noqa: E402
import orchestration_phase as op  # noqa: E402

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class _FakeRunner:
    def __init__(self, results: dict | None = None):
        self.calls: list[tuple[str, list[str]]] = []
        self.results = results or {}

    def __call__(self, name: str, argv: list[str]) -> dict:
        self.calls.append((name, list(argv)))
        return self.results.get(
            name, {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}
        )

    @property
    def order(self) -> list[str]:
        return [c[0] for c in self.calls]


def _cfg(**over) -> dict:
    c = dict(ao.DEFAULT_CONFIG)
    c.update(over)
    return c


def _transcript(projects: Path, project_root: Path, sid: str, mtime: float | None = None) -> Path:
    """A transcript where the host files one for a session in `project_root`."""
    import session_binding
    folder = projects / session_binding.host_folder_name(str(project_root.resolve()))
    folder.mkdir(parents=True, exist_ok=True)
    t = folder / f"{sid}.jsonl"
    t.write_text('{"type": "user"}\n', encoding="utf-8")
    if mtime is not None:
        os.utime(t, (mtime, mtime))
    return t


class TestPostWork(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.projects = self.root / "claude-projects"
        self._env = mock.patch.dict(os.environ, {"AGENTM_CLAUDE_PROJECTS": str(self.projects)})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self.tmp.cleanup()

    def test_stages_the_session_and_records_fire(self) -> None:
        t = _transcript(self.projects, self.root, "sid-1")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "staged")
        name, argv = runner.calls[0]
        self.assertEqual(name, "reflect")
        self.assertTrue(argv[0].endswith("lesson_compile.py"))
        self.assertEqual(argv[1:4], ["collect", "--transcript", str(t)])
        self.assertIn("phase_reflect", ao.load_state(self.vault)["last_fire"])

    def test_never_mines_or_files(self) -> None:
        # Task 199: the regex miner stops filing on its own. The dispatch only
        # stages a slice; it never runs reflect.py and never routes.
        _transcript(self.projects, self.root, "sid-1")
        runner = _FakeRunner()
        op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=runner)
        for _, argv in runner.calls:
            self.assertFalse(any(a.endswith("reflect.py") for a in argv), argv)
            self.assertNotIn("--route", argv)

    def test_writes_no_marker(self) -> None:
        _transcript(self.projects, self.root, "sid-1")
        op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=_FakeRunner())
        self.assertEqual(list(self.root.glob(".harness/session-id-*")), [])

    def test_disabled_is_skip(self) -> None:
        _transcript(self.projects, self.root, "sid-1")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(enable_phase_integration=False), _NOW, runner=runner)
        self.assertEqual(out["status"], "disabled")
        self.assertEqual(runner.calls, [])

    def test_no_session(self) -> None:
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=_FakeRunner())
        self.assertEqual(out["status"], "no-session")

    def test_cooldown_stages_nothing(self) -> None:
        _transcript(self.projects, self.root, "sid-1")
        op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=_FakeRunner())  # fires
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW + timedelta(minutes=10), runner=runner)
        self.assertEqual(out["status"], "cooldown")
        self.assertEqual(runner.calls, [])

    def test_stage_failure_does_not_consume_the_cooldown(self) -> None:
        _transcript(self.projects, self.root, "sid-1")
        runner = _FakeRunner(results={"reflect": {"returncode": 2, "stdout": "", "stderr": "vault?", "timed_out": False}})
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "stage-failed")
        self.assertNotIn("phase_reflect", ao.load_state(self.vault)["last_fire"])

    def test_newest_transcript_without_a_session_id(self) -> None:
        _transcript(self.projects, self.root, "sid-old", mtime=1_000_000)
        new = _transcript(self.projects, self.root, "sid-new", mtime=2_000_000)
        runner = _FakeRunner()
        op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=runner)
        self.assertEqual(runner.calls[0][1][3], str(new))

    def test_session_id_targets_its_own_transcript_amid_others(self) -> None:
        cur = _transcript(self.projects, self.root, "sid-cur", mtime=1_000_000)
        _transcript(self.projects, self.root, "sid-other", mtime=2_000_000)
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, session_id="sid-cur", runner=runner)
        self.assertEqual(out["status"], "staged")
        self.assertEqual(runner.calls[0][1][3], str(cur))

    def test_unknown_session_id_is_no_session(self) -> None:
        _transcript(self.projects, self.root, "sid-other")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, session_id="sid-x", runner=runner)
        self.assertEqual(out["status"], "no-session")
        self.assertEqual(runner.calls, [])

    def test_explicit_transcript(self) -> None:
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, transcript="/tmp/explicit.jsonl", runner=runner)
        self.assertEqual(out["status"], "staged")
        self.assertEqual(runner.calls[0][1][3], "/tmp/explicit.jsonl")

    def test_dry_run_no_side_effects(self) -> None:
        _transcript(self.projects, self.root, "sid-1")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, dry_run=True, runner=runner)
        self.assertEqual(out["status"], "dry-run")
        self.assertIn("collect", out["argv"])
        self.assertEqual(runner.calls, [])
        self.assertFalse(ao.state_path(self.vault).exists())   # no state write


class TestPostRelease(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_runs_index_then_discover_and_records(self) -> None:
        runner = _FakeRunner()
        out = op.post_release_refresh(self.vault, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "ran")
        self.assertEqual(runner.order, ["index-skills", "discover-skills"])
        # discover is cadence-checked so a release never blocks on a full fetch
        self.assertIn("--cadence-check", runner.calls[1][1])
        self.assertIn("phase_release", ao.load_state(self.vault)["last_fire"])

    def test_disabled_is_skip(self) -> None:
        runner = _FakeRunner()
        out = op.post_release_refresh(self.vault, _cfg(enable_phase_integration=False), _NOW, runner=runner)
        self.assertEqual(out["status"], "disabled")
        self.assertEqual(runner.calls, [])

    def test_cooldown_blocks(self) -> None:
        op.post_release_refresh(self.vault, _cfg(), _NOW, runner=_FakeRunner())
        out = op.post_release_refresh(self.vault, _cfg(), _NOW + timedelta(minutes=30), runner=_FakeRunner())
        self.assertEqual(out["status"], "cooldown")

    def test_dry_run_no_side_effects(self) -> None:
        runner = _FakeRunner()
        out = op.post_release_refresh(self.vault, _cfg(), _NOW, dry_run=True, runner=runner)
        self.assertEqual(out["status"], "dry-run")
        self.assertEqual([s["name"] for s in out["steps"]], ["index-skills", "discover-skills"])
        self.assertEqual(runner.calls, [])
        self.assertFalse(ao.state_path(self.vault).exists())

    def test_reflect_and_release_chains_are_independent(self) -> None:
        # A phase_reflect fire must NOT block a post-release refresh (separate
        # chains, same cooldown value).
        root = self.vault / "proj"
        root.mkdir(parents=True)
        op.post_work_reflect(self.vault, root, _cfg(), _NOW, transcript="/tmp/t.jsonl",
                             runner=_FakeRunner())   # phase_reflect fires
        out = op.post_release_refresh(self.vault, _cfg(), _NOW, runner=_FakeRunner())  # phase_release independent
        self.assertEqual(out["status"], "ran")


# Every test here gets its own engine state directory. The phase records each
# fire in `auto-orchestration-state.json` there, so on a hand run the first
# test's fire put the rest in cooldown, which failed ten of the sixteen, and
# the fires landed in the machine's own cooldown state.
import os.path as _osp  # noqa: E402
import sys as _sys  # noqa: E402

if _osp.dirname(_osp.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _osp.dirname(_osp.abspath(__file__)))
from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main()
