#!/usr/bin/env python3
"""Integration tests for harness/skills/memory/scripts/orchestration_idle.py
(V4 #23 task 4 — the idle orchestration chain driver).

Covers the orchestration contract — NOT the underlying scripts (reflect.py /
discover_skills.py / adapt_skills.py have their own behavior): chain ordering,
the bounded `--max-batches`/`--limit` flags, the `enable_idle_chain` toggle, the
`idle_chain` cooldown + last-fire recording, dry-run (plan-only, no side effects,
no state write), no-op-when-empty resilience, and never-raises.

The injectable `runner` is the testable seam: a fake records calls + returns
canned per-step results, so the chain logic is exercised without spawning the
real (network/transcript-touching) scripts.

Run: python3 scripts/test_orchestration_idle.py
"""
from __future__ import annotations

import json
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
import orchestration_idle as oi  # noqa: E402

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class _FakeRunner:
    """Records (name, argv) calls; returns canned results. Optionally raises on
    a named step (to exercise the never-raises guard)."""

    def __init__(self, results: dict | None = None, raise_on: str | None = None):
        self.calls: list[tuple[str, list[str]]] = []
        self.results = results or {}
        self.raise_on = raise_on

    def __call__(self, name: str, argv: list[str]) -> dict:
        self.calls.append((name, list(argv)))
        if self.raise_on == name:
            raise RuntimeError("boom")
        return self.results.get(
            name, {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}
        )

    @property
    def order(self) -> list[str]:
        return [c[0] for c in self.calls]

    def argv_for(self, name: str) -> list[str]:
        for n, argv in self.calls:
            if n == name:
                return argv
        raise KeyError(name)


def _cfg(**over) -> dict:
    c = dict(ao.DEFAULT_CONFIG)
    c.update(over)
    return c


class TestIdleChain(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ── plan / dry-run ──────────────────────────────────────────────────────
    def test_dry_run_returns_plan_without_side_effects(self) -> None:
        runner = _FakeRunner()
        out = oi.run_idle_chain(self.vault, _cfg(), _NOW, dry_run=True, runner=runner)
        self.assertEqual(out["status"], "dry-run")
        self.assertEqual([s["name"] for s in out["steps"]],
                         ["reflect-corpus", "discover-skills", "adapt-pass1"])
        self.assertEqual(runner.calls, [])                 # runner never invoked
        self.assertFalse(ao.state_path(self.vault).exists())  # no state write

    def test_plan_carries_bounded_flags(self) -> None:
        out = oi.run_idle_chain(self.vault, _cfg(), _NOW, dry_run=True)
        steps = {s["name"]: s["argv"] for s in out["steps"]}
        corpus = steps["reflect-corpus"]
        self.assertIn("--execute", corpus)
        self.assertEqual(corpus[corpus.index("--batch-size") + 1], "5")
        self.assertEqual(corpus[corpus.index("--max-batches") + 1], "1")
        adapt = steps["adapt-pass1"]
        self.assertEqual(adapt[adapt.index("--limit") + 1], "3")

    # ── execution / ordering ────────────────────────────────────────────────
    def test_steps_run_in_order_and_record_fire(self) -> None:
        runner = _FakeRunner()
        out = oi.run_idle_chain(self.vault, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "ran")
        self.assertEqual(runner.order,
                         ["reflect-corpus", "discover-skills", "adapt-pass1"])
        st = ao.load_state(self.vault)
        self.assertIn("idle_chain", st["last_fire"])

    def test_respects_limit_and_max_batches_when_executed(self) -> None:
        runner = _FakeRunner()
        oi.run_idle_chain(self.vault, _cfg(), _NOW, runner=runner)
        self.assertIn("--limit", runner.argv_for("adapt-pass1"))
        self.assertEqual(runner.argv_for("adapt-pass1")[-1], "3")
        corpus = runner.argv_for("reflect-corpus")
        self.assertEqual(corpus[corpus.index("--max-batches") + 1], "1")

    # ── toggle ──────────────────────────────────────────────────────────────
    def test_disabled_is_skip(self) -> None:
        runner = _FakeRunner()
        out = oi.run_idle_chain(self.vault, _cfg(enable_idle_chain=False), _NOW, runner=runner)
        self.assertEqual(out["status"], "disabled")
        self.assertEqual(runner.calls, [])
        self.assertFalse(ao.state_path(self.vault).exists())

    # ── cooldown ────────────────────────────────────────────────────────────
    def test_cooldown_blocks_within_window(self) -> None:
        oi.run_idle_chain(self.vault, _cfg(), _NOW, runner=_FakeRunner())  # fires
        runner = _FakeRunner()
        soon = _NOW + timedelta(hours=2)                                  # <24h
        out = oi.run_idle_chain(self.vault, _cfg(), soon, runner=runner)
        self.assertEqual(out["status"], "cooldown")
        self.assertEqual(runner.calls, [])

    def test_past_cooldown_fires_again(self) -> None:
        oi.run_idle_chain(self.vault, _cfg(), _NOW, runner=_FakeRunner())
        later = _NOW + timedelta(hours=25)                               # >24h
        out = oi.run_idle_chain(self.vault, _cfg(), later, runner=_FakeRunner())
        self.assertEqual(out["status"], "ran")

    def test_dry_run_reports_cooldown_but_does_not_consume_it(self) -> None:
        # A dry-run after a real fire still reports cooldown_ok=False but writes
        # nothing — it must not reset or consume the cooldown.
        oi.run_idle_chain(self.vault, _cfg(), _NOW, runner=_FakeRunner())
        before = ao.load_state(self.vault)["last_fire"]["idle_chain"]
        out = oi.run_idle_chain(self.vault, _cfg(), _NOW + timedelta(hours=1), dry_run=True)
        self.assertEqual(out["status"], "dry-run")
        self.assertFalse(out["cooldown_ok"])
        self.assertEqual(ao.load_state(self.vault)["last_fire"]["idle_chain"], before)

    # ── resilience ──────────────────────────────────────────────────────────
    def test_empty_steps_noop_cleanly(self) -> None:
        # Every step reports an empty/no-op result; the chain still completes.
        runner = _FakeRunner(results={
            "reflect-corpus": {"returncode": 0, "stdout": "", "stderr": "[reflect.corpus] nothing to process", "timed_out": False},
            "discover-skills": {"returncode": 0, "stdout": "", "stderr": "cadence not due; skip", "timed_out": False},
            "adapt-pass1": {"returncode": 0, "stdout": json.dumps({"evaluated_count": 0}), "stderr": "", "timed_out": False},
        })
        out = oi.run_idle_chain(self.vault, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "ran")
        self.assertEqual([s["outcome"] for s in out["steps"]],
                         ["noop", "throttled", "noop"])

    def test_step_failure_does_not_abort_chain(self) -> None:
        runner = _FakeRunner(results={
            "reflect-corpus": {"returncode": 2, "stdout": "", "stderr": "boom", "timed_out": False},
        })
        out = oi.run_idle_chain(self.vault, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "ran")
        self.assertEqual(runner.order,
                         ["reflect-corpus", "discover-skills", "adapt-pass1"])

    def test_runner_exception_is_swallowed(self) -> None:
        runner = _FakeRunner(raise_on="discover-skills")
        out = oi.run_idle_chain(self.vault, _cfg(), _NOW, runner=runner)
        self.assertEqual(out["status"], "error")
        self.assertIn("error", out)

    # ── staged-candidate surfacing ──────────────────────────────────────────
    def test_staged_candidates_counted(self) -> None:
        adapt = self.vault / "_meta" / "skill-discovery-cache" / "adapt-state"
        (adapt / "src-a").mkdir(parents=True)
        (adapt / "src-a" / "p1.json").write_text("{}", encoding="utf-8")
        (adapt / "src-a" / "p2.json").write_text("{}", encoding="utf-8")
        (adapt / "evaluated.json").write_text("{}", encoding="utf-8")  # not a source dir
        out = oi.run_idle_chain(self.vault, _cfg(), _NOW, runner=_FakeRunner())
        self.assertEqual(out["staged_candidates"], 2)


if __name__ == "__main__":
    unittest.main()
