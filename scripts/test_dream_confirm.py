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


def _read_manifest_staged_at(vault_path: Path, run_id: str) -> float:
    import json

    manifest_path = vault_path / "_dream-staging" / run_id / "proposals.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))["staged_at"]


if __name__ == "__main__":
    unittest.main()
