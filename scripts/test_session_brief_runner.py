#!/usr/bin/env python3
"""test_session_brief_runner.py — the session brief carries the runner's refusals
(filing-v2 remainders task 1).

A refused job manifest used to stop every scheduled job on the machine with a
traceback in a launchd log as the only trace. The runner now leaves its last
cycle's account at `~/.cache/agentm/runner/last-cycle.json`, and the brief the
operator sees at session start names what was refused.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
for _p in (_HERE / "health", _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import session_brief  # noqa: E402
from runner import state as runner_state  # noqa: E402

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


class BriefCarriesRefusals(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "diagnostics" / "digests").mkdir(parents=True)
        self.cycle = self.root / "last-cycle.json"

    def _digest(self):
        (self.vault / "diagnostics" / "digests" / "20260905-digest-daily.md").write_text(
            "# Daily digest — all quiet\n\nNothing notable.\n", encoding="utf-8")

    def _brief(self):
        return session_brief.build_brief(
            vault=self.vault, now=NOW,
            park_dir=self.root / "no-park", history_path=self.root / "no-history.jsonl",
            runner_cycle_path=self.cycle,
        )

    def test_a_refusal_rides_the_fresh_line_and_its_signature(self):
        self._digest()
        clean = self._brief()
        self.assertIsNotNone(clean)
        self.assertNotIn("refused", clean["line"])
        self.cycle.write_text(json.dumps({"at": 1.0, "loaded": 3, "refused": [
            {"file": "dreaming.yaml", "reason": "invalid YAML"}], "outcomes": []}), encoding="utf-8")
        brief = self._brief()
        self.assertIn("⚠ runner refused 1 manifest: dreaming.yaml", brief["line"])
        self.assertIn("every other job still runs", brief["line"])
        self.assertNotEqual(brief["signature"], clean["signature"], "a refusal is a change the anti-fatigue guard must not suppress")

    def test_a_refusal_speaks_even_when_the_ladder_never_ran(self):
        self.assertIsNone(self._brief(), "honest-quiet without a digest, a ledger or a refusal")
        self.cycle.write_text(json.dumps({"at": 1.0, "loaded": 0, "refused": [
            {"file": "a.yaml", "reason": "x"}, {"file": "b.yaml", "reason": "y"}], "outcomes": []}), encoding="utf-8")
        brief = self._brief()
        self.assertIsNotNone(brief)
        self.assertIn("refused 2 manifests: a.yaml, b.yaml", brief["line"])

    def test_an_unreadable_or_absent_summary_is_no_refusal(self):
        self._digest()
        self.cycle.write_text("{not json", encoding="utf-8")
        self.assertNotIn("refused", self._brief()["line"])
        self.assertEqual(session_brief.runner_refusals(self.root / "missing.json"), [])


class TheBriefReadsTheCycleWhereTheRunnerWritesIt(unittest.TestCase):
    """The brief's default cycle path is the runner's own resolver's answer.

    Until 2026-09-14 the brief named `~/.cache/agentm/runner/last-cycle.json`
    from the home directory on its own, while the runner could be pointed
    elsewhere, so a moved cache root left the brief reading a file the runner
    no longer wrote."""

    def test_it_follows_the_cache_root(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": td}):
            self.assertEqual(session_brief.default_runner_cycle_path(),
                             Path(td) / "agentm" / "runner" / "last-cycle.json")
            self.assertEqual(session_brief.default_runner_cycle_path(), runner_state.cycle_summary_path())

    def test_with_no_cache_root_it_is_the_path_the_live_runner_writes(self):
        without = {k: v for k, v in os.environ.items() if k != "XDG_CACHE_HOME"}
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, without, clear=True), \
                mock.patch.object(Path, "home", return_value=Path(td)):
            self.assertEqual(session_brief.default_runner_cycle_path(),
                             Path(td) / ".cache" / "agentm" / "runner" / "last-cycle.json")

    def test_a_refusal_the_runner_left_reaches_the_brief_by_default(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": td}):
            cycle = runner_state.cycle_summary_path()
            # Refuse before writing: were the runner's root ever to stop
            # following the variable, this would write the live runner's own
            # record on a real machine.
            self.assertIn(Path(td), cycle.parents,
                          f"the runner's cycle summary no longer follows XDG_CACHE_HOME ({cycle}); "
                          "refusing to write where the live runner keeps its records")
            cycle.parent.mkdir(parents=True)
            cycle.write_text(json.dumps({"at": 1.0, "loaded": 0, "refused": [
                {"file": "a.yaml", "reason": "x"}], "outcomes": []}), encoding="utf-8")
            self.assertEqual([r["file"] for r in session_brief.runner_refusals()], ["a.yaml"])

    def test_no_cycle_reads_as_no_refusal_and_makes_nothing(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": td}):
            self.assertEqual(session_brief.runner_refusals(), [])
            self.assertEqual(session_brief.parked_jobs(), [])
            self.assertFalse(runner_state.default_state_root().exists(),
                             "the brief made the runner's state root by reading from it")


# Every test here gets its own engine state, cache root and recall ledger.
# The brief asks the runner's watchdog what is parked, and reads the last
# cycle's account, through the runner's default state root. That root follows
# `XDG_CACHE_HOME` since 2026-09-14, so this keeps a hand run off the live
# runner's records; test_engine_state_not_leaked runs this suite by hand.
# The path insert is here rather than assumed: a hand run reaches this module
# as `scripts.<name>`, which puts the repo root on the path and not `scripts/`.
import os.path as _osp  # noqa: E402
import sys as _sys  # noqa: E402

if _osp.dirname(_osp.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _osp.dirname(_osp.abspath(__file__)))
from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main()
