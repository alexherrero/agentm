#!/usr/bin/env python3
"""Unit tests for `opinion_supplement.py` — the accumulate loop's Stages 2-3
(recurrence gate, contradiction check, composition), per the ten locked
design calls in `wiki/designs/agentm-experience-and-dreaming.md`.

Run: python3 scripts/test_opinion_supplement.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import opinion_supplement as osup  # noqa: E402


_ENTRY_TEMPLATE = (
    "---\nkind: opinion-supplement\nstatus: proposed\ncreated: {created}\n"
    "slug: {slug}\nopinion: {opinion}\n{sessions_line}---\n\n"
    "## {title}\n\n{body}\n\n"
    "## Mining metadata\n\n- **Category**: `workflow`\n"
)


class _SupplementTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        self.repo_root = Path(self._tmp.name) / "repo"
        (self.repo_root / "opinions").mkdir(parents=True)

    def _write_base(self, opinion: str, body: str) -> None:
        (self.repo_root / "opinions" / f"{opinion}.md").write_text(
            f"---\nname: {opinion}\nkind: opinion\n---\n{body}\n", encoding="utf-8"
        )

    def _write_entry(self, opinion: str, slug: str, title: str, body: str, *,
                      created: str = "2026-01-01T00:00:00+00:00",
                      sessions: "list | None" = None) -> Path:
        lane = self.vault / "personal" / "_opinions" / opinion
        lane.mkdir(parents=True, exist_ok=True)
        sessions_line = f"sessions: [{', '.join(sessions)}]\n" if sessions else ""
        content = _ENTRY_TEMPLATE.format(
            created=created, slug=slug, opinion=opinion, sessions_line=sessions_line,
            title=title, body=body,
        )
        path = lane / f"{slug}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def _process(self, opinion: str, *, now: str = "2026-07-25T00:00:00+00:00"):
        return osup.process_lane(self.vault, opinion, now=now, root=self.repo_root)


class RecurrenceGateTests(_SupplementTestBase):
    def test_single_occurrence_parks_never_promotes(self) -> None:
        self._write_base("good", "Always write a test for new behavior.")
        self._write_entry("good", "solo", "Rule", "Always run the linter before committing.",
                           sessions=["proj/s1"])
        result = self._process("good")
        self.assertIsNotNone(result)
        served = self.vault / "personal" / "_opinions" / "good.md"
        self.assertFalse(served.exists())
        patched = dict(result.mutations)
        solo_path = self.vault / "personal" / "_opinions" / "good" / "solo.md"
        self.assertIn("status: parked", patched[solo_path])

    def test_two_distinct_sessions_promotes(self) -> None:
        self._write_base("good", "Always write a test for new behavior.")
        self._write_entry("good", "a1", "Rule", "Always run the linter before committing.",
                           created="2026-01-01T00:00:00+00:00", sessions=["proj/s1"])
        self._write_entry("good", "a2", "Rule", "Always run the linter before committing!",
                           created="2026-01-02T00:00:00+00:00", sessions=["proj/s2"])
        result = self._process("good")
        self.assertIsNotNone(result)
        served = self.vault / "personal" / "_opinions" / "good.md"
        patched = dict(result.mutations)
        self.assertIn(served, patched)
        self.assertIn("Always run the linter", patched[served])
        survivor = self.vault / "personal" / "_opinions" / "good" / "a1.md"
        self.assertIn("status: promoted", patched[survivor])

    def test_same_session_twice_does_not_satisfy_the_gate(self) -> None:
        # Two entries from the SAME session are one occurrence, not two —
        # the spec's own anecdote test. Similar enough to cluster, but the
        # unioned session set still has only one distinct id.
        self._write_base("good", "Always write a test for new behavior.")
        self._write_entry("good", "a1", "Rule", "Always run the linter before committing.",
                           created="2026-01-01T00:00:00+00:00", sessions=["proj/s1"])
        self._write_entry("good", "a2", "Rule", "Always run the linter before committing!",
                           created="2026-01-02T00:00:00+00:00", sessions=["proj/s1"])
        result = self._process("good")
        served = self.vault / "personal" / "_opinions" / "good.md"
        self.assertFalse(served.exists())
        if result is not None:
            patched = dict(result.mutations)
            self.assertNotIn(served, patched)

    def test_zero_sessions_entry_never_promotes_alone(self) -> None:
        # A Stage-1-era entry with no `sessions:` field at all counts as
        # zero sessions and can never satisfy the gate by itself.
        self._write_base("good", "Always write a test for new behavior.")
        self._write_entry("good", "legacy", "Rule", "Always run the linter before committing.")
        result = self._process("good")
        served = self.vault / "personal" / "_opinions" / "good.md"
        self.assertFalse(served.exists())
        if result is not None:
            self.assertNotIn(served, dict(result.mutations))

    def test_dissimilar_entries_never_cluster(self) -> None:
        self._write_base("good", "Always write a test for new behavior.")
        self._write_entry("good", "a1", "Rule A", "Always run the linter before committing.",
                           sessions=["proj/s1"])
        self._write_entry("good", "b1", "Rule B", "Never merge without an approving review.",
                           sessions=["proj/s2"])
        result = self._process("good")
        served = self.vault / "personal" / "_opinions" / "good.md"
        self.assertFalse(served.exists())
        if result is not None:
            self.assertNotIn(served, dict(result.mutations))

    def test_no_active_entries_returns_none(self) -> None:
        self.assertIsNone(self._process("good"))

    def test_missing_lane_dir_returns_none(self) -> None:
        self.assertIsNone(osup.process_lane(self.vault, "never-seeded", root=self.repo_root))


class ContradictionCheckTests(_SupplementTestBase):
    def test_direct_reversal_on_shared_anchor_parks_and_flags(self) -> None:
        self._write_base("recoverable", "You must always confirm before a force-push to a shared branch.")
        self._write_entry("recoverable", "a1", "Force-push rule",
                           "Never confirm before a force-push to a shared branch.",
                           created="2026-01-01T00:00:00+00:00", sessions=["proj/s1"])
        self._write_entry("recoverable", "a2", "Force-push rule",
                           "Never confirm before a force-push to a shared branch!",
                           created="2026-01-02T00:00:00+00:00", sessions=["proj/s2"])
        result = self._process("recoverable")
        self.assertIsNotNone(result)
        served = self.vault / "personal" / "_opinions" / "recoverable.md"
        self.assertFalse(served.exists(), "a contradicting group must never be served")
        self.assertEqual(len(result.base_change_proposals), 1)
        self.assertEqual(result.base_change_proposals[0]["opinion"], "recoverable")

    def test_unresolved_contradiction_still_reports_on_a_stable_rerun(self) -> None:
        # A contradiction flagged on cycle 1 must still be visible on cycle
        # 2 even if NOTHING else in the lane changed -- the aggregate
        # base-proposals file must never silently drop a still-open finding
        # just because its own group stopped mutating.
        self._write_base("recoverable", "You must always confirm before a force-push to a shared branch.")
        self._write_entry("recoverable", "a1", "Force-push rule",
                           "Never confirm before a force-push to a shared branch.",
                           created="2026-01-01T00:00:00+00:00", sessions=["proj/s1"])
        self._write_entry("recoverable", "a2", "Force-push rule",
                           "Never confirm before a force-push to a shared branch!",
                           created="2026-01-02T00:00:00+00:00", sessions=["proj/s2"])
        first = self._process("recoverable")
        self.assertEqual(len(first.base_change_proposals), 1)
        for path, content in first.mutations:
            if content is None:
                path.unlink()
            else:
                path.write_text(content, encoding="utf-8")
        second = self._process("recoverable")
        self.assertIsNotNone(second, "a still-open contradiction must not vanish from the report")
        self.assertEqual(len(second.base_change_proposals), 1)

    def test_agreeing_polarity_is_not_a_contradiction(self) -> None:
        self._write_base("recoverable", "You must always confirm before a force-push to a shared branch.")
        self._write_entry("recoverable", "a1", "Force-push rule",
                           "You must always confirm before a force-push to a shared branch.",
                           created="2026-01-01T00:00:00+00:00", sessions=["proj/s1"])
        self._write_entry("recoverable", "a2", "Force-push rule",
                           "You must always confirm before a force-push to a shared branch!",
                           created="2026-01-02T00:00:00+00:00", sessions=["proj/s2"])
        result = self._process("recoverable")
        self.assertIsNotNone(result)
        self.assertEqual(result.base_change_proposals, [])
        served = self.vault / "personal" / "_opinions" / "recoverable.md"
        self.assertIn(served, dict(result.mutations))

    def test_opposite_polarity_without_shared_anchor_is_not_flagged(self) -> None:
        # Narrow-by-design (locked call 5): opposite polarity alone, with NO
        # shared work-domain anchor, must not trip the check.
        self._write_base("recoverable", "You must always confirm before a force-push to a shared branch.")
        self._write_entry("recoverable", "a1", "Unrelated rule",
                           "Never leave a TODO comment unresolved in reviewed code.",
                           created="2026-01-01T00:00:00+00:00", sessions=["proj/s1"])
        self._write_entry("recoverable", "a2", "Unrelated rule",
                           "Never leave a TODO comment unresolved in reviewed code!",
                           created="2026-01-02T00:00:00+00:00", sessions=["proj/s2"])
        result = self._process("recoverable")
        self.assertIsNotNone(result)
        self.assertEqual(result.base_change_proposals, [])


class CompositionTests(_SupplementTestBase):
    def test_composed_file_never_duplicates_identical_paraphrases(self) -> None:
        self._write_base("done", "Ship only what passes the gate battery.")
        self._write_entry("done", "a1", "Gate rule", "Always run the full gate battery before committing.",
                           created="2026-01-01T00:00:00+00:00", sessions=["proj/s1"])
        self._write_entry("done", "a2", "Gate rule", "Always run the full gate battery before committing.",
                           created="2026-01-02T00:00:00+00:00", sessions=["proj/s2"])
        result = self._process("done")
        served = self.vault / "personal" / "_opinions" / "done.md"
        content = dict(result.mutations)[served]
        self.assertEqual(content.count("Always run the full gate battery"), 1)

    def test_rerun_with_no_new_signal_proposes_nothing(self) -> None:
        # Idempotence: applying the same lane state twice in a row (as if
        # confirmed then re-run) must not re-propose an identical mutation.
        self._write_base("done", "Ship only what passes the gate battery.")
        a1 = self._write_entry("done", "a1", "Gate rule", "Always run the full gate battery.",
                                created="2026-01-01T00:00:00+00:00", sessions=["proj/s1"])
        a2 = self._write_entry("done", "a2", "Gate rule", "Always run the full gate battery!",
                                created="2026-01-02T00:00:00+00:00", sessions=["proj/s2"])
        result = self._process("done")
        self.assertIsNotNone(result)
        # Apply the proposal for real (simulating a confirm).
        for path, content in result.mutations:
            if content is None:
                path.unlink()
            else:
                path.write_text(content, encoding="utf-8")
        rerun = self._process("done")
        self.assertIsNone(rerun, "an unchanged, already-confirmed lane must propose nothing")

    def test_empty_promoted_set_removes_the_served_file(self) -> None:
        self._write_base("done", "Ship only what passes the gate battery.")
        served = self.vault / "personal" / "_opinions" / "done.md"
        served.parent.mkdir(parents=True, exist_ok=True)
        served.write_text("---\nkind: opinion-supplement\nstatus: promoted\n---\n\nStale content.\n",
                           encoding="utf-8")
        # No lane entries at all with status promoted -- self-healing must
        # remove the now-orphaned served file.
        (self.vault / "personal" / "_opinions" / "done").mkdir(parents=True, exist_ok=True)
        result = osup.process_lane(self.vault, "done", root=self.repo_root)
        self.assertIsNotNone(result)
        self.assertIn(served, dict(result.mutations))
        self.assertIsNone(dict(result.mutations)[served])

    def test_no_coded_base_skips_promotion_entirely(self) -> None:
        # A hand-made lane with no matching opinions/<name>.md base has
        # nothing authoritative to extend -- never promotes.
        self._write_entry("bogus", "a1", "Rule", "Always do the thing.",
                           created="2026-01-01T00:00:00+00:00", sessions=["proj/s1"])
        self._write_entry("bogus", "a2", "Rule", "Always do the thing!",
                           created="2026-01-02T00:00:00+00:00", sessions=["proj/s2"])
        result = osup.process_lane(self.vault, "bogus", root=self.repo_root)
        served = self.vault / "personal" / "_opinions" / "bogus.md"
        self.assertFalse(served.exists())
        if result is not None:
            self.assertNotIn(served, dict(result.mutations))


class HealthAndBaseProposalsTests(_SupplementTestBase):
    def test_lane_health_counts_by_status(self) -> None:
        lane = self.vault / "personal" / "_opinions" / "done"
        lane.mkdir(parents=True)
        (lane / "a.md").write_text(
            "---\nkind: opinion-supplement\nstatus: promoted\nrefs: [PR-1]\n---\nA\n", encoding="utf-8")
        (lane / "b.md").write_text(
            "---\nkind: opinion-supplement\nstatus: parked\n---\nB\n", encoding="utf-8")
        (lane / "c.md").write_text(
            "---\nkind: opinion-supplement\nstatus: proposed\n---\nC\n", encoding="utf-8")
        (lane / "d.md").write_text(
            "---\nkind: opinion-supplement\nstatus: superseded\n---\nD\n", encoding="utf-8")
        health = osup.lane_health(self.vault, "done")
        self.assertEqual(health["lane_depth"], 2)
        self.assertEqual(health["promoted_count"], 1)
        self.assertEqual(health["parked_count"], 1)
        self.assertEqual(health["provenance_coverage"], 1.0)

    def test_lane_health_on_missing_lane_is_zeroed_not_an_error(self) -> None:
        health = osup.lane_health(self.vault, "never-seeded")
        self.assertEqual(health["lane_depth"], 0)
        self.assertEqual(health["base_proposal_count"], 0)

    def test_read_base_proposals_missing_file_returns_empty(self) -> None:
        self.assertEqual(osup.read_base_proposals(self.vault), [])

    def test_lane_health_counts_only_this_opinions_base_proposals(self) -> None:
        meta = self.vault / "_meta"
        meta.mkdir(parents=True)
        (meta / osup.BASE_PROPOSALS_FILENAME).write_text(json.dumps([
            {"opinion": "recoverable", "entry": "x"},
            {"opinion": "recoverable", "entry": "y"},
            {"opinion": "good", "entry": "z"},
        ]), encoding="utf-8")
        health = osup.lane_health(self.vault, "recoverable")
        self.assertEqual(health["base_proposal_count"], 2)
        health_good = osup.lane_health(self.vault, "good")
        self.assertEqual(health_good["base_proposal_count"], 1)


class LaneDirsTests(_SupplementTestBase):
    def test_lane_dirs_lists_only_directories(self) -> None:
        base = self.vault / "personal" / "_opinions"
        (base / "good").mkdir(parents=True)
        (base / "done").mkdir(parents=True)
        base.mkdir(parents=True, exist_ok=True)
        (base / "good.md").write_text("served\n", encoding="utf-8")
        dirs = osup.lane_dirs(self.vault)
        self.assertEqual({p.name for p in dirs}, {"good", "done"})

    def test_lane_dirs_on_missing_opinions_dir_is_empty(self) -> None:
        self.assertEqual(osup.lane_dirs(self.vault), [])


if __name__ == "__main__":
    unittest.main()
