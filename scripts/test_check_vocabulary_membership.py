#!/usr/bin/env python3
"""The vocabulary-membership gate's ratchet semantics, pinned.

The census finding this gate answers: the XOR rule held while enum membership
was never checked. The pinned behaviors: a baseline records legacy drift once;
any offender not in it fails immediately (a *set* comparison, so a swap cannot
hide inside a stable count); the baseline only ever shrinks; retired values are
migration-pending, never violations; `--strict` ignores the baseline entirely.

No daemon binary: the contract is injected through `storage_rules`' module
cache, which is the same seam the runtime uses — these tests exercise the real
audit walker and the real gate logic over scratch vaults.
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (str(_HERE), str(_SKILL_SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import storage_rules  # noqa: E402
from storage_rules import StorageRules  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_vocabulary_membership", _HERE / "check-vocabulary-membership.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


CONTRACT = {
    "memory_types": ["preference", "convention", "reference", "workflow", "fix", "idea"],
    "record_kinds": ["brief", "telemetry"],
    "deprecations": {"insight": "idea"},
}


def _note(kind_line: str) -> str:
    return f"---\n{kind_line}\n---\n\nbody\n"


class RatchetSemantics(unittest.TestCase):
    def setUp(self):
        self._saved_cache = storage_rules._CACHE
        storage_rules._CACHE = StorageRules(CONTRACT)
        self._td = tempfile.TemporaryDirectory()
        self.vault = Path(self._td.name)
        self.notes = self.vault / "memory" / "semantic"
        self.notes.mkdir(parents=True)
        self.baseline = self.vault / "baseline.json"

    def tearDown(self):
        storage_rules._CACHE = self._saved_cache
        self._td.cleanup()

    def _run(self, *, strict: bool = False) -> tuple:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate.run_corpus_check(self.vault, self.baseline, strict=strict)
        return code, out.getvalue()

    def test_first_run_records_the_baseline_and_passes(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("baseline recorded", out)
        code, _ = self._run()
        self.assertEqual(code, 0, "the recorded offender must be tolerated on the next run")

    def test_a_new_offender_fails_and_is_named(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        self._run()  # record baseline
        (self.notes / "fresh.md").write_text(_note("kind: report"), encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 1)
        self.assertIn("fresh.md", out)
        self.assertIn("report", out)
        self.assertNotIn("legacy.md: ", out.split("FAIL")[1],
                         "the tolerated legacy offender must not be re-reported as new")

    def test_a_swap_cannot_hide_inside_a_stable_count(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        self._run()
        (self.notes / "legacy.md").write_text(_note("kind: brief"), encoding="utf-8")  # fixed
        (self.notes / "swap.md").write_text(_note("kind: seed-meta"), encoding="utf-8")  # new
        code, out = self._run()
        self.assertEqual(code, 1, "count stayed at one, but the offender is new — sets, not counts")
        self.assertIn("swap.md", out)

    def test_draining_ratchets_the_baseline_down(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        self._run()
        (self.notes / "legacy.md").write_text(_note("kind: brief"), encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("ratcheted down", out)
        # The drained offender must not be tolerated if it reappears.
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        code, _ = self._run()
        self.assertEqual(code, 1, "a drained offender that returns is new again")

    def test_retired_values_are_migration_pending_not_violations(self):
        (self.notes / "old.md").write_text(_note("kind: insight"), encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("1 retired-value note", out)
        self.assertIn("0 unregistered-value offender", out)

    def test_malformed_values_are_violations(self):
        (self.notes / "bad.md").write_text(_note("kind: Not Kebab"), encoding="utf-8")
        self._run()  # baseline tolerates it
        (self.notes / "bad2.md").write_text(_note("kind: Also Bad"), encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 1)
        self.assertIn("bad2.md", out)

    def test_strict_ignores_the_baseline(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        self._run()  # tolerated by ratchet
        code, out = self._run(strict=True)
        self.assertEqual(code, 1, "--strict is the post-migration mode: any violation fails")
        self.assertIn("legacy.md", out)
        (self.notes / "legacy.md").write_text(_note("kind: brief"), encoding="utf-8")
        code, _ = self._run(strict=True)
        self.assertEqual(code, 0)


class SelfTestMembershipHalf(unittest.TestCase):
    """The audit half of --self-test, runnable without a daemon binary."""

    def test_unregistered_value_in_scratch_vault_is_caught(self):
        saved = storage_rules._CACHE
        storage_rules._CACHE = StorageRules(CONTRACT)
        try:
            with tempfile.TemporaryDirectory() as td:
                vault = Path(td)
                notes = vault / "memory" / "semantic"
                notes.mkdir(parents=True)
                (notes / "stray.md").write_text(_note("kind: definitely-not-registered"),
                                                encoding="utf-8")
                import kind_registry
                offenders = gate._offenders(kind_registry.audit(vault))
                self.assertIn(("memory/semantic/stray.md", "definitely-not-registered"),
                              offenders)
        finally:
            storage_rules._CACHE = saved


if __name__ == "__main__":
    unittest.main()
