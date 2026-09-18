#!/usr/bin/env python3
"""One decay curve, two arms, and the test that proves it is one curve.

The daemon ranks what a session actually queries; `recall.py`'s in-process
engine ranks when the daemon is absent or slow. Before the axis-per-space
landing they disagreed by construction: the Go arm ran stepped bands written as
constants (full strength to 182 days), the Python arm ran a thirty-day
exponential, and the contract's own five `decay_*` lines were read by neither.
The same note, the same age, two different answers depending on which half of
the system happened to answer.

Now both read the contract. That is only a claim until something runs both, so
this drives one table of ages through the shipped Go binary (`agentmd decay`)
and through `lifecycle.compute_decay_score`, against the same contract file, and
compares the numbers.

This is deliberately not the shape of the older `test_eligibility_parity.py`,
whose own comment admits it compares the Go *data* through a Python
*reimplementation* of the Go rule. Here the Go implementation runs.
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

import lifecycle  # noqa: E402
import storage_rules  # noqa: E402

SHIPPED = _REPO / "daemon" / "internal" / "rules" / "storage-rules.default.md"

# Every age the part file names, plus the day on each side of every band edge —
# an off-by-one in either arm is the failure this is most likely to catch, and a
# table that only sampled the middle of each band would not see it.
DAYS = [
    0, 1,
    179, 180, 181,
    364, 365, 366,
    1094, 1095, 1096,
    1824, 1825, 1826,
    3650, 36500,
]

_BUILD_DIR = None
_ORIGINAL_DAEMON_BIN = storage_rules.DAEMON_BIN


def _can_answer(binary: str) -> bool:
    """Whether `binary` is a daemon that carries the verb this module reads.

    The question is capability, not existence: an installed binary from before
    this landing exists, runs, and answers `unknown subcommand "decay"`.
    """
    if not binary:
        return False
    if not (Path(binary).exists() or shutil.which(binary)):
        return False
    try:
        proc = subprocess.run([binary, "decay", "--json"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def setUpModule() -> None:
    """Build a binary from this tree unless the one already named can answer.

    `$AGENTMD` is probed for the verb this module needs rather than checked for
    being set, and that is a real failure this caught rather than defensiveness.
    Standalone the suite passed; inside the battery all four parity tests failed
    against a binary with no `decay` verb at all.

    Two ways that happens, both cross-module. Half a dozen modules here build a
    temp binary, export `$AGENTMD` and delete the directory in their own
    teardown, and every one of them takes an `if os.environ.get("AGENTMD"):
    return` early exit — so a later module can inherit a path to a binary that
    no longer exists. Worse, `test_corpus_migration_3.py` sets `$AGENTMD` to
    `shutil.which("agentmd")`, the *installed* binary, and never clears it: from
    that point on, every module that takes the early exit is testing whatever is
    deployed on the machine rather than the code in the tree. A contract-shape
    test survives that; a test of new Go behaviour cannot, and it fails in a way
    that reads like the new code being broken.
    """
    global _BUILD_DIR
    if _can_answer(os.environ.get("AGENTMD", "").strip()):
        # Point the contract reader at it too — see the note in
        # test_eligibility_parity.setUpModule for what taking this exit without
        # that leaves behind.
        storage_rules.DAEMON_BIN = os.environ["AGENTMD"].strip()
        storage_rules._CACHE = None
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
    """Undo what this module set, not just the directory — a `$AGENTMD` left
    pointing into a deleted temp dir is what made a full discover run fail once,
    because every later module takes its own early exit and then shells out to a
    binary that is no longer there."""
    global _BUILD_DIR
    if _BUILD_DIR is None:
        return
    _BUILD_DIR.cleanup()
    _BUILD_DIR = None
    os.environ.pop("AGENTMD", None)
    storage_rules.DAEMON_BIN = _ORIGINAL_DAEMON_BIN
    storage_rules._CACHE = None


class _Base(unittest.TestCase):
    """Pin both arms to one contract file. The vault's own copy wins resolution
    at runtime and may not carry these lines yet — which is the arrangement
    working as designed, and exactly what made the first live run of space
    dampening a silent no-op."""

    contract = SHIPPED

    def setUp(self) -> None:
        self._saved = os.environ.get("AGENTM_STORAGE_RULES")
        os.environ["AGENTM_STORAGE_RULES"] = str(self.contract)
        storage_rules._CACHE = None
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._saved is None:
            os.environ.pop("AGENTM_STORAGE_RULES", None)
        else:
            os.environ["AGENTM_STORAGE_RULES"] = self._saved
        storage_rules._CACHE = None

    def go_scores(self) -> dict:
        binary = os.environ["AGENTMD"]
        proc = subprocess.run(
            [binary, "decay", "--file", str(self.contract), "--json",
             "--days", ",".join(str(d) for d in DAYS)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        return {int(row["days"]): row["score"] for row in report["days"]}

    def python_scores(self) -> dict:
        # The same function recall.py's ranking calls, through its own contract
        # read — not a local copy of the bands.
        return {d: lifecycle._score_from_bands(float(d)) for d in DAYS}


class Parity(_Base):

    def test_both_arms_score_every_age_identically(self):
        go = self.go_scores()
        py = self.python_scores()
        for d in DAYS:
            with self.subTest(days=d):
                self.assertEqual(go[d], py[d],
                                 f"the two arms disagree at {d} days: "
                                 f"Go {go[d]}, Python {py[d]}")

    def test_the_four_ages_the_part_file_names(self):
        """Verbatim from the part file's criteria: days 0, 180, 365 and 1,095."""
        go = self.go_scores()
        py = self.python_scores()
        for d in (0, 180, 365, 1095):
            self.assertEqual(go[d], py[d])
        # And they are the shipped contract's own bands, not a coincidence of
        # two defaults: 180 is the contract's number where the Go constant said
        # 182, so this row is the one that would have differed before.
        self.assertEqual(go[180], 1.0)
        self.assertEqual(go[181], 0.5)

    def test_both_arms_read_the_same_bands(self):
        binary = os.environ["AGENTMD"]
        proc = subprocess.run([binary, "decay", "--file", str(self.contract), "--json"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        go_bands = [(b["days"], b["score"]) for b in json.loads(proc.stdout)["bands"]]
        py_bands = [(float(d), s) for d, s in lifecycle.decay_bands()]
        self.assertEqual(go_bands, py_bands)


class ParityOnAnOperatorsOwnNumbers(_Base):
    """The thresholds are the operator's, so the parity has to hold on numbers
    nobody compiled in. A contract with bands at 10/20/30/40 is not a realistic
    vault — it is the check that neither arm is quietly using its defaults."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="contract-")
        self.addCleanup(self._tmp.cleanup)
        text = SHIPPED.read_text(encoding="utf-8")
        for key, was, now in (
            ("decay_full_days", "180", "10"),
            ("decay_half_days", "365", "20"),
            ("decay_eighth_days", "1095", "30"),
            ("decay_floor_days", "1825", "40"),
            ("decay_floor_weight", "0.0625", "0.5"),
        ):
            before = f"  {key}: {was}\n"
            after = f"  {key}: {now}\n"
            self.assertIn(before, text, f"the shipped contract no longer spells {key} this way")
            text = text.replace(before, after, 1)
        self.contract = Path(self._tmp.name) / "storage-rules.md"
        self.contract.write_text(text, encoding="utf-8")
        super().setUp()

    def test_both_arms_follow_the_contract_rather_than_their_defaults(self):
        go = self.go_scores()
        py = self.python_scores()
        for d in DAYS:
            with self.subTest(days=d):
                self.assertEqual(go[d], py[d])
        self.assertEqual(go[1], 1.0)
        self.assertEqual(go[179], 0.5, "an arm still bands at its compiled-in 180/182")
        self.assertEqual(go[36500], 0.5, "the floor weight is the contract's 0.5")


if __name__ == "__main__":
    unittest.main()
