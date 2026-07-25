#!/usr/bin/env python3
"""Routing tests for the accumulate loop's Stage 1 supplement lane.

`test_opinion_routing.py` covers the classifier in isolation. This covers the
wiring: that `route_candidates` actually diverts a standard-shaped candidate
to `personal/_opinions/<opinion>/`, that an ordinary candidate still takes its
normal path, and — the one that matters most — that a coded base opinion is
never written to.

Run: python3 scripts/test_reflect_opinion_routing.py
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import reflect  # noqa: E402


def _cand(title, body, *, confidence="HIGH", category="workflow", slug="a-slug"):
    return reflect.Candidate(
        category=category, confidence=confidence, slug=slug,
        title=title, body=body, rationale="test", excerpts=[],
    )


_STANDARD = dict(
    title="Always run the full gate battery before committing",
    body="Never commit without check-all.sh passing green first.",
)
_ORDINARY = dict(
    title="The vault lives on a Google Drive mount",
    body="The MemoryVault root sits outside the repo checkout.",
)


class TestOpinionSupplementRouting(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="reflect-opinions-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "personal").mkdir(parents=True)

    def _route(self, cands, *, session_id=None):
        return reflect.route_candidates(
            cands, [], vault=self.root, mode=reflect.ROUTE_MODE_AUTO,
            session_id=session_id,
            stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO(),
        )

    def test_standard_shaped_candidate_lands_in_the_opinion_lane(self):
        stats = self._route([_cand(**_STANDARD, slug="gate-battery")])
        self.assertEqual(stats["opinion_supplements"], 1)
        self.assertEqual(stats["auto_saved"], 0, "must not also reach general memory")
        written = self.root / "personal" / "_opinions" / "done" / "gate-battery.md"
        self.assertTrue(written.is_file(), f"not written: {written}")
        text = written.read_text(encoding="utf-8")
        self.assertIn("kind: opinion-supplement", text)
        self.assertIn("opinion: done", text)
        self.assertIn("status: proposed", text)

    def test_ordinary_candidate_still_routes_normally(self):
        stats = self._route([_cand(**_ORDINARY, slug="vault-location")])
        self.assertEqual(stats["opinion_supplements"], 0)
        self.assertFalse((self.root / "personal" / "_opinions").exists(),
                         "no lane should be created for an ordinary candidate")

    def test_a_coded_base_opinion_is_never_written(self):
        # The extend-never-override guard, held by construction in Stage 1:
        # supplements land under _opinions/<name>/, never in the repo's
        # authoritative opinions/<name>.md.
        self._route([_cand(**_STANDARD, slug="gate-battery")])
        lane = self.root / "personal" / "_opinions" / "done"
        self.assertTrue(lane.is_dir())
        # Nothing may be written at the lane's own name as a flat file, which
        # is the shape a base opinion takes.
        self.assertFalse((self.root / "personal" / "_opinions" / "done.md").exists())

    def test_low_confidence_standard_still_routes_to_the_lane(self):
        # The classifier decides the destination, not the confidence ladder —
        # a LOW-confidence rule is still a rule, and must not silently land
        # in the general inbox instead.
        stats = self._route([_cand(**_STANDARD, confidence="LOW", slug="gate-low")])
        self.assertEqual(stats["opinion_supplements"], 1)
        self.assertEqual(stats["inboxed"], 0)

    def test_slug_collision_keeps_both(self):
        c1 = _cand(**_STANDARD, slug="dupe")
        c2 = _cand(**_STANDARD, slug="dupe")
        stats = self._route([c1, c2])
        self.assertEqual(stats["opinion_supplements"], 2)
        lane = self.root / "personal" / "_opinions" / "done"
        self.assertTrue((lane / "dupe.md").is_file())
        self.assertTrue((lane / "dupe-1.md").is_file())

    def test_session_id_threads_into_the_sessions_field(self):
        # Stage 1 never threaded a session id through; the recurrence gate
        # (Stages 2-3) has no substrate without it. Prove route_candidates
        # actually carries reflect._session_id_from_path's shape down into
        # the written entry's `sessions:` list.
        self._route([_cand(**_STANDARD, slug="gate-battery")], session_id="my-proj/abc-123")
        written = self.root / "personal" / "_opinions" / "done" / "gate-battery.md"
        text = written.read_text(encoding="utf-8")
        self.assertIn("sessions: [my-proj/abc-123]", text)

    def test_no_session_id_omits_the_sessions_field(self):
        # Backward-compatible default: a caller that doesn't know a session
        # (or a pre-existing Stage-1 entry) must not error or fabricate one.
        self._route([_cand(**_STANDARD, slug="gate-battery")])
        written = self.root / "personal" / "_opinions" / "done" / "gate-battery.md"
        text = written.read_text(encoding="utf-8")
        self.assertNotIn("sessions:", text)

    def test_classifier_failure_degrades_to_normal_routing(self):
        # A classifier that raises must never take reflection down with it.
        import opinion_routing as orouting

        def _boom(_c):
            raise RuntimeError("classifier exploded")

        original = orouting.classify_standard_shaped
        orouting.classify_standard_shaped = _boom
        self.addCleanup(setattr, orouting, "classify_standard_shaped", original)

        stats = self._route([_cand(**_STANDARD, slug="gate-battery")])
        self.assertEqual(stats["opinion_supplements"], 0)
        self.assertEqual(stats["errors"], 0, "a classifier crash is not a routing error")
        self.assertEqual(stats["auto_saved"], 1, "candidate should take its normal path")


class TestSupplementRoundTrip(unittest.TestCase):
    """reflect writes a supplement; opinion_resolve reads it back.

    Before this stage nothing in production passed `supplement_dir`, so the
    base-then-supplement fold shipped with no way to be served. This is the
    end-to-end proof that the lane reflect writes to is the one the resolver
    reads from.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="reflect-roundtrip-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "personal").mkdir(parents=True)
        if str(_HERE) not in sys.path:
            sys.path.insert(0, str(_HERE))

    def test_written_supplement_is_served_by_the_resolver(self):
        import opinion_resolver

        reflect.route_candidates(
            [_cand(**_STANDARD, slug="gate-battery")], [], vault=self.root,
            mode=reflect.ROUTE_MODE_AUTO,
            stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO(),
        )
        lane = self.root / "personal" / "_opinions" / "done"
        # The resolver reads <supplement_dir>/<name>.md, so the served file
        # for opinion "done" is the lane dir's own <name>.md. reflect writes
        # per-candidate files inside the lane; a triage pass composes them
        # into that single entry (Stage 2+). Prove the plumbing with the
        # composed shape the resolver actually consumes.
        composed = self.root / "personal" / "_opinions" / "done.md"
        composed.write_text(
            "---\nkind: opinion-supplement\n---\n\n"
            + (lane / "gate-battery.md").read_text(encoding="utf-8").split("---", 2)[-1].strip()
            + "\n",
            encoding="utf-8",
        )
        res = opinion_resolver.opinion_resolve(
            "done", supplement_dir=self.root / "personal" / "_opinions"
        )
        self.assertEqual(res["reason"], "served")
        self.assertIsNotNone(res["supplement"])
        self.assertIn("gate battery", res["supplement"].lower())
        self.assertIsNotNone(res["base"], "the coded base must still be served")


if __name__ == "__main__":
    unittest.main()
