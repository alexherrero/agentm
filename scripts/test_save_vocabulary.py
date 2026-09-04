#!/usr/bin/env python3
"""The write side of the two-register rule.

`save_entry()` takes one vocabulary value and has to decide two things from it:
which frontmatter field the note carries, and — since the target path is
`vault/group/<value>/slug.md` — where the note lands. A memory carries `type`, a
record carries `kind`, and neither carries both.

The interesting case is a retired value. The collapse is meant to be mechanical,
so a writer handed `domain-reference` migrates it to `reference` rather than
refusing: refusing would break the ingest path to make a point the deprecation
map already makes. What it will not do is invent vocabulary — a value neither
register carries is an error, because guessing which register it belongs in is
exactly the improvising the whole contract exists to prevent.
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

import save  # noqa: E402
import storage_rules  # noqa: E402

_BUILD_DIR = None


# What `storage_rules` pointed at before this module touched it, so the
# teardown restores a real value rather than a guess.
_ORIGINAL_DAEMON_BIN = storage_rules.DAEMON_BIN


def setUpModule() -> None:
    """Build the daemon that parses the contract, unless one is already named."""
    global _BUILD_DIR
    if os.environ.get("AGENTMD", "").strip():
        return
    if shutil.which("go") is None:
        raise unittest.SkipTest(
            "go is not on this machine, so the daemon that parses the filing "
            "contract cannot be built; set $AGENTMD to a built binary"
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


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    block = text.split("---", 2)[1]
    out = {}
    for line in block.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name)
        storage_rules._CACHE = None
        self.addCleanup(setattr, storage_rules, "_CACHE", None)


class VocabularyRoutingTests(_Base):
    def test_a_memory_type_is_written_as_type(self) -> None:
        path = save.save_entry(self.vault, "workflow", "a-recipe", "Body.")
        fm = frontmatter(path)
        self.assertEqual(fm["type"], "workflow")
        self.assertNotIn("kind", fm)

    def test_a_record_kind_is_written_as_kind(self) -> None:
        path = save.save_entry(self.vault, "brief", "a-brief", "Body.")
        fm = frontmatter(path)
        self.assertEqual(fm["kind"], "brief")
        self.assertNotIn("type", fm)

    def test_a_retired_value_is_migrated_rather_than_refused(self) -> None:
        """`domain-reference` is what the ingest path passed for years. Refusing
        it would break that path to make a point the deprecation map makes."""
        path = save.save_entry(self.vault, "domain-reference", "a-fact", "Body.")
        fm = frontmatter(path)
        self.assertEqual(fm["type"], "reference")
        self.assertNotIn("kind", fm)

    def test_a_migrated_value_decides_the_path_too(self) -> None:
        """The value is a path segment, so migrating it also moves where new
        notes of that value land — the collapse working, not a side effect."""
        path = save.save_entry(self.vault, "domain-reference", "a-fact", "Body.")
        # The migrated value decides the class the contract routes it to (filing-v2
        # part 4): `domain-reference` → `reference` → `semantic/`.
        self.assertIn("/semantic/", str(path).replace("\\", "/"))
        self.assertNotIn("domain-reference", str(path))

    def test_a_value_no_register_carries_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            save.save_entry(self.vault, "musing", "a-thought", "Body.")
        self.assertIn("neither register", str(caught.exception))

    def test_the_refusal_names_the_types_that_would_work(self) -> None:
        with self.assertRaises(ValueError) as caught:
            save.save_entry(self.vault, "musing", "a-thought", "Body.")
        self.assertIn("workflow", str(caught.exception))


class AltitudeTests(_Base):
    def test_every_entry_carries_the_default_altitude(self) -> None:
        """Written rather than left implied: a field that is present and default
        is one a later pass can change in place."""
        path = save.save_entry(self.vault, "workflow", "a-recipe", "Body.")
        self.assertEqual(frontmatter(path)["altitude"], "artifact")

    def test_a_note_earns_canonical_rather_than_assuming_it(self) -> None:
        path = save.save_entry(self.vault, "convention", "a-rule", "Body.")
        self.assertEqual(frontmatter(path)["altitude"], save.DEFAULT_ALTITUDE)
        self.assertNotEqual(frontmatter(path)["altitude"], "canonical")


class ContractUnavailableTests(_Base):
    def test_a_write_is_refused_when_there_is_no_contract(self) -> None:
        """Fail closed at the write path too. Filing something under a guessed
        vocabulary is the failure the whole arrangement exists to prevent."""
        saved = storage_rules.DAEMON_BIN
        storage_rules.DAEMON_BIN = str(self.vault / "no-such-binary")
        storage_rules._CACHE = None
        try:
            with self.assertRaises(ValueError) as caught:
                save.save_entry(self.vault, "workflow", "a-recipe", "Body.")
            self.assertIn("filing contract is unavailable", str(caught.exception))
        finally:
            storage_rules.DAEMON_BIN = saved
            storage_rules._CACHE = None


if __name__ == "__main__":
    unittest.main()
