#!/usr/bin/env python3
"""Tests for storage_rules.py — the Python side of the filing contract.

The parser is not here. It lives in the daemon, in Go, and
`daemon/internal/rules/rules_test.go` is where block parsing, shape validation,
resolution order and the content hash are pinned. Duplicating those assertions in
Python would test a second implementation that does not exist.

What is tested here is what Python actually owns:

  * the client contract — every way of not having a contract converges on one
    exception, because every one of them means the same thing to a caller: there
    is nothing to file against, so filing halts;
  * `note_type()`, which reads a corpus that is half-way through a field rename
    and refuses a note that carries both names;
  * the hash watch, which is what makes a rules edit loud in the nightly digest.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import storage_rules  # noqa: E402
from storage_rules import ContractViolation, StorageRulesError  # noqa: E402

_BUILD_DIR: tempfile.TemporaryDirectory | None = None


# What `storage_rules` pointed at before this module touched it, so the
# teardown restores a real value rather than a guess.
_ORIGINAL_DAEMON_BIN = storage_rules.DAEMON_BIN


def setUpModule() -> None:
    """Point the client at a binary built from this tree.

    Not at whatever `agentmd` is installed: a stale global install would grade the
    last release rather than this diff, and the contract is exactly the thing that
    just changed. `check-all.sh` exports AGENTMD for the same reason, so this is a
    no-op inside the battery and a convenience when the file is run alone.
    """
    global _BUILD_DIR
    if os.environ.get("AGENTMD", "").strip():
        return
    if shutil.which("go") is None:
        raise unittest.SkipTest(
            "go is not on this machine, so the daemon that parses the filing "
            "contract cannot be built; set $AGENTMD to a built binary to run these"
        )
    _BUILD_DIR = tempfile.TemporaryDirectory(prefix="agentmd-build-")
    binary = Path(_BUILD_DIR.name) / "agentmd"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/agentmd"],
                   cwd=_REPO / "daemon", check=True, capture_output=True)
    os.environ["AGENTMD"] = str(binary)
    storage_rules.DAEMON_BIN = str(binary)
    storage_rules._CACHE = None


def tearDownModule() -> None:
    """Undo everything setUpModule did, not just the directory.

    Deleting the build directory while leaving `$AGENTMD` pointing into it is
    what made a full `unittest discover` run fail: every later module takes its
    own `if os.environ.get("AGENTMD"): return` early exit, then shells out to a
    binary that is no longer there.

    Only what this module set. A module that inherited `$AGENTMD` from the
    environment returned early and built nothing, so the variable is not its to
    clear.
    """
    global _BUILD_DIR
    if _BUILD_DIR is None:
        return
    _BUILD_DIR.cleanup()
    _BUILD_DIR = None
    os.environ.pop("AGENTMD", None)
    storage_rules.DAEMON_BIN = _ORIGINAL_DAEMON_BIN
    storage_rules._CACHE = None


VALID_BLOCK = """\
classes:
  semantic: Facts and principles.
  procedural: How to do a thing.
  episodic: Session traces.
  entities: One file per referent.
  crystallized: Distilled lessons.
  mocs: Maps of content.
memory_types: [preference, convention, reference, workflow, fix, idea]
default_type: preference
routing:
  preference: memory/semantic
  convention: memory/semantic
  reference: memory/semantic
  workflow: memory/procedural
  fix: memory/procedural
  idea: desk
record_kinds: [brief, telemetry]
deprecations: {preferences: preference, insight: idea}
warrants: {}
thresholds: {low_confidence: 0.65}
"""


def rules_file(block: str) -> str:
    return f"# Storage rules\n\nProse.\n\n```storage-rules\n{block}```\n"


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.addCleanup(setattr, storage_rules, "_CACHE", None)
        storage_rules._CACHE = None

    def _write(self, block: str) -> Path:
        path = self.tmp / "storage-rules.md"
        path.write_text(rules_file(block), encoding="utf-8")
        return path


class ClientTests(_Base):
    """What the daemon says, the client reports."""

    def test_a_valid_contract_comes_back_whole(self) -> None:
        rules = storage_rules.load_file(self._write(VALID_BLOCK))
        self.assertEqual(
            rules.memory_types(),
            frozenset({"preference", "convention", "reference", "workflow", "fix", "idea"}),
        )
        self.assertEqual(rules.record_kinds(), frozenset({"brief", "telemetry"}))
        self.assertEqual(rules.default_type(), "preference")
        self.assertEqual(rules.routing()["workflow"], "memory/procedural")
        self.assertEqual(rules.thresholds()["low_confidence"], 0.65)
        self.assertEqual(rules.resolve_deprecated("preferences"), "preference")
        self.assertIsNone(rules.resolve_deprecated("workflow"))
        self.assertTrue(rules.content_hash())

    def test_known_values_is_the_union_of_both_registers(self) -> None:
        rules = storage_rules.load_file(self._write(VALID_BLOCK))
        self.assertEqual(rules.known_values(),
                         rules.memory_types() | rules.record_kinds())

    def test_a_contract_that_will_not_parse_raises(self) -> None:
        path = self.tmp / "broken.md"
        path.write_text(rules_file("memory_types: [unclosed\n"), encoding="utf-8")
        with self.assertRaises(StorageRulesError) as caught:
            storage_rules.load_file(path)
        self.assertIn("not valid YAML", str(caught.exception))

    def test_a_missing_file_raises_rather_than_returning_a_default(self) -> None:
        with self.assertRaises(StorageRulesError):
            storage_rules.load_file(self.tmp / "nothing-here.md")

    def test_a_missing_binary_raises_the_same_way_a_broken_file_does(self) -> None:
        """Every way of not having a contract means the same thing to a caller."""
        saved = storage_rules.DAEMON_BIN
        storage_rules.DAEMON_BIN = str(self.tmp / "no-such-binary")
        try:
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load()
            self.assertIn("not on PATH", str(caught.exception))
        finally:
            storage_rules.DAEMON_BIN = saved

    def test_the_enrichment_enum_is_sorted(self) -> None:
        """A JSON Schema enum is an ordered array, and a stable order keeps a
        prompt's cache key stable."""
        os.environ["AGENTM_STORAGE_RULES"] = str(self._write(VALID_BLOCK))
        self.addCleanup(os.environ.pop, "AGENTM_STORAGE_RULES", None)
        storage_rules._CACHE = None
        enum = storage_rules.enrichment_schema_enum()
        self.assertEqual(enum, sorted(enum))
        self.assertEqual(set(enum), storage_rules.memory_types())

    def test_the_shipped_contract_resolves_when_nothing_else_does(self) -> None:
        """The fallback that keeps the enums defined in a checkout with no vault.

        Pointed at an empty directory on purpose. This assertion used to call
        `load()` with no vault argument and pass — but only because the machine
        it ran on had no rules file yet. The daemon resolves its own configured
        vault, so once one was seeded the test started reading it, which is
        neither what the name claimed nor a fallback at all."""
        os.environ.pop("AGENTM_STORAGE_RULES", None)
        rules = storage_rules.load(vault_path=self.tmp / "no-vault-here")
        self.assertTrue(rules.is_packaged_default)
        self.assertEqual(len(rules.memory_types()), 6)


class NoteTypeTests(unittest.TestCase):
    """Reading a corpus half-way through a field rename."""

    def test_type_is_read(self) -> None:
        self.assertEqual(storage_rules.note_type({"type": "workflow"}), "workflow")

    def test_kind_is_read_when_type_is_absent(self) -> None:
        self.assertEqual(storage_rules.note_type({"kind": "brief"}), "brief")

    def test_neither_is_not_an_error(self) -> None:
        """A capture that has not been filed yet has no type, and that is the
        ordinary state rather than a defect."""
        self.assertIsNone(storage_rules.note_type({"status": "unfiled"}))

    def test_blank_values_read_as_absent(self) -> None:
        self.assertIsNone(storage_rules.note_type({"type": "   ", "kind": ""}))

    def test_both_is_a_contract_violation(self) -> None:
        """Two fields that can disagree about what a note is will eventually
        disagree; silently preferring one is how a file starts lying."""
        with self.assertRaises(ContractViolation):
            storage_rules.note_type({"type": "workflow", "kind": "brief"})


class IsMemoryTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        os.environ["AGENTM_STORAGE_RULES"] = str(self._write(VALID_BLOCK))
        self.addCleanup(os.environ.pop, "AGENTM_STORAGE_RULES", None)
        storage_rules._CACHE = None

    def test_a_memory_type_is_a_memory(self) -> None:
        self.assertTrue(storage_rules.is_memory({"type": "workflow"}))

    def test_a_record_kind_is_not(self) -> None:
        self.assertFalse(storage_rules.is_memory({"kind": "brief"}))

    def test_an_unjudged_capture_is_neither(self) -> None:
        self.assertFalse(storage_rules.is_memory({"status": "unfiled"}))


class HashWatchTests(_Base):
    """A rules edit is loud by construction."""

    def setUp(self) -> None:
        super().setUp()
        self.vault = self.tmp / "vault"
        self.vault.mkdir()

    def test_first_run_records_without_claiming_a_change(self) -> None:
        watch = storage_rules.hash_watch(self.vault, current="abc123")
        self.assertTrue(watch["first_run"])
        self.assertFalse(watch["changed"])
        self.assertTrue((storage_rules.engine_state.engine_state_dir() / "storage-rules-state.json").is_file())

    def test_an_unchanged_second_run_reports_no_change(self) -> None:
        storage_rules.hash_watch(self.vault, current="abc123")
        watch = storage_rules.hash_watch(self.vault, current="abc123")
        self.assertFalse(watch["changed"])
        self.assertFalse(watch["first_run"])

    def test_a_changed_hash_reports_the_old_one(self) -> None:
        storage_rules.hash_watch(self.vault, current="abc123")
        watch = storage_rules.hash_watch(self.vault, current="def456")
        self.assertTrue(watch["changed"])
        self.assertEqual(watch["previous"], "abc123")

    def test_record_false_leaves_no_trace(self) -> None:
        storage_rules.hash_watch(self.vault, current="abc123", record=False)
        self.assertFalse((storage_rules.engine_state.engine_state_dir() / "storage-rules-state.json").exists())

    def test_a_corrupt_watch_file_loses_the_comparison_but_never_halts(self) -> None:
        """Bookkeeping, not the contract. A broken watch file costs one cycle's
        change announcement and nothing else."""
        state = self.vault / "_meta" / "storage-rules-state.json"
        state.parent.mkdir(parents=True)
        state.write_text("{not json", encoding="utf-8")
        watch = storage_rules.hash_watch(self.vault, current="abc123")
        self.assertTrue(watch["first_run"])


if __name__ == "__main__":
    unittest.main()
