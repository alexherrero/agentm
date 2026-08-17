#!/usr/bin/env python3
"""Tests for retrieval_scorecard.py's goldv3 additions: folder-level prefix
acceptance, the hook arm's `hook_reachable: false` denominator exclusion, and
`layer: gate-only` negative reporting (_harness/PLAN.md task 3).

`search()` and `search_via_hook()` shell out to the daemon; every test here
monkeypatches them to a canned ranked list so these run with no daemon, no
embedder, and no vault on disk. That keeps the mutation checks below honest —
they fail because the *scoring logic* changed, not because a fixture drifted.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "health"))

import retrieval_scorecard as rs  # noqa: E402


def entry(id_, expected=None, prefixes=None, stratum="pure-paraphrase",
          hook_reachable=None, layer=None, question="does not matter"):
    e = {"id": id_, "question": question, "stratum": stratum,
         "expected_note_paths": expected or []}
    if prefixes is not None:
        e["expected_note_prefixes"] = prefixes
    if hook_reachable is not None:
        e["hook_reachable"] = hook_reachable
    if layer is not None:
        e["layer"] = layer
    return e


def fake_search(ranked):
    """A `search()` stand-in returning a fixed ranked list, no subprocess."""
    def _search(query, k, mode="and", target=(), question=None, lex3=False):
        return list(ranked), 1.0, "", None, None
    return _search


class PrefixAcceptanceTests(unittest.TestCase):
    """goldv3 pp09: a folder-level `expected_note_prefixes` accepts any
    returned path under it, exact paths continuing to work unchanged."""

    def test_prefix_hit_when_no_exact_path_matches(self):
        e = entry("pp09", expected=["Agent/external/primos/_index.md"],
                   prefixes=["Agent/external/primos/"])
        ranked = ["Agent/external/primos/analysis/other-note.md"]
        with mock.patch.object(rs, "search", fake_search(ranked)):
            result = rs.score([e], k=5)
        row = result["rows"][0]
        self.assertTrue(row["hit"])
        self.assertEqual(row["first_hit_rank"], 1)

    def test_prefix_does_not_match_a_sibling_folder(self):
        e = entry("pp09", expected=["Agent/external/primos/_index.md"],
                   prefixes=["Agent/external/primos/"])
        ranked = ["Agent/external/other-project/_index.md"]
        with mock.patch.object(rs, "search", fake_search(ranked)):
            result = rs.score([e], k=5)
        self.assertFalse(result["rows"][0]["hit"])

    def test_exact_path_still_works_with_no_prefix_field(self):
        """v2-shaped entry (no `expected_note_prefixes` key at all)."""
        e = entry("dt01", expected=["Agent/desk/scratch/_index.md"])
        with mock.patch.object(rs, "search", fake_search(["Agent/desk/scratch/_index.md"])):
            result = rs.score([e], k=5)
        self.assertTrue(result["rows"][0]["hit"])

    def test_mutation_breaking_prefix_match_fails_this_test(self):
        """Guards against a no-op prefix implementation: with the prefix
        matching disabled (as if `startswith` were replaced by exact `==`),
        this must fail — proving the test can catch a broken implementation,
        not just confirm a working one."""
        e = entry("pp09", expected=["Agent/external/primos/_index.md"],
                   prefixes=["Agent/external/primos/"])
        ranked = ["Agent/external/primos/analysis/other-note.md"]
        with mock.patch.object(rs, "search", fake_search(ranked)):
            result = rs.score([e], k=5)
        # Direct mutation check: simulate "prefix matching removed" by
        # recomputing hit the way v2 did (exact paths only) and asserting the
        # two disagree — the fixture is deliberately built to hit only via
        # the prefix, so a broken implementation would score it a miss.
        exact_only_hit = bool(set(e["expected_note_paths"]) & set(ranked))
        self.assertFalse(exact_only_hit)
        self.assertTrue(result["rows"][0]["hit"])


class HookExclusionTests(unittest.TestCase):
    """goldv3 Group A (dt01/ep10/ep12): `hook_reachable: false` excludes a
    row from the hook arm's denominator only, never from a bare/--question
    run scoring the same entry."""

    def test_excluded_from_hook_arm_denominator(self):
        excluded = entry("dt01", expected=["Agent/desk/scratch/_index.md"],
                          hook_reachable=False, question="excluded question")
        # A render() call needs at least one ordinarily-scored row to divide
        # by — a companion hit keeps this test about exclusion, not about
        # render()'s pre-existing all-excluded-rows edge case.
        counted = entry("dt02", expected=["Agent/y.md"], question="counted question")

        def fake_hook(question_text, k, vault, index, budget_ms=None):
            if question_text == excluded["question"]:
                return [], 1.0, "", None, None
            return ["Agent/y.md"], 1.0, "", None, None

        with mock.patch.object(rs, "search_via_hook", fake_hook):
            result = rs.score([excluded, counted], k=5, via_hook=True, vault="/v", index="/i")
        row = result["rows"][0]
        self.assertTrue(row["hook_excluded"])
        # excluded rows are never counted as generic misses in render()
        rendered = rs.render(result, k=5)
        self.assertIn("hook-excluded (policy)", rendered)
        overall_line = next(l for l in rendered.splitlines() if l.startswith("OVERALL"))
        self.assertEqual(int(overall_line.split()[2]), 1)  # n=1 -- only `counted` in the denominator

    def test_not_excluded_on_a_non_hook_run(self):
        e = entry("dt01", expected=["Agent/desk/scratch/_index.md"],
                   hook_reachable=False)
        with mock.patch.object(rs, "search",
                                fake_search(["Agent/desk/scratch/_index.md"])):
            result = rs.score([e], k=5, via_hook=False)
        row = result["rows"][0]
        self.assertFalse(row["hook_excluded"])
        self.assertTrue(row["hit"])

    def test_default_hook_reachable_is_true_for_v2_entries(self):
        e = entry("dt02", expected=["Agent/x.md"])  # no hook_reachable key
        with mock.patch.object(rs, "search_via_hook",
                                return_value=(["Agent/x.md"], 1.0, "", None, None)):
            result = rs.score([e], k=5, via_hook=True, vault="/v", index="/i")
        self.assertFalse(result["rows"][0]["hook_excluded"])

    def test_mutation_removing_exclusion_fails_this_test(self):
        e = entry("dt01", expected=["Agent/desk/scratch/_index.md"],
                   hook_reachable=False)
        with mock.patch.object(rs, "search_via_hook",
                                return_value=([], 1.0, "", None, None)):
            result = rs.score([e], k=5, via_hook=True, vault="/v", index="/i")
        # If exclusion were silently dropped, this row would count as a
        # generic (scored, non-negative, non-excluded) miss instead.
        would_be_scored_as_generic_miss = (
            not result["rows"][0]["is_negative"]
            and not result["rows"][0]["hook_excluded"]
            and not result["rows"][0]["hit"]
        )
        self.assertFalse(would_be_scored_as_generic_miss)


class GateOnlyNegativeTests(unittest.TestCase):
    """goldv3: `layer: gate-only` negatives report in their own block,
    never inline in the per-stratum table or the ordinary rejection line."""

    def test_gate_only_negative_flagged_and_excluded_from_rejection_floor(self):
        neg = entry("ng01", stratum="negative", layer="gate-only")
        pos = entry("dt01", expected=["Agent/a.md"])  # render() needs a scored row

        def fake(query, k, mode="and", target=(), question=None, lex3=False):
            return ([], 1.0, "", None, None) if query == "?" else (["Agent/a.md"], 1.0, "", None, None)

        with mock.patch.object(rs, "search", fake), \
             mock.patch.object(rs, "to_query", side_effect=lambda q: "?" if q == neg["question"] else "x"):
            result = rs.score([neg, pos], k=5)
        row = result["rows"][0]
        self.assertTrue(row["is_negative"])
        self.assertTrue(row["gate_only"])
        rendered = rs.render(result, k=5)
        self.assertIn("gate-only (not scored here)", rendered)
        self.assertNotIn("negative", rendered.split("OVERALL")[0])  # not in the inline per-stratum table
        self.assertNotIn("rejection (floor)", rendered)  # all-gate-only, so the old line doesn't print

    def test_ordinary_negative_without_layer_still_reports_inline(self):
        """v2-shaped negative (no `layer` key) — old inline behavior."""
        neg = entry("ng01", stratum="negative")
        pos = entry("dt01", expected=["Agent/a.md"])

        def fake(query, k, mode="and", target=(), question=None, lex3=False):
            return ([], 1.0, "", None, None) if query == "?" else (["Agent/a.md"], 1.0, "", None, None)

        with mock.patch.object(rs, "search", fake), \
             mock.patch.object(rs, "to_query", side_effect=lambda q: "?" if q == neg["question"] else "x"):
            result = rs.score([neg, pos], k=5)
        row = result["rows"][0]
        self.assertFalse(row["gate_only"])
        rendered = rs.render(result, k=5)
        self.assertIn("negative", rendered.split("OVERALL")[0])  # inline, as v2 always rendered it
        self.assertIn("rejection (floor)", rendered)
        self.assertNotIn("gate-only (not scored here)", rendered)

    def test_mutation_forcing_gate_only_false_fails_this_test(self):
        e = entry("ng01", stratum="negative", layer="gate-only")
        with mock.patch.object(rs, "search", fake_search([])):
            result = rs.score([e], k=5)
        # A broken implementation that never reads `layer` would leave this
        # False for every negative, indistinguishable from a v2 entry.
        self.assertTrue(result["rows"][0]["gate_only"],
                         "gate_only must be True when the entry carries "
                         "layer: gate-only — a no-op implementation would "
                         "leave this False, same as an unannotated v2 entry")


class BackwardCompatibilityTests(unittest.TestCase):
    """A v2-shaped gold set (no expected_note_prefixes/hook_reachable/layer
    anywhere) must score and render identically to the pre-goldv3 scorer."""

    def test_v2_shaped_positive_entry_unaffected(self):
        e = entry("dt01", expected=["Agent/desk/scratch/_index.md"])
        with mock.patch.object(rs, "search",
                                fake_search(["Agent/desk/scratch/_index.md"])):
            result = rs.score([e], k=5)
        row = result["rows"][0]
        self.assertFalse(row["is_negative"])
        self.assertFalse(row["gate_only"])
        self.assertFalse(row["hook_excluded"])
        self.assertTrue(row["hit"])

    def test_v2_shaped_negative_entry_unaffected(self):
        e = entry("ng01", stratum="negative")
        with mock.patch.object(rs, "search", fake_search([])):
            result = rs.score([e], k=5)
        row = result["rows"][0]
        self.assertTrue(row["is_negative"])
        self.assertFalse(row["gate_only"])
        self.assertIsNone(row["hit"])
        self.assertTrue(row["correct_rejection"])

    def test_render_and_arm_cells_do_not_crash_on_v2_shape(self):
        entries = [
            entry("dt01", expected=["Agent/a.md"]),
            entry("ng01", stratum="negative"),
        ]
        with mock.patch.object(rs, "search", fake_search(["Agent/a.md"])):
            result = rs.score(entries, k=5)
        rs.render(result, k=5)  # must not raise
        rs.arm_cells(result, k=5)  # must not raise


if __name__ == "__main__":
    unittest.main()
