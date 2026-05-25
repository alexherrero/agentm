#!/usr/bin/env python3
"""Unit tests for scripts/vault_project.py — stdlib `unittest`, cross-platform.

Run directly:

    python3 scripts/test_vault_project.py

Or via discovery:

    python3 -m unittest scripts.test_vault_project

Covers the 5 cases enumerated in PLAN.md task 1:
    (a) explicit `vault_project` field is returned verbatim
    (b) github.repo fallback extracts `<repo>` from `<owner>/<repo>`
    (c) git remote get-url origin → strip .git → basename
    (d) graceful-skip returns None when not a git repo / no origin
    (e) write_vault_project preserves existing fields

Plus an extra test (f) covering URL-shape variety in tier-3 fallback
(https, ssh, file:, trailing slash, no .git).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Allow `from vault_project import ...` regardless of how pytest/discovery
# imports this module.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vault_project as vp  # noqa: E402


def _init_git_repo(root: Path, origin_url: str | None = None) -> None:
    """Initialize an empty git repo at `root`, optionally with an `origin` remote."""
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    # Disable signing/hooks so tests pass on any host.
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    if origin_url is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", origin_url],
            cwd=root,
            check=True,
            capture_output=True,
        )


def _write_project_json(root: Path, data: dict) -> None:
    harness = root / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "project.json").write_bytes(
        (json.dumps(data, indent=2) + "\n").encode("utf-8")
    )


class TestReadVaultProject(unittest.TestCase):
    """Tier-1, Tier-2, Tier-3, and no-signal cases of read_vault_project()."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- (a) tier-1 explicit field --------------------------------------------------
    def test_explicit_field_wins(self) -> None:
        _write_project_json(self.root, {"vault_project": "my-explicit-slug"})
        # Even with github.repo + git origin pointing elsewhere, explicit wins.
        _init_git_repo(self.root, "https://github.com/someone/other-repo.git")
        self.assertEqual(vp.read_vault_project(self.root), "my-explicit-slug")

    def test_explicit_field_strips_whitespace(self) -> None:
        _write_project_json(self.root, {"vault_project": "  trimmed-slug  "})
        self.assertEqual(vp.read_vault_project(self.root), "trimmed-slug")

    def test_explicit_field_empty_falls_through(self) -> None:
        """Empty explicit field should fall through to next tier, not return ''."""
        _write_project_json(
            self.root,
            {
                "vault_project": "   ",
                "github": {"repo": "alexherrero/harness-fallback"},
            },
        )
        self.assertEqual(vp.read_vault_project(self.root), "harness-fallback")

    # --- (b) tier-2 github.repo fallback --------------------------------------------
    def test_github_repo_fallback(self) -> None:
        _write_project_json(
            self.root, {"github": {"repo": "alexherrero/agentm"}}
        )
        self.assertEqual(vp.read_vault_project(self.root), "agentm")

    def test_github_repo_bare_basename(self) -> None:
        """If only `<repo>` (no owner/) is recorded, take it as-is."""
        _write_project_json(self.root, {"github": {"repo": "agentm"}})
        self.assertEqual(vp.read_vault_project(self.root), "agentm")

    def test_github_repo_strips_git_suffix(self) -> None:
        _write_project_json(
            self.root, {"github": {"repo": "alexherrero/agentm.git"}}
        )
        self.assertEqual(vp.read_vault_project(self.root), "agentm")

    # --- (c) tier-3 git origin fallback ---------------------------------------------
    def test_git_origin_fallback_https(self) -> None:
        # No project.json, no github.repo — should auto-detect from git origin.
        _init_git_repo(self.root, "https://github.com/alexherrero/agentm.git")
        self.assertEqual(vp.read_vault_project(self.root), "agentm")

    def test_git_origin_fallback_ssh(self) -> None:
        _init_git_repo(self.root, "git@example.com:alexherrero/agentm.git")
        self.assertEqual(vp.read_vault_project(self.root), "agentm")

    def test_git_origin_with_partial_project_json(self) -> None:
        """If project.json exists but neither vault_project nor github.repo is set,
        still fall through to git origin."""
        _write_project_json(self.root, {"unrelated_field": True})
        _init_git_repo(self.root, "https://github.com/alexherrero/agentm.git")
        self.assertEqual(vp.read_vault_project(self.root), "agentm")

    # --- (d) no-signal / not-a-git-repo ---------------------------------------------
    def test_no_signal_returns_none(self) -> None:
        # No .harness/, no git repo — should return None.
        self.assertIsNone(vp.read_vault_project(self.root))

    def test_git_repo_without_origin_returns_none(self) -> None:
        _init_git_repo(self.root, origin_url=None)
        self.assertIsNone(vp.read_vault_project(self.root))

    def test_malformed_project_json_returns_none(self) -> None:
        harness = self.root / ".harness"
        harness.mkdir()
        (harness / "project.json").write_bytes(b"{not valid json")
        # No git repo either → no signal.
        self.assertIsNone(vp.read_vault_project(self.root))


class TestWriteVaultProject(unittest.TestCase):
    """write_vault_project() preserves existing fields + is atomic."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- (e) preserves existing fields ----------------------------------------------
    def test_preserves_existing_fields(self) -> None:
        original = {
            "github": {
                "owner": "alexherrero",
                "number": 2,
                "repo": "alexherrero/agentm",
            },
            "env": {"MEMORY_VAULT_PATH": "/some/path"},
        }
        _write_project_json(self.root, original)
        vp.write_vault_project(self.root, "my-slug")

        with (self.root / ".harness" / "project.json").open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["vault_project"], "my-slug")
        self.assertEqual(data["github"]["owner"], "alexherrero")
        self.assertEqual(data["github"]["number"], 2)
        self.assertEqual(data["github"]["repo"], "alexherrero/agentm")
        self.assertEqual(data["env"]["MEMORY_VAULT_PATH"], "/some/path")

    def test_creates_file_when_absent(self) -> None:
        # No .harness/ at all.
        target = vp.write_vault_project(self.root, "fresh-slug")
        self.assertTrue(target.is_file())
        with target.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data, {"vault_project": "fresh-slug"})

    def test_overwrites_existing_vault_project(self) -> None:
        _write_project_json(self.root, {"vault_project": "old-slug"})
        vp.write_vault_project(self.root, "new-slug")
        with (self.root / ".harness" / "project.json").open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["vault_project"], "new-slug")

    def test_empty_slug_raises(self) -> None:
        with self.assertRaises(ValueError):
            vp.write_vault_project(self.root, "   ")
        with self.assertRaises(ValueError):
            vp.write_vault_project(self.root, "")

    def test_round_trip(self) -> None:
        """write then read returns the same slug."""
        vp.write_vault_project(self.root, "round-trip-slug")
        self.assertEqual(vp.read_vault_project(self.root), "round-trip-slug")


class TestSlugFromOriginUrl(unittest.TestCase):
    """(f) Extra: URL-shape variety in tier-3 fallback."""

    def test_https_with_git_suffix(self) -> None:
        self.assertEqual(
            vp._slug_from_origin_url("https://github.com/alexherrero/agentm.git"),
            "agentm",
        )

    def test_https_no_git_suffix(self) -> None:
        self.assertEqual(
            vp._slug_from_origin_url("https://github.com/alexherrero/agentm"),
            "agentm",
        )

    def test_https_trailing_slash(self) -> None:
        self.assertEqual(
            vp._slug_from_origin_url("https://github.com/alexherrero/agentm/"),
            "agentm",
        )

    def test_ssh_form(self) -> None:
        self.assertEqual(
            vp._slug_from_origin_url("git@example.com:alexherrero/agentm.git"),
            "agentm",
        )

    def test_ssh_protocol_form(self) -> None:
        self.assertEqual(
            vp._slug_from_origin_url("ssh://git@example.com/alexherrero/agentm.git"),
            "agentm",
        )

    def test_file_protocol(self) -> None:
        self.assertEqual(
            vp._slug_from_origin_url("file:///srv/git/agentm.git"),
            "agentm",
        )

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(vp._slug_from_origin_url(""))
        self.assertIsNone(vp._slug_from_origin_url("   "))


class TestCLI(unittest.TestCase):
    """CLI interface — exit codes + stdout behavior."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_HERE / "vault_project.py"), *args],
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_cli_read_returns_slug(self) -> None:
        _write_project_json(self.root, {"vault_project": "cli-slug"})
        result = self._run("read", str(self.root))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "cli-slug")

    def test_cli_read_no_signal_exits_1(self) -> None:
        result = self._run("read", str(self.root))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "")

    def test_cli_write_then_read(self) -> None:
        wr = self._run("write", "written-slug", str(self.root))
        self.assertEqual(wr.returncode, 0)
        rd = self._run("read", str(self.root))
        self.assertEqual(rd.returncode, 0)
        self.assertEqual(rd.stdout.strip(), "written-slug")

    def test_cli_unknown_command(self) -> None:
        result = self._run("explode")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
