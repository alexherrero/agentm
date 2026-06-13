#!/usr/bin/env python3
"""Tests for the V5-10 worktree slug-resolution probe (the agentm safety guard).

Covers all three tasks of the `worktree-slug-probe` plan:

  Task 1 — the `slug == origin basename` check:
    * `vault_project.check_worktree_slug_safety()` unit cases (safe / divergent via a
      Tier-1 `vault_project` override / divergent via a Tier-2 github.repo override /
      no-origin), driven against real throwaway git repos;
    * the `check-worktree-slug.sh` gate via subprocess — passes on the live repo and a
      conformant fixture, **fails loudly (exit 1)** on a divergent fixture (the
      mandatory negative test — never a silent wrong-slug resolution), warns-but-passes
      when there is no origin remote.

  Task 3 — the no-auto-worktree-spawn guard:
    * the `check-no-auto-worktree.sh` gate via subprocess — clean on the live repo,
      **fails (exit 1)** on a fixture whose automation surface spawns a worktree, and
      correctly ignores the verb in a `test_*.py` file and a `*.md` doc (neither is an
      automation code path).

The pure-Python helper tests run everywhere (git is cross-platform). The bash-gate
tests are POSIX-only (the gates are bash scripts), matching the other bash-driving
suites in this directory.

DC-7 (the frozen memory-engine public API) is untouched by this probe — the dedicated
`verify-memory-roundtrip` gate proves that invariant; nothing here imports or exercises
the memory engine.

Run directly:

    python3 scripts/test_worktree_slug_probe.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Allow `import vault_project` regardless of how discovery imports this module.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vault_project as vp  # noqa: E402

_SLUG_GATE = _HERE / "check-worktree-slug.sh"
_SPAWN_GATE = _HERE / "check-no-auto-worktree.sh"


# -----------------------------------------------------------------------------
# Fixture helpers (self-contained — mirror test_vault_project.py for independence)
# -----------------------------------------------------------------------------

def _init_git_repo(root: Path, origin_url: str | None = None) -> None:
    """Initialize an empty git repo at `root`, optionally with an `origin` remote."""
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=root, check=True, capture_output=True
    )
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


def _run_slug_gate(root: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["bash", str(_SLUG_GATE)]
    if root is not None:
        cmd += ["--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _run_spawn_gate(root: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["bash", str(_SPAWN_GATE)]
    if root is not None:
        cmd += ["--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


# -----------------------------------------------------------------------------
# Task 1 — check_worktree_slug_safety() (pure Python, cross-platform)
# -----------------------------------------------------------------------------

class CheckWorktreeSlugSafety(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- safe: the slug a normal checkout sees == the origin basename -----------

    def test_origin_only_is_safe(self) -> None:
        # No project.json (the agentm/crickets shape today): resolves via Tier 3,
        # so the full chain trivially equals the origin basename.
        _init_git_repo(self.root, "https://github.com/acme/widgets.git")
        report = vp.check_worktree_slug_safety(self.root)
        self.assertEqual(report.status, vp.WORKTREE_SLUG_OK)
        self.assertEqual(report.resolved, "widgets")
        self.assertEqual(report.origin_basename, "widgets")

    def test_agreeing_override_is_safe(self) -> None:
        # An explicit vault_project that AGREES with the origin basename is fine —
        # a worktree resolving via Tier 3 lands on the same slug.
        _init_git_repo(self.root, "https://github.com/acme/widgets.git")
        _write_project_json(self.root, {"vault_project": "widgets"})
        report = vp.check_worktree_slug_safety(self.root)
        self.assertEqual(report.status, vp.WORKTREE_SLUG_OK)

    # --- divergent: the foot-gun (a worktree would write to the wrong slug) ------

    def test_tier1_override_divergence_is_loud(self) -> None:
        # A Tier-1 vault_project override that DIFFERS from the origin basename: the
        # normal checkout writes to 'legacy-name', but a worktree (which can't see the
        # gitignored .harness/) would resolve to 'widgets' — the wrong slug.
        _init_git_repo(self.root, "https://github.com/acme/widgets.git")
        _write_project_json(self.root, {"vault_project": "legacy-name"})
        report = vp.check_worktree_slug_safety(self.root)
        self.assertEqual(report.status, vp.WORKTREE_SLUG_DIVERGENT)
        self.assertEqual(report.resolved, "legacy-name")
        self.assertEqual(report.origin_basename, "widgets")
        self.assertIn("WRONG vault slug", report.detail)

    def test_tier2_github_repo_divergence_is_loud(self) -> None:
        # A Tier-2 github.repo override diverging from the origin basename is caught
        # the same way.
        _init_git_repo(self.root, "https://github.com/acme/widgets.git")
        _write_project_json(self.root, {"github": {"repo": "acme/other-name"}})
        report = vp.check_worktree_slug_safety(self.root)
        self.assertEqual(report.status, vp.WORKTREE_SLUG_DIVERGENT)
        self.assertEqual(report.resolved, "other-name")
        self.assertEqual(report.origin_basename, "widgets")

    # --- no-origin: unverifiable, but not a foot-gun -----------------------------

    def test_no_origin_is_warn_not_fail(self) -> None:
        _init_git_repo(self.root, origin_url=None)
        report = vp.check_worktree_slug_safety(self.root)
        self.assertEqual(report.status, vp.WORKTREE_SLUG_NO_ORIGIN)
        self.assertIsNone(report.origin_basename)

    def test_not_a_git_repo_is_no_origin(self) -> None:
        # A bare directory with no git at all also has no origin to verify against.
        report = vp.check_worktree_slug_safety(self.root)
        self.assertEqual(report.status, vp.WORKTREE_SLUG_NO_ORIGIN)

    # --- resolve_origin_basename: the Tier-3-only slug ignores project.json ------

    def test_resolve_origin_basename_ignores_project_json(self) -> None:
        _init_git_repo(self.root, "git@example.com:acme/widgets.git")
        _write_project_json(self.root, {"vault_project": "legacy-name"})
        # read_vault_project honors the override; resolve_origin_basename does not.
        self.assertEqual(vp.read_vault_project(self.root), "legacy-name")
        self.assertEqual(vp.resolve_origin_basename(self.root), "widgets")

    def test_resolve_origin_basename_none_without_origin(self) -> None:
        _init_git_repo(self.root, origin_url=None)
        self.assertIsNone(vp.resolve_origin_basename(self.root))


# -----------------------------------------------------------------------------
# Task 1 — the CLI verb's distinct exit codes (0 ok / 1 divergent / 3 no-origin)
# -----------------------------------------------------------------------------

class CheckWorktreeSlugCLI(unittest.TestCase):
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

    def test_cli_ok_exits_0(self) -> None:
        _init_git_repo(self.root, "https://github.com/acme/widgets.git")
        r = self._run("check-worktree-slug", str(self.root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)

    def test_cli_divergent_exits_1(self) -> None:
        _init_git_repo(self.root, "https://github.com/acme/widgets.git")
        _write_project_json(self.root, {"vault_project": "legacy-name"})
        r = self._run("check-worktree-slug", str(self.root))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("DIVERGENT", r.stderr)

    def test_cli_no_origin_exits_3(self) -> None:
        _init_git_repo(self.root, origin_url=None)
        r = self._run("check-worktree-slug", str(self.root))
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("NO-ORIGIN", r.stderr)


# -----------------------------------------------------------------------------
# Task 1 — the check-worktree-slug.sh gate via subprocess (POSIX-only)
# -----------------------------------------------------------------------------

@unittest.skipIf(os.name == "nt", "bash gate — POSIX only")
class CheckWorktreeSlugGate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_gate_passes_on_live_repo(self) -> None:
        # agentm resolves 'agentm' via Tier 3 with no divergent override.
        proc = _run_slug_gate()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_passes_on_conformant_fixture(self) -> None:
        _init_git_repo(self.root, "https://github.com/acme/widgets.git")
        proc = _run_slug_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_fails_loudly_on_divergence(self) -> None:
        # The mandatory negative test: a divergent fixture must fail with a clear
        # message — never a silent wrong-slug resolution.
        _init_git_repo(self.root, "https://github.com/acme/widgets.git")
        _write_project_json(self.root, {"vault_project": "legacy-name"})
        proc = _run_slug_gate(self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("WRONG vault slug", proc.stderr)
        self.assertIn("widgets", proc.stderr)
        self.assertIn("legacy-name", proc.stderr)

    def test_gate_warns_but_passes_without_origin(self) -> None:
        _init_git_repo(self.root, origin_url=None)
        proc = _run_slug_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("WARN", proc.stderr)


# -----------------------------------------------------------------------------
# Task 3 — the check-no-auto-worktree.sh guard via subprocess (POSIX-only)
# -----------------------------------------------------------------------------

@unittest.skipIf(os.name == "nt", "bash gate — POSIX only")
class CheckNoAutoWorktreeGate(unittest.TestCase):
    # The literal spawn verb, assembled at runtime so this test file never contains
    # the exact string the guard greps for (it would be excluded as a test_*.py anyway,
    # but this keeps the fixtures unambiguous).
    SPAWN = "git " + "worktree " + "add"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "scripts").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_guard_passes_on_live_repo(self) -> None:
        proc = _run_spawn_gate()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_guard_passes_on_clean_fixture(self) -> None:
        (self.root / "scripts" / "ok.sh").write_text(
            "#!/usr/bin/env bash\ngit worktree list\n", encoding="utf-8"
        )
        proc = _run_spawn_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_guard_fails_on_auto_spawn(self) -> None:
        (self.root / "scripts" / "evil.sh").write_text(
            f"#!/usr/bin/env bash\n{self.SPAWN} /tmp/wt some-branch\n", encoding="utf-8"
        )
        proc = _run_spawn_gate(self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("evil.sh", proc.stderr)

    def test_guard_ignores_test_files(self) -> None:
        # A test that exercises the verb is not an automation code path.
        (self.root / "scripts" / "test_thing.py").write_text(
            f'cmd = "{self.SPAWN} /tmp/x b"\n', encoding="utf-8"
        )
        proc = _run_spawn_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_guard_ignores_markdown_prose(self) -> None:
        # Design/plan docs that discuss worktree spawning must not trip the guard.
        (self.root / "scripts" / "notes.md").write_text(
            f"Run `{self.SPAWN} <path> <branch>` only when the operator asks.\n",
            encoding="utf-8",
        )
        proc = _run_spawn_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
