#!/usr/bin/env python3
"""The wall, and the dampens, in the arm that runs when the daemon does not.

`recall_exempt_areas` is the one rule in the filing contract that removes a note
from the corpus rather than ranking it: never indexed, never embedded, never
served to any surface. The folder it was written for holds birth certificates, a
marriage licence and a file named `Recovery Codes`, and before this landing they
sat in the index where a query that happened to match them was served at x0.30
into whatever asked.

The Go arm has its own tests for the index, the notifier and the search path.
This file covers the half that runs when the daemon is absent — which is also
the state in which the contract is hardest to read, since reading it means
shelling out to the daemon that is missing.

It also drives one table of paths through *both* arms, because two
implementations of a path rule is a drift surface and the only honest way to
check two implementations is to run both.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import recall  # noqa: E402
import storage_rules  # noqa: E402

SHIPPED = _REPO / "daemon" / "internal" / "rules" / "storage-rules.default.md"
WALLED = "personal/Home/Important Docs"

_BUILD_DIR = None
_ORIGINAL_DAEMON_BIN = storage_rules.DAEMON_BIN


def _can_answer(binary: str) -> bool:
    """Whether `binary` carries the verb this module reads. Capability, not
    existence: an installed binary from before this landing runs fine and
    answers `flag provided but not defined: -path`."""
    if not binary:
        return False
    if not (Path(binary).exists() or shutil.which(binary)):
        return False
    try:
        proc = subprocess.run([binary, "classify", "--path", "a/b.md", "--json"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def setUpModule() -> None:
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
    global _BUILD_DIR
    if _BUILD_DIR is None:
        return
    _BUILD_DIR.cleanup()
    _BUILD_DIR = None
    os.environ.pop("AGENTMD", None)
    storage_rules.DAEMON_BIN = _ORIGINAL_DAEMON_BIN
    storage_rules._CACHE = None


class _Base(unittest.TestCase):
    """Both arms pinned to the shipped contract, and a flat fixture vault.

    Flat on purpose: the memory root and the vault root are the same directory
    there, which is the layout in which `personal/` is inside the walk at all.
    The wall has to hold in the layout that can reach it.
    """

    def setUp(self) -> None:
        self._saved = os.environ.get("AGENTM_STORAGE_RULES")
        os.environ["AGENTM_STORAGE_RULES"] = str(SHIPPED)
        storage_rules._CACHE = None
        self.addCleanup(self._restore)

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir(parents=True)

    def _restore(self) -> None:
        if self._saved is None:
            os.environ.pop("AGENTM_STORAGE_RULES", None)
        else:
            os.environ["AGENTM_STORAGE_RULES"] = self._saved
        storage_rules._CACHE = None

    def write(self, rel: str, body: str = "the recovery codes are 111111") -> None:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\nkind: note\nslug: {Path(rel).stem}\n---\n{body}\n",
                     encoding="utf-8")


class TheWalk(_Base):

    def test_a_walled_note_never_enters_the_candidate_set(self):
        self.write(f"{WALLED}/Recovery Codes.md")
        self.write("personal/Home/Recipes/turkey.md", "the recovery time for the brine")
        paths = {p.name for p in recall._iter_entry_paths(self.vault)}
        self.assertNotIn("Recovery Codes.md", paths)
        self.assertIn("turkey.md", paths)

    def test_the_near_miss_is_not_swallowed(self):
        """`personal/Homework` is not inside `personal/Home`. A wall with a
        near-miss in it is worse than no wall, because it reads as one."""
        self.write("personal/Homework/algebra.md", "recovery of the constant term")
        paths = {p.name for p in recall._iter_entry_paths(self.vault)}
        self.assertIn("algebra.md", paths)

    def test_no_flag_reaches_the_wall(self):
        self.write(f"{WALLED}/Recovery Codes.md")
        for kwargs in ({}, {"include_inbox": True}, {"include_archive": True},
                       {"include_inbox": True, "include_archive": True}):
            with self.subTest(**kwargs):
                paths = {p.name for p in recall._iter_entry_paths(self.vault, **kwargs)}
                self.assertNotIn("Recovery Codes.md", paths)


class TheQuery(_Base):

    def test_a_query_that_matches_it_is_answered_without_it(self):
        self.write(f"{WALLED}/Recovery Codes.md", "widget subsystem recovery codes")
        self.write("memory/reference/live.md", "widget subsystem retry logic")
        results = recall.query(vault=self.vault, query_text="widget subsystem", k=5)
        paths = [r["path"] for r in results]
        self.assertIn("memory/reference/live.md", paths)
        self.assertNotIn(f"{WALLED}/Recovery Codes.md", paths)

    def test_the_fast_path_leaves_the_wall_to_the_daemon(self):
        """Not a gap — a budget.

        Reading the contract from Python means spawning the daemon, and the
        prompt-submit path has 300ms for everything. Re-checking the wall on a
        daemon answer would spend a second process per prompt to re-derive what
        the process that just answered already enforced: the Go arm walls at the
        walk *and* again at search time by path, so a walled note cannot be in a
        daemon answer from a fresh index or a stale one.

        Pinned here so the absence reads as a decision rather than an oversight,
        and so that a future change removing the Go arm's search-time wall has
        to argue with this test.
        """
        self.assertTrue(recall._daemon_admissible(
            f"{WALLED}/Recovery Codes.md", include_inbox=True, include_archive=True))
        # And what it does still enforce, unchanged.
        self.assertFalse(recall._daemon_admissible(
            "agent/desk/scratch/prop.md", include_inbox=True, include_archive=True))


class TheFallbackWhenTheContractCannotBeRead(_Base):
    """The arm that reads this rule is the one that runs when the daemon is
    missing — which is also when the contract cannot be read, since reading it
    means shelling out to that daemon.

    Failing open there would open the wall on exactly the machine state it has
    to survive. Failing fully closed would black out the corpus. So it falls
    back to the shipped contract's own list, and this is the pin that keeps the
    mirrored copy from drifting away from the file it mirrors.
    """

    def test_the_fallback_matches_the_shipped_contract(self):
        # Read through the parser rather than with a regex over the file: the
        # thing that must not drift is what the contract *means*, and there is
        # one parser for that.
        shipped = tuple(storage_rules.load_file(SHIPPED).recall_exempt_areas())
        self.assertEqual(shipped, storage_rules._FALLBACK_RECALL_EXEMPT_AREAS)

    def test_the_wall_holds_with_no_daemon_at_all(self):
        saved = storage_rules.DAEMON_BIN
        storage_rules.DAEMON_BIN = str(self.vault / "no-such-binary")
        storage_rules._CACHE = None
        self.addCleanup(lambda: setattr(storage_rules, "DAEMON_BIN", saved))
        self.assertTrue(storage_rules.is_recall_exempt(f"{WALLED}/Recovery Codes.md"))
        # And the corpus is not blacked out to buy that.
        self.assertFalse(storage_rules.is_recall_exempt("memory/reference/live.md"))
        self.assertFalse(storage_rules.is_recall_exempt("personal/Home/Recipes/turkey.md"))


# One table of paths, both implementations. Every row is a claim about where a
# note sits and what that costs it.
CASES = [
    # (vault-relative path, walled?, the multiplier the path alone earns)
    (f"{WALLED}/Recovery Codes.md", True, 0.30),
    ("personal/Home/Recipes/turkey.md", False, 0.30),
    ("personal/Homework/algebra.md", False, 0.30),
    ("agent/diagnostics/lint/2026-09-17.md", False, 0.30),
    ("agent/memory/semantic/a-fact.md", False, 1.0),
    ("agent/archive/memory/semantic/a-fact.md", False, 0.30),
    ("projects/agentm/completed/a-brief.md", False, 0.30),
    ("projects/completed/blog/x.md", False, 0.30),
    ("projects/blog/drafts/_archive/old.md", False, 0.30),
    ("projects/agentm/decisions/a-ruling.md", False, 1.0),
    # Archival twice over is not twice as finished.
    ("projects/completed/blog/drafts/_archive/x.md", False, 0.30),
    # A dampened area holding finished work is both, and composes.
    ("personal/Home/_Archive/2019.md", False, 0.09),
    # A file is not a directory; a word inside a name is not the name.
    ("projects/agentm/completed.md", False, 1.0),
    ("projects/agentm/archived-ideas/x.md", False, 1.0),
]


class Parity(_Base):
    """The two arms agree on every row, run rather than reasoned about."""

    def go_verdict(self, rel: str) -> dict:
        binary = os.environ["AGENTMD"]
        env = dict(os.environ, AGENTM_STORAGE_RULES=str(SHIPPED))
        proc = subprocess.run([binary, "classify", "--path", rel, "--json"],
                              capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_both_arms_wall_the_same_paths(self):
        for rel, walled, _ in CASES:
            with self.subTest(path=rel):
                go = self.go_verdict(rel)
                self.assertEqual(go["recall_walled"], walled, "the Go arm disagrees")
                self.assertEqual(storage_rules.is_recall_exempt(rel), walled,
                                 "the Python arm disagrees")

    def test_both_arms_dampen_the_same_paths_by_the_same_weight(self):
        for rel, _, weight in CASES:
            with self.subTest(path=rel):
                go = self.go_verdict(rel)
                self.assertAlmostEqual(go["weight"], weight, places=6,
                                       msg="the Go arm disagrees")
                self.assertAlmostEqual(recall._area_demotion(rel, self.vault), weight,
                                       places=6, msg="the Python arm disagrees")

    def test_the_three_archive_names_are_the_same_three(self):
        """The list is duplicated across two languages, which is the cost of not
        running a subprocess per note for a string comparison. This is what
        makes the duplication safe."""
        text = (_REPO / "daemon" / "internal" / "note" / "classify.go").read_text()
        block = re.search(r'ArchiveSegments = \[\]string\{([^}]*)\}', text)
        self.assertIsNotNone(block, "the Go arm no longer declares ArchiveSegments")
        go_names = tuple(s.strip().strip('"') for s in block.group(1).split(",") if s.strip())
        self.assertEqual(go_names, recall.ARCHIVE_SEGMENTS)


if __name__ == "__main__":
    unittest.main()
