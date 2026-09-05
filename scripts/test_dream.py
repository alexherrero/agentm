#!/usr/bin/env python3
"""Unit tests for `dream.py` — the thin manual `/dream` pass (AG Wave E
dreaming plan, task 2).

`dream.py` lives in `harness/skills/memory/scripts/` (same cross-dir import
pattern as `test_revert_log.py` / `test_memory_write_concurrency.py`).

Covers (plan task 2 verification):
  - a manual run against a seeded fixture corpus (dedup pair + contradiction
    pair + a supersession chain + one untouched control entry) produces a
    digest listing every proposed disposition, each with a revert pointer
  - NO source file is mutated by the run itself — every original entry is
    byte-identical after `run_dream()` returns (proposals are staged data,
    never applied)
  - the derived-insights layer's writes are all `status: candidate`
  - dedup only fires above the similarity threshold; an unrelated entry is
    never proposed
  - contradiction triage is advisory-only (no mutations) — v1 never
    auto-resolves
  - compression never deletes a source file (never-delete-sources)
  - a dispositionless run writes no insight candidate and an explicit
    "None this run" digest
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import dream  # noqa: E402


class _DreamTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def _write(self, name: str, content: str) -> Path:
        path = self.vault / name
        path.write_text(content, encoding="utf-8")
        return path

    def _snapshot(self, paths: list) -> dict:
        return {str(p): p.read_bytes() for p in paths}


class FullPassFixtureTests(_DreamTestBase):
    """The plan's own scenario: a seeded fixture corpus exercising every
    source-touching stage in one run."""

    def setUp(self) -> None:
        super().setUp()
        # Dedup pair — near-identical bodies.
        self.dup_a = self._write(
            "dup-a.md", "---\nslug: dup\nkind: fix\n---\nThe server retries three times on timeout.\n"
        )
        self.dup_b = self._write(
            "dup-b.md", "---\nslug: dup-b\nkind: fix\n---\nThe server retries three times on timeout!\n"
        )
        # Contradiction pair — same slug, differing content.
        self.con_a = self._write(
            "con-a.md", "---\nslug: contradiction\nkind: preference\n---\nUse tabs for indentation.\n"
        )
        self.con_b = self._write(
            "con-b.md", "---\nslug: contradiction\nkind: preference\n---\nUse spaces for indentation.\n"
        )
        # Supersession chain of 3 — c3 <- c2 <- c1 (c1 supersedes c2 supersedes c3).
        self.chain_1 = self._write("chain-1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v3.\n".format(self.vault / "chain-2.md"))
        self.chain_2 = self._write("chain-2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v2.\n".format(self.vault / "chain-3.md"))
        self.chain_3 = self._write("chain-3.md", "---\nkind: fix\n---\nFix v1.\n")
        # Control entry — should trigger nothing.
        self.control = self._write("control.md", "---\nkind: workflow\n---\nCompletely unrelated content about cats.\n")

        self.all_paths = [
            self.dup_a, self.dup_b, self.con_a, self.con_b,
            self.chain_1, self.chain_2, self.chain_3, self.control,
        ]
        self.pre_snapshot = self._snapshot(self.all_paths)

    def test_digest_lists_every_proposed_disposition_with_revert_pointer(self) -> None:
        digest = dream.run_dream(self.vault, run_id="run-fixture")

        stages = {p.stage for p in digest.proposals}
        self.assertIn("dedup", stages)
        self.assertIn("contradiction_triage", stages)
        self.assertIn("compression", stages)

        self.assertTrue(digest.digest_path.exists())
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        for p in digest.proposals:
            if p.mutations:
                self.assertIn("revert pointer", digest_text)
                self.assertIn("run-fixture", digest_text)
                self.assertIn(p.stage, digest_text)

    def test_no_source_file_mutated_until_operator_confirms(self) -> None:
        dream.run_dream(self.vault, run_id="run-fixture-2")
        post_snapshot = self._snapshot(self.all_paths)
        self.assertEqual(post_snapshot, self.pre_snapshot)

    def test_insight_candidate_writes_are_all_status_candidate(self) -> None:
        digest = dream.run_dream(self.vault, run_id="run-fixture-3")
        self.assertTrue(digest.insight_candidates, "fixture has dispositions — expected an insight candidate")
        for c in digest.insight_candidates:
            self.assertTrue(c.path.exists())
            fm, _ = dream._parse_frontmatter(c.path.read_text(encoding="utf-8"))
            self.assertEqual(fm.get("status"), "candidate")
            self.assertEqual(fm.get("kind"), "insight")

    def test_compression_never_deletes_a_source_file(self) -> None:
        dream.run_dream(self.vault, run_id="run-fixture-4")
        # never-delete-sources: every chain member still exists on disk.
        self.assertTrue(self.chain_1.exists())
        self.assertTrue(self.chain_2.exists())
        self.assertTrue(self.chain_3.exists())

    def test_control_entry_never_appears_in_any_proposal(self) -> None:
        digest = dream.run_dream(self.vault, run_id="run-fixture-5")
        touched = {p for prop in digest.proposals for p in prop.paths}
        self.assertNotIn(str(self.control), touched)


class DedupThresholdTests(_DreamTestBase):
    def test_below_threshold_is_not_proposed(self) -> None:
        self._write("a.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog.\n")
        self._write("b.md", "---\nkind: fix\n---\nCompletely different subject matter about spreadsheets.\n")
        digest = dream.run_dream(self.vault, run_id="run-below")
        self.assertEqual([p for p in digest.proposals if p.stage == "dedup"], [])

    def test_above_threshold_is_proposed_as_merge(self) -> None:
        self._write("a.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today.\n")
        self._write("b.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today!\n")
        digest = dream.run_dream(self.vault, run_id="run-above")
        dedup_proposals = [p for p in digest.proposals if p.stage == "dedup"]
        self.assertEqual(len(dedup_proposals), 1)
        self.assertEqual(dedup_proposals[0].kind, "merge")
        self.assertEqual(len(dedup_proposals[0].mutations), 2)


class ContradictionAdvisoryOnlyTests(_DreamTestBase):
    def test_contradiction_proposal_carries_no_mutations(self) -> None:
        self._write("a.md", "---\nslug: x\n---\nOption A.\n")
        self._write("b.md", "---\nslug: x\n---\nOption B.\n")
        digest = dream.run_dream(self.vault, run_id="run-contra")
        contra = [p for p in digest.proposals if p.stage == "contradiction_triage"]
        self.assertEqual(len(contra), 1)
        self.assertEqual(contra[0].kind, "keep_both")
        self.assertEqual(contra[0].mutations, [])

    def test_same_slug_identical_body_is_not_a_contradiction(self) -> None:
        self._write("a.md", "---\nslug: x\n---\nSame content.\n")
        self._write("b.md", "---\nslug: x\n---\nSame content.\n")
        digest = dream.run_dream(self.vault, run_id="run-identical")
        contra = [p for p in digest.proposals if p.stage == "contradiction_triage"]
        self.assertEqual(contra, [])


class OpinionsDirExclusionTests(_DreamTestBase):
    """Accumulate loop, Stages 2-3, locked call 6 (second half): `_opinions/`
    joins `_EXCLUDE_DIRS`. Before this fix the directory sat in the general
    corpus, so a served supplement or a lane entry could be merged, shelved,
    or link-annotated by the wrong stage — changing text the agent reads as
    its own standards."""

    def test_iter_entries_skips_opinions_dir(self) -> None:
        (self.vault / "memory" / "_opinions" / "done").mkdir(parents=True)
        self._write("memory/_opinions/done/lesson.md", "---\nkind: opinion-supplement\n---\nAlways X.\n")
        self._write("memory/_opinions/done.md", "---\nkind: opinion-supplement\n---\nServed.\n")
        self._write("ordinary.md", "---\nkind: workflow\n---\nUnrelated content.\n")
        entries = dream._iter_entries(self.vault)
        rels = {p.relative_to(self.vault) for p in entries}
        self.assertEqual(rels, {Path("ordinary.md")})

    def test_iter_entries_skips_crystallized_lanes_and_served_supplements(self) -> None:
        """Filing-v2 part 3: the lanes fold into memory/crystallized/ and the
        same hazard follows them; a crystallized memory beside them stays in
        the general corpus."""
        (self.vault / "memory" / "crystallized" / "done").mkdir(parents=True)
        self._write("memory/crystallized/done/lesson.md", "---\nkind: opinion-supplement\n---\nAlways X.\n")
        self._write("memory/crystallized/done.md", "---\nkind: opinion-supplement\nstatus: promoted\n---\nServed.\n")
        self._write("memory/crystallized/distilled.md", "---\ntype: workflow\nstatus: active\n---\nA lesson.\n")
        entries = dream._iter_entries(self.vault)
        rels = {p.relative_to(self.vault) for p in entries}
        self.assertEqual(rels, {Path("memory/crystallized/distilled.md")})

    def test_run_dream_never_proposes_a_general_stage_merge_inside_the_opinions_lane(self) -> None:
        (self.vault / "memory" / "_opinions" / "done").mkdir(parents=True)
        # A near-verbatim pair that would trip dedup's own 0.92 threshold if
        # the general corpus still walked this directory. The dedicated
        # opinion_promote stage (Stages 2-3) is EXPECTED to process this
        # lane on its own similarity threshold — what must never happen is
        # a GENERAL-corpus stage (dedup/tidying/link_improvement/lint/
        # suffix_backlog_drain/compression/contradiction_triage) reaching
        # in here, since general dedup's own merge shape would concatenate
        # bodies and write **Related:** lines a served supplement was never
        # meant to carry.
        self._write("memory/_opinions/done/a.md", "---\nkind: opinion-supplement\n---\nAlways run the gates first.\n")
        self._write("memory/_opinions/done/b.md", "---\nkind: opinion-supplement\n---\nAlways run the gates first!\n")
        digest = dream.run_dream(self.vault, run_id="run-opinions-exclusion")
        general_stages = {
            "dedup", "contradiction_triage", "compression", "tidying",
            "link_improvement", "suffix_backlog_drain", "lint",
        }
        offenders = [p for p in digest.proposals if p.stage in general_stages]
        self.assertEqual(offenders, [], "no general-corpus stage may touch _opinions/ content")


class OpinionSupplementStageTests(_DreamTestBase):
    """Accumulate loop, Stages 2-3 — `_stage_opinion_supplement()` joining
    `run_dream()`'s own hand-wired sequence (locked calls 4, 5, 7, 9)."""

    def _write_lane_pair(self, opinion="good"):
        lane = self.vault / "memory" / "_opinions" / opinion
        lane.mkdir(parents=True)
        for slug, session, created in (("a1", "proj/s1", "2026-01-01T00:00:00+00:00"),
                                        ("a2", "proj/s2", "2026-01-02T00:00:00+00:00")):
            self._write(
                f"memory/_opinions/{opinion}/{slug}.md",
                "---\nkind: opinion-supplement\nstatus: proposed\n"
                f"created: {created}\nslug: {slug}\nopinion: {opinion}\n"
                f"sessions: [{session}]\n---\n\n"
                "## Always run the linter before committing\n\n"
                "Run the linter first, always.\n",
            )
        return lane

    def test_two_session_lane_stages_an_opinion_promote_proposal(self) -> None:
        self._write_lane_pair()
        digest = dream.run_dream(self.vault, run_id="run-opinion-stage")
        op_proposals = [p for p in digest.proposals if p.stage == "opinion_promote"]
        self.assertEqual(len(op_proposals), 1)

    def test_run_dream_never_applies_the_opinion_promote_proposal(self) -> None:
        self._write_lane_pair()
        dream.run_dream(self.vault, run_id="run-opinion-propose-only")
        served = self.vault / "memory" / "_opinions" / "good.md"
        self.assertFalse(served.exists(), "run_dream must be propose-only")

    def test_opinion_promote_is_confirm_gated_not_auto_applied(self) -> None:
        import dream_confirm
        self.assertNotIn("opinion_promote", dream_confirm.AUTO_APPLY_STAGES)
        self._write_lane_pair()
        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-opinion-auto-apply",
            log_root=self.vault.parent / "revert-log", lock_root=self.vault.parent / "locks",
        )
        served = self.vault / "memory" / "_opinions" / "good.md"
        self.assertFalse(served.exists(), "opinion_promote must never auto-apply")
        self.assertNotIn("opinion_promote", batch.stages)
        pending = [p for p in digest.proposals if p.stage == "opinion_promote"]
        self.assertEqual(len(pending), 1, "the proposal must survive, still pending")

    def test_confirming_the_proposal_serves_the_supplement(self) -> None:
        import dream_confirm
        from revert_log import RevertLog
        self._write_lane_pair()
        digest = dream.run_dream(self.vault, run_id="run-opinion-confirm")
        idx = next(i for i, p in enumerate(digest.proposals, start=1) if p.stage == "opinion_promote")
        rl = RevertLog(self.vault, log_root=self.vault.parent / "revert-log")
        dream_confirm.confirm(self.vault, digest.run_id, idx, rl)
        served = self.vault / "memory" / "_opinions" / "good.md"
        self.assertTrue(served.is_file())
        self.assertIn("Run the linter first, always.", served.read_text(encoding="utf-8"))

    def test_meta_pointer_files_written_every_cycle(self) -> None:
        self._write_lane_pair()
        dream.run_dream(self.vault, run_id="run-opinion-meta")
        self.assertTrue((dream.engine_state.engine_state_dir() / "opinion-base-proposals.json").is_file())
        self.assertTrue((dream.engine_state.engine_state_dir() / "opinion-supplement-health-latest.json").is_file())

    def test_no_opinions_dir_at_all_proposes_nothing_and_still_writes_pointers(self) -> None:
        # A vault where Stage 1 has never mined a single standard yet.
        self._write("ordinary.md", "---\nkind: workflow\n---\nUnrelated.\n")
        digest = dream.run_dream(self.vault, run_id="run-no-opinions")
        self.assertEqual([p for p in digest.proposals if p.stage == "opinion_promote"], [])
        self.assertTrue((dream.engine_state.engine_state_dir() / "opinion-base-proposals.json").is_file())


class EmptyRunTests(_DreamTestBase):
    def test_no_dispositions_writes_no_insight_and_digest_says_none(self) -> None:
        self._write("solo.md", "---\nkind: workflow\n---\nNothing to dedup, no slug, no chain.\n")
        digest = dream.run_dream(self.vault, run_id="run-empty")
        self.assertEqual(digest.proposals, [])
        self.assertEqual(digest.insight_candidates, [])
        self.assertIn("None this run", digest.digest_path.read_text(encoding="utf-8"))


class CliTests(_DreamTestBase):
    def test_main_smoke_run(self) -> None:
        self._write("a.md", "---\nkind: workflow\n---\nJust one file.\n")
        rc = dream.main(["--vault-path", str(self.vault), "--run-id", "cli-run"])
        self.assertEqual(rc, 0)
        self.assertTrue((dream.engine_state.engine_state_dir() / "dream-runs" / "cli-run" / "digest.md").exists())

    def test_main_no_vault_path_errors(self) -> None:
        import os

        prev = os.environ.pop("MEMORY_VAULT_PATH", None)
        try:
            rc = dream.main([])
        finally:
            if prev is not None:
                os.environ["MEMORY_VAULT_PATH"] = prev
        self.assertEqual(rc, 1)

    def test_main_auto_applies_compression_via_log_root_override(self) -> None:
        """CLI end-to-end: a compression ('expire') proposal auto-applies
        with no confirm call, using --log-root/--lock-root to keep the
        revert log off the real ~/.cache during the test."""
        self._write(
            "chain-1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v3.\n".format(self.vault / "chain-2.md")
        )
        self._write(
            "chain-2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v2.\n".format(self.vault / "chain-3.md")
        )
        self._write("chain-3.md", "---\nkind: fix\n---\nFix v1.\n")

        scratch = Path(self._tmp.name) / "scratch"
        rc = dream.main([
            "--vault-path", str(self.vault), "--run-id", "cli-auto-run",
            "--log-root", str(scratch / "revert-log"),
            "--lock-root", str(scratch / "locks"),
        ])
        self.assertEqual(rc, 0)

        digest_text = (dream.engine_state.engine_state_dir() / "dream-runs" / "cli-auto-run" / "digest.md").read_text(encoding="utf-8")
        self.assertIn("Auto-expired this run", digest_text)
        self.assertIn("AUTO-APPLIED", digest_text)

        auto_expired = json.loads(
            (dream.engine_state.engine_state_dir() / "dream-runs" / "cli-auto-run" / "auto-expired.json").read_text(encoding="utf-8")
        )
        self.assertEqual(auto_expired["count"], 1)
        # "stages" reports the full AUTO_APPLY_STAGES watched set for this
        # call, not just the stages with an item this run -- tidying joined
        # compression in that set (auto-organization part 1, task 3),
        # link_improvement joined both (auto-organization part 2, task 4),
        # suffix_backlog_drain joined all three (auto-organization part 3,
        # task 6), and lint joined all four (task 7, wikilink_repair only).
        self.assertEqual(
            auto_expired["stages"],
            ["compression", "link_improvement", "lint", "suffix_backlog_drain", "tidying"],
        )

        latest = json.loads(
            (dream.engine_state.engine_state_dir() / "dream-auto-expired-latest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(latest, auto_expired)

    def test_main_no_auto_apply_flag_leaves_everything_pending(self) -> None:
        self._write(
            "chain-1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v3.\n".format(self.vault / "chain-2.md")
        )
        self._write(
            "chain-2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v2.\n".format(self.vault / "chain-3.md")
        )
        self._write("chain-3.md", "---\nkind: fix\n---\nFix v1.\n")

        rc = dream.main([
            "--vault-path", str(self.vault), "--run-id", "cli-no-auto-run", "--no-auto-apply",
        ])
        self.assertEqual(rc, 0)
        self.assertFalse((dream.engine_state.engine_state_dir() / "dream-runs" / "cli-no-auto-run" / "auto-expired.json").exists())
        digest_text = (dream.engine_state.engine_state_dir() / "dream-runs" / "cli-no-auto-run" / "digest.md").read_text(encoding="utf-8")
        self.assertNotIn("AUTO-APPLIED", digest_text)
        self.assertIn("staged — NOT applied; operator confirmation required", digest_text)


class RunDreamAndAutoApplyTests(_DreamTestBase):
    """`run_dream_and_auto_apply` -- the additive wrapper around the
    unchanged `run_dream()` that auto-applies the compression ('expire')
    stage per the 2026-07-11 operator ruling. Injects a scratch RevertLog
    so nothing touches the real ~/.cache during tests."""

    def setUp(self) -> None:
        super().setUp()
        from revert_log import RevertLog  # noqa: E402  (sibling script, same import pattern as test_dream_confirm.py)

        self.scratch = Path(self._tmp.name) / "scratch"
        self.revert_log = RevertLog(
            self.vault, log_root=self.scratch / "revert-log", lock_root=self.scratch / "locks"
        )

    def test_compression_auto_applies_dedup_and_contradiction_stay_pending(self) -> None:
        import sys as _sys

        _sys.path.insert(0, str(_SKILL_SCRIPTS))
        import dream_confirm as dc  # noqa: E402

        dup_a = self._write("dup-a.md", "---\nslug: dup\nkind: fix\n---\nThe server retries three times on timeout.\n")
        dup_b = self._write("dup-b.md", "---\nslug: dup-b\nkind: fix\n---\nThe server retries three times on timeout!\n")
        self._write("chain-1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v3.\n".format(self.vault / "chain-2.md"))
        self._write("chain-2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v2.\n".format(self.vault / "chain-3.md"))
        self._write("chain-3.md", "---\nkind: fix\n---\nFix v1.\n")
        pre_dup_a, pre_dup_b = dup_a.read_bytes(), dup_b.read_bytes()

        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-wrapper", revert_log=self.revert_log,
        )

        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.items[0]["stage"], "compression")

        # Dedup ("promote") is completely untouched by the wrapper.
        self.assertEqual(dup_a.read_bytes(), pre_dup_a)
        self.assertEqual(dup_b.read_bytes(), pre_dup_b)
        pending = dc.list_pending(self.vault, "run-wrapper")
        dedup_status = [p.status for p in pending if p.stage == "dedup"][0]
        self.assertEqual(dedup_status, "pending")

    def test_batch_cap_is_threaded_through(self) -> None:
        self._write("a1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix a3.\n".format(self.vault / "a2.md"))
        self._write("a2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix a2.\n".format(self.vault / "a3.md"))
        self._write("a3.md", "---\nkind: fix\n---\nFix a1.\n")
        self._write("b1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix b3.\n".format(self.vault / "b2.md"))
        self._write("b2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix b2.\n".format(self.vault / "b3.md"))
        self._write("b3.md", "---\nkind: fix\n---\nFix b1.\n")

        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-wrapper-cap", revert_log=self.revert_log, batch_cap=1,
        )
        self.assertEqual(len(batch.items), 1)

    def test_zero_dispositions_still_writes_a_current_auto_expired_record(self) -> None:
        self._write("solo.md", "---\nkind: workflow\n---\nNothing to dedup, no slug, no chain.\n")
        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-wrapper-empty", revert_log=self.revert_log,
        )
        self.assertEqual(batch.items, [])
        latest_path = dream.engine_state.engine_state_dir() / "dream-auto-expired-latest.json"
        self.assertTrue(latest_path.exists())
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["run_id"], "run-wrapper-empty")
        self.assertEqual(payload["count"], 0)
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("Auto-expired this run", digest_text)
        self.assertIn("None this run", digest_text)


class _LifecycleRules:
    def thresholds(self):
        return {"dormant_after_days": 365, "archive_after_days": 1825}

    def lifecycles(self):
        return ["pinned", "active", "dormant", "archived", "superseded"]


class LifecycleStageBandTests(_DreamTestBase):
    """Task 3's bands, on the lifecycle axis (filing v2 part 6): dormant
    fixture entries at 4.4y/4.6y/5.1y silence produce, respectively, no
    action / a digest preview line / a staged in-place archive proposal —
    never a move. Calls `_stage_lifecycle` directly with an injected `now`
    for exact, deterministic band boundaries."""
    _NOW = "2026-01-01"

    def _write_aged(self, name: str, days_silent: int, *, lifecycle: str = "dormant", kind: str = "fix") -> Path:
        import datetime
        created = (
            datetime.date.fromisoformat(self._NOW) - datetime.timedelta(days=days_silent)
        ).isoformat()
        (self.vault / "memory" / "semantic").mkdir(parents=True, exist_ok=True)
        return self._write(
            f"memory/semantic/{name}",
            f"---\nkind: {kind}\nslug: {Path(name).stem}\nlifecycle: {lifecycle}\ncreated: {created}\n---\nBody.\n",
        )

    def _run_stage(self):
        return dream._stage_lifecycle(self.vault, now=self._NOW, rules=_LifecycleRules())

    def test_4_4_years_silent_no_action(self) -> None:
        self._write_aged("recent.md", 1607)  # ~4.4y, below the 0.9 × archive line
        proposals, previews = self._run_stage()
        self.assertEqual(proposals, [])
        self.assertEqual(previews, [])

    def test_4_6_years_silent_preview_only(self) -> None:
        self._write_aged("aging.md", 1680)  # ~4.6y, between 0.9 × the line and the line
        proposals, previews = self._run_stage()
        self.assertEqual(proposals, [])
        self.assertEqual(len(previews), 1)
        self.assertIn("aging.md", previews[0])

    def test_5_1_years_silent_stages_an_in_place_archive_proposal(self) -> None:
        path = self._write_aged("cold.md", 1863)  # ~5.1y, past the line
        before = path.read_text(encoding="utf-8")
        proposals, previews = self._run_stage()
        self.assertEqual(previews, [])
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual((p.stage, p.kind, p.paths), ("lifecycle", "archive", ["memory/semantic/cold.md"]))
        # One mutation: the same path, the note with `lifecycle: archived`. No
        # deletion, no second path — a memory never moves for lifecycle.
        (mpath, content), = p.mutations
        self.assertEqual(mpath, path)
        self.assertIn("lifecycle: archived", content)
        self.assertTrue(content.endswith("---\nBody.\n"))
        self.assertEqual(path.read_text(encoding="utf-8"), before, "a proposal applies nothing")
        self.assertEqual(sorted(q.name for q in path.parent.iterdir()), ["cold.md"])

    def test_an_active_note_is_the_policys_before_it_is_this_stages(self) -> None:
        # Silent for 5.1y but still `active`: the automatic lane sinks it to
        # dormant first; only a dormant note is proposed for the archive.
        self._write_aged("still-active.md", 1863, lifecycle="active")
        proposals, previews = self._run_stage()
        self.assertEqual((proposals, previews), ([], []))

    def test_decay_exempt_entry_never_proposed_or_previewed(self) -> None:
        self._write_aged("incident.md", 5000, kind="failure-incident")
        proposals, previews = self._run_stage()
        self.assertEqual(proposals, [])
        self.assertEqual(previews, [])

class ArtifactShelfBandTests(_DreamTestBase):
    """Task 4 verification: a fixture artifact untouched for 370 days
    stages a shelf move; the same artifact "used" (touched) mid-cycle does
    not shelve; a previously-shelved artifact that gets touched is
    confirmed to return on the next cycle's pass. An "artifact" here is
    any entry with no `kind:` frontmatter field at all — the operator's
    2026-07-18 ruling reusing recall.py's existing touch mechanism rather
    than inventing a new one."""

    _NOW = "2026-01-01"

    def _write_artifact(self, name: str, days_untouched: int) -> Path:
        import datetime
        created = (
            datetime.date.fromisoformat(self._NOW) - datetime.timedelta(days=days_untouched)
        ).isoformat()
        # Deliberately NO `kind:` field -- that absence is what makes this
        # an "artifact" rather than a memory.
        return self._write(name, f"---\nslug: {Path(name).stem}\ncreated: {created}\n---\nBody.\n")

    def _run_stage(self):
        entries = dream._iter_entries(self.vault)
        loaded = dream._load(entries)
        return dream._stage_tidying(self.vault, entries, loaded, now=self._NOW)

    def test_kind_tagged_entry_never_enters_the_artifact_lane(self) -> None:
        # A memory (has `kind:`) untouched 370 days should archive-preview
        # or no-op via the memory lane, never shelve, regardless of age.
        self._write(
            "memory.md",
            "---\nkind: fix\nslug: memory\ncreated: 2020-01-01\n---\nBody.\n",
        )
        proposals, _ = self._run_stage()
        kinds = {p.kind for p in proposals}
        self.assertNotIn("shelve", kinds)

    def test_370_days_untouched_stages_a_shelf_move(self) -> None:
        path = self._write_artifact("plan-notes.md", 370)
        proposals, _ = self._run_stage()
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual(p.stage, "tidying")
        self.assertEqual(p.kind, "shelve")
        dest = self.vault / "_shelf" / "plan-notes.md"
        mutated_paths = {str(m[0]) for m in p.mutations}
        self.assertIn(str(path), mutated_paths)
        self.assertIn(str(dest), mutated_paths)

    def test_recently_used_artifact_does_not_shelve(self) -> None:
        self._write_artifact("fresh-notes.md", 10)
        proposals, _ = self._run_stage()
        self.assertEqual(proposals, [])

    def test_364_days_is_not_yet_past_the_threshold(self) -> None:
        self._write_artifact("almost.md", 364)
        proposals, _ = self._run_stage()
        self.assertEqual(proposals, [])

    def test_shelved_artifact_untouched_stays_shelved_no_action(self) -> None:
        (self.vault / "_shelf").mkdir()
        self._write_artifact("_shelf/old-plan.md", 400)
        proposals, _ = self._run_stage()
        self.assertEqual(proposals, [])

    def test_shelved_artifact_touched_since_shelving_proposes_return(self) -> None:
        (self.vault / "_shelf").mkdir()
        path = self._write_artifact("_shelf/came-back.md", 400)

        import lifecycle  # noqa: E402
        fm, _ = dream._parse_frontmatter(path.read_text(encoding="utf-8"))
        # A genuine recall access on the shelved copy, shortly before "now".
        lifecycle.record_recall_access(self.vault, "came-back", fm, "_shelf/came-back.md", today="2025-12-30")

        proposals, _ = self._run_stage()
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual(p.kind, "unshelve")
        dest = self.vault / "came-back.md"
        mutated_paths = {str(m[0]) for m in p.mutations}
        self.assertIn(str(path), mutated_paths)
        self.assertIn(str(dest), mutated_paths)

    def test_personal_and_projects_tier_shelf_insertion(self) -> None:
        self.assertEqual(dream._shelved_path(Path("memory/foo.md")), Path("memory/_shelf/foo.md"))
        self.assertEqual(
            dream._shelved_path(Path("desk/projects/agentm/notes/foo.md")),
            Path("desk/projects/agentm/_shelf/notes/foo.md"),
        )

    def test_unshelved_path_is_the_exact_inverse(self) -> None:
        for original in (Path("memory/foo.md"), Path("desk/projects/agentm/notes/foo.md"), Path("bare.md")):
            shelved = dream._shelved_path(original)
            self.assertEqual(dream._unshelved_path(shelved), original)


class TidyingDigestAndAutoApplyIntegrationTests(_DreamTestBase):
    """The full `run_dream()` / `run_dream_and_auto_apply()` pipeline, using
    REAL relative dates (today - N days) rather than an injected `now` —
    exercises the actual wiring (stage inclusion, digest rendering,
    auto-apply, revert), not just the isolated band function above."""

    def setUp(self) -> None:
        super().setUp()
        from revert_log import RevertLog  # noqa: E402

        self.scratch = Path(self._tmp.name) / "scratch"
        self.revert_log = RevertLog(
            self.vault, log_root=self.scratch / "revert-log", lock_root=self.scratch / "locks"
        )

    def _write_aged(self, name: str, days_silent: int) -> Path:
        # No `kind`: an operational artifact — the population the tidying
        # stage still moves (to `_shelf/`, past a year). A memory never
        # moves for lifecycle since filing v2 part 6.
        import datetime
        created = (datetime.date.today() - datetime.timedelta(days=days_silent)).isoformat()
        return self._write(name, f"---\nslug: {Path(name).stem}\ncreated: {created}\n---\nBody.\n")

    def _write_dormant(self, name: str, days_silent: int) -> Path:
        import datetime
        created = (datetime.date.today() - datetime.timedelta(days=days_silent)).isoformat()
        (self.vault / "memory" / "semantic").mkdir(parents=True, exist_ok=True)
        return self._write(f"memory/semantic/{name}",
                           f"---\nkind: fix\nslug: {Path(name).stem}\nlifecycle: dormant\ncreated: {created}\n---\nBody.\n")

    def _write_aged_artifact(self, name: str, days_silent: int) -> Path:
        # No `kind`: an operational artifact, the one population the shelf
        # lane still moves (a memory never moves for lifecycle — part 6).
        import datetime
        created = (datetime.date.today() - datetime.timedelta(days=days_silent)).isoformat()
        return self._write(name, f"---\nslug: {Path(name).stem}\ncreated: {created}\n---\nBody.\n")

    def test_lifecycle_archive_proposal_appears_in_run_dream_digest(self) -> None:
        self._write_dormant("very-cold.md", 1900)  # well past the archive line, and dormant
        digest = dream.run_dream(self.vault, run_id="run-tidy-1")
        lifecycle = [p for p in digest.proposals if p.stage == "lifecycle"]
        self.assertEqual(len(lifecycle), 1)
        self.assertEqual(lifecycle[0].kind, "archive")
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("lifecycle", digest_text)

    def test_preview_section_renders_in_digest(self) -> None:
        self._write_dormant("getting-old.md", 1680)  # ~4.6y, dormant
        digest = dream.run_dream(self.vault, run_id="run-tidy-2")
        self.assertEqual(len(digest.tidying_previews), 1)
        self.assertIn("getting-old.md", digest.tidying_previews[0])
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("Archive preview", digest_text)
        self.assertIn("getting-old.md", digest_text)

    def test_tidying_auto_applies_no_confirm_required(self) -> None:
        old_path = self._write_aged("ancient.md", 1900)
        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-tidy-3", revert_log=self.revert_log,
        )
        tidying_items = [i for i in batch.items if i["stage"] == "tidying"]
        self.assertEqual(len(tidying_items), 1)

        self.assertFalse(old_path.exists())
        new_path = self.vault / "_shelf" / "ancient.md"
        self.assertTrue(new_path.exists())
        self.assertIn("Body.", new_path.read_text(encoding="utf-8"))

    def test_tidying_move_reverts_cleanly(self) -> None:
        # The shelf lane: an artifact silent past a year moves to `_shelf/`,
        # and the revert log puts it back byte for byte.
        old_path = self._write_aged_artifact("revertme.md", 1900)
        original_content = old_path.read_text(encoding="utf-8")
        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-tidy-4", revert_log=self.revert_log,
        )
        entry_id = batch.items[0]["entry_id"]
        new_path = self.vault / "_shelf" / "revertme.md"
        self.assertTrue(new_path.exists())

        self.revert_log.revert("run-tidy-4", entry_id)

        self.assertTrue(old_path.exists())
        self.assertEqual(old_path.read_text(encoding="utf-8"), original_content)
        self.assertFalse(new_path.exists())


class CrossStageAutoApplyCollisionTests(_DreamTestBase):
    """Adversarial-review regression (auto-organization part 3 task 6): a
    note that's simultaneously tidying-eligible (aged past the 5y archive
    threshold) AND suffix_backlog_drain-eligible (a fingerprint-exact
    duplicate of an older active note) must not get corrupted by both
    stages auto-applying in the same `run_dream_and_auto_apply()` cycle.

    Before the fix in `dream_confirm.auto_apply_batch`: tidying's move
    (delete the old path, write `_archive/<name>.md`) applied first, then
    suffix_backlog_drain's independently-captured, now-stale mutation
    unconditionally rewrote the just-deleted old path back into
    existence — a resurrected ghost file with stale content, while the
    real archived survivor was left un-superseded. The fix tracks paths
    touched earlier in the same batch and skips (leaves pending) any
    later proposal that targets one of them."""

    def setUp(self) -> None:
        super().setUp()
        from revert_log import RevertLog  # noqa: E402

        self.scratch = Path(self._tmp.name) / "scratch"
        self.revert_log = RevertLog(
            self.vault, log_root=self.scratch / "revert-log", lock_root=self.scratch / "locks"
        )

    def _write_aged_active(self, name: str, days_silent: int, body: str) -> Path:
        import datetime
        created = (datetime.date.today() - datetime.timedelta(days=days_silent)).isoformat()
        return self._write(
            name,
            f"---\nkind: fix\nslug: {Path(name).stem}\nstatus: active\ncreated: {created}\n---\n{body}",
        )

    def test_a_move_and_a_rewrite_of_the_same_path_never_collide(self) -> None:
        # The guard itself, on two synthetic proposals: a tidying move of a
        # path (delete the old, write the shelved copy) and a later
        # suffix_backlog_drain rewrite of the same old path. The move wins
        # (lower index); the rewrite is skipped and stays pending; no ghost
        # file is resurrected at the old path. The memory-archive lane that
        # first produced this collision retired with part 6, but the guard
        # protects every stage pair that can target one path in one batch.
        import dream_confirm as dc  # noqa: E402
        old_path = self.vault / "copy.md"
        old_path.write_text("---\nslug: copy\nstatus: active\n---\nDuplicate legacy content.\n", encoding="utf-8")
        raw = old_path.read_text(encoding="utf-8")
        dest = self.vault / "_shelf" / "copy.md"
        proposals = [
            dream.Proposal(stage="tidying", kind="shelve", paths=["copy.md"], summary="move",
                           mutations=[(old_path, None), (dest, raw)]),
            dream.Proposal(stage="suffix_backlog_drain", kind="supersede", paths=["copy.md"], summary="rewrite",
                           mutations=[(old_path, raw.replace("status: active", "status: superseded"))]),
        ]
        digest = dream.DreamDigest(run_id="run-collide", corpus_stats=dream._stage_corpus_stats([]),
                                   proposals=proposals, insight_candidates=[])
        dream._stage_digest_and_staging(self.vault, digest)
        batch = dc.auto_apply_batch(self.vault, "run-collide", self.revert_log, batch_cap=25)
        self.assertEqual([i["stage"] for i in batch.items], ["tidying"])
        self.assertFalse(old_path.exists(), "no ghost resurrected at the old path")
        self.assertTrue(dest.exists())
        self.assertIn("status: active", dest.read_text(encoding="utf-8"))
        pending = dc.list_pending(self.vault, "run-collide")
        skipped = [p for p in pending if p.stage == "suffix_backlog_drain"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].status, "pending")


class TidyingAnomalyBreakerIntegrationTests(_DreamTestBase):
    """Task 6 verification: a fixture cycle with an artificially inflated
    proposal count is confirmed to apply nothing and flag the console,
    rather than applying an abnormal batch — exercised through the real
    `run_dream_and_auto_apply()` pipeline, not just the isolated
    `check_tidying_anomaly` unit above."""

    def setUp(self) -> None:
        super().setUp()
        from revert_log import RevertLog  # noqa: E402
        import dream_confirm  # noqa: E402

        self.dc = dream_confirm
        self.scratch = Path(self._tmp.name) / "scratch"
        self.revert_log = RevertLog(
            self.vault, log_root=self.scratch / "revert-log", lock_root=self.scratch / "locks"
        )

    def _write_aged(self, name: str, days_silent: int) -> Path:
        # No `kind`: an operational artifact — the population the tidying
        # stage still moves (to `_shelf/`, past a year). A memory never
        # moves for lifecycle since filing v2 part 6.
        import datetime
        created = (datetime.date.today() - datetime.timedelta(days=days_silent)).isoformat()
        return self._write(name, f"---\nslug: {Path(name).stem}\ncreated: {created}\n---\nBody.\n")

    def test_inflated_batch_applies_nothing_and_flags_the_digest(self) -> None:
        # Seed a "usual" baseline of small tidying cycles.
        for _ in range(self.dc.ANOMALY_MIN_HISTORY + 2):
            self.dc.check_tidying_anomaly(self.vault, 1)

        # A cycle with a way-past-baseline number of cold entries.
        n = 20
        for i in range(n):
            self._write_aged(f"cold-{i}.md", 1900)

        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-anomaly", revert_log=self.revert_log,
        )
        tidying_in_digest = [p for p in digest.proposals if p.stage == "tidying"]
        self.assertEqual(len(tidying_in_digest), n)

        tidying_applied = [i for i in batch.items if i["stage"] == "tidying"]
        self.assertEqual(tidying_applied, [], "nothing should auto-apply from the tripped stage")

        # Every tidying proposal must still exist as ordinary pending state.
        pending = self.dc.list_pending(self.vault, "run-anomaly")
        tidying_pending = [p for p in pending if p.stage == "tidying"]
        self.assertEqual(len(tidying_pending), n)
        self.assertTrue(all(p.status == "pending" for p in tidying_pending))

        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("ANOMALY BREAKER TRIPPED", digest_text)

        # dream-anomaly-latest.json is now a LIST of tripped stages (task 9
        # generalized the breaker beyond tidying-only) -- exactly one
        # entry here since only tidying tripped this cycle.
        anomaly_flag_path = dream.engine_state.engine_state_dir() / "dream-anomaly-latest.json"
        self.assertTrue(anomaly_flag_path.exists())
        payload = json.loads(anomaly_flag_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["run_id"], "run-anomaly")
        self.assertEqual(payload[0]["stage"], "tidying")
        self.assertEqual(payload[0]["current_count"], n)

    def test_normal_batch_after_seeded_history_applies_as_usual(self) -> None:
        for _ in range(self.dc.ANOMALY_MIN_HISTORY + 2):
            self.dc.check_tidying_anomaly(self.vault, 2)

        self._write_aged("cold-a.md", 1900)
        self._write_aged("cold-b.md", 1900)

        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-normal", revert_log=self.revert_log,
        )
        tidying_applied = [i for i in batch.items if i["stage"] == "tidying"]
        self.assertEqual(len(tidying_applied), 2)

        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertNotIn("ANOMALY BREAKER TRIPPED", digest_text)
        self.assertFalse((dream.engine_state.engine_state_dir() / "dream-anomaly-latest.json").exists())

    def test_compression_still_auto_applies_when_tidying_is_suppressed(self) -> None:
        # Each watched stage's breaker is independent -- compression isn't
        # even watched by the breaker at all, and a tidying-side trip must
        # never affect it either way.
        for _ in range(self.dc.ANOMALY_MIN_HISTORY + 2):
            self.dc.check_tidying_anomaly(self.vault, 1)

        for i in range(10):
            self._write_aged(f"cold-{i}.md", 1900)
        self._write("chain-1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v3.\n".format(self.vault / "chain-2.md"))
        self._write("chain-2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v2.\n".format(self.vault / "chain-3.md"))
        self._write("chain-3.md", "---\nkind: fix\n---\nFix v1.\n")

        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-mixed", revert_log=self.revert_log,
        )
        stages_applied = {i["stage"] for i in batch.items}
        self.assertIn("compression", stages_applied)
        self.assertNotIn("tidying", stages_applied)


class MultiStageAnomalyBreakerIntegrationTests(_DreamTestBase):
    """Task 9 verification: the anomaly breaker generalized beyond tidying
    (part 1) now also watches `suffix_backlog_drain` and `lint` (part 3's
    own dedup/lint mutations) -- each with its OWN independent history, so
    a spike in one watched stage never trips or suppresses another."""

    def setUp(self) -> None:
        super().setUp()
        from revert_log import RevertLog  # noqa: E402
        import dream_confirm  # noqa: E402

        self.dc = dream_confirm
        self.scratch = Path(self._tmp.name) / "scratch"
        self.revert_log = RevertLog(
            self.vault, log_root=self.scratch / "revert-log", lock_root=self.scratch / "locks"
        )

    def _write_suffix_family(self, index: int) -> None:
        base = f"family-{index:02d}"
        body = f"identical legacy content, family {index}\n"
        self._write(
            f"memory/reference/{base}.md",
            f"---\nkind: reference\nslug: {base}\nstatus: active\ncreated: 2025-01-01\n---\n{body}",
        )
        self._write(
            f"memory/reference/{base}_1.md",
            f"---\nkind: reference\nslug: {base}_1\nstatus: active\ncreated: 2025-06-01\n---\n{body}",
        )

    def test_suffix_backlog_drain_spike_trips_independently_of_tidying(self) -> None:
        (self.vault / "memory" / "reference").mkdir(parents=True)
        # Seed a "usual" baseline of 1 suffix-family collapse per cycle for
        # suffix_backlog_drain; tidying gets no history at all (cold start).
        for _ in range(self.dc.ANOMALY_MIN_HISTORY + 2):
            self.dc.check_stage_anomaly(self.vault, "suffix_backlog_drain", 1)

        # 6 families this cycle -- well past baseline(1) * multiplier(3.0).
        for i in range(6):
            self._write_suffix_family(i)

        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-drain-anomaly", revert_log=self.revert_log,
        )

        drain_applied = [i for i in batch.items if i["stage"] == "suffix_backlog_drain"]
        self.assertEqual(drain_applied, [], "nothing should auto-apply from the tripped stage")

        pending = self.dc.list_pending(self.vault, "run-drain-anomaly")
        drain_pending = [p for p in pending if p.stage == "suffix_backlog_drain"]
        self.assertEqual(len(drain_pending), 6)
        self.assertTrue(all(p.status == "pending" for p in drain_pending))

        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("ANOMALY BREAKER TRIPPED — suffix_backlog_drain", digest_text)

        payload = json.loads((dream.engine_state.engine_state_dir() / "dream-anomaly-latest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["stage"], "suffix_backlog_drain")
        self.assertEqual(payload[0]["current_count"], 6)

        # tidying was never watched with any history this cycle -- a cold
        # start never trips, regardless of the suffix_backlog_drain spike.
        tidying_flagged = any(entry["stage"] == "tidying" for entry in payload)
        self.assertFalse(tidying_flagged)


class SuffixBacklogDrainTests(_DreamTestBase):
    """Task 6 verification: a fixture vault with N > 25 suffix families
    confirms exactly 25 collapse in one cycle, the remainder carrying
    over; a second cycle against the same fixture processes the
    remainder and doesn't reprocess already-collapsed families."""

    _N = 30  # > _SUFFIX_BACKLOG_BATCH_CAP (25)

    def setUp(self) -> None:
        super().setUp()
        (self.vault / "memory" / "reference").mkdir(parents=True)
        for i in range(self._N):
            base = f"family-{i:02d}"
            body = f"identical legacy content, family {i}\n"
            self._write(
                f"memory/reference/{base}.md",
                f"---\nkind: reference\nslug: {base}\nstatus: active\n"
                f"created: 2025-01-01\n---\n{body}",
            )
            self._write(
                f"memory/reference/{base}_1.md",
                f"---\nkind: reference\nslug: {base}_1\nstatus: active\n"
                f"created: 2025-06-01\n---\n{body}",
            )

    def _run_cycle(self):
        entries = dream._iter_entries(self.vault)
        loaded = dream._load(entries)
        return dream._stage_suffix_backlog_drain(self.vault, entries, loaded)

    def _apply(self, proposals):
        for p in proposals:
            for path, content in p.mutations:
                path.write_text(content, encoding="utf-8")

    def test_exactly_cap_collapses_remainder_carries_over_no_reprocess(self) -> None:
        proposals_1 = self._run_cycle()
        self.assertEqual(len(proposals_1), dream._SUFFIX_BACKLOG_BATCH_CAP)
        expected_first_batch = {f"memory/reference/family-{i:02d}.md" for i in range(25)}
        self.assertEqual({p.paths[0] for p in proposals_1}, expected_first_batch)
        for p in proposals_1:
            self.assertEqual(p.stage, "suffix_backlog_drain")
            self.assertEqual(p.kind, "collapse")
            self.assertEqual(len(p.mutations), 1)
            copy_path, new_content = p.mutations[0]
            self.assertTrue(copy_path.name.endswith("_1.md"))
            self.assertIn("status: superseded", new_content)
            self.assertIn(f"supersedes: {p.paths[0]}", new_content)

        self._apply(proposals_1)

        # Cycle 2: the remainder (families 25-29) carries over; nothing
        # already collapsed in cycle 1 is reprocessed.
        proposals_2 = self._run_cycle()
        self.assertEqual(len(proposals_2), self._N - dream._SUFFIX_BACKLOG_BATCH_CAP)
        expected_second_batch = {f"memory/reference/family-{i:02d}.md" for i in range(25, self._N)}
        self.assertEqual({p.paths[0] for p in proposals_2}, expected_second_batch)

    def test_survivor_is_earliest_by_created_and_stays_unmutated(self) -> None:
        proposals = self._run_cycle()
        p = next(p for p in proposals if p.paths[0] == "memory/reference/family-00.md")
        mutated = {str(path.relative_to(self.vault)).replace("\\", "/") for path, _c in p.mutations}
        self.assertNotIn("memory/reference/family-00.md", mutated)
        self.assertIn("memory/reference/family-00_1.md", mutated)
        # The canonical's own file on disk is untouched by the proposal
        # (mutations only ever target copies).
        original = (self.vault / "memory/reference/family-00.md").read_text(encoding="utf-8")
        self.assertIn("status: active", original)

    def test_always_load_notes_excluded_from_collapse(self) -> None:
        (self.vault / "_always-load").mkdir(parents=True)
        self._write(
            "_always-load/pinned.md",
            "---\nkind: convention\nslug: pinned\nstatus: active\n"
            "created: 2020-01-01\n---\nidentical legacy content, family 0\n",
        )
        proposals = self._run_cycle()
        touched = {p for prop in proposals for p in prop.paths}
        self.assertNotIn("_always-load/pinned.md", touched)
        family_0 = next(p for p in proposals if p.paths[0] == "memory/reference/family-00.md")
        self.assertEqual(len(family_0.mutations), 1)  # still just the one _1 copy


class ConnectivityMeterTests(_DreamTestBase):
    """Task 7 verification: the two counts are computed independently, and
    a note with only a generated (Related-line) link doesn't count toward
    organic connectivity. Pure content parsing -- no sqlite-vec, never
    skips."""

    def _load(self):
        entries = dream._iter_entries(self.vault)
        return dream._load(entries)

    def test_generated_only_note_does_not_count_as_organic(self) -> None:
        # A: organic link in prose. B: ONLY a generated Related-line link.
        # C: no links at all.
        self._write("a.md", "---\nslug: a\n---\nsee [[b]] for the details\n")
        self._write("b.md", "---\nslug: b\n---\nbody\n\n**Related:** [[a]]\n")
        self._write("c.md", "---\nslug: c\n---\nno links here\n")

        meter = dream._connectivity_meter(self._load())

        self.assertEqual(meter["organically_linked_count"], 1)  # only A
        self.assertAlmostEqual(meter["organic_connectivity"], 1 / 3)
        self.assertEqual(meter["generated_link_count"], 1)  # B's Related link

    def test_counts_are_independent_organic_note_with_generated_links_too(self) -> None:
        # A note with BOTH an organic prose link and a generated Related
        # line counts organic once AND contributes its Related links to
        # the generated count -- the two numbers move independently.
        self._write(
            "both.md",
            "---\nslug: both\n---\nbuilds on [[other]]\n\n**Related:** [[x]], [[y]]\n",
        )
        meter = dream._connectivity_meter(self._load())
        self.assertEqual(meter["organically_linked_count"], 1)
        self.assertEqual(meter["generated_link_count"], 2)

    def test_fenced_wikilink_counts_toward_neither(self) -> None:
        self._write(
            "fenced.md",
            "---\nslug: fenced\n---\nexample:\n\n```\nsee [[example-link]]\n**Related:** [[fenced-example]]\n```\n",
        )
        meter = dream._connectivity_meter(self._load())
        self.assertEqual(meter["organically_linked_count"], 0)
        self.assertEqual(meter["generated_link_count"], 0)

    def test_supersedes_frontmatter_counts_as_organic(self) -> None:
        self._write("super.md", "---\nslug: super\nsupersedes: old-note\n---\nbody\n")
        meter = dream._connectivity_meter(self._load())
        self.assertEqual(meter["organically_linked_count"], 1)

    def test_empty_corpus_reports_zero_not_crash(self) -> None:
        meter = dream._connectivity_meter({})
        self.assertEqual(meter["organic_connectivity"], 0.0)
        self.assertEqual(meter["generated_link_count"], 0)

    def test_both_numbers_land_in_the_digest(self) -> None:
        self._write("a.md", "---\nslug: a\n---\nsee [[b]]\n")
        self._write("b.md", "---\nslug: b\n---\nbody\n\n**Related:** [[a]]\n")
        digest = dream.run_dream(self.vault, run_id="run-meter-1")
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("Connectivity:", digest_text)
        self.assertIn("organic", digest_text)
        self.assertIn("1 generated link(s) (counted separately)", digest_text)
        self.assertEqual(digest.corpus_stats["organically_linked_count"], 1)
        self.assertEqual(digest.corpus_stats["generated_link_count"], 1)


class BrowseSurfaceCountsTests(_DreamTestBase):
    """Task 8 verification: a fixture cycle exercising all three states
    (a live note, a shelved artifact, an archived memory) produces
    correct counts in both the digest and `corpus_stats` (the dashboard's
    data source). The acceptance test this meter encodes: browsing shows
    live notes, and archived material is still readable on request."""

    def _write(self, name: str, content: str):
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_three_states_counted_correctly(self) -> None:
        self._write("memory/reference/live.md", "---\nslug: live\n---\nbody\n")
        self._write("memory/reference/_shelf/shelved.md", "---\nslug: shelved\n---\nbody\n")
        self._write("memory/reference/_archive/archived.md", "---\nslug: archived\n---\nbody\n")

        entries = dream._iter_entries(self.vault)
        counts = dream._browse_surface_counts(self.vault, entries)

        self.assertEqual(counts["browse_live_count"], 1)
        self.assertEqual(counts["browse_shelved_count"], 1)
        self.assertEqual(counts["browse_archived_count"], 1)

    def test_archived_note_content_still_readable(self) -> None:
        # The acceptance test in the operator's own words: aged material
        # sits in the archive, still there on request -- never deleted.
        archived_path = self._write(
            "memory/reference/_archive/archived.md", "---\nslug: archived\n---\noriginal content\n"
        )
        entries = dream._iter_entries(self.vault)
        dream._browse_surface_counts(self.vault, entries)  # never mutates anything
        self.assertTrue(archived_path.is_file())
        self.assertIn("original content", archived_path.read_text(encoding="utf-8"))

    def test_empty_vault_reports_all_zero(self) -> None:
        entries = dream._iter_entries(self.vault)
        counts = dream._browse_surface_counts(self.vault, entries)
        self.assertEqual(counts["browse_live_count"], 0)
        self.assertEqual(counts["browse_shelved_count"], 0)
        self.assertEqual(counts["browse_archived_count"], 0)

    def test_counts_land_in_the_digest(self) -> None:
        self._write("memory/reference/live.md", "---\nslug: live\n---\nbody\n")
        self._write("memory/reference/_shelf/shelved.md", "---\nslug: shelved\n---\nbody\n")
        self._write("memory/reference/_archive/archived.md", "---\nslug: archived\n---\nbody\n")

        digest = dream.run_dream(self.vault, run_id="run-browse-1")
        digest_text = digest.digest_path.read_text(encoding="utf-8")

        self.assertIn("Browse surface:", digest_text)
        self.assertEqual(digest.corpus_stats["browse_live_count"], 1)
        self.assertEqual(digest.corpus_stats["browse_shelved_count"], 1)
        self.assertEqual(digest.corpus_stats["browse_archived_count"], 1)


if __name__ == "__main__":
    unittest.main()
