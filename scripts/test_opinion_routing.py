#!/usr/bin/env python3
"""Tests for the accumulate loop's Stage 1 standard-shaped routing rule.

The contract's own sentence is the spec these assert against: a candidate
routes to an opinion supplement when it is "a rule about how work should be
judged or done, not a fact or preference."

The negative cases carry most of the weight. Over-routing puts noise in front
of the standards the agent works to; under-routing just sends a candidate down
its normal path. So the facts, preferences, and voice lessons below are the
tests that matter, and a classifier that always returned a target would fail
this file loudly.

Run: python3 scripts/test_opinion_routing.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import opinion_routing as orouting  # noqa: E402


class _Cand:
    """Minimal stand-in for reflect's Candidate (duck-typed on purpose)."""

    def __init__(self, title="", body="", category="workflow"):
        self.title = title
        self.body = body
        self.category = category


class TestRoutesStandards(unittest.TestCase):
    def test_gate_rule_routes_to_done(self):
        c = _Cand(
            title="Always run the full gate battery before committing",
            body="Never commit without check-all.sh passing green first.",
        )
        self.assertEqual(orouting.classify_standard_shaped(c), "done")

    def test_safety_rule_routes_to_recoverable(self):
        c = _Cand(
            title="Never force-push a shared branch",
            body="A force push that rewrites published history is unrecoverable; "
                 "you must confirm before any destructive push.",
        )
        self.assertEqual(orouting.classify_standard_shaped(c), "recoverable")

    def test_pii_rule_routes_to_private(self):
        c = _Cand(
            title="Never commit credentials",
            body="You must scrub any API key or secret out of a test fixture "
                 "before you commit it.",
        )
        self.assertEqual(orouting.classify_standard_shaped(c), "private")

    def test_cost_rule_routes_to_efficient(self):
        c = _Cand(
            title="Don't run a frontier model for mechanical edits",
            body="You should route a mechanical refactor to a cheaper tier; "
                 "the token cost of Opus on a rename is never justified.",
        )
        self.assertEqual(orouting.classify_standard_shaped(c), "efficient")

    def test_quality_rule_routes_to_good(self):
        c = _Cand(
            title="A regression test must fail against the unfixed code first",
            body="Always prove a new test has teeth before you keep it, or the "
                 "bug it claims to cover is not actually covered.",
        )
        self.assertEqual(orouting.classify_standard_shaped(c), "good")


class TestRejectsNonStandards(unittest.TestCase):
    """The load-bearing half — everything here must route normally."""

    def test_plain_fact_does_not_route(self):
        c = _Cand(
            title="The vault lives on a Google Drive mount",
            body="The MemoryVault root is outside the repo checkout.",
            category="preferences",
        )
        self.assertIsNone(orouting.classify_standard_shaped(c))

    def test_personal_preference_does_not_route(self):
        # Normative grammar, but about taste rather than work.
        c = _Cand(
            title="Always use dark mode",
            body="The operator prefers a dark theme in every editor.",
            category="preferences",
        )
        self.assertIsNone(orouting.classify_standard_shaped(c))

    def test_voice_lesson_never_routes(self):
        # The spec forbids double-capture: voice lessons live in the style
        # overlay, so this must not become an opinion supplement even though
        # it is rule-shaped AND mentions docs.
        c = _Cand(
            title="Never use peacock words in docs",
            body="You must always strip 'groundbreaking' and 'seamless' from "
                 "prose; the tone should stay plain.",
        )
        self.assertIsNone(orouting.classify_standard_shaped(c))

    def test_work_related_but_not_rule_shaped_does_not_route(self):
        c = _Cand(
            title="The release shipped on Tuesday",
            body="CI was green and the tag was pushed.",
        )
        self.assertIsNone(orouting.classify_standard_shaped(c))

    def test_rule_shaped_but_not_work_related_does_not_route(self):
        c = _Cand(
            title="You should always water the plants",
            body="Never skip a week.",
        )
        self.assertIsNone(orouting.classify_standard_shaped(c))

    def test_unclassifiable_standard_routes_normally(self):
        # Rule-shaped and work-related, but matching no opinion's vocabulary.
        # Deliberately NOT defaulted into how-we-engineer — that's the
        # MEDIUM-confidence case whose LLM assist is undesigned.
        c = _Cand(
            title="Always coordinate the deploy with the on-call",
            body="You must never deploy without telling them.",
        )
        self.assertIsNone(orouting.classify_standard_shaped(c))

    def test_empty_candidate_does_not_route(self):
        self.assertIsNone(orouting.classify_standard_shaped(_Cand()))


class TestSpecificityOrdering(unittest.TestCase):
    def test_privacy_wins_over_broader_families(self):
        # Mentions review (good) and commit (done) too, but the privacy
        # signal is the specific one and must win.
        c = _Cand(
            title="Never let a credential reach a review",
            body="You must scrub secrets before you commit or open a PR.",
        )
        self.assertEqual(orouting.classify_standard_shaped(c), "private")

    def test_only_documented_targets_are_returned(self):
        # simple / ready / worth-knowing are real opinions but are not
        # targets in the spec's signal map; nothing may route to them.
        for cand in (
            _Cand(title="Always keep the design simple",
                  body="You should never over-engineer a test."),
            _Cand(title="Never ship before it is ready",
                  body="You must verify the build."),
        ):
            got = orouting.classify_standard_shaped(cand)
            self.assertIn(got, (None,) + orouting.ROUTABLE_OPINIONS)
            self.assertNotIn(got, ("simple", "ready", "worth-knowing"))


if __name__ == "__main__":
    unittest.main()
