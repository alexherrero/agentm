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
        self.assertTrue((self.vault / "_dream-staging" / "cli-run" / "digest.md").exists())

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

        digest_text = (self.vault / "_dream-staging" / "cli-auto-run" / "digest.md").read_text(encoding="utf-8")
        self.assertIn("Auto-expired this run", digest_text)
        self.assertIn("AUTO-APPLIED", digest_text)

        auto_expired = json.loads(
            (self.vault / "_dream-staging" / "cli-auto-run" / "auto-expired.json").read_text(encoding="utf-8")
        )
        self.assertEqual(auto_expired["count"], 1)
        # "stages" reports the full AUTO_APPLY_STAGES watched set for this
        # call, not just the stages with an item this run -- tidying joined
        # compression in that set (auto-organization part 1, task 3), and
        # link_improvement joined both (auto-organization part 2, task 4).
        self.assertEqual(auto_expired["stages"], ["compression", "link_improvement", "tidying"])

        latest = json.loads(
            (self.vault / "_meta" / "dream-auto-expired-latest.json").read_text(encoding="utf-8")
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
        self.assertFalse((self.vault / "_dream-staging" / "cli-no-auto-run" / "auto-expired.json").exists())
        digest_text = (self.vault / "_dream-staging" / "cli-no-auto-run" / "digest.md").read_text(encoding="utf-8")
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
        latest_path = self.vault / "_meta" / "dream-auto-expired-latest.json"
        self.assertTrue(latest_path.exists())
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["run_id"], "run-wrapper-empty")
        self.assertEqual(payload["count"], 0)
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("Auto-expired this run", digest_text)
        self.assertIn("None this run", digest_text)


class ArchivedPathTests(unittest.TestCase):
    """Task 3's pure path-transform helper — no vault, no I/O."""

    def test_personal_tier_inserts_archive_after_personal(self) -> None:
        got = dream._archived_path(Path("personal/preferences/foo.md"))
        self.assertEqual(got, Path("personal/_archive/preferences/foo.md"))

    def test_projects_tier_inserts_archive_after_the_project_segment(self) -> None:
        got = dream._archived_path(Path("projects/agentm/idea/foo.md"))
        self.assertEqual(got, Path("projects/agentm/_archive/idea/foo.md"))

    def test_bare_root_file_archives_at_the_root_not_after_the_filename(self) -> None:
        # A naive "insert after the first segment" rule would produce the
        # nonsensical foo.md/_archive — must prepend instead.
        got = dream._archived_path(Path("foo.md"))
        self.assertEqual(got, Path("_archive/foo.md"))

    def test_personal_tier_with_no_kind_subfolder(self) -> None:
        got = dream._archived_path(Path("personal/foo.md"))
        self.assertEqual(got, Path("personal/_archive/foo.md"))


class TidyingStageBandTests(_DreamTestBase):
    """Task 3 verification: fixture entries at 4.4y/4.6y/5.1y silence
    produce, respectively, no action / a digest preview line / an actual
    staged archive move. Calls `_stage_tidying` directly with an injected
    `now` for exact, deterministic band boundaries."""

    _NOW = "2026-01-01"

    def _write_aged(self, name: str, days_silent: int, *, extra_fm: str = "") -> Path:
        import datetime
        created = (
            datetime.date.fromisoformat(self._NOW) - datetime.timedelta(days=days_silent)
        ).isoformat()
        return self._write(
            name, f"---\nkind: fix\nslug: {Path(name).stem}\ncreated: {created}\n{extra_fm}---\nBody.\n"
        )

    def _run_stage(self):
        entries = dream._iter_entries(self.vault)
        loaded = dream._load(entries)
        return dream._stage_tidying(self.vault, entries, loaded, now=self._NOW)

    def test_4_4_years_silent_no_action(self) -> None:
        self._write_aged("recent.md", 1607)  # ~4.4y, below the 4.5y preview line
        proposals, previews = self._run_stage()
        self.assertEqual(proposals, [])
        self.assertEqual(previews, [])

    def test_4_6_years_silent_preview_only(self) -> None:
        self._write_aged("aging.md", 1680)  # ~4.6y, between 4.5y and 5y
        proposals, previews = self._run_stage()
        self.assertEqual(proposals, [])
        self.assertEqual(len(previews), 1)
        self.assertIn("aging.md", previews[0])

    def test_5_1_years_silent_stages_an_archive_move(self) -> None:
        path = self._write_aged("cold.md", 1863)  # ~5.1y, past the 5y threshold
        proposals, previews = self._run_stage()
        self.assertEqual(previews, [])
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual(p.stage, "tidying")
        self.assertEqual(p.kind, "archive")
        self.assertEqual(p.paths, ["cold.md"])
        # mutations: delete the old path, write the archived copy.
        mutated_paths = {str(m[0]) for m in p.mutations}
        self.assertIn(str(path), mutated_paths)
        dest = self.vault / "_archive" / "cold.md"
        self.assertIn(str(dest), mutated_paths)
        old_mutation = next(m for m in p.mutations if m[0] == path)
        self.assertIsNone(old_mutation[1])
        new_mutation = next(m for m in p.mutations if m[0] == dest)
        self.assertEqual(new_mutation[1], path.read_text(encoding="utf-8"))

    def test_decay_exempt_entry_never_archived_or_previewed(self) -> None:
        self._write_aged("incident.md", 5000, extra_fm="kind: failure-incident\n")
        proposals, previews = self._run_stage()
        self.assertEqual(proposals, [])
        self.assertEqual(previews, [])

    def test_decisions_path_exempt_entry_never_archived(self) -> None:
        (self.vault / "projects" / "agentm" / "decisions").mkdir(parents=True)
        self._write_aged("projects/agentm/decisions/old-call.md", 5000)
        proposals, previews = self._run_stage()
        self.assertEqual(proposals, [])
        self.assertEqual(previews, [])

    def test_explicit_durable_tag_never_archived(self) -> None:
        self._write_aged("pinned.md", 5000, extra_fm="lifecycle_tier: durable\n")
        proposals, previews = self._run_stage()
        self.assertEqual(proposals, [])
        self.assertEqual(previews, [])

    def test_no_anchor_at_all_never_archived(self) -> None:
        self._write("no-dates.md", "---\nkind: fix\n---\nBody.\n")
        proposals, previews = self._run_stage()
        self.assertEqual(proposals, [])
        self.assertEqual(previews, [])

    def test_genuine_recall_between_cycles_resets_a_previously_aging_entry(self) -> None:
        # Red-test (task 3 verification, bullet 2): a recall access must
        # reset the clock, tested against the real sidecar (.lifecycle.json).
        path = self._write_aged("was-cold.md", 1863)  # would stage an archive move…
        proposals_before, _ = self._run_stage()
        self.assertEqual(len(proposals_before), 1)

        import lifecycle  # noqa: E402
        fm, _ = dream._parse_frontmatter(path.read_text(encoding="utf-8"))
        lifecycle.record_recall_access(self.vault, "was-cold", fm, "was-cold.md", today=self._NOW)

        # …but a genuine recall between cycles resets it to fully fresh.
        proposals_after, previews_after = self._run_stage()
        self.assertEqual(proposals_after, [])
        self.assertEqual(previews_after, [])


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
        self.assertEqual(dream._shelved_path(Path("personal/foo.md")), Path("personal/_shelf/foo.md"))
        self.assertEqual(
            dream._shelved_path(Path("projects/agentm/notes/foo.md")),
            Path("projects/agentm/_shelf/notes/foo.md"),
        )

    def test_unshelved_path_is_the_exact_inverse(self) -> None:
        for original in (Path("personal/foo.md"), Path("projects/agentm/notes/foo.md"), Path("bare.md")):
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
        import datetime
        created = (datetime.date.today() - datetime.timedelta(days=days_silent)).isoformat()
        return self._write(name, f"---\nkind: fix\nslug: {Path(name).stem}\ncreated: {created}\n---\nBody.\n")

    def test_tidying_proposal_appears_in_run_dream_digest(self) -> None:
        self._write_aged("very-cold.md", 1900)  # well past 5y
        digest = dream.run_dream(self.vault, run_id="run-tidy-1")
        tidying = [p for p in digest.proposals if p.stage == "tidying"]
        self.assertEqual(len(tidying), 1)
        digest_text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("tidying", digest_text)

    def test_preview_section_renders_in_digest(self) -> None:
        self._write_aged("getting-old.md", 1680)  # ~4.6y
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
        new_path = self.vault / "_archive" / "ancient.md"
        self.assertTrue(new_path.exists())
        self.assertIn("Body.", new_path.read_text(encoding="utf-8"))

    def test_tidying_move_reverts_cleanly(self) -> None:
        old_path = self._write_aged("revertme.md", 1900)
        original_content = old_path.read_text(encoding="utf-8")
        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="run-tidy-4", revert_log=self.revert_log,
        )
        entry_id = batch.items[0]["entry_id"]
        new_path = self.vault / "_archive" / "revertme.md"
        self.assertTrue(new_path.exists())

        self.revert_log.revert("run-tidy-4", entry_id)

        self.assertTrue(old_path.exists())
        self.assertEqual(old_path.read_text(encoding="utf-8"), original_content)
        self.assertFalse(new_path.exists())


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
        import datetime
        created = (datetime.date.today() - datetime.timedelta(days=days_silent)).isoformat()
        return self._write(name, f"---\nkind: fix\nslug: {Path(name).stem}\ncreated: {created}\n---\nBody.\n")

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

        anomaly_flag_path = self.vault / "_meta" / "dream-anomaly-latest.json"
        self.assertTrue(anomaly_flag_path.exists())
        payload = json.loads(anomaly_flag_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["run_id"], "run-anomaly")
        self.assertEqual(payload["stage"], "tidying")
        self.assertEqual(payload["current_count"], n)

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
        self.assertFalse((self.vault / "_meta" / "dream-anomaly-latest.json").exists())

    def test_compression_still_auto_applies_when_tidying_is_suppressed(self) -> None:
        # The breaker is scoped to tidying only -- compression's own
        # auto-apply must be unaffected by a tidying-side trip.
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


def _vec_backend_available(vault: Path) -> bool:
    import vec_index
    conn = vec_index._open_index(vault)
    if conn is None:
        return False
    conn.close()
    return True


def _unit_vector(hot_index: int, sign: float = 1.0) -> list:
    import vec_index
    v = [0.0] * vec_index.EMBEDDING_DIM
    v[hot_index] = sign
    return v


class LinkImprovementStageTests(_DreamTestBase):
    """Task 4 verification: a clear-match pair gets both-directions links,
    capped, revert-logged; an ambiguous-match pair (between the base floor
    and the confident threshold) is left unlinked -- the cheap-model band
    isn't built (dream.cheap_model_tier_available() always returns False),
    which IS the design's own "budget exhausted / tier unavailable"
    fallback, just unconditional. A call-count check on that seam proves no
    model call is ever attempted for the ambiguous case."""

    _NOW = "2026-01-01"

    def setUp(self) -> None:
        super().setUp()
        import vec_index
        self.vec_index = vec_index
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        (self.vault / "personal" / "reference").mkdir(parents=True)

    def _rel(self, name: str) -> str:
        # graph_snapshot._walk_vault_paths only walks personal/, projects/,
        # _idea-incubator/ (the real vault's own layout) -- a fixture at
        # the bare vault root is invisible to it, so every fixture here
        # lives under personal/reference/.
        return f"personal/reference/{name}"

    def _write_entry(self, name: str, slug: str, body: str = "body") -> Path:
        return self._write(
            self._rel(name), f"---\nkind: reference\nslug: {slug}\ncreated: {self._NOW}\n---\n{body}\n"
        )

    def _seed_older_entry(self, name: str, slug: str, embedding: list) -> Path:
        """Writes + indexes an entry as already-known-as-of-last-cycle:
        advances the link-sweep cursor (dream.py's OWN "changed since last
        cycle" tracker — deliberately independent of graph_snapshot's
        internal mtime store, see dream._read_link_sweep_cursor's
        docstring) to just past this file's own mtime, mirroring a note
        that arrived in a PRIOR sweep and was already processed then.

        Uses the file's OWN recorded mtime (+ a small buffer) rather than
        a fresh `time.time()` call, so this is immune to filesystem mtime
        granularity (HFS+ truncates to 1s; a `time.time()` call and the
        immediately-following write could otherwise land in the same
        truncated second, or a LATER write in the same test could too)."""
        path = self._write_entry(name, slug)
        self.vec_index.upsert_entry(self.vault, self._rel(name), embedding)
        dream._write_link_sweep_cursor(self.vault, path.stat().st_mtime + 1.0)
        return path

    def _run_stage_with_new_entry(self, name: str, slug: str, *, embedding: list):
        """Writes the "just arrived" origin note (with an explicit mtime
        safely after the cursor -- see `_seed_older_entry`'s own note on
        filesystem mtime granularity), mocks embed.embed_text to return
        `embedding` for it, and runs the stage."""
        import os
        path = self._write_entry(name, slug)
        cursor = dream._read_link_sweep_cursor(self.vault)
        newer = max(cursor + 2.0, path.stat().st_mtime)
        os.utime(path, (newer, newer))
        entries = dream._iter_entries(self.vault)
        loaded = dream._load(entries)
        import embed
        with unittest.mock.patch.object(embed, "embed_text", return_value=embedding):
            return dream._stage_link_improvement(self.vault, entries, loaded, now=self._NOW)

    def test_clear_match_pair_gets_both_direction_links(self) -> None:
        self._seed_older_entry("old-note.md", "old-note", _unit_vector(0, 0.99))
        proposals = self._run_stage_with_new_entry(
            "new-note.md", "new-note", embedding=_unit_vector(0)
        )
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual(p.stage, "link_improvement")
        self.assertEqual(p.kind, "link")
        mutated = {str(path): content for path, content in p.mutations}
        new_path = str(self.vault / self._rel("new-note.md"))
        old_path = str(self.vault / self._rel("old-note.md"))
        self.assertIn(new_path, mutated)
        self.assertIn(old_path, mutated)
        self.assertIn("[[old-note]]", mutated[new_path])
        self.assertIn("[[new-note]]", mutated[old_path])

    def test_ambiguous_match_pair_left_unlinked_no_model_call(self) -> None:
        # A vector whose similarity to the origin sits in the band between
        # LINK_SIMILARITY_FLOOR (0.70) and CONFIDENT_SIMILARITY_THRESHOLD
        # (0.85) -- ambiguous, not clear.
        import write_time_linker
        import math
        # vec_index.nearest's "similarity" is 1 - L2_distance/2, NOT raw
        # cosine similarity -- for two unit vectors, L2_dist = sqrt(2*(1 -
        # cos(theta))), so a target similarity of 0.77 (midpoint of the
        # [0.70, 0.85) ambiguous band) needs cos(theta) = 1 - 2*(1-0.77)^2
        # ~= 0.894, not 0.77 itself.
        target_similarity = 0.77
        cos_theta = 1 - 2 * (1 - target_similarity) ** 2
        angle = math.acos(cos_theta)
        ambiguous_vec = [0.0] * self.vec_index.EMBEDDING_DIM
        ambiguous_vec[0] = math.cos(angle)
        ambiguous_vec[1] = math.sin(angle)
        self._seed_older_entry("mid-note.md", "mid-note", ambiguous_vec)

        with unittest.mock.patch("dream.cheap_model_tier_available", return_value=False) as spy:
            proposals = self._run_stage_with_new_entry(
                "new-note.md", "new-note", embedding=_unit_vector(0)
            )
        self.assertEqual(proposals, [])
        spy.assert_called()  # the seam was checked, not silently skipped
        # And, separately: the similarity really did land in the ambiguous
        # band, not below the floor entirely (belt-and-suspenders on the
        # fixture's own math).
        sim = self.vec_index.nearest(
            self.vault, _unit_vector(0), k=2, similarity_floor=0.0
        )
        mid_sim = next(s for path, s in sim if path == self._rel("mid-note.md"))
        self.assertGreaterEqual(mid_sim, write_time_linker.LINK_SIMILARITY_FLOOR)
        self.assertLess(mid_sim, write_time_linker.CONFIDENT_SIMILARITY_THRESHOLD)

    def test_below_floor_pair_not_a_candidate_at_all(self) -> None:
        self._seed_older_entry("unrelated.md", "unrelated", _unit_vector(1))  # orthogonal
        with unittest.mock.patch("dream.cheap_model_tier_available") as spy:
            proposals = self._run_stage_with_new_entry(
                "new-note.md", "new-note", embedding=_unit_vector(0)
            )
        self.assertEqual(proposals, [])
        spy.assert_not_called()  # never even reaches the ambiguous-band check

    def test_decay_exempt_entry_never_becomes_an_origin(self) -> None:
        self._write(
            self._rel("incident.md"),
            f"---\nkind: failure-incident\nslug: incident\ncreated: {self._NOW}\n---\nbody\n",
        )
        entries = dream._iter_entries(self.vault)
        loaded = dream._load(entries)
        import embed
        with unittest.mock.patch.object(embed, "embed_text") as spy:
            proposals = dream._stage_link_improvement(self.vault, entries, loaded, now=self._NOW)
        self.assertEqual(proposals, [])
        spy.assert_not_called()

    def test_already_linked_both_ways_produces_no_proposal(self) -> None:
        self._seed_older_entry("old-note.md", "old-note", _unit_vector(0, 0.99))
        # Run once -- links both ways.
        first = self._run_stage_with_new_entry("new-note.md", "new-note", embedding=_unit_vector(0))
        self.assertEqual(len(first), 1)

        # A second sweep over the same (now already-linked) pair proposes nothing.
        entries = dream._iter_entries(self.vault)
        loaded = dream._load(entries)
        import graph_snapshot
        graph_snapshot.rebuild(self.vault)  # settle -- nothing "changed" anymore
        import embed
        with unittest.mock.patch.object(embed, "embed_text", return_value=_unit_vector(0)):
            second = dream._stage_link_improvement(self.vault, entries, loaded, now=self._NOW)
        self.assertEqual(second, [])

    def test_a_write_time_linked_note_still_counts_as_changed_for_the_sweep(self) -> None:
        # Review-caught defect: write_time_linker.apply() (task 3) calls
        # graph_snapshot.rebuild(vault, paths=[rel_path]) after every link
        # it applies -- which, under the OLD (buggy) implementation that
        # read graph_snapshot.RebuildStats.touched_paths as the "changed
        # since last cycle" signal, silently "consumed" that note's mtime
        # before the weekly sweep ever ran. The fix uses dream.py's OWN
        # independent cursor -- this proves a note that already got an
        # ordinary write-time link (the common case) is STILL visible to
        # the weekly sweep as a valid origin.
        import graph_snapshot
        self._seed_older_entry("old-note.md", "old-note", _unit_vector(0, 0.99))

        new_path = self._write_entry("new-note.md", "new-note")
        # Simulate write_time_linker.apply() already having run on this
        # note (a real "Related" line, plus the exact snapshot nudge call
        # apply() itself makes) BEFORE the weekly sweep ever sees it.
        new_path.write_text(
            new_path.read_text(encoding="utf-8").rstrip("\n") + "\n\n**Related:** [[unrelated-old]]\n",
            encoding="utf-8",
        )
        # Explicit mtime, safely after the cursor _seed_older_entry set --
        # a bare write here could otherwise land within the same
        # filesystem-mtime-granularity window as the cursor (see
        # _seed_older_entry's own note on this).
        import os
        cursor = dream._read_link_sweep_cursor(self.vault)
        newer = cursor + 2.0
        os.utime(new_path, (newer, newer))
        graph_snapshot.rebuild(self.vault, paths=[self._rel("new-note.md")])

        entries = dream._iter_entries(self.vault)
        loaded = dream._load(entries)
        import embed
        with unittest.mock.patch.object(embed, "embed_text", return_value=_unit_vector(0)):
            proposals = dream._stage_link_improvement(self.vault, entries, loaded, now=self._NOW)

        self.assertEqual(len(proposals), 1)  # new-note.md was still visible as a changed origin
        mutated = {str(path) for path, _content in proposals[0].mutations}
        self.assertIn(str(self.vault / self._rel("new-note.md")), mutated)

    def test_asymmetric_similarity_reverse_direction_still_gets_evaluated(self) -> None:
        # Review-caught defect (the exact original scenario): TWO notes, A
        # and B, both "changed" this cycle -- both get processed as
        # origins in the SAME sweep call. A's turn comes first
        # (alphabetical _iter_entries order); A's freshly-recomputed query
        # vector lands only AMBIGUOUS relative to B's stored vector, so A
        # produces no proposal (and, critically, doesn't touch B). B's
        # turn comes next; B's OWN freshly-recomputed query vector lands
        # CONFIDENT relative to A's stored vector -- a genuinely different
        # result than A's own query got, because each note's query vector
        # is independently, freshly recomputed (not symmetric by
        # construction). The old `seen_pairs` tracking would have marked
        # this pair permanently ineligible the moment A's ambiguous check
        # ran, silently dropping B's later, confident match.
        import math

        def cos_theta_for_similarity(sim: float) -> float:
            return 1 - 2 * (1 - sim) ** 2

        # A's STORED vector (what B's query will be compared against) --
        # arbitrary; what matters is A's own QUERY vector below.
        a_stored = _unit_vector(0)
        # B's STORED vector sits AMBIGUOUS relative to A's own query vector
        # (both are _unit_vector(0) -- identical -- so instead make A's
        # own query vector explicitly ambiguous toward B's stored vector).
        ambiguous_angle = math.acos(cos_theta_for_similarity(0.77))
        b_stored = [0.0] * self.vec_index.EMBEDDING_DIM
        b_stored[0] = math.cos(ambiguous_angle)
        b_stored[1] = math.sin(ambiguous_angle)

        a_path = self._write_entry("note-a.md", "note-a")
        b_path = self._write_entry("note-b.md", "note-b")
        self.vec_index.upsert_entry(self.vault, self._rel("note-a.md"), a_stored)
        self.vec_index.upsert_entry(self.vault, self._rel("note-b.md"), b_stored)
        cursor = dream._read_link_sweep_cursor(self.vault)
        import os
        newer = cursor + 2.0
        os.utime(a_path, (newer, newer))
        os.utime(b_path, (newer, newer))

        entries = dream._iter_entries(self.vault)
        loaded = dream._load(entries)

        # A's own fresh query vector: ambiguous toward B's stored vector
        # (same angle as above, from A's "side"). B's own fresh query
        # vector: confident toward A's stored vector (identical vectors).
        confident_angle = math.acos(cos_theta_for_similarity(0.95))
        a_query_vector = [0.0] * self.vec_index.EMBEDDING_DIM
        a_query_vector[0] = math.cos(ambiguous_angle)
        a_query_vector[1] = math.sin(ambiguous_angle)
        b_query_vector = [0.0] * self.vec_index.EMBEDDING_DIM
        b_query_vector[0] = math.cos(confident_angle)

        def fake_embed_text(text, mode=None):
            if text.startswith("note-a "):
                return a_query_vector
            if text.startswith("note-b "):
                return b_query_vector
            raise AssertionError(f"unexpected embed_text call: {text[:40]!r}")

        import embed
        with unittest.mock.patch.object(embed, "embed_text", side_effect=fake_embed_text):
            proposals = dream._stage_link_improvement(self.vault, entries, loaded, now=self._NOW)

        # A's own (ambiguous) query produces nothing; B's later, confident
        # query must still produce a proposal linking A and B -- the exact
        # case the old seen_pairs tracking would have silently dropped.
        self.assertEqual(len(proposals), 1)
        self.assertIn(self._rel("note-a.md"), proposals[0].paths)
        self.assertIn(self._rel("note-b.md"), proposals[0].paths)


class LinkImprovementIntegrationTests(_DreamTestBase):
    """The full run_dream_and_auto_apply()/revert pipeline for
    link_improvement -- auto-applies without a confirm() call (joined
    AUTO_APPLY_STAGES per the auto-org design's own already-approved "every
    link move auto-applies" ruling), and reverts cleanly."""

    def setUp(self) -> None:
        super().setUp()
        import vec_index
        self.vec_index = vec_index
        if not _vec_backend_available(self.vault):
            self.skipTest("sqlite-vec backend unavailable on this Python")
        (self.vault / "personal" / "reference").mkdir(parents=True)
        from revert_log import RevertLog  # noqa: E402
        self.scratch = Path(self._tmp.name) / "scratch"
        self.revert_log = RevertLog(
            self.vault, log_root=self.scratch / "revert-log", lock_root=self.scratch / "locks"
        )

    def _rel(self, name: str) -> str:
        return f"personal/reference/{name}"

    def _write_entry(self, name: str, slug: str) -> Path:
        return self._write(
            self._rel(name), f"---\nkind: reference\nslug: {slug}\ncreated: 2026-01-01\n---\nbody\n"
        )

    def test_link_improvement_auto_applies_and_reverts(self) -> None:
        import graph_snapshot
        old_path = self._write_entry("old-note.md", "old-note")
        self.vec_index.upsert_entry(self.vault, self._rel("old-note.md"), _unit_vector(0, 0.99))
        graph_snapshot.rebuild(self.vault)

        new_path = self._write_entry("new-note.md", "new-note")
        original_new_content = new_path.read_text(encoding="utf-8")
        original_old_content = old_path.read_text(encoding="utf-8")

        import embed
        with unittest.mock.patch.object(embed, "embed_text", return_value=_unit_vector(0)):
            digest, batch = dream.run_dream_and_auto_apply(
                self.vault, run_id="run-link-1", revert_log=self.revert_log,
            )

        link_applied = [i for i in batch.items if i["stage"] == "link_improvement"]
        self.assertEqual(len(link_applied), 1)
        self.assertIn("[[old-note]]", new_path.read_text(encoding="utf-8"))
        self.assertIn("[[new-note]]", old_path.read_text(encoding="utf-8"))

        entry_id = link_applied[0]["entry_id"]
        self.revert_log.revert("run-link-1", entry_id)
        self.assertEqual(new_path.read_text(encoding="utf-8"), original_new_content)
        self.assertEqual(old_path.read_text(encoding="utf-8"), original_old_content)


if __name__ == "__main__":
    unittest.main()
