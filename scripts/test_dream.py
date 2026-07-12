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
        self.assertEqual(auto_expired["stages"], ["compression"])

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


if __name__ == "__main__":
    unittest.main()
