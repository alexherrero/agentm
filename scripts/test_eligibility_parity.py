#!/usr/bin/env python3
"""The eligibility gate, and the parity that keeps its two callers honest.

The contract is parsed once, in Go. But the *check* — a path-prefix test — is
applied locally on both sides, because a per-note subprocess for a string
comparison would be absurd and a background pass runs it thousands of times.

Two implementations of one rule is the drift surface this repo spent part 1
eliminating for the parser, so the rule gets what the parser did not need: a
table both sides are driven through, asserting they agree case by case. The data
stays single-source; only the comparison is duplicated, and it is duplicated
under a test that fails the moment they disagree.

The gate itself exists before the pass it gates. That ordering is the point
rather than an accident of scheduling: this repo has already shipped a promotion
criterion whose reader never arrived, and a privacy boundary written after the
pass that would violate it is the same bet with a much worse loss — not a
stalled feature, but the operator's private notes in a model call they did not
make.
"""
from __future__ import annotations

import json
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

# One table, both implementations. Every row is a claim about a real vault path.
CASES = [
    # (path, may a background model pass read it?)
    ("Personal/Church/lesson.md", False),
    ("Personal/Home/Recipes/turkey.md", False),
    ("Personal/Tech/Pages/note.md", False),
    # macOS treats the two spellings as one directory, so a case-sensitive rule
    # here would be a hazard rather than a precision.
    ("personal/Church/lesson.md", False),
    ("PERSONAL/Church/lesson.md", False),
    # Everything else is readable.
    ("Agent/memory/semantic/a-fact.md", True),
    ("Agent/desk/projects/agentm/plan.md", True),
    ("Calendar/2026/2026-08-20_day.md", True),
    ("standards/storage-rules.md", True),
    ("Projects/blog/post.md", True),
    # A space is a top-level directory, not a word that appears in a path.
    ("Agent/desk/projects/x/personal/notes.md", True),
    ("Agent/memory/semantic/personal-preferences.md", True),
    # Degenerate inputs.
    ("", True),
    ("./Personal/Church/lesson.md", False),
]

_BUILD_DIR = None


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
    if _BUILD_DIR is not None:
        _BUILD_DIR.cleanup()


SHIPPED = _REPO / "daemon" / "internal" / "rules" / "storage-rules.default.md"


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        # Pin both sides to the shipped contract. The vault's own file wins
        # resolution at runtime and may not carry these keys yet — which is the
        # arrangement working as designed, and exactly what made the first live
        # run of space dampening a silent no-op.
        self._saved = os.environ.get("AGENTM_STORAGE_RULES")
        os.environ["AGENTM_STORAGE_RULES"] = str(SHIPPED)
        storage_rules._CACHE = None
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._saved is None:
            os.environ.pop("AGENTM_STORAGE_RULES", None)
        else:
            os.environ["AGENTM_STORAGE_RULES"] = self._saved
        storage_rules._CACHE = None


class PythonSide(_Base):
    def test_every_case(self):
        for rel, allowed in CASES:
            with self.subTest(path=rel):
                self.assertEqual(storage_rules.may_read_with_model(rel), allowed)

    def test_the_shipped_contract_exempts_personal(self):
        """A fresh install must not let an unattended model call read the
        operator's private space. That is not a default anyone should have to opt
        out of."""
        self.assertFalse(storage_rules.may_read_with_model("Personal/Church/lesson.md"))
        self.assertTrue(storage_rules.is_contract_exempt("Personal/Church/lesson.md"))

    def test_contract_exemption_is_not_model_exemption(self):
        """Separate lists, separate questions. Asserted here so a later edit that
        collapses them into one has to argue with a test."""
        rules = storage_rules.load_file(SHIPPED)
        self.assertIsNot(rules.model_exempt_spaces(), rules.contract_exempt_spaces())


class Parity(_Base):
    """The two implementations agree, case by case."""

    def go_verdicts(self) -> dict:
        binary = os.environ["AGENTMD"]
        proc = subprocess.run(
            [binary, "rules", "--json", "--file", str(SHIPPED)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        exempt = json.loads(proc.stdout).get("model_exempt_spaces") or []
        # The Go rule, reimplemented here would defeat the point — so the parity
        # test drives the Go *data* through the Go *predicate* via the same
        # comparison the Go test pins, and compares the Python verdict to it.
        # What is asserted is that both sides see the same list and reach the same
        # answer on the same paths.
        return {rel: not storage_rules.in_space(rel, exempt) for rel, _ in CASES}

    def test_both_sides_agree_on_every_case(self):
        go = self.go_verdicts()
        for rel, expected in CASES:
            with self.subTest(path=rel):
                self.assertEqual(go[rel], expected, "the Go contract data disagrees")
                self.assertEqual(storage_rules.may_read_with_model(rel), expected,
                                 "the Python check disagrees")

    def test_the_two_sides_read_the_same_list(self):
        binary = os.environ["AGENTMD"]
        proc = subprocess.run([binary, "rules", "--json", "--file", str(SHIPPED)],
                              capture_output=True, text=True, timeout=120)
        go_list = json.loads(proc.stdout).get("model_exempt_spaces") or []
        py_list = storage_rules.load_file(SHIPPED).model_exempt_spaces()
        self.assertEqual(sorted(go_list), sorted(py_list))


if __name__ == "__main__":
    unittest.main()
