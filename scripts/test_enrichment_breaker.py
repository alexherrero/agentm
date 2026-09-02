#!/usr/bin/env python3
"""The breaker: it stays tripped, and clearing it is somebody's decision.

The detector underneath (`dream_confirm.check_stage_anomaly`) already decides
whether tonight is anomalous. What these tests are about is the part it does not
do — staying paused across cycles, and resuming only because a person said so.
"""

from __future__ import annotations

import json
import sys
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "harness/skills/memory/scripts"))

import enrichment_breaker as eb  # noqa: E402

AT = datetime(2026, 8, 23, 3, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 8, 24, 3, 0, tzinfo=timezone.utc)


def rising(value: float, threshold: float = 0.60) -> eb.Trip:
    """A meter where up is bad — pairwise similarity, say."""
    return eb.Trip(meter="pairwise-similarity", value=value,
                   threshold=threshold, direction="above")


def falling(value: float, threshold: float = 0.02) -> eb.Trip:
    """A meter where down is bad — nearest-neighbour dispersion."""
    return eb.Trip(meter="dispersion", value=value, threshold=threshold,
                   direction="below")


class TripTests(unittest.TestCase):
    def test_a_reading_inside_its_line_does_not_trip(self):
        with tempfile.TemporaryDirectory() as d:
            st = eb.consider(Path(d), "enrich", rising(0.50), now=AT)
        self.assertTrue(st.may_auto_apply())
        self.assertFalse(st.open)

    def test_a_reading_past_its_line_pauses_auto_apply(self):
        with tempfile.TemporaryDirectory() as d:
            st = eb.consider(Path(d), "enrich", rising(0.71), now=AT)
        self.assertFalse(st.may_auto_apply())
        self.assertIn("pairwise-similarity", st.reason)
        self.assertIn("0.71", st.reason)
        self.assertIn("0.60", st.reason, "the line it crossed is not in the reason")

    def test_direction_is_per_meter(self):
        """A falling meter and a rising one are both 'bad' in opposite
        directions, and a breaker that only understood one would sit silent
        through the other."""
        # Breaker state is per-machine engine state now (filing-v2 2a); a
        # fresh context means a fresh $AGENTM_STATE_DIR, not a fresh vault.
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as s:
            os.environ["AGENTM_STATE_DIR"] = s
            self.assertFalse(
                eb.consider(Path(d), "a", falling(0.01), now=AT).may_auto_apply())
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as s:
            os.environ["AGENTM_STATE_DIR"] = s
            self.assertTrue(
                eb.consider(Path(d), "a", falling(0.05), now=AT).may_auto_apply())

    def test_it_stays_tripped_when_the_next_reading_looks_fine(self):
        """The property the per-cycle detector does not have.

        A corpus that spiked on Tuesday and looked ordinary on Wednesday would
        otherwise resume on Wednesday with nobody having seen Tuesday.
        """
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            st = eb.consider(vault, "enrich", rising(0.40), now=LATER)

        self.assertFalse(st.may_auto_apply(), "a quiet night resumed the pass")
        self.assertIn("0.71", st.reason,
                      "the reason was overwritten by the quiet night's numbers")
        self.assertEqual(st.tripped_at, "2026-08-23T03:00:00Z", "the trip time moved")

    def test_the_first_reason_is_the_one_kept(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            st = eb.consider(vault, "enrich", rising(0.95), now=LATER)
        self.assertIn("0.71", st.reason,
                      "a worse night replaced what somebody is being asked to look at")

    def test_stages_do_not_pause_each_other(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            self.assertTrue(eb.state(vault, "entity-rollup").may_auto_apply())


class ResumeTests(unittest.TestCase):
    def test_resuming_needs_a_name(self):
        """A resume nobody can be asked about is a timeout wearing a person's
        clothes."""
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            for bad in ("", "   "):
                with self.assertRaises(ValueError):
                    eb.resume(vault, "enrich", by=bad, now=LATER)
            self.assertFalse(eb.state(vault, "enrich").may_auto_apply(),
                             "a rejected resume cleared the breaker anyway")

    def test_resuming_records_who_and_when(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            st = eb.resume(vault, "enrich", by="alex", now=LATER)

        self.assertTrue(st.may_auto_apply())
        self.assertEqual(st.acknowledged_by, "alex")
        self.assertEqual(st.acknowledged_at, "2026-08-24T03:00:00Z")

    def test_the_acknowledged_reading_does_not_trip_again(self):
        """Somebody looked at this number and said continue. Re-alarming on it
        would make the acknowledgement meaningless and train them to ignore it.
        """
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            eb.resume(vault, "enrich", by="alex", now=LATER)
            st = eb.consider(vault, "enrich", rising(0.71), now=LATER)

        self.assertTrue(st.may_auto_apply(),
                        "the acknowledged reading tripped the breaker again")

    def test_a_worse_reading_trips_even_after_an_acknowledgement(self):
        """Acknowledging one number is not acknowledging every future number."""
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            eb.resume(vault, "enrich", by="alex", now=LATER)
            st = eb.consider(vault, "enrich", rising(0.88), now=LATER)

        self.assertFalse(st.may_auto_apply(),
                         "a worse reading was waved through by an older "
                         "acknowledgement")

    def test_resuming_a_closed_breaker_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            st = eb.resume(vault, "enrich", by="alex", now=AT)
        self.assertTrue(st.may_auto_apply())
        self.assertEqual(st.acknowledged_by, "")


class DigestTests(unittest.TestCase):
    def test_an_open_breaker_says_what_and_how_to_clear_it(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            line = eb.digest_line(eb.state(vault, "enrich"))

        self.assertIn("paused", line)
        self.assertIn("pairwise-similarity", line)
        self.assertIn("--resume", line, "the digest does not say how to clear it")

    def test_a_closed_breaker_still_reports(self):
        """Silence would leave the reader unable to tell 'not paused' from
        'nobody checked' — the same absence-versus-zero confusion the scorecards
        are built to avoid."""
        with tempfile.TemporaryDirectory() as d:
            line = eb.digest_line(eb.state(Path(d), "enrich"))
        self.assertTrue(line.strip())
        self.assertIn("never tripped", line)

    def test_a_cleared_breaker_says_who_cleared_it(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            eb.resume(vault, "enrich", by="alex", now=LATER)
            line = eb.digest_line(eb.state(vault, "enrich"))
        self.assertIn("alex", line)


class DurabilityTests(unittest.TestCase):
    def test_the_pause_survives_a_new_process(self):
        """State on disk, not in a run. A breaker held in memory would clear
        itself every night at midnight."""
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            self.assertFalse(eb.state(vault, "enrich").may_auto_apply())
            self.assertTrue((eb.engine_state.engine_state_dir() / "enrich-breaker.json").is_file())

    def test_an_unreadable_file_fails_open(self):
        """This guards a convenience. A breaker that jammed shut over a
        truncated JSON file would stop the nightly pass for an unrelated fault;
        failing open costs one more cycle before somebody notices."""
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            p = eb.engine_state.engine_state_dir() / "enrich-breaker.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{ truncated", encoding="utf-8")
            self.assertTrue(eb.state(vault, "enrich").may_auto_apply())

    def test_the_record_is_readable_json(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            data = json.loads((eb.engine_state.engine_state_dir() / "enrich-breaker.json")
                              .read_text(encoding="utf-8"))
        self.assertTrue(data["open"])
        self.assertEqual(data["trip"]["meter"], "pairwise-similarity")
        self.assertEqual(data["trip"]["threshold"], 0.60)


class WiringTests(unittest.TestCase):
    """The criterion, not the latch in isolation: a trip has to actually stop
    the drain and actually reach the digest."""

    def setUp(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import test_dream_stages as tds  # noqa: E402

        self.tds = tds
        import dream_stages  # noqa: E402
        import work_ledger  # noqa: E402

        self.dream_stages = dream_stages
        self.work_ledger = work_ledger

    def install(self, fake):
        """Point the stages at `fake` for the duration of one test.

        The same shape `StageTestCase` next door uses — patch the individual
        functions, restore them on cleanup. An earlier version replaced the
        module reference outright and never put it back, which passed in
        isolation and corrupted every later test in a full discover run.
        """
        for name in ("entity_mentions", "dangling_targets", "backlinks",
                     "pending", "enqueue"):
            original = getattr(self.work_ledger, name)
            setattr(self.work_ledger, name, getattr(fake, name))
            self.addCleanup(setattr, self.work_ledger, name, original)
        return fake

    def test_an_open_breaker_stops_the_drain_enqueuing_anything(self):
        fake = self.tds.FakeLedger(pending={
            "eligible": 10, "current": 0,
            "pending": [{"target": f"memory/{i}.md", "reason": "never"}
                        for i in range(10)]})
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            self.install(fake)
            res = self.dream_stages.stage_unfiled_drain(
                enabled=True, budget=5, vault_path=vault)

        self.assertEqual(fake.enqueued, [],
                         "a paused pass enqueued work anyway")
        self.assertTrue(any("paused" in n for n in res.notes),
                        f"the drain did not say why it did nothing: {res.notes}")

    def test_a_closed_breaker_lets_the_drain_run(self):
        """Otherwise the test above would pass over a drain that never enqueues."""
        fake = self.tds.FakeLedger(pending={
            "eligible": 10, "current": 0,
            "pending": [{"target": f"memory/{i}.md", "reason": "never"}
                        for i in range(10)]})
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            self.install(fake)
            self.dream_stages.stage_unfiled_drain(
                enabled=True, budget=5, vault_path=vault)

        self.assertEqual(len(fake.enqueued), 5,
                         "the drain did nothing even with the breaker closed")

    def test_the_breaker_reaches_the_digest_through_the_stage_list(self):
        """Through `run_new_stages`, not by calling the stage directly.

        Calling a function is not the same as it being wired in, and only the
        second puts the breaker in the digest — measured: removing it from the
        list changed nothing while this test called it by hand.
        """
        fake = self.tds.FakeLedger(
            pending={"eligible": 0, "current": 0, "pending": []})
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            eb.consider(vault, "enrich", rising(0.71), now=AT)
            self.install(fake)
            results = self.dream_stages.run_new_stages(vault)

        breaker = next((r for r in results if r.stage == "breaker"), None)
        self.assertIsNotNone(breaker,
                             "the breaker is not among the stages the digest folds in")
        self.assertTrue(any("paused" in n for n in breaker.notes))


if __name__ == "__main__":
    unittest.main()
