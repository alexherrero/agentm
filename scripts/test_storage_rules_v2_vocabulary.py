#!/usr/bin/env python3
"""The v2 vocabulary reaches the Python side of the contract.

The parser lives in Go and Python asks it, so these tests do not parse a rules
file — they pin the accessor surface `StorageRules` exposes over the daemon's
JSON, in both directions: a v2 contract answers with the new vocabulary, and a
pre-v2 contract (no lifecycle, no sources, no facets) answers with honest
emptiness rather than an error, because absence is the tolerated migration
state, not corruption.

No daemon binary is needed here: `StorageRules` wraps the dict the daemon would
have emitted, and the dict is what these tests construct. The Go tests
(`rules_test.go`) own proving the daemon emits it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from storage_rules import StorageRules  # noqa: E402


V2_CONTRACT = {
    "memory_types": ["preference", "convention", "reference", "workflow", "fix", "idea"],
    "record_kinds": ["brief", "calendar-facet", "day-index", "calendar-review"],
    "lifecycle": ["pinned", "active", "dormant", "archived", "superseded"],
    "default_lifecycle": "active",
    "sources": {
        "operator-direct": "trusted",
        "conversation": "trusted",
        "external-fetch": "untrusted",
        "email": "untrusted",
    },
    "facets": ["meetings", "correspondence", "docs", "diary"],
}

PRE_V2_CONTRACT = {
    "memory_types": ["preference", "convention", "reference", "workflow", "fix", "idea"],
    "record_kinds": ["brief"],
}


class V2VocabularyReachesPython(unittest.TestCase):
    def setUp(self):
        self.rules = StorageRules(V2_CONTRACT)

    def test_lifecycle_axis_round_trips(self):
        self.assertEqual(
            self.rules.lifecycles(),
            frozenset({"pinned", "active", "dormant", "archived", "superseded"}))
        self.assertNotIn("expired", self.rules.lifecycles(),
                         "`expired` was retired as a data-quality artifact, not a state")
        self.assertEqual(self.rules.default_lifecycle(), "active")

    def test_sources_carry_their_tiers(self):
        sources = self.rules.sources()
        self.assertEqual(sources["operator-direct"], "trusted")
        self.assertEqual(sources["external-fetch"], "untrusted",
                         "a fetched page is untrusted however plausible it reads")
        self.assertNotIn("carrier-pigeon", sources)

    def test_facets_keep_registry_order(self):
        self.assertEqual(self.rules.facets(),
                         ("meetings", "correspondence", "docs", "diary"))

    def test_calendar_kinds_are_registered(self):
        for kind in ("calendar-facet", "day-index", "calendar-review"):
            self.assertIn(kind, self.rules.record_kinds())


class PreV2ContractAnswersWithAbsence(unittest.TestCase):
    """A contract from before the v2 edit is the tolerated migration state.

    The accessors answer with emptiness, never an exception — the same shape
    `model_exempt_spaces()` already has for a contract that names none.
    """

    def setUp(self):
        self.rules = StorageRules(PRE_V2_CONTRACT)

    def test_absent_vocabulary_is_empty_not_an_error(self):
        self.assertEqual(self.rules.lifecycles(), frozenset())
        self.assertEqual(self.rules.default_lifecycle(), "")
        self.assertEqual(self.rules.sources(), {})
        self.assertEqual(self.rules.facets(), ())


if __name__ == "__main__":
    unittest.main()
