#!/usr/bin/env python3
"""Tests for storage_rules.py — the runtime-read filing contract.

Two properties carry the design and are tested hardest here.

**Absence falls through; corruption halts.** A rules file that is not there is
not an error — resolution moves to the next source. A rules file that *is* there
and will not parse raises, and never quietly falls back to the shipped default.
The fallback is what would let a typo file a week of memories somewhere
surprising, which is the failure the whole fail-closed arrangement exists to
prevent.

**Behaviour changes by editing markdown.** Every enum test mutates a fixture
rules *file* and asserts the consumer's answer changed — never by mutating
Python. A test that reached into the module to change the enum would prove
nothing about the property the design is claiming.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import storage_rules  # noqa: E402
from storage_rules import StorageRulesError  # noqa: E402


VALID_BLOCK = """\
classes:
  semantic: Facts and principles.
  procedural: How to do a thing.
  episodic: Session traces.
  entities: One file per referent.
  crystallized: Distilled lessons.
  mocs: Maps of content.

memory_types:
  - preference
  - convention
  - reference
  - workflow
  - fix
  - idea

routing:
  preference: memory/semantic
  convention: memory/semantic
  reference: memory/semantic
  workflow: memory/procedural
  fix: memory/procedural
  idea: desk

record_kinds:
  - brief
  - telemetry

deprecations:
  preferences: preference
  insight: idea

warrants: {}

thresholds:
  low_confidence: 0.65
"""


def rules_file(block: str, *, prose: str = "# Storage rules\n\nSome prose.\n") -> str:
    """A whole rules file around a block body."""
    return f"{prose}\n```storage-rules\n{block}```\n\nTrailing prose.\n"


class TempVault:
    """A scratch directory, torn down on exit."""

    def __enter__(self) -> Path:
        self._dir = Path(tempfile.mkdtemp(prefix="storage-rules-test-"))
        return self._dir

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)


class EnvGuard:
    """Restore the two env vars resolution reads, whatever the test does."""

    _KEYS = ("AGENTM_STORAGE_RULES", "MEMORY_VAULT_PATH")

    def __enter__(self) -> "EnvGuard":
        self._saved = {k: os.environ.get(k) for k in self._KEYS}
        for k in self._KEYS:
            os.environ.pop(k, None)
        return self

    def __exit__(self, *exc) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class ParseTests(unittest.TestCase):
    """The block comes out of the file, and comes out right."""

    def test_valid_block_round_trips(self):
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text(rules_file(VALID_BLOCK), encoding="utf-8")
            rules = storage_rules.load_file(path)
            self.assertEqual(
                rules.memory_types(),
                frozenset({"preference", "convention", "reference", "workflow", "fix", "idea"}),
            )
            self.assertEqual(rules.record_kinds(), frozenset({"brief", "telemetry"}))
            self.assertEqual(rules.routing()["workflow"], "memory/procedural")
            self.assertEqual(rules.thresholds()["low_confidence"], 0.65)
            self.assertEqual(rules.resolve_deprecated("preferences"), "preference")
            self.assertIsNone(rules.resolve_deprecated("workflow"))

    def test_missing_block_is_an_error(self):
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text("# Storage rules\n\nProse only, no block.\n", encoding="utf-8")
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load_file(path)
            self.assertIn("no ```storage-rules", str(caught.exception))

    def test_unparseable_yaml_raises(self):
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text(rules_file("memory_types: [unclosed\n"), encoding="utf-8")
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load_file(path)
            self.assertIn("not valid YAML", str(caught.exception))

    def test_missing_required_key_raises(self):
        block = VALID_BLOCK.replace("thresholds:\n  low_confidence: 0.65\n", "")
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text(rules_file(block), encoding="utf-8")
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load_file(path)
            self.assertIn("thresholds", str(caught.exception))

    def test_wrong_class_set_raises(self):
        """A class is a directory, and a directory is close to permanent."""
        block = VALID_BLOCK.replace("  mocs: Maps of content.\n", "  meetings: A meeting.\n")
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text(rules_file(block), encoding="utf-8")
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load_file(path)
            self.assertIn("six retrieval classes", str(caught.exception))

    def test_value_in_both_registers_raises(self):
        block = VALID_BLOCK.replace("  - brief\n", "  - brief\n  - workflow\n")
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text(rules_file(block), encoding="utf-8")
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load_file(path)
            self.assertIn("both", str(caught.exception))

    def test_unrouted_type_raises(self):
        """A type with nowhere to go files nowhere."""
        block = VALID_BLOCK.replace("  fix: memory/procedural\n", "")
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text(rules_file(block), encoding="utf-8")
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load_file(path)
            self.assertIn("no `routing` entry", str(caught.exception))

    def test_deprecation_to_unknown_target_raises(self):
        block = VALID_BLOCK.replace("  insight: idea\n", "  insight: musing\n")
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text(rules_file(block), encoding="utf-8")
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load_file(path)
            self.assertIn("unknown value", str(caught.exception))

    def test_value_both_retired_and_registered_raises(self):
        block = VALID_BLOCK.replace("  insight: idea\n", "  brief: idea\n")
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text(rules_file(block), encoding="utf-8")
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load_file(path)
            self.assertIn("retired or current", str(caught.exception))

    def test_non_kebab_type_raises(self):
        block = VALID_BLOCK.replace("  - preference\n", "  - Preference\n")
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text(rules_file(block), encoding="utf-8")
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load_file(path)
            self.assertIn("kebab-case", str(caught.exception))

    def test_warrant_missing_a_field_raises(self):
        block = VALID_BLOCK.replace(
            "warrants: {}\n",
            "warrants:\n  person:\n    query_class: who is X\n    nearest: reference\n",
        )
        with TempVault() as tmp:
            path = tmp / "storage-rules.md"
            path.write_text(rules_file(block), encoding="utf-8")
            with self.assertRaises(StorageRulesError) as caught:
                storage_rules.load_file(path)
            self.assertIn("why_not", str(caught.exception))


class ResolutionTests(unittest.TestCase):
    """Absence falls through. Corruption halts."""

    def test_explicit_env_var_wins(self):
        with TempVault() as tmp, EnvGuard():
            path = tmp / "elsewhere.md"
            block = VALID_BLOCK.replace("  - idea\n", "  - idea\n  - musing\n")
            block = block.replace("  idea: desk\n", "  idea: desk\n  musing: desk\n")
            block = block.replace(
                "warrants: {}\n",
                "warrants:\n  musing:\n    query_class: half-formed thoughts\n"
                "    nearest: idea\n    why_not: an idea is actionable\n",
            )
            path.write_text(rules_file(block), encoding="utf-8")
            os.environ["AGENTM_STORAGE_RULES"] = str(path)
            rules = storage_rules.load()
            self.assertIn("musing", rules.memory_types())
            self.assertFalse(rules.is_packaged_default)

    def test_flat_vault_layout_is_probed(self):
        with TempVault() as tmp, EnvGuard():
            (tmp / "standards").mkdir()
            (tmp / "standards" / "storage-rules.md").write_text(
                rules_file(VALID_BLOCK), encoding="utf-8")
            os.environ["MEMORY_VAULT_PATH"] = str(tmp)
            rules = storage_rules.load()
            self.assertEqual(rules.source, (tmp / "standards" / "storage-rules.md").resolve())

    def test_split_vault_layout_is_probed(self):
        """MEMORY_VAULT_PATH names the memory root; standards/ is its sibling."""
        with TempVault() as tmp, EnvGuard():
            memory_root = tmp / "Agent"
            memory_root.mkdir()
            (tmp / "standards").mkdir()
            (tmp / "standards" / "storage-rules.md").write_text(
                rules_file(VALID_BLOCK), encoding="utf-8")
            os.environ["MEMORY_VAULT_PATH"] = str(memory_root)
            rules = storage_rules.load()
            self.assertEqual(rules.source, (tmp / "standards" / "storage-rules.md").resolve())

    def test_absent_vault_file_falls_through_to_packaged_default(self):
        with TempVault() as tmp, EnvGuard():
            os.environ["MEMORY_VAULT_PATH"] = str(tmp)
            rules = storage_rules.load()
            self.assertTrue(rules.is_packaged_default)
            self.assertEqual(rules.source, storage_rules.PACKAGED_DEFAULT)

    def test_corrupt_vault_file_raises_and_never_falls_back(self):
        """The one that matters. A broken rules file halts; it does not degrade."""
        with TempVault() as tmp, EnvGuard():
            (tmp / "standards").mkdir()
            (tmp / "standards" / "storage-rules.md").write_text(
                rules_file("memory_types: [unclosed\n"), encoding="utf-8")
            os.environ["MEMORY_VAULT_PATH"] = str(tmp)
            with self.assertRaises(StorageRulesError):
                storage_rules.load()

    def test_corrupt_explicit_file_raises_and_never_falls_back(self):
        with TempVault() as tmp, EnvGuard():
            path = tmp / "broken.md"
            path.write_text("no block at all\n", encoding="utf-8")
            os.environ["AGENTM_STORAGE_RULES"] = str(path)
            with self.assertRaises(StorageRulesError):
                storage_rules.load()

    def test_absent_explicit_file_falls_through(self):
        """An override pointing at nothing is absence, not corruption."""
        with TempVault() as tmp, EnvGuard():
            os.environ["AGENTM_STORAGE_RULES"] = str(tmp / "does-not-exist.md")
            rules = storage_rules.load()
            self.assertTrue(rules.is_packaged_default)


class ContentHashTests(unittest.TestCase):
    """`rules_hash` records which rules a judgment was made under."""

    def _hash_of(self, tmp: Path, text: str) -> str:
        path = tmp / "storage-rules.md"
        path.write_text(text, encoding="utf-8")
        return storage_rules.load_file(path).content_hash()

    def test_prose_edits_do_not_change_the_hash(self):
        """Otherwise every rewording invalidates every judgment in the corpus."""
        with TempVault() as tmp:
            a = self._hash_of(tmp, rules_file(VALID_BLOCK, prose="# Rules\n\nOne.\n"))
            b = self._hash_of(tmp, rules_file(VALID_BLOCK, prose="# Rules\n\nQuite another.\n"))
            self.assertEqual(a, b)

    def test_block_reformatting_does_not_change_the_hash(self):
        with TempVault() as tmp:
            a = self._hash_of(tmp, rules_file(VALID_BLOCK))
            reflowed = VALID_BLOCK.replace("memory_types:\n  - preference\n",
                                           "memory_types:\n\n  - preference\n")
            b = self._hash_of(tmp, rules_file(reflowed))
            self.assertEqual(a, b)

    def test_changing_what_the_block_says_changes_the_hash(self):
        with TempVault() as tmp:
            a = self._hash_of(tmp, rules_file(VALID_BLOCK))
            changed = VALID_BLOCK.replace("low_confidence: 0.65", "low_confidence: 0.8")
            b = self._hash_of(tmp, rules_file(changed))
            self.assertNotEqual(a, b)


class PackagedDefaultTests(unittest.TestCase):
    """The shipped default is a real rules file, not a stub."""

    def setUp(self):
        self.rules = storage_rules.load_file(storage_rules.PACKAGED_DEFAULT)

    def test_it_parses(self):
        self.assertEqual(len(self.rules.memory_types()), 6)

    def test_the_six_types_are_the_designs_six(self):
        self.assertEqual(
            self.rules.memory_types(),
            frozenset({"preference", "convention", "reference", "workflow", "fix", "idea"}),
        )

    def test_classes_are_the_six_retrieval_classes(self):
        self.assertEqual(
            set(self.rules.classes()),
            set(storage_rules.OBSERVATIONAL_CLASSES) | set(storage_rules.DERIVED_CLASSES),
        )

    def test_every_memory_type_routes_into_an_observational_class_or_the_desk(self):
        """Filing may never write into the three derived classes."""
        derived = {f"memory/{c}" for c in storage_rules.DERIVED_CLASSES}
        for mtype, destination in self.rules.routing().items():
            self.assertNotIn(destination, derived,
                             f"{mtype} routes into a derived class, which filing may never write")

    def test_enrichment_schema_enum_is_sorted_and_matches(self):
        with EnvGuard():
            os.environ["AGENTM_STORAGE_RULES"] = str(storage_rules.PACKAGED_DEFAULT)
            storage_rules.rules(refresh=True)
            try:
                enum = storage_rules.enrichment_schema_enum()
                self.assertEqual(enum, sorted(enum))
                self.assertEqual(set(enum), self.rules.memory_types())
            finally:
                storage_rules._CACHE = None


if __name__ == "__main__":
    unittest.main()
