#!/usr/bin/env python3
"""Unit tests for `dream_confirm.py` — the `_dream-staging/` inbox contract
(AG Wave E dreaming plan, task 3).

`dream_confirm.py` lives in `harness/skills/memory/scripts/` (same cross-dir
import pattern as `test_dream.py` / `test_revert_log.py`).

Covers (plan task 3 verification):
  - red-test: a staged proposal that is NEVER confirmed expires and stays
    inert once its TTL passes — no silent apply on timeout
  - red-test: a confirmed proposal's apply path routes through the
    revert-log (`RevertLog.record_and_apply`), not a direct write — proven
    by reverting the confirmed mutation via the SAME `RevertLog` and
    checking it restores the pre-confirm content, which only works if the
    apply actually went through the journal
  - confirming an expired proposal raises and never applies it
  - confirming twice raises (no accidental double-apply)
  - confirming an unknown index raises
  - a non-expired pending proposal stays pending until confirmed
  - two REAL concurrent confirm() calls (different proposal indices, same
    run, real threads) both land in state.json — no lost update. An
    adversarial review caught a genuine race here: confirm()'s state.json
    read-modify-write wasn't serialized against itself, so two concurrent
    callers could interleave their load/save and silently drop one
    confirmation even though its mutation had already applied to disk
    (which also defeated the AlreadyConfirmedError one-shot guard and could
    cause a second record_and_apply to journal already-mutated content as
    its "pre-image"). Fixed by _confirm_lock — a mutex separate from
    revert_log's own, held across confirm()'s entire body.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import dream  # noqa: E402
import dream_confirm as dc  # noqa: E402
from revert_log import RevertLog  # noqa: E402


class _DreamConfirmTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.log_root = self.root / "revert-log"
        self.lock_root = self.root / "locks"
        self.revert_log = RevertLog(self.vault, log_root=self.log_root, lock_root=self.lock_root)

    def _write(self, name: str, content: str) -> Path:
        path = self.vault / name
        path.write_text(content, encoding="utf-8")
        return path

    def _stage_a_dedup_run(self, run_id: str):
        a = self._write("a.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today.\n")
        b = self._write("b.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today!\n")
        digest = dream.run_dream(self.vault, run_id=run_id)
        dedup = [p for p in digest.proposals if p.stage == "dedup"]
        self.assertEqual(len(dedup), 1, "fixture must produce exactly one dedup proposal")
        return a, b, digest

    def _stage_a_compression_chain_run(self, run_id: str):
        """A supersession chain of 3 (c1 <- c2 <- c3), the exact fixture
        shape `test_dream.py`'s own compression tests use -- the one
        stage-kind the 2026-07-11 operator ruling flips to confirm-free
        auto-apply (dedup/contradiction-triage are unaffected, see
        `AUTO_APPLY_STAGES`'s own docstring in dream_confirm.py)."""
        c1 = self._write(
            "chain-1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v3.\n".format(self.vault / "chain-2.md")
        )
        c2 = self._write(
            "chain-2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix v2.\n".format(self.vault / "chain-3.md")
        )
        c3 = self._write("chain-3.md", "---\nkind: fix\n---\nFix v1.\n")
        digest = dream.run_dream(self.vault, run_id=run_id)
        compression = [p for p in digest.proposals if p.stage == "compression"]
        self.assertEqual(len(compression), 1, "fixture must produce exactly one compression proposal")
        return c1, c2, c3, digest


class ConfirmAppliesThroughRevertLogTests(_DreamConfirmTestBase):
    def test_confirm_applies_mutation_and_revert_log_can_undo_it(self) -> None:
        a, b, digest = self._stage_a_dedup_run("run-confirm")
        pre_a, pre_b = a.read_bytes(), b.read_bytes()

        entry_id = dc.confirm(self.vault, "run-confirm", 1, self.revert_log)
        self.assertIsInstance(entry_id, str)

        # The mutation really landed.
        self.assertNotEqual(a.read_bytes(), pre_a)
        self.assertNotEqual(b.read_bytes(), pre_b)

        # It went through revert_log, not a direct write: the SAME
        # RevertLog instance can undo it, restoring the pre-confirm bytes.
        self.revert_log.revert("run-confirm", entry_id=entry_id)
        self.assertEqual(a.read_bytes(), pre_a)
        self.assertEqual(b.read_bytes(), pre_b)

    def test_confirm_marks_proposal_confirmed_in_list_pending(self) -> None:
        self._stage_a_dedup_run("run-confirm-2")
        dc.confirm(self.vault, "run-confirm-2", 1, self.revert_log)
        pending = dc.list_pending(self.vault, "run-confirm-2")
        self.assertEqual(pending[0].status, "confirmed")


class NoSilentApplyOnTimeoutTests(_DreamConfirmTestBase):
    """The plan's own red-test: an unconfirmed proposal expires/stays inert
    — no silent apply on timeout."""

    def test_expired_proposal_is_never_applied(self) -> None:
        a, b, digest = self._stage_a_dedup_run("run-expire")
        pre_a, pre_b = a.read_bytes(), b.read_bytes()

        staged_at = digest_staged_at = _read_manifest_staged_at(self.vault, "run-expire")
        far_future = staged_at + (dc.DEFAULT_TTL_DAYS + 1) * 86400

        with self.assertRaises(dc.ExpiredProposalError):
            dc.confirm(self.vault, "run-expire", 1, self.revert_log, now=far_future)

        # Never applied — source files are byte-identical to pre-confirm.
        self.assertEqual(a.read_bytes(), pre_a)
        self.assertEqual(b.read_bytes(), pre_b)

    def test_expire_stale_marks_pending_proposals_expired_and_stays_inert(self) -> None:
        a, b, digest = self._stage_a_dedup_run("run-expire-2")
        staged_at = _read_manifest_staged_at(self.vault, "run-expire-2")
        far_future = staged_at + (dc.DEFAULT_TTL_DAYS + 1) * 86400

        expired_indices = dc.expire_stale(self.vault, "run-expire-2", now=far_future)
        self.assertEqual(expired_indices, [1])

        pending = dc.list_pending(self.vault, "run-expire-2", now=far_future)
        self.assertEqual(pending[0].status, "expired")

        # Still inert — no file was touched by expiry itself.
        self.assertIn(b"quick brown fox", a.read_bytes())
        self.assertIn(b"quick brown fox", b.read_bytes())

    def test_not_yet_expired_proposal_stays_pending(self) -> None:
        _, _, digest = self._stage_a_dedup_run("run-not-expired")
        staged_at = _read_manifest_staged_at(self.vault, "run-not-expired")
        soon = staged_at + 3600  # one hour later — nowhere near the 30-day TTL
        pending = dc.list_pending(self.vault, "run-not-expired", now=soon)
        self.assertEqual(pending[0].status, "pending")


class DoubleConfirmAndUnknownProposalTests(_DreamConfirmTestBase):
    def test_confirming_twice_raises(self) -> None:
        self._stage_a_dedup_run("run-double")
        dc.confirm(self.vault, "run-double", 1, self.revert_log)
        with self.assertRaises(dc.AlreadyConfirmedError):
            dc.confirm(self.vault, "run-double", 1, self.revert_log)

    def test_unknown_proposal_index_raises(self) -> None:
        self._stage_a_dedup_run("run-unknown-idx")
        with self.assertRaises(dc.UnknownProposalError):
            dc.confirm(self.vault, "run-unknown-idx", 99, self.revert_log)

    def test_unknown_run_raises(self) -> None:
        with self.assertRaises(dc.UnknownRunError):
            dc.confirm(self.vault, "no-such-run", 1, self.revert_log)
        with self.assertRaises(dc.UnknownRunError):
            dc.list_pending(self.vault, "no-such-run")


class ConcurrentConfirmTests(_DreamConfirmTestBase):
    """Regression test for the adversarial-review finding: confirm()'s
    state.json read-modify-write must be serialized against itself, or two
    concurrent confirms silently lose a confirmation even though its
    mutation already applied to disk."""

    def _stage_two_independent_dedup_proposals(self, run_id: str):
        self._write("a1.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today.\n")
        self._write("a2.md", "---\nkind: fix\n---\nThe quick brown fox jumps over the lazy dog today!\n")
        self._write("b1.md", "---\nkind: fix\n---\nPack my box with five dozen liquor jugs now.\n")
        self._write("b2.md", "---\nkind: fix\n---\nPack my box with five dozen liquor jugs now!\n")
        digest = dream.run_dream(self.vault, run_id=run_id)
        self.assertGreaterEqual(len(digest.proposals), 2, "fixture must produce >=2 independent proposals")
        return digest

    def test_two_concurrent_confirms_both_land_no_lost_update(self) -> None:
        self._stage_two_independent_dedup_proposals("run-concurrent")

        results = {}

        def worker(index: int) -> None:
            try:
                entry_id = dc.confirm(self.vault, "run-concurrent", index, self.revert_log)
                results[index] = ("ok", entry_id)
            except Exception as e:  # pragma: no cover - failure path only
                results[index] = ("error", e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results[1][0], "ok", results[1])
        self.assertEqual(results[2][0], "ok", results[2])

        statuses = {p.index: p.status for p in dc.list_pending(self.vault, "run-concurrent")}
        self.assertEqual(statuses[1], "confirmed")
        self.assertEqual(statuses[2], "confirmed")

        # A second confirm on either index must still be rejected — the
        # one-shot guard survives the concurrent window.
        with self.assertRaises(dc.AlreadyConfirmedError):
            dc.confirm(self.vault, "run-concurrent", 1, self.revert_log)
        with self.assertRaises(dc.AlreadyConfirmedError):
            dc.confirm(self.vault, "run-concurrent", 2, self.revert_log)


class AutoApplyExpireTests(_DreamConfirmTestBase):
    """The 2026-07-11 operator ruling: expire (compression) auto-applies
    with no confirm() call; promote (dedup) and link (contradiction-
    triage) are UNCHANGED and must still require an explicit confirm()."""

    def test_compression_auto_applies_with_no_confirm_call(self) -> None:
        c1, c2, c3, digest = self._stage_a_compression_chain_run("run-auto-1")
        pre = {p: p.read_bytes() for p in (c1, c2, c3)}

        batch = dc.auto_apply_batch(self.vault, digest.run_id, self.revert_log)

        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.items[0]["stage"], "compression")
        self.assertIn("entry_id", batch.items[0])

        # The mutation really landed — no dream_confirm.confirm() call was
        # ever made by this test, only auto_apply_batch().
        self.assertNotEqual(c1.read_bytes(), pre[c1])

        # It's provably through the revert log, not a direct write: the
        # SAME RevertLog instance can undo it.
        self.revert_log.revert(digest.run_id, entry_id=batch.items[0]["entry_id"])
        self.assertEqual(c1.read_bytes(), pre[c1])
        self.assertEqual(c2.read_bytes(), pre[c2])
        self.assertEqual(c3.read_bytes(), pre[c3])

        # list_pending now reports it confirmed — a later manual confirm()
        # on the same index correctly raises AlreadyConfirmedError rather
        # than double-applying.
        pending = dc.list_pending(self.vault, digest.run_id)
        compression_status = [p.status for p in pending if p.stage == "compression"][0]
        self.assertEqual(compression_status, "confirmed")
        with self.assertRaises(dc.AlreadyConfirmedError):
            dc.confirm(self.vault, digest.run_id, batch.items[0]["index"], self.revert_log)

    def test_dedup_promote_is_not_auto_applied(self) -> None:
        """Regression test: dedup ('promote') must never be swept up by
        auto_apply_batch — it stays pending, exactly as before this
        ruling, requiring an explicit confirm() call."""
        a, b, digest = self._stage_a_dedup_run("run-auto-2")
        pre_a, pre_b = a.read_bytes(), b.read_bytes()

        batch = dc.auto_apply_batch(self.vault, digest.run_id, self.revert_log)

        self.assertEqual(batch.items, [], "dedup ('promote') must never auto-apply")
        self.assertEqual(a.read_bytes(), pre_a)
        self.assertEqual(b.read_bytes(), pre_b)

        pending = dc.list_pending(self.vault, digest.run_id)
        self.assertEqual(pending[0].status, "pending")
        # Still requires the human path — this must succeed unchanged.
        entry_id = dc.confirm(self.vault, digest.run_id, 1, self.revert_log)
        self.assertIsInstance(entry_id, str)

    def test_contradiction_triage_link_is_not_auto_applied(self) -> None:
        """Regression test: contradiction-triage ('link') never carries
        mutations (advisory-only, unchanged) — auto_apply_batch must not
        touch it or mark it confirmed."""
        self._write("con-a.md", "---\nslug: contradiction\nkind: preference\n---\nUse tabs for indentation.\n")
        self._write("con-b.md", "---\nslug: contradiction\nkind: preference\n---\nUse spaces for indentation.\n")
        digest = dream.run_dream(self.vault, run_id="run-auto-3")
        contra = [p for p in digest.proposals if p.stage == "contradiction_triage"]
        self.assertEqual(len(contra), 1)

        batch = dc.auto_apply_batch(self.vault, "run-auto-3", self.revert_log)
        self.assertEqual(batch.items, [])

        pending = dc.list_pending(self.vault, "run-auto-3")
        self.assertEqual(pending[0].status, "pending")

    def test_batch_cap_limits_auto_apply_per_cycle(self) -> None:
        # Two independent supersession chains in one run.
        self._write("a1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix a3.\n".format(self.vault / "a2.md"))
        self._write("a2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix a2.\n".format(self.vault / "a3.md"))
        self._write("a3.md", "---\nkind: fix\n---\nFix a1.\n")
        self._write("b1.md", "---\nkind: fix\nsupersedes: {}\n---\nFix b3.\n".format(self.vault / "b2.md"))
        self._write("b2.md", "---\nkind: fix\nsupersedes: {}\n---\nFix b2.\n".format(self.vault / "b3.md"))
        self._write("b3.md", "---\nkind: fix\n---\nFix b1.\n")
        digest = dream.run_dream(self.vault, run_id="run-cap")
        compression = [p for p in digest.proposals if p.stage == "compression"]
        self.assertEqual(len(compression), 2, "fixture must produce two independent compression proposals")

        batch = dc.auto_apply_batch(self.vault, "run-cap", self.revert_log, batch_cap=1)
        self.assertEqual(len(batch.items), 1, "batch_cap=1 must apply exactly one, leaving the other pending")

        pending = dc.list_pending(self.vault, "run-cap")
        statuses = sorted(p.status for p in pending if p.stage == "compression")
        self.assertEqual(statuses, ["confirmed", "pending"])

    def test_zero_item_batch_still_returns_a_record(self) -> None:
        self._stage_a_dedup_run("run-zero")
        batch = dc.auto_apply_batch(self.vault, "run-zero", self.revert_log)
        self.assertEqual(batch.items, [])
        self.assertEqual(batch.run_id, "run-zero")
        self.assertEqual(batch.stages, dc.AUTO_APPLY_STAGES)

    def test_render_auto_applied_json_shape_and_revert_pointer(self) -> None:
        _, _, _, digest = self._stage_a_compression_chain_run("run-render")
        batch = dc.auto_apply_batch(self.vault, digest.run_id, self.revert_log)
        payload = json.loads(dc.render_auto_applied_json(batch))

        self.assertEqual(payload["run_id"], digest.run_id)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["stages"], ["compression"])
        self.assertEqual(payload["batch_cap"], dc.DEFAULT_AUTO_APPLY_BATCH_CAP)
        self.assertEqual(len(payload["items"]), 1)
        self.assertIn("entry_id", payload["items"][0])
        self.assertIn("paths", payload["items"][0])
        self.assertIn(digest.run_id, payload["revert"]["how"])
        self.assertIn("RevertLog", payload["revert"]["how"])


def _read_manifest_staged_at(vault_path: Path, run_id: str) -> float:
    import json

    manifest_path = vault_path / "_dream-staging" / run_id / "proposals.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))["staged_at"]


if __name__ == "__main__":
    unittest.main()
