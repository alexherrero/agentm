#!/usr/bin/env python3
"""Unit tests for `revert_log.py` — the dreaming pipeline's undo primitive
(AG Wave E dreaming plan, task 1: the hard prerequisite that must exist
before any content-touching dream mutation is safe to run).

`revert_log.py` lives in `harness/skills/memory/scripts/` (same cross-dir
import pattern as `test_memory_write_concurrency.py` / `test_vault_lint.py`)
and imports the co-located vendored `vault_lock.py` sibling.

Covers (plan task 1 verification):
  - the red test: a dream-style mutation (merge/supersede two fixture
    entries), reverted via the revert-log, restores the corpus BYTE-IDENTICAL
    to its pre-mutation state
  - multi-stage runs: reverting one journaled entry_id undoes only that
    stage, not the whole run
  - reverting an unknown run/entry raises UnknownRunError
  - a stage that creates a new file is undone by deleting it (existed=False
    pre-image)
  - byte-fidelity survives non-UTF-8-clean / CRLF content (base64 pre-image,
    not text)
  - mutex discipline: one `vault_mutex` acquisition per `record_and_apply` /
    per reverted stage, never the whole pass under one lock

All lock + journal activity is redirected to temp dirs so the real
`~/.cache/agentm/` is never touched (mirrors the R4 rule 1 test hygiene in
`test_memory_write_concurrency.py`).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import revert_log as rl  # noqa: E402
import vault_lock as vl  # noqa: E402


class _RevertLogTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.log_root = self.root / "revert-log"
        self.lock_root = self.root / "locks"
        self.log = rl.RevertLog(
            self.vault, log_root=self.log_root, lock_root=self.lock_root
        )

    def _write(self, name: str, content: str) -> Path:
        path = self.vault / name
        path.write_bytes(content.encode("utf-8"))
        return path

    def _corpus_snapshot(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.vault)): p.read_bytes()
            for p in sorted(self.vault.glob("*.md"))
        }


class DreamMutationRevertTests(_RevertLogTestBase):
    """The plan's own red-test: dedup/merge/supersede two fixture entries,
    revert, assert the corpus is byte-identical to its pre-mutation state."""

    def test_merge_two_entries_then_revert_is_byte_identical(self) -> None:
        entry_a = self._write("entry-a.md", "---\nkind: fix\n---\nOriginal A\n")
        entry_b = self._write("entry-b.md", "---\nkind: fix\n---\nOriginal B\n")

        pre_snapshot = self._corpus_snapshot()

        # A dream-style "merge": entry-a absorbs entry-b's content, entry-b
        # is flipped to status: superseded (dreaming never hard-deletes).
        merged_a = "---\nkind: fix\n---\nOriginal A\nOriginal B (merged)\n"
        superseded_b = "---\nkind: fix\nstatus: superseded\n---\nOriginal B\n"
        entry_id = self.log.record_and_apply(
            "run-1",
            "dedup",
            [(entry_a, merged_a), (entry_b, superseded_b)],
        )
        self.assertIsInstance(entry_id, str)

        # The mutation really landed.
        self.assertEqual(entry_a.read_text(encoding="utf-8"), merged_a)
        self.assertEqual(entry_b.read_text(encoding="utf-8"), superseded_b)
        self.assertNotEqual(self._corpus_snapshot(), pre_snapshot)

        self.log.revert("run-1")

        self.assertEqual(self._corpus_snapshot(), pre_snapshot)
        self.assertEqual(entry_a.read_bytes(), pre_snapshot["entry-a.md"])
        self.assertEqual(entry_b.read_bytes(), pre_snapshot["entry-b.md"])

    def test_compress_two_entries_into_one_then_revert(self) -> None:
        """A 'compress' mutation: two entries collapse into one file and the
        second is deleted outright. Revert must recreate the deleted file."""
        entry_a = self._write("entry-a.md", "A body\n")
        entry_b = self._write("entry-b.md", "B body\n")
        pre_snapshot = self._corpus_snapshot()

        self.log.record_and_apply(
            "run-2",
            "compression",
            [(entry_a, "A body\nB body\n"), (entry_b, None)],
        )
        self.assertFalse(entry_b.exists())

        self.log.revert("run-2")

        self.assertTrue(entry_b.exists())
        self.assertEqual(self._corpus_snapshot(), pre_snapshot)


class MultiStageRevertTests(_RevertLogTestBase):
    def test_reverting_one_entry_id_undoes_only_that_stage(self) -> None:
        entry = self._write("entry.md", "v0\n")

        id1 = self.log.record_and_apply("run-3", "dedup", [(entry, "v1\n")])
        id2 = self.log.record_and_apply("run-3", "compression", [(entry, "v2\n")])
        self.assertEqual(entry.read_text(encoding="utf-8"), "v2\n")

        # Revert only the compression stage — should restore to v1, not v0.
        self.log.revert("run-3", entry_id=id2)
        self.assertEqual(entry.read_text(encoding="utf-8"), "v1\n")

        # Now revert the dedup stage too — back to the original.
        self.log.revert("run-3", entry_id=id1)
        self.assertEqual(entry.read_text(encoding="utf-8"), "v0\n")

    def test_revert_whole_run_reverses_all_stages_in_order(self) -> None:
        entry = self._write("entry.md", "v0\n")
        self.log.record_and_apply("run-4", "dedup", [(entry, "v1\n")])
        self.log.record_and_apply("run-4", "compression", [(entry, "v2\n")])

        self.log.revert("run-4")

        self.assertEqual(entry.read_text(encoding="utf-8"), "v0\n")


class NewFileRevertTests(_RevertLogTestBase):
    def test_stage_creating_a_new_file_is_undone_by_deleting_it(self) -> None:
        new_path = self.vault / "crystallized.md"
        self.assertFalse(new_path.exists())

        self.log.record_and_apply(
            "run-5", "crystallization", [(new_path, "derived insight\n")]
        )
        self.assertTrue(new_path.exists())

        self.log.revert("run-5")
        self.assertFalse(new_path.exists())


class UnknownRunTests(_RevertLogTestBase):
    def test_revert_unknown_run_raises(self) -> None:
        with self.assertRaises(rl.UnknownRunError):
            self.log.revert("no-such-run")

    def test_revert_unknown_entry_id_in_known_run_raises(self) -> None:
        entry = self._write("entry.md", "v0\n")
        self.log.record_and_apply("run-6", "dedup", [(entry, "v1\n")])
        with self.assertRaises(rl.UnknownRunError):
            self.log.revert("run-6", entry_id="does-not-exist")


class ByteFidelityTests(_RevertLogTestBase):
    def test_crlf_and_non_ascii_survive_round_trip(self) -> None:
        # Deliberately CRLF + non-ASCII — proves the pre-image is captured
        # and restored as raw bytes (base64), not decoded/re-encoded text
        # that could normalize line endings or mangle encoding.
        original = "line one\r\nlíne twö — ünïcödé\r\n".encode("utf-8")
        path = self.vault / "weird.md"
        path.write_bytes(original)

        self.log.record_and_apply(
            "run-7", "dedup", [(path, "mutated\n".encode("utf-8"))]
        )
        self.assertNotEqual(path.read_bytes(), original)

        self.log.revert("run-7")
        self.assertEqual(path.read_bytes(), original)


class MutexDisciplineTests(_RevertLogTestBase):
    """Proves the per-stage (not per-pass) locking discipline the runner
    design locks: `agentm-runner.md` — "acquires the mutex around each
    atomic stage rather than holding it for the whole pass"."""

    def test_one_mutex_acquisition_per_stage_not_per_pass(self) -> None:
        entry = self._write("entry.md", "v0\n")
        enters = []

        real_mutex = vl.vault_mutex

        class _CountingMutex(real_mutex):
            def __enter__(self):
                enters.append("enter")
                return super().__enter__()

        with mock.patch.object(rl, "vault_mutex", _CountingMutex):
            self.log.record_and_apply("run-8", "dedup", [(entry, "v1\n")])
            self.log.record_and_apply("run-8", "compression", [(entry, "v2\n")])

        # Two stages -> two separate lock acquisitions, never one covering
        # both (a single-acquisition pass would starve concurrent sessions
        # for the pass's full ~20-30 min per the runner design).
        self.assertEqual(len(enters), 2)

    def test_revert_of_multi_stage_run_acquires_mutex_per_stage(self) -> None:
        entry = self._write("entry.md", "v0\n")
        self.log.record_and_apply("run-9", "dedup", [(entry, "v1\n")])
        self.log.record_and_apply("run-9", "compression", [(entry, "v2\n")])

        enters = []
        real_mutex = vl.vault_mutex

        class _CountingMutex(real_mutex):
            def __enter__(self):
                enters.append("enter")
                return super().__enter__()

        with mock.patch.object(rl, "vault_mutex", _CountingMutex):
            self.log.revert("run-9")

        self.assertEqual(len(enters), 2)


class NoTmpRemnantTests(_RevertLogTestBase):
    def test_apply_and_revert_leave_no_tmp_remnants(self) -> None:
        entry = self._write("entry.md", "v0\n")
        self.log.record_and_apply("run-10", "dedup", [(entry, "v1\n")])
        self.log.revert("run-10")
        self.assertEqual(list(self.vault.rglob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
