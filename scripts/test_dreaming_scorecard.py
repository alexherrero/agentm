#!/usr/bin/env python3
"""The dreaming scorecard: what a run did, and how far things moved.

Two properties carry this file. A stage that could not run must never report
zero — that is inherited from `StageResult` and has to survive being rendered.
And a delta must never be invented: the first edition of a report has no
yesterday, and "unchanged" would be a claim about a night nobody observed.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "harness/skills/memory/scripts"))

import dreaming_scorecard as ds  # noqa: E402


AT = datetime(2026, 8, 22, 9, 30, tzinfo=timezone.utc)
REL = Path("desk/diagnostics")
STAGE = Path("desk/scratch")


def answers(**by_command):
    def fake(args):
        cmd = args[0]
        if cmd not in by_command:
            raise ds.DaemonUnavailable(f"no fake answer for {cmd}")
        value = by_command[cmd]
        if isinstance(value, Exception):
            raise value
        return value
    return fake


def daemon(*, unfiled=100, current=40, eligible=200, enrich_depth=7):
    return {
        "status": {"health": {"queue": {"unfiled": unfiled}},
                   "spaces": {"projects": "Agent/desk/projects"},
                   "vault": "/somewhere"},
        "ledger": {"current": current, "eligible": eligible},
        "queue": [{"owner": "enrich", "depth": enrich_depth, "parked": []}],
        "tiers": [{"job": "crystallize", "tier": "strong", "why": "pinned"}],
    }


def stage_a_run(vault: Path, run_id="run-1", staged_at=1000.0, proposals=None,
                stages=None):
    d = vault / STAGE / run_id
    d.mkdir(parents=True, exist_ok=True)
    body = {"run_id": run_id, "staged_at": staged_at,
            "proposals": proposals if proposals is not None else []}
    if stages is not None:
        body["stages"] = stages
    (d / "proposals.json").write_text(json.dumps(body), encoding="utf-8")
    return d


class MovementTests(unittest.TestCase):
    def build(self, tmp, fake, *, now=AT):
        with mock.patch.object(ds, "_agentmd", side_effect=fake):
            return ds.build(tmp, now=now, rel=REL, staging=STAGE)

    def read(self, tmp):
        return (tmp / REL / ds.STABLE_NAME).read_text(encoding="utf-8")

    def test_the_first_edition_invents_no_delta(self):
        """There is no yesterday, and the report says so.

        "unchanged" would be a claim about a night nobody observed, and "+0"
        would be the same claim in a costume.
        """
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp)
            self.build(tmp, answers(**daemon()))
            body = self.read(tmp)

        self.assertIn("no previous reading to compare against", body)
        self.assertNotIn("unchanged", body)

    def test_the_second_edition_reads_the_first(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp)
            self.build(tmp, answers(**daemon(unfiled=100)))
            self.build(tmp, answers(**daemon(unfiled=140)))
            body = self.read(tmp)

        self.assertIn("up 40", body)

    def test_a_move_in_the_bad_direction_is_flagged(self):
        """A growing queue and growing coverage are both "up" and only one is
        bad. The direction is written down per number rather than inferred."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp)
            self.build(tmp, answers(**daemon(unfiled=100, current=40)))
            self.build(tmp, answers(**daemon(unfiled=140, current=90)))
            body = self.read(tmp)

        queue_row = next(l for l in body.splitlines() if l.startswith("| unfiled queue |"))
        cover_row = next(l for l in body.splitlines()
                         if l.startswith("| enrichment coverage |"))
        self.assertIn("⚠", queue_row, "a growing queue was not flagged")
        self.assertNotIn("⚠", cover_row, "growing coverage was flagged as a problem")

    def test_an_unchanged_number_says_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp)
            self.build(tmp, answers(**daemon()))
            self.build(tmp, answers(**daemon()))
            body = self.read(tmp)

        self.assertIn("unchanged", body)

    def test_the_report_carries_its_own_state(self):
        """The report is the state file. A separate one would be a second thing
        to keep in agreement with the report it describes."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp)
            self.build(tmp, answers(**daemon(unfiled=123)))
            body = self.read(tmp)

        line = next(l for l in body.splitlines() if l.startswith("readings: "))
        self.assertEqual(json.loads(line[len("readings: "):])["unfiled"], 123)

    def test_hand_edited_frontmatter_does_not_break_the_next_run(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp)
            self.build(tmp, answers(**daemon()))
            stable = tmp / REL / ds.STABLE_NAME
            stable.write_text(
                stable.read_text(encoding="utf-8").replace(
                    "readings: {", "readings: {oops"),
                encoding="utf-8")
            self.build(tmp, answers(**daemon()))
            body = self.read(tmp)

        self.assertIn("no previous reading to compare against", body)


class StageTests(unittest.TestCase):
    def build(self, tmp, fake):
        with mock.patch.object(ds, "_agentmd", side_effect=fake):
            return ds.build(tmp, now=AT, rel=REL, staging=STAGE)

    def read(self, tmp):
        return (tmp / REL / ds.STABLE_NAME).read_text(encoding="utf-8")

    def test_a_stage_that_could_not_run_says_why(self):
        """The property inherited from StageResult: a cycle that ran without the
        daemon did not do less work badly, it did not do the work."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp, stages=[
                {"stage": "entity_rollups", "unavailable": "agentmd is not on PATH"}])
            self.build(tmp, answers(**daemon()))
            body = self.read(tmp)

        row = next(l for l in body.splitlines() if l.startswith("| entity_rollups |"))
        self.assertIn("did not run", row)
        self.assertIn("agentmd is not on PATH", row)
        self.assertNotIn("| 0 |", row, "an unavailable stage reported a zero")

    def test_a_stage_with_nothing_to_do_is_not_the_same_as_one_that_failed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp, stages=[{"stage": "stub_synthesis", "considered": 0}])
            self.build(tmp, answers(**daemon()))
            body = self.read(tmp)

        row = next(l for l in body.splitlines() if l.startswith("| stub_synthesis |"))
        self.assertIn("nothing to do", row)
        self.assertNotIn("did not run", row)

    def test_no_run_at_all_is_stated_rather_than_shown_as_empty(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.build(tmp, answers(**daemon()))
            body = self.read(tmp)

        self.assertIn("No staged run was found", body)

    def test_proposals_are_counted_by_stage(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp, proposals=[
                {"stage": "dedup", "mutations": [["a", "b"]]},
                {"stage": "dedup", "mutations": []},
                {"stage": "expire", "mutations": [["c", "d"]]},
            ])
            self.build(tmp, answers(**daemon()))
            body = self.read(tmp)

        dedup = next(l for l in body.splitlines() if l.startswith("| dedup |"))
        self.assertIn("2 considered", dedup)
        self.assertIn("1 written", dedup)


class LatestRunTests(unittest.TestCase):
    def test_the_newest_run_is_chosen_by_its_own_timestamp(self):
        """Not by directory mtime. The vault syncs across machines and through
        git, and both rewrite mtimes — a scorecard reporting the wrong night's
        run is worse than one reporting none."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            # The newest run is created first and named so it sorts first, so
            # that neither creation order nor alphabetical order points at the
            # right answer. An earlier fixture created them the other way round
            # and a "take whichever comes last" implementation passed it.
            stage_a_run(tmp, "a-newest", staged_at=200.0)
            older = stage_a_run(tmp, "z-oldest", staged_at=100.0)

            import os
            import time

            # And the older one is made to look newest on disk.
            os.utime(older / "proposals.json", (time.time(), time.time()))

            run = ds.latest_run(tmp, STAGE)
        self.assertEqual(run["run_id"], "a-newest",
                         "the run was chosen by something other than the "
                         "timestamp it recorded for itself")

    def test_an_unreadable_manifest_is_skipped_rather_than_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp, "run-good", staged_at=100.0)
            bad = tmp / STAGE / "run-bad"
            bad.mkdir(parents=True)
            (bad / "proposals.json").write_text("{ not json", encoding="utf-8")

            run = ds.latest_run(tmp, STAGE)
        self.assertEqual(run["run_id"], "run-good")

    def test_no_staging_directory_is_none_rather_than_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(ds.latest_run(Path(d), STAGE))


class FileTests(unittest.TestCase):
    def test_it_writes_a_dated_file_and_a_stable_one(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp)
            with mock.patch.object(ds, "_agentmd", side_effect=answers(**daemon())):
                dated, stable = ds.build(tmp, now=AT, rel=REL, staging=STAGE)

            self.assertTrue(dated.exists())
            self.assertTrue(stable.exists())
            self.assertEqual(dated.read_text(encoding="utf-8"),
                             stable.read_text(encoding="utf-8"))
            self.assertIn("2026-08-22", dated.name)
            self.assertIn("dreaming", dated.name)

    def test_a_silent_daemon_still_produces_a_report(self):
        """One component down does not take the report with it — and every row
        it could not fill says why."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stage_a_run(tmp)
            with mock.patch.object(ds, "_agentmd", side_effect=answers()):
                ds.build(tmp, now=AT, rel=REL, staging=STAGE)
            body = (tmp / REL / ds.STABLE_NAME).read_text(encoding="utf-8")

        self.assertIn("not measured:", body)
        for line in body.splitlines():
            if line.startswith("| unfiled queue |") or line.startswith("| enrichment coverage |"):
                self.assertIn(" — ", line, f"a silent daemon produced a number: {line}")


if __name__ == "__main__":
    unittest.main()
