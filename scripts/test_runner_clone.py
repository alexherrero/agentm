#!/usr/bin/env python3
"""A cycle that starts from a stale clone runs its jobs and says so (#815).

The operator's ruling of 2026-10-23: the runner compares the clone with its
local origin/main at the start of each cycle, never fetching, records the count
of commits behind in the cycle record and the job records, and holds nothing
back. Scratch clones only, with the global git config shut out: a scratch repo
otherwise inherits its `hooksPath`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from runner import cycle, state  # noqa: E402
from test_clone_drift import clone_pair, commit, git  # noqa: E402


def _write_job(jobs_dir: Path, name: str, **fields) -> Path:
    """test_runner's helper: one manifest, its fields as JSON scalars."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    p = jobs_dir / f"{name}.yaml"
    p.write_text("".join(f"{k}: {json.dumps(v)}\n" for k, v in fields.items()), encoding="utf-8")
    return p


class AStaleCloneRunsAndSaysSo(unittest.TestCase):

    def _cycle(self, root: Path, work: Path):
        jobs_dir, state_root, report_path = root / "jobs", root / "state", root / "digest.jsonl"
        ran = root / "it-ran"
        _write_job(jobs_dir, "j", schedule="daily", lookback="6h",
                   command=f"touch {ran}", tier="T3", dry_run=False)
        report = cycle.run_cycle(jobs_dir, now=1000.0, state_root=state_root,
                                 report_path=report_path, clone_repo=work)
        summary = json.loads(state.cycle_summary_path(state_root).read_text(encoding="utf-8"))
        records = [json.loads(l) for l in report_path.read_text(encoding="utf-8").splitlines()]
        return report, summary, records, ran.exists()

    def test_behind_by_two_the_job_runs_and_the_records_carry_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work, other = clone_pair(root)
            commit(other, "b.md")
            commit(other, "the fix.md")
            git(other, "push", "-q", "origin", "main")
            git(work, "fetch", "-q", "origin")
            report, summary, records, ran = self._cycle(root, work)
        self.assertTrue(ran, "the job was held back; the ruling says it runs")
        self.assertTrue(report.outcomes[0].ran)
        self.assertEqual(summary["clone"]["behind"], 2)
        self.assertEqual(summary["clone"]["newest_missing_subject"], "the fix.md")
        self.assertEqual(records[0]["clone_behind"], 2)

    def test_level_records_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work, _ = clone_pair(root)
            _, summary, records, ran = self._cycle(root, work)
        self.assertTrue(ran)
        self.assertEqual(summary["clone"]["behind"], 0)
        self.assertEqual(records[0]["clone_behind"], 0)

    def test_on_a_branch_is_recorded_and_runs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work, _ = clone_pair(root)
            git(work, "checkout", "-q", "-b", "feature")
            commit(work, "mine.md")
            report, summary, _, ran = self._cycle(root, work)
        self.assertTrue(ran)
        self.assertEqual((summary["clone"]["branch"], summary["clone"]["ahead"]), ("feature", 1))
        self.assertNotIn("error", summary["clone"])

    def test_no_clone_named_leaves_the_record_as_it_was(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs_dir, state_root = root / "jobs", root / "state"
            _write_job(jobs_dir, "j", schedule="daily", lookback="6h",
                       command="true", tier="T3", dry_run=False)
            cycle.run_cycle(jobs_dir, now=1000.0, state_root=state_root)
            summary = json.loads(state.cycle_summary_path(state_root).read_text(encoding="utf-8"))
        self.assertNotIn("clone", summary)


if __name__ == "__main__":
    unittest.main()
