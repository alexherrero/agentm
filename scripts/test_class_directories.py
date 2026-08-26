#!/usr/bin/env python3
"""The six class directories, and the one thing that can drift about them.

A class is a directory, and a directory is close to permanent — which is why the
contract names exactly six and the Go validator refuses a rules file that names a
different set. What a *test* can still catch is the other direction: a class
directory whose `_index.md` describes it differently from the contract that
defines it. Those pages are generated from the rules file, so a mismatch means
someone hand-edited a generated page and the rules file no longer describes the
tree it governs.

Graceful-skip when no vault resolves. The directories live in the operator's
vault, not in the repo, and CI has neither — the assertion that matters off-vault
(the six are exactly the six) is pinned in `rules_test.go` and runs everywhere.
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
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import storage_rules  # noqa: E402

_BUILD_DIR = None


# What `storage_rules` pointed at before this module touched it, so the
# teardown restores a real value rather than a guess.
_ORIGINAL_DAEMON_BIN = storage_rules.DAEMON_BIN


def setUpModule() -> None:
    global _BUILD_DIR
    if os.environ.get("AGENTMD", "").strip():
        return
    if shutil.which("go") is None:
        raise unittest.SkipTest("go is not on this machine; set $AGENTMD to a built binary")
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


def memory_root() -> Path | None:
    """The vault's memory root, or None when this machine has no vault."""
    try:
        import harness_memory
    except ImportError:
        return None
    try:
        root = harness_memory.memory_root()
    except Exception:
        return None
    if not root:
        return None
    path = Path(root) / "memory"
    return path if path.is_dir() else None


class ClassDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.memory = memory_root()
        if self.memory is None:
            self.skipTest("no vault resolves on this machine")
        storage_rules._CACHE = None
        self.addCleanup(setattr, storage_rules, "_CACHE", None)
        self.rules = storage_rules.load()

    def test_every_class_the_contract_names_exists_as_a_directory(self) -> None:
        for name in self.rules.classes():
            self.assertTrue((self.memory / name).is_dir(),
                            f"the contract names class {name!r} and no directory exists for it")

    def test_every_class_directory_carries_an_index(self) -> None:
        for name in self.rules.classes():
            self.assertTrue((self.memory / name / "_index.md").is_file(),
                            f"{name}/ has no _index.md saying what it holds")

    def test_the_index_states_the_meaning_the_contract_gives(self) -> None:
        """The drift this test exists for. The pages are generated from the rules
        file; a mismatch means one was hand-edited and the rules no longer
        describe the tree they govern."""
        for name, meaning in self.rules.classes().items():
            text = (self.memory / name / "_index.md").read_text(encoding="utf-8")
            self.assertIn(meaning, text,
                          f"{name}/_index.md does not state the contract's meaning "
                          f"for it: {meaning!r}")

    def test_the_derived_three_say_they_are_rebuildable(self) -> None:
        """Deleting one of these loses nothing, and the page has to say so —
        otherwise the first person to find a stale rollup treats it as data."""
        for name in storage_rules.DERIVED_CLASSES:
            text = (self.memory / name / "_index.md").read_text(encoding="utf-8")
            self.assertIn("rebuildable", text.lower(), f"{name}/_index.md does not")
            self.assertIn("never write here", text.lower(),
                          f"{name}/_index.md does not say filing is barred from it")

    def test_the_observational_three_say_filing_writes_there(self) -> None:
        for name in storage_rules.OBSERVATIONAL_CLASSES:
            text = (self.memory / name / "_index.md").read_text(encoding="utf-8")
            self.assertIn("filing may write into", text.lower(), f"{name}/_index.md does not")


class VaultRulesInstanceTests(unittest.TestCase):
    """The vault's own rules file is what should be running, not the embedded copy."""

    def setUp(self) -> None:
        try:
            import harness_memory
        except ImportError:
            self.skipTest("harness_memory unavailable")
        raw = harness_memory.vault_path()
        if not raw or not Path(raw).is_dir():
            self.skipTest("no vault resolves on this machine")
        self.vault = Path(raw)
        storage_rules._CACHE = None
        self.addCleanup(setattr, storage_rules, "_CACHE", None)

    def test_the_vault_carries_its_own_rules_file(self) -> None:
        live = self.vault / "standards" / "storage-rules.md"
        if not live.is_file():
            self.skipTest("this vault has not been seeded yet (`agentmd rules --init`)")
        rules = storage_rules.load(vault_path=self.vault)
        self.assertFalse(rules.is_packaged_default,
                         "the vault has a rules file but the embedded default won "
                         "resolution — edits to the operator's file are going nowhere")


if __name__ == "__main__":
    unittest.main()
