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

    def test_the_always_load_fallback_matches_the_shipped_contract(self):
        """Same pin on the fourth list. It fails *open* rather than closed —
        missing it costs a duplicate in the window, where missing the wall
        above costs a recovery code in a prompt — but a fallback that has
        drifted from the file it mirrors is wrong in either direction."""
        shipped = tuple(storage_rules.load_file(SHIPPED).always_load_areas())
        self.assertEqual(shipped, storage_rules._FALLBACK_ALWAYS_LOAD_AREAS)

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
        # `--vault` on a command that never opens the vault, because the
        # command still resolves one before it runs, and a machine with no
        # configured vault — every CI runner — cannot answer. The fixture
        # directory satisfies the resolver and the classification is of the
        # path string, so what is on disk under it is beside the point.
        proc = subprocess.run([binary, "classify", "--path", rel, "--json",
                               "--vault", str(self.vault)],
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


class TheAlwaysLoadExclusion(_Base):
    """`always_load_areas`: the tier the session already read, kept out of the
    ranked answer without being kept out of the corpus.

    The distinction from the wall above is the whole design. A walled area is
    never indexed; this one is indexed, embedded and findable by name on
    purpose, because Drive-side surfaces search by title and `agentmd search`
    for a rule should answer. Only the ranked arms drop it.
    """

    def test_a_loaded_standards_file_is_in_the_set_and_a_memory_note_is_not(self):
        self.assertTrue(storage_rules.is_always_load_area("standards/storage-rules.md"))
        self.assertTrue(storage_rules.is_always_load_area("standards/user-preferences.md"))
        self.assertFalse(storage_rules.is_always_load_area("agent/memory/semantic/a-fact.md"))

    def test_a_word_inside_a_name_is_not_the_name(self):
        """The same rule the archive segments follow: an area is a directory,
        so a file that merely starts with the word is not in it."""
        self.assertFalse(storage_rules.is_always_load_area("standards-draft/x.md"))
        self.assertFalse(storage_rules.is_always_load_area("projects/agentm/standards.md"))

    def test_a_subdirectory_the_loader_never_reads_stays_in_recall(self):
        """The rule this list got wrong first, and why it is non-recursive.

        The loader's glob is `<area>/*.md` — `standards/voice/` has never been
        injected, so excluding it from recall too makes the operator's voice
        library unreachable from every memory surface at once. Four gold
        questions went to a miss and the retrieval gate refused the change.
        """
        self.assertTrue(storage_rules.is_always_load_area("standards/storage-rules.md"))
        self.assertFalse(
            storage_rules.is_always_load_area("standards/voice/design-doc-prose.md"),
            "the voice library left recall; the loader never injected it")

    def test_a_generated_map_stays_in_recall(self):
        """Same reasoning: the loader skips a `moc-` map, so a session never
        has one, so recall has to keep answering with it."""
        self.assertFalse(storage_rules.is_always_load_area("standards/moc-standards.md"))

    def test_the_hook_reads_the_contract_only_for_a_binary_that_cannot(self):
        """The cost gate on the belt-and-braces path.

        The daemon does this drop itself, and reaching for the contract anyway
        would mean an `agentmd rules --json` subprocess on every prompt, inside
        the interactive budget, to re-check work already done. A current daemon
        always emits `always_load_hidden`, so its presence is the signal. The
        fallback still exists for the window a contract line is live and the
        binary that reads it is not — the ordinary state on the day of a
        landing, and the day a session would otherwise get the contract twice.
        """
        import subprocess as sp
        from unittest import mock

        def run_with(payload: dict):
            seen = []

            def fake(argv, **_kw):
                seen.append(list(argv))
                return sp.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

            with mock.patch.object(recall.subprocess, "run", side_effect=fake):
                recall._daemon_search(vault=self.vault, query_text="filing rules",
                                      k=5, drops={})
            return seen

        current = run_with({"results": [], "always_load_hidden": 0})
        self.assertEqual(len(current), 1,
                         "a current daemon's response made the hook read the "
                         f"contract as well: {current}")

        old = run_with({"results": []})
        self.assertEqual(len(old), 2,
                         "a binary too old to know the key left the hook "
                         f"trusting it anyway: {old}")
        self.assertIn("rules", old[1])

    def go_always_load(self, rel: str) -> bool:
        binary = os.environ["AGENTMD"]
        env = dict(os.environ, AGENTM_STORAGE_RULES=str(SHIPPED))
        proc = subprocess.run([binary, "classify", "--path", rel, "--json",
                               "--vault", str(self.vault)],
                              capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return bool(json.loads(proc.stdout)["always_load"])

    def test_the_two_arms_agree_on_every_row(self):
        """Two implementations of one path rule, run rather than reasoned about.
        The Python half is what runs with no daemon; the Go half is what runs
        with one, and a session cannot tell which answered it."""
        rows = [
            ("standards/storage-rules.md", True),
            ("standards/user-preferences.md", True),
            ("standards/voice/design-doc-prose.md", False),
            ("standards/moc-standards.md", False),
            ("standards-draft/proposal.md", False),
            ("agent/memory/semantic/a-fact.md", False),
            ("standards.md", False),
        ]
        areas = ["standards"]
        for rel, want in rows:
            with self.subTest(path=rel):
                self.assertEqual(
                    storage_rules.in_always_load_set(rel, areas), want,
                    "the Python arm disagrees")
                self.assertEqual(self.go_always_load(rel), want,
                                 "the Go arm disagrees")

    def test_it_is_not_a_wall(self):
        """The one property that separates the fourth list from the first: the
        walk still reads these files, so they stay in the index. A test that
        only checked the drop would pass with the files walled out entirely,
        which is the change this must not become."""
        self.assertFalse(storage_rules.is_recall_exempt("standards/storage-rules.md"))

    def test_the_prompt_arm_drops_a_standards_hit_on_a_flat_vault(self):
        areas = recall._always_load_areas()
        self.assertTrue(areas, "the contract names no always-load area")
        # This fixture is flat, so the memory root and the vault root are one
        # directory and a standards file keys without a `../`.
        self.assertTrue(
            recall._in_always_load_area("standards/storage-rules.md", self.vault, areas))
        self.assertFalse(
            recall._in_always_load_area("memory/semantic/a-fact.md", self.vault, areas))

    def test_the_prompt_arm_drops_it_on_a_nested_vault_too(self):
        """The two-bases case, which is the shipped layout and the one that has
        silently matched nothing four times in this codebase.

        Recall keys are memory-root-relative, so a vault-root file arrives as
        `../standards/x.md`, while the contract's areas are written from the
        vault root. A rule compared against the wrong base matches nothing and
        says nothing — and the symptom would be the contract quietly coming
        back in recall again, which is the bug this list was added to fix.
        """
        areas = recall._always_load_areas()
        root = Path(self._tmp.name) / "nested"
        (root / "standards").mkdir(parents=True)
        (root / "standards" / "storage-rules.md").write_text("x", encoding="utf-8")
        memory_root = root / "agent"
        (memory_root / "memory" / "semantic").mkdir(parents=True)
        self.assertTrue(
            recall._in_always_load_area("../standards/storage-rules.md", memory_root, areas),
            "a memory-root-relative key for a vault-root file did not resolve")
        self.assertFalse(
            recall._in_always_load_area("memory/semantic/a-fact.md", memory_root, areas))

    def test_an_empty_area_list_drops_nothing(self):
        """Fails open, deliberately, and this is the pin on that direction: an
        empty list must not read as `match everything` and blank the answer."""
        self.assertFalse(
            recall._in_always_load_area("standards/storage-rules.md", self.vault, []))

    def test_the_go_arm_declares_the_same_rule(self):
        """Two implementations of one path rule is a drift surface. The Python
        half is checked above; this is the pin that the Go half exists and is
        wired into the ranked path rather than declared and forgotten."""
        space = (_REPO / "daemon" / "internal" / "note" / "space.go").read_text()
        self.assertIn("func InAlwaysLoadArea(", space)
        self.assertIn("func SetAlwaysLoadAreas(", space)
        search = (_REPO / "daemon" / "internal" / "index" / "search.go").read_text()
        self.assertIn("note.InAlwaysLoadArea(r.Path)", search)
        cfg = (_REPO / "daemon" / "internal" / "config" / "config.go").read_text()
        self.assertIn("note.SetAlwaysLoadAreas(loaded.AlwaysLoadAreas)", cfg)


if __name__ == "__main__":
    unittest.main()
