#!/usr/bin/env python3
"""Unit tests for scripts/vault_probe.py (v4.5.2 installer-probe bugfix).

Covers the marker-ranking + nested-vault-refinement logic that fixes the bug
where the installer's first-run vault detection picked the parent Obsidian
app-vault instead of the nested AgentMemory subfolder.

Run directly:

    python3 scripts/test_vault_probe.py

Discovered by CI via `(cd scripts && python3 -m unittest discover -p 'test_*.py')`.
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vault_probe as vp  # noqa: E402


# Operator's real layout (the bug): the repos.json marker is 2 levels below the
# Obsidian root, which itself carries the .obsidian marker.
_OBSIDIAN = "/Users/x/Library/CloudStorage/GoogleDrive-y/.shortcut-targets-by-id/ID/Obsidian"
_AGENTMEM = _OBSIDIAN + "/AgentMemory"


# -----------------------------------------------------------------------------
# rank_candidates — pure path logic
# -----------------------------------------------------------------------------

class TestRankCandidates(unittest.TestCase):

    def test_operator_scenario_suppresses_obsidian_parent_of_repos(self) -> None:
        # If BOTH markers are found (repos.json reachable), the .obsidian parent
        # that wraps the repos root is suppressed; only the repos root survives.
        markers = [
            _OBSIDIAN + "/.obsidian",
            _AGENTMEM + "/_meta/repos.json",
        ]
        ranked = vp.rank_candidates(markers)
        self.assertEqual(ranked, [{"root": _AGENTMEM, "kind": "repos"}])

    def test_repos_ranked_before_unrelated_obsidian(self) -> None:
        markers = [
            "/vaults/some-obsidian/.obsidian",      # unrelated, not an ancestor
            "/vaults/mem/_meta/repos.json",
        ]
        ranked = vp.rank_candidates(markers)
        self.assertEqual(ranked, [
            {"root": "/vaults/mem", "kind": "repos"},
            {"root": "/vaults/some-obsidian", "kind": "obsidian"},
        ])

    def test_only_obsidian_marker_returns_obsidian_root(self) -> None:
        # The operator's shallow-find reality: repos.json is too deep, only the
        # .obsidian hit survives the find. rank keeps it (refine descends later).
        ranked = vp.rank_candidates([_OBSIDIAN + "/.obsidian"])
        self.assertEqual(ranked, [{"root": _OBSIDIAN, "kind": "obsidian"}])

    def test_only_repos_marker(self) -> None:
        ranked = vp.rank_candidates(["/v/mem/_meta/repos.json"])
        self.assertEqual(ranked, [{"root": "/v/mem", "kind": "repos"}])

    def test_dedup_same_root_from_repeat_markers(self) -> None:
        ranked = vp.rank_candidates([
            "/v/mem/_meta/repos.json",
            "/v/mem/_meta/repos.json",
        ])
        self.assertEqual(ranked, [{"root": "/v/mem", "kind": "repos"}])

    def test_obsidian_equal_to_repos_root_is_suppressed(self) -> None:
        # A vault that is BOTH an Obsidian vault AND a MemoryVault at the same
        # dir → keep it once, as repos (authoritative), not duplicated.
        markers = ["/v/mem/.obsidian", "/v/mem/_meta/repos.json"]
        ranked = vp.rank_candidates(markers)
        self.assertEqual(ranked, [{"root": "/v/mem", "kind": "repos"}])

    def test_unrecognized_and_blank_markers_ignored(self) -> None:
        ranked = vp.rank_candidates(["", "  ", "/random/file.txt", "/v/.git"])
        self.assertEqual(ranked, [])

    def test_multiple_obsidian_none_repos_preserves_order(self) -> None:
        ranked = vp.rank_candidates(["/a/.obsidian", "/b/.obsidian"])
        self.assertEqual([c["root"] for c in ranked], ["/a", "/b"])


# -----------------------------------------------------------------------------
# find_nested_vault — filesystem refinement
# -----------------------------------------------------------------------------

class TestFindNestedVault(unittest.TestCase):

    def _mk_vault_shape(self, base: Path, *, repos: bool = True) -> None:
        if repos:
            (base / "_meta").mkdir(parents=True, exist_ok=True)
            (base / "_meta" / "repos.json").write_text("{}", encoding="utf-8")
        (base / "personal").mkdir(parents=True, exist_ok=True)

    def test_root_itself_has_shape_returns_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "mem"
            self._mk_vault_shape(root)
            self.assertEqual(vp.find_nested_vault(str(root)), str(root))

    def test_obsidian_wrapper_descends_to_single_nested_child(self) -> None:
        # The operator's exact case: .../Obsidian (no shape) wraps AgentMemory.
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "Obsidian"
            (obs / ".obsidian").mkdir(parents=True)
            # noise siblings without vault shape
            (obs / "Church").mkdir()
            (obs / "Home").mkdir()
            agentmem = obs / "AgentMemory"
            self._mk_vault_shape(agentmem)
            self.assertEqual(vp.find_nested_vault(str(obs)), str(agentmem))

    def test_personal_private_only_counts_as_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "Obsidian"
            obs.mkdir()
            child = obs / "AgentMemory"
            child.mkdir()
            (child / "personal").mkdir()  # shape via personal only
            self.assertEqual(vp.find_nested_vault(str(obs)), str(child))

    def test_no_shape_anywhere_returns_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "Obsidian"
            (obs / "Notes").mkdir(parents=True)
            self.assertEqual(vp.find_nested_vault(str(obs)), str(obs))

    def test_ambiguous_multiple_nested_vaults_returns_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "Obsidian"
            obs.mkdir()
            self._mk_vault_shape(obs / "VaultA")
            self._mk_vault_shape(obs / "VaultB")
            # Two candidates → ambiguous → don't guess; return the root as-is.
            self.assertEqual(vp.find_nested_vault(str(obs)), str(obs))

    def test_nonexistent_path_returns_root(self) -> None:
        self.assertEqual(vp.find_nested_vault("/no/such/dir/xyz"), "/no/such/dir/xyz")


# -----------------------------------------------------------------------------
# End-to-end: the operator's bug, reproduced + fixed
# -----------------------------------------------------------------------------

class TestOperatorBugEndToEnd(unittest.TestCase):

    def test_shallow_obsidian_hit_refines_to_nested_agentmemory(self) -> None:
        # Simulate what the installer now does: the find only surfaced the
        # .obsidian marker (repos.json was too deep). rank → .obsidian root;
        # refine → descend to the AgentMemory subfolder.
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "Obsidian"
            (obs / ".obsidian").mkdir(parents=True)
            (obs / "Church").mkdir()
            agentmem = obs / "AgentMemory"
            (agentmem / "_meta").mkdir(parents=True)
            (agentmem / "_meta" / "repos.json").write_text("{}", encoding="utf-8")

            # rank_candidates parses macOS `find` output (POSIX paths), so feed
            # POSIX form via as_posix() — on Windows CI, str(Path) uses
            # backslashes which PurePosixPath (correctly) wouldn't split.
            ranked = vp.rank_candidates([(obs / ".obsidian").as_posix()])
            self.assertEqual(ranked, [{"root": obs.as_posix(), "kind": "obsidian"}])
            refined = vp.find_nested_vault(ranked[0]["root"])
            self.assertEqual(refined, str(agentmem))  # the correct nested dir (was missed pre-v4.5.2)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

class TestCLI(unittest.TestCase):

    def _run(self, argv, stdin=""):
        out = io.StringIO()
        with mock_stdin(stdin), contextlib.redirect_stdout(out):
            rc = vp.main(argv)
        return rc, out.getvalue()

    def test_rank_cli_reads_stdin_emits_roots(self) -> None:
        stdin = _OBSIDIAN + "/.obsidian\n" + _AGENTMEM + "/_meta/repos.json\n"
        rc, out = self._run(["--rank"], stdin=stdin)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), _AGENTMEM)

    def test_rank_show_kind(self) -> None:
        rc, out = self._run(["--rank", "--show-kind"], stdin="/v/mem/_meta/repos.json\n")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "repos\t/v/mem")

    def test_refine_cli_returns_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "Obsidian"
            obs.mkdir()
            child = obs / "AgentMemory"
            (child / "_meta").mkdir(parents=True)
            (child / "_meta" / "repos.json").write_text("{}", encoding="utf-8")
            rc, out = self._run(["--refine", str(obs)])
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), str(child))


@contextlib.contextmanager
def mock_stdin(text: str):
    saved = sys.stdin
    sys.stdin = io.StringIO(text)
    try:
        yield
    finally:
        sys.stdin = saved


if __name__ == "__main__":
    unittest.main()
