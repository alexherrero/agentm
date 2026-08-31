#!/usr/bin/env python3
"""The scorecard section, and the three ways it must refuse to claim things.

Every test here is about a number *not* being shown, or being shown with what
it cannot support attached. The section's whole job is to survive being read
cold by someone who was not here.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "health"))

import online_recall_row as row  # noqa: E402


def write(tmp, rows, *, age_days=0.0):
    p = pathlib.Path(tmp) / "panel.json"
    p.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    if age_days:
        old = time.time() - age_days * 86400
        import os
        os.utime(p, (old, old))
    return p


def panel_rows(n_suff, n_insuff, n_na):
    out = []
    i = 0
    for verdict, count in (("sufficient", n_suff), ("insufficient", n_insuff),
                           ("n/a", n_na)):
        for _ in range(count):
            out.append({"id": f"t{i}", "claude": verdict, "gemini": verdict})
            i += 1
    return out


class TheAbsentCase(unittest.TestCase):
    def test_no_artifact_is_empty_not_zero(self):
        got = row.compute(pathlib.Path("/nonexistent/panel.json"))
        self.assertEqual(got["state"], "ABSENT")
        self.assertNotIn("rate", got)
        md = "\n".join(row.render(got))
        self.assertIn("Not yet measured", md)
        self.assertNotIn("0.0%", md)


class TheStaleCase(unittest.TestCase):
    def test_an_old_artifact_renders_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write(tmp, panel_rows(30, 200, 60),
                      age_days=row.STALE_AFTER_DAYS + 5)
            got = row.compute(p)
            self.assertEqual(got["state"], "STALE")
            md = "\n".join(row.render(got))
            self.assertIn("STALE", md)
            self.assertIn("moved since", md)

    def test_a_fresh_artifact_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = row.compute(write(tmp, panel_rows(30, 200, 60)))
            self.assertNotEqual(got["state"], "STALE")
            self.assertNotIn("stale_note", got)

    def test_a_stale_artifact_still_shows_its_number(self):
        # Staleness is a caveat, not a reason to hide what was measured.
        with tempfile.TemporaryDirectory() as tmp:
            got = row.compute(write(tmp, panel_rows(30, 200, 60),
                                    age_days=40))
            self.assertIn("rate", got)


class TheMuteCase(unittest.TestCase):
    def test_too_few_turns_shows_the_rate_and_refuses_the_interval(self):
        # The rate is still a fact about what was seen; the interval is what
        # cannot be supported.
        with tempfile.TemporaryDirectory() as tmp:
            got = row.compute(write(tmp, panel_rows(2, 8, 5)))
            self.assertEqual(got["state"], "MUTE")
            self.assertAlmostEqual(got["rate"], 0.2)
            md = "\n".join(row.render(got))
            self.assertIn("sample too small", md)

    def test_enough_turns_gives_a_real_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = row.compute(write(tmp, panel_rows(75, 225, 100)))
            self.assertEqual(got["state"], "OK")
            lo, hi = got["always_valid_ci"]
            self.assertGreater(lo, 0.0)
            self.assertLess(hi, 1.0)

    def test_no_scored_turn_is_a_note_not_a_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = row.compute(write(tmp, panel_rows(0, 0, 40)))
            self.assertNotIn("rate", got)
            self.assertIn("statement about the judge", got["note"])


class TheExclusions(unittest.TestCase):
    def _reasons(self, tmp, ids):
        q = pathlib.Path(tmp) / "reasons.json"
        q.write_text(json.dumps({
            i: {"verdict": "insufficient",
                "why": ["what 'both' refers to in the prior turn"]}
            for i in ids}), encoding="utf-8")
        return q

    def test_a_gap_in_the_conversation_is_not_a_recall_failure(self):
        # Half the judge's insufficiency verdicts named a referent no note
        # could hold. Counting them moved the headline nine points.
        with tempfile.TemporaryDirectory() as tmp:
            rows = panel_rows(20, 60, 20)
            p_ = write(tmp, rows)
            drop = [r["id"] for r in rows if r["claude"] == "insufficient"][:40]
            got = row.compute(p_, reasons_path=self._reasons(tmp, drop))
            self.assertEqual(got["excluded"]["gap_not_retrievable"], 40)
            self.assertEqual(got["scored"], 40)
            self.assertEqual(got["scored_before_exclusions"], 80)
            self.assertGreater(got["rate"], 0.25)

    def test_the_exclusion_is_shown_not_applied_silently(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = panel_rows(20, 60, 20)
            drop = [r["id"] for r in rows if r["claude"] == "insufficient"][:40]
            md = "\n".join(row.render(row.compute(
                write(tmp, rows), reasons_path=self._reasons(tmp, drop))))
            self.assertIn("gap not retrievable", md)
            self.assertIn("could not have served them", md)

    def test_a_retired_arm_is_dropped_when_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = panel_rows(20, 60, 20)
            for r in rows[:15]:
                r["arm"] = "lexical"
            for r in rows[15:]:
                r["arm"] = "hybrid"
            got = row.compute(write(tmp, rows))
            self.assertEqual(got["excluded"]["retired_retrieval_arm"], 15)
            self.assertTrue(got["retrieval_arm_recorded"])

    def test_an_absent_arm_field_says_so_rather_than_skipping_quietly(self):
        # Absent is not the same as empty. Skipping silently would read as
        # "no turn ran on a retired arm", when nobody recorded any of them.
        with tempfile.TemporaryDirectory() as tmp:
            got = row.compute(write(tmp, panel_rows(20, 60, 20)))
            self.assertFalse(got["retrieval_arm_recorded"])
            self.assertIn("does not record which retrieval arm",
                          got["arm_note"])
            self.assertIn("that much pessimistic", got["arm_note"])

    def test_no_exclusions_means_no_exclusion_furniture(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = panel_rows(20, 60, 20)
            for r in rows:
                r["arm"] = "hybrid"
            got = row.compute(write(tmp, rows))
            self.assertIsNone(got["excluded"])
            self.assertNotIn("could not have served",
                             "\n".join(row.render(got)))


class TheUnvalidatedMarker(unittest.TestCase):
    def test_it_is_on_every_render_that_shows_a_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rows in (panel_rows(75, 225, 100), panel_rows(2, 8, 5),
                         panel_rows(30, 200, 60)):
                md = "\n".join(row.render(row.compute(write(tmp, rows))))
                self.assertIn("UNVALIDATED", md)

    def test_it_says_why_rather_than_just_flagging(self):
        # A bare flag invites someone to clear it. The reason is that no human
        # labels are obtainable from this data, which does not change by
        # waiting.
        with tempfile.TemporaryDirectory() as tmp:
            got = row.compute(write(tmp, panel_rows(75, 225, 100)))
            self.assertIn("same model family", got["validation_note"])
            self.assertIn("not independent", got["validation_note"])

    def test_grader_agreement_is_not_called_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = row.compute(write(tmp, panel_rows(75, 225, 100)))
            self.assertIn("not against a person", got["grader_kappa_note"])
            self.assertEqual(got["validation"], "UNVALIDATED")


class TheDriftBand(unittest.TestCase):
    def test_it_is_rendered_beside_the_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = "\n".join(row.render(
                row.compute(write(tmp, panel_rows(75, 225, 100)))))
            self.assertIn("instrument drift", md)
            self.assertIn("always-valid", md)


if __name__ == "__main__":
    unittest.main()
