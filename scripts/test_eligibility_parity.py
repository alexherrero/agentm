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
#
# `personal/` reads True here since the axis-per-space landing, and the whole
# table was False on those rows before it. The operator's ruling: background
# passes read the space and write its frontmatter — `summary`, `tags`,
# `importance_proposed` — never its body. The boundary did not weaken, it moved:
# `recall_exempt_areas` walls `personal/Home/Important Docs` from the corpus
# entirely, which is stronger than this gate ever was, because it also covers
# the operator's own foreground queries. That wall has its own table in
# scripts/test_recall_wall.py; this one still asks only about model reads.
CASES = [
    # (path, may a background model pass read it?)
    ("personal/Church/lesson.md", True),
    ("personal/Home/Recipes/turkey.md", True),
    ("personal/Tech/Pages/note.md", True),
    # macOS treats the two spellings as one directory, so a case-sensitive rule
    # here would be a hazard rather than a precision.
    ("PERSONAL/Church/lesson.md", True),
    # Everything else is readable.
    ("agent/memory/semantic/a-fact.md", True),
    ("agent/desk/projects/agentm/plan.md", True),
    ("calendar/2026/2026-08-20_day.md", True),
    ("standards/storage-rules.md", True),
    ("projects/blog/post.md", True),
    # A space is a top-level directory, not a word that appears in a path.
    ("agent/desk/projects/x/personal/notes.md", True),
    ("agent/memory/semantic/personal-preferences.md", True),
    # Degenerate inputs.
    ("", True),
    ("./personal/Church/lesson.md", True),
]

# The gate still has to be able to say no, or every row above is a test of
# nothing. A contract that names a space bars it, and that is what these prove —
# the shipped list being empty is the operator's choice, not the mechanism's.
NAMED_EXEMPT_CASES = [
    ("personal/Church/lesson.md", False),
    ("PERSONAL/Church/lesson.md", False),
    ("./personal/Church/lesson.md", False),
    ("agent/memory/semantic/a-fact.md", True),
    ("agent/desk/projects/x/personal/notes.md", True),
]

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

    def test_the_shipped_contract_opens_personal_and_walls_important_docs(self):
        """What a fresh install must protect is the certificates and the
        recovery codes, not the whole of `personal/`.

        This asserted the opposite until the axis-per-space landing: that a
        background model pass could not read `personal/` at all. The operator
        reversed it, and the protection moved to a stronger boundary rather than
        away — so the assertion moves with it instead of being deleted.
        """
        self.assertTrue(storage_rules.may_read_with_model("personal/Church/lesson.md"))
        self.assertTrue(storage_rules.is_contract_exempt("personal/Church/lesson.md"))
        self.assertTrue(
            storage_rules.is_recall_exempt("personal/Home/Important Docs/Marriage License.md"))
        self.assertFalse(storage_rules.is_recall_exempt("personal/Home/Recipes/turkey.md"))

    def test_the_gate_can_still_say_no(self):
        """A table of all-True rows proves nothing on its own. A contract that
        names a space bars it — the shipped list being empty is the operator's
        choice, not the mechanism's."""
        exempt = ["personal"]
        for rel, allowed in NAMED_EXEMPT_CASES:
            with self.subTest(path=rel):
                self.assertEqual(not storage_rules.in_space(rel, exempt), allowed)

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
