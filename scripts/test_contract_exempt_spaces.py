#!/usr/bin/env python3
"""Contract exemption, made explicit rather than left accidental.

Before this, `Personal/` produced zero validation findings — but only because the
validators scope themselves to `memory` and `desk/projects` *under* the memory
root, and `Personal/` sits outside it. Nothing looked, so nothing complained.

That is a fragile kind of correct, and part 3 is literally in the business of
widening what search reaches. Widen a scope list and 385 documents become
findings with no rule anywhere saying they should not be. These tests point the
validator directly at an exempt path — bypassing the scope accident — so what is
asserted is the rule and not the oversight.

One correction to the design worth carrying here: it says these files "carry no
frontmatter". Every one of them has frontmatter, just of its own shape — `title`,
`created`, `updated`, and nothing the memory contract asks for. A rule written
against "no frontmatter" would have matched none of them, which is why the
exemption is by path.
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

import frontmatter_validator as fv  # noqa: E402
import lifecycle  # noqa: E402
import storage_rules  # noqa: E402

SHIPPED = _REPO / "daemon" / "internal" / "rules" / "storage-rules.default.md"

# The real shape, taken from a note in the operator's vault. Not "no
# frontmatter" — frontmatter that answers a different contract.
PERSONAL_NOTE = """---
title: 5th Sunday Lesson - Family History and how to use it
updated: 2016-12-11T20:56:54
created: 2016-05-28T15:33:51
---

Material sourced from the church site.
"""

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


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get("AGENTM_STORAGE_RULES")
        os.environ["AGENTM_STORAGE_RULES"] = str(SHIPPED)
        storage_rules._CACHE = None
        self.addCleanup(self._restore)

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _restore(self) -> None:
        if self._saved is None:
            os.environ.pop("AGENTM_STORAGE_RULES", None)
        else:
            os.environ["AGENTM_STORAGE_RULES"] = self._saved
        storage_rules._CACHE = None

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class ValidationExemption(_Base):
    def test_a_personal_note_produces_no_findings(self):
        """Pointed directly at the file, not reached through a scope list — so
        this asserts the rule rather than the oversight that used to stand in for
        it."""
        note = self.write("Personal/Church/lesson.md", PERSONAL_NOTE)
        self.assertEqual(fv.validate(note, vault=self.root), [])

    def test_the_same_note_outside_the_exempt_space_is_held_to_the_contract(self):
        """The control. Without it, "no findings" is also what a broken validator
        returns."""
        note = self.write("Agent/memory/semantic/lesson.md", PERSONAL_NOTE)
        findings = fv.validate(note, vault=self.root)
        self.assertTrue(findings,
                        "the identical file outside the exempt space produced no "
                        "findings either — the exemption is not what is being tested")

    def test_a_note_with_no_frontmatter_at_all_is_also_exempt(self):
        """The exemption is by path, so it does not depend on what the file
        happens to contain."""
        note = self.write("Personal/Home/scratch.md", "Just a heading\n")
        self.assertEqual(fv.validate(note, vault=self.root), [])

    def test_a_nested_folder_named_personal_is_not_exempt(self):
        """A space is a top-level directory, not a word in a path."""
        note = self.write("Agent/desk/projects/x/personal/notes.md", PERSONAL_NOTE)
        self.assertTrue(fv.validate(note, vault=self.root),
                        "a nested folder named `personal` inherited the operator's "
                        "space exemption")


class DecayExemption(_Base):
    """Nothing in an exempt space decays. Decay models a fact going cold because
    nobody has needed it; a 2016 lesson is not less true for being ten years old,
    and ranking it as though it were applies a memory's physics to a document."""

    def test_a_personal_note_never_decays(self):
        fm = {"created": "2016-05-28", "updated": "2016-12-11"}
        score = lifecycle.compute_decay_score(
            self.root, "lesson", fm, "Personal/Church/lesson.md", now="2026-08-20")
        self.assertEqual(score, 1.0)

    def test_the_same_note_outside_the_space_does_decay(self):
        """The control again: ten years of silence has to cost something, or the
        test above is measuring a decay function that never fires."""
        fm = {"created": "2016-05-28", "updated": "2016-12-11"}
        score = lifecycle.compute_decay_score(
            self.root, "lesson", fm, "Agent/memory/semantic/lesson.md", now="2026-08-20")
        self.assertLess(score, 1.0)

    def test_the_stepped_curve_exempts_it_too(self):
        fm = {"created": "2016-05-28", "updated": "2016-12-11"}
        score = lifecycle.compute_decay_score_stepped(
            self.root, "lesson", fm, "Personal/Church/lesson.md", now="2026-08-20")
        self.assertEqual(score, 1.0)


class FallsOpenWithoutAContract(_Base):
    """A validator that flagged everything because it could not read the rules
    would be worse than one that flagged nothing."""

    def test_validation_does_not_crash_without_a_contract(self):
        saved = storage_rules.DAEMON_BIN
        storage_rules.DAEMON_BIN = str(self.root / "no-such-binary")
        storage_rules._CACHE = None
        try:
            note = self.write("Personal/Church/lesson.md", PERSONAL_NOTE)
            # Not exempt without a contract to say so — but it must answer rather
            # than raise, because the caller is a lint pass and not a filing one.
            fv.validate(note, vault=self.root)
        finally:
            storage_rules.DAEMON_BIN = saved
            storage_rules._CACHE = None


if __name__ == "__main__":
    unittest.main()
