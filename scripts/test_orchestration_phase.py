#!/usr/bin/env python3
"""Tests for harness/skills/memory/scripts/orchestration_phase.py
(V4 #23 task 5 — phase-integration auto-dispatch).

Covers both dispatches' contract: the enable_phase_integration toggle, the
phase_reflect cooldown (+ separate phase_release chain), dry-run (plan-only, no
state), and — for post-work — the dedup cooperation with the memory-reflect-stop
hook via the `.reflected` session marker (reflect → rename .start→.reflected;
already-reflected → skip; failed reflect → leave marker for the Stop hook;
cooldown → leave marker for the Stop hook). The injectable runner is the seam.

Run: python3 scripts/test_orchestration_phase.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
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


def _write_start_marker(project_root: Path, sid: str, transcript: str) -> Path:
    d = project_root / ".harness"
    d.mkdir(parents=True, exist_ok=True)
    m = d / f"session-id-{sid}.start"
    m.write_text(
        f"session_id: {sid}\nstarted_at: 2026-06-01T11:00:00Z\ntranscript: {transcript}\n",
        encoding="utf-8",
    )
    return m


class TestPostWork(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reflects_marks_and_records_fire(self) -> None:
        marker = _write_start_marker(self.root, "sid-1", "/tmp/t.jsonl")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "reflected")
        # marker renamed .start → .reflected (so the Stop hook skips)
        self.assertFalse(marker.exists())
        self.assertTrue((self.root / ".harness" / "session-id-sid-1.reflected").exists())
        # right reflect argv
        name, argv = runner.calls[0]
        self.assertEqual(name, "reflect")
        self.assertIn("--summary", argv)
        self.assertIn("--route", argv)
        self.assertEqual(argv[1], "/tmp/t.jsonl")
        # fire recorded under the phase_reflect chain
        self.assertIn("phase_reflect", ao.load_state(self.vault)["last_fire"])

    def test_disabled_is_skip(self) -> None:
        _write_start_marker(self.root, "sid-1", "/tmp/t.jsonl")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(enable_phase_integration=False), _NOW, runner=runner)
        self.assertEqual(out["status"], "disabled")
        self.assertEqual(runner.calls, [])

    def test_no_session_marker(self) -> None:
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=_FakeRunner())
        self.assertEqual(out["status"], "no-session")

    def test_already_reflected_dedup(self) -> None:
        # Defensive: a `.reflected` sibling coexisting with `.start` → skip.
        marker = _write_start_marker(self.root, "sid-1", "/tmp/t.jsonl")
        marker.with_suffix(".reflected").write_text("x", encoding="utf-8")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "already-reflected")
        self.assertEqual(runner.calls, [])

    def test_cooldown_leaves_marker_for_stop_hook(self) -> None:
        marker = _write_start_marker(self.root, "sid-1", "/tmp/t.jsonl")
        op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=_FakeRunner())  # fires
        # re-marker the (now-reflected) session as a fresh start to simulate a 2nd /work
        marker2 = _write_start_marker(self.root, "sid-2", "/tmp/t2.jsonl")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW + timedelta(minutes=10), runner=runner)
        self.assertEqual(out["status"], "cooldown")
        self.assertEqual(runner.calls, [])
        self.assertTrue(marker2.exists())  # NOT renamed → Stop hook will reflect it

    def test_reflect_failure_leaves_marker_and_no_fire(self) -> None:
        marker = _write_start_marker(self.root, "sid-1", "/tmp/t.jsonl")
        runner = _FakeRunner(results={"reflect": {"returncode": 2, "stdout": "", "stderr": "vault?", "timed_out": False}})
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "reflect-failed")
        self.assertTrue(marker.exists())  # still .start → Stop hook retries
        self.assertNotIn("phase_reflect", ao.load_state(self.vault)["last_fire"])  # cooldown not consumed

    def test_two_markers_is_ambiguous_and_defers(self) -> None:
        # Adversarial #1 (concurrency): two coexisting .start markers (concurrent
        # agents in one repo, or an active session beside a recent orphan) must
        # NOT be guessed by mtime — reflecting the wrong session would burn the
        # shared cooldown for it. Defer to the session-exact Stop hook instead.
        m1 = _write_start_marker(self.root, "sid-cur", "/tmp/cur.jsonl")
        m2 = _write_start_marker(self.root, "sid-other", "/tmp/other.jsonl")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "ambiguous-session")
        self.assertEqual(runner.calls, [])                      # nothing reflected
        self.assertTrue(m1.exists() and m2.exists())           # neither renamed
        self.assertFalse(ao.state_path(self.vault).exists())   # cooldown not burned

    def test_session_id_targets_exact_marker_amid_others(self) -> None:
        # Even with multiple markers, an explicit session id targets precisely.
        _write_start_marker(self.root, "sid-cur", "/tmp/cur.jsonl")
        _write_start_marker(self.root, "sid-other", "/tmp/other.jsonl")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, session_id="sid-cur", runner=runner)
        self.assertEqual(out["status"], "reflected")
        self.assertEqual(runner.calls[0][1][1], "/tmp/cur.jsonl")  # the exact one
        self.assertTrue((self.root / ".harness" / "session-id-sid-cur.reflected").exists())
        self.assertTrue((self.root / ".harness" / "session-id-sid-other.start").exists())  # untouched

    def test_session_id_already_reflected(self) -> None:
        (self.root / ".harness").mkdir(parents=True, exist_ok=True)
        (self.root / ".harness" / "session-id-sid-x.reflected").write_text("x", encoding="utf-8")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, session_id="sid-x", runner=runner)
        self.assertEqual(out["status"], "already-reflected")
        self.assertEqual(runner.calls, [])

    def test_explicit_transcript_without_marker(self) -> None:
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, transcript="/tmp/explicit.jsonl", runner=runner)
        self.assertEqual(out["status"], "reflected")
        self.assertEqual(runner.calls[0][1][1], "/tmp/explicit.jsonl")

    def test_dry_run_no_side_effects(self) -> None:
        marker = _write_start_marker(self.root, "sid-1", "/tmp/t.jsonl")
        runner = _FakeRunner()
        out = op.post_work_reflect(self.vault, self.root, _cfg(), _NOW, dry_run=True, runner=runner)
        self.assertEqual(out["status"], "dry-run")
        self.assertIn("--route", out["argv"])
        self.assertEqual(runner.calls, [])
        self.assertTrue(marker.exists())                       # not renamed
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
        (root / ".harness").mkdir(parents=True)
        (root / ".harness" / "session-id-s.start").write_text(
            "session_id: s\ntranscript: /tmp/t.jsonl\n", encoding="utf-8")
        op.post_work_reflect(self.vault, root, _cfg(), _NOW, runner=_FakeRunner())   # phase_reflect fires
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
