#!/usr/bin/env python3
"""Unit tests for harness/hooks/harness-context-session-start/harness-context-session-start.sh (V4 #39).

Drives the bash hook as a subprocess with a synthetic SessionStart event JSON on
stdin + a fixture vault, asserting the inject/skip/graceful-skip behaviors.

Hermetic: each test points the hook at a fake `HOME` whose
`.claude/.agentm-config.json` resolves `harness_memory.py` to THIS repo (CI has
no `~/.claude/.agentm-config.json` nor a `~/Antigravity/agentm` clone, so without
this the hook would correctly skip and the inject case couldn't be exercised).
The fixture vault is selected via MEMORY_VAULT_PATH (env wins in the resolver).

Run: python3 scripts/test_harness_context_hook.py
Skipped on non-POSIX (bash hook).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_HOOK = _REPO / "harness" / "hooks" / "harness-context-session-start" / "harness-context-session-start.sh"
_RESOLVER = _REPO / "scripts" / "harness_memory.py"


@unittest.skipIf(os.name == "nt", "bash hook — POSIX only")
class TestHarnessContextHook(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "projects").mkdir(parents=True)  # mark the projects layout
        self.proj = self.root / "myfixtureproj"
        self.proj.mkdir()
        # Tier-1 slug for the fixture project.
        (self.proj / ".harness").mkdir()
        (self.proj / ".harness" / "project.json").write_text(
            json.dumps({"vault_project": "myfixtureproj"}), encoding="utf-8",
        )
        # Fake HOME so the hook resolves harness_memory.py from THIS repo
        # (config.source_clones.agentm = repo root) regardless of machine layout.
        self.fake_home = self.root / "home"
        (self.fake_home / ".claude").mkdir(parents=True)
        (self.fake_home / ".claude" / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "source_clones": {"agentm": str(_REPO)}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _env(self, **over) -> dict:
        env = {**os.environ, "HOME": str(self.fake_home), "MEMORY_VAULT_PATH": str(self.vault)}
        env.pop("AGENTM_INSTALL_PREFIX", None)  # MEMORY_VAULT_PATH wins anyway
        env.update(over)
        return env

    def _resolve(self, name: str, env: dict) -> str:
        r = subprocess.run(
            [sys.executable, str(_RESOLVER), "vault-state-path", name],
            cwd=str(self.proj), env=env, capture_output=True, text=True,
        )
        return r.stdout.strip()

    def _run_hook(self, cwd: str, env: dict):
        payload = json.dumps({"session_id": "doctor-probe", "cwd": cwd})
        return subprocess.run(
            ["bash", str(_HOOK)], input=payload, env=env, capture_output=True, text=True,
        )

    def test_injects_block_when_both_state_files_exist(self) -> None:
        env = self._env()
        plan = self._resolve("PLAN.md", env)
        prog = self._resolve("progress.md", env)
        self.assertTrue(plan and prog, f"resolver returned empty: plan={plan!r} prog={prog!r}")
        for p in (plan, prog):
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            Path(p).write_text("# fixture\n", encoding="utf-8")

        r = self._run_hook(str(self.proj), env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[agentm] Project state for this repo lives in the vault", r.stdout)
        self.assertIn(plan, r.stdout)
        self.assertIn(prog, r.stdout)
        self.assertIn("Read PLAN.md before", r.stdout)
        self.assertIn("injected vault paths", r.stderr)

    def test_skips_when_state_files_absent(self) -> None:
        # Resolver found (fake HOME config) but no PLAN.md/progress.md on disk → skip.
        env = self._env()
        r = self._run_hook(str(self.proj), env)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("[agentm] Project state", r.stdout)
        self.assertIn("skipped", r.stderr)

    # ── Named plans (V5-10 part 1, task 5) ─────────────────────────────────────

    def _harness_dir(self, env: dict) -> Path:
        """The resolved _harness/ dir = parent of the constructed PLAN.md path."""
        hdir = Path(self._resolve("PLAN.md", env)).parent
        hdir.mkdir(parents=True, exist_ok=True)
        return hdir

    def test_named_plans_surfaced(self) -> None:
        # Two named plans, no unnamed PLAN.md → both surfaced in named-plan mode.
        env = self._env()
        hdir = self._harness_dir(env)
        (hdir / "PLAN-foo.md").write_text("# foo\n", encoding="utf-8")
        (hdir / "PLAN-bar.md").write_text("# bar\n", encoding="utf-8")
        r = self._run_hook(str(self.proj), env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[agentm] Project state", r.stdout)  # doctor --live probe anchor
        self.assertIn("Named-plan mode", r.stdout)
        self.assertIn("PLAN-foo.md", r.stdout)
        self.assertIn("PLAN-bar.md", r.stdout)
        self.assertIn("named plan(s)", r.stderr)

    def test_named_plans_list_unnamed_singleton_too(self) -> None:
        # Unnamed PLAN.md alongside a named one → BOTH listed (surface them all).
        env = self._env()
        plan = self._resolve("PLAN.md", env)
        prog = self._resolve("progress.md", env)
        Path(plan).parent.mkdir(parents=True, exist_ok=True)
        Path(plan).write_text("# unnamed\n", encoding="utf-8")
        Path(prog).write_text("# progress\n", encoding="utf-8")
        (Path(plan).parent / "PLAN-foo.md").write_text("# foo\n", encoding="utf-8")
        r = self._run_hook(str(self.proj), env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Named-plan mode", r.stdout)
        self.assertIn("PLAN.md", r.stdout)
        self.assertIn("PLAN-foo.md", r.stdout)
        self.assertIn(plan, r.stdout)

    def test_named_plan_active_marker_highlighted(self) -> None:
        env = self._env()
        hdir = self._harness_dir(env)
        (hdir / "PLAN-foo.md").write_text("# foo\n", encoding="utf-8")
        (hdir / "PLAN-bar.md").write_text("# bar\n", encoding="utf-8")
        (self.proj / ".harness" / "active-plan").write_text("foo\n", encoding="utf-8")
        r = self._run_hook(str(self.proj), env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Active plan (.harness/active-plan", r.stdout)
        self.assertIn("foo", r.stdout)
        self.assertNotIn("DANGLING", r.stdout)

    def test_named_plan_dangling_marker_flagged(self) -> None:
        # active-plan names a plan with no PLAN-<name>.md → flagged, never fatal.
        env = self._env()
        hdir = self._harness_dir(env)
        (hdir / "PLAN-foo.md").write_text("# foo\n", encoding="utf-8")
        (self.proj / ".harness" / "active-plan").write_text("ghost\n", encoding="utf-8")
        r = self._run_hook(str(self.proj), env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DANGLING", r.stdout)
        self.assertIn("PLAN-ghost.md not found", r.stdout)

    def test_named_plan_excludes_conflict_copy(self) -> None:
        env = self._env()
        hdir = self._harness_dir(env)
        (hdir / "PLAN-foo.md").write_text("# foo\n", encoding="utf-8")
        (hdir / "PLAN-foo (conflicted copy 2026-06-12) - Mac.md").write_text(
            "# dup\n", encoding="utf-8"
        )
        r = self._run_hook(str(self.proj), env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PLAN-foo.md", r.stdout)
        self.assertNotIn("conflicted copy", r.stdout)

    def test_skips_when_event_cwd_missing(self) -> None:
        r = self._run_hook(str(self.root / "does-not-exist"), self._env())
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("[agentm] Project state", r.stdout)
        self.assertIn("skipped", r.stderr)

    def test_graceful_skip_when_resolver_unavailable(self) -> None:
        # Fake HOME with no .agentm-config.json + no ~/Antigravity/agentm fallback
        # → resolver cannot be located → graceful skip, never blocks.
        bare_home = self.root / "barehome"
        bare_home.mkdir()
        env = self._env(HOME=str(bare_home))
        r = self._run_hook(str(self.proj), env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("resolver unavailable", r.stderr)

    # ── V4 #32 nudge branch ────────────────────────────────────────────────

    def _init_git(self, path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=str(path), check=True, capture_output=True)

    def test_nudge_emitted_for_unconfigured_git_repo(self) -> None:
        # .git present, not registered, no marker, not a harness-source bypass
        # → the configure-nudge fires (vault PLAN/progress don't resolve).
        repo = self.root / "freshrepo"
        repo.mkdir()
        self._init_git(repo)
        r = self._run_hook(str(repo), self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("[agentm] Project state", r.stdout)
        self.assertIn("[agentm] New project", r.stdout)
        self.assertIn("configure-nudge emitted", r.stderr)

    def test_silent_when_no_register_marker(self) -> None:
        repo = self.root / "optedoutrepo"
        repo.mkdir()
        self._init_git(repo)
        (repo / ".agentm-no-register").write_text("", encoding="utf-8")
        r = self._run_hook(str(repo), self._env())
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("[agentm] New project", r.stdout)
        self.assertIn("skipped", r.stderr)

    def test_silent_when_registered_via_skills_block(self) -> None:
        # Configured (project.json carries a skills block) but no PLAN yet → no nudge.
        repo = self.root / "configuredrepo"
        (repo / ".harness").mkdir(parents=True)
        self._init_git(repo)
        (repo / ".harness" / "project.json").write_text(
            json.dumps({"vault_project": "configuredrepo", "skills": {"memory": {"enabled": True}}}),
            encoding="utf-8",
        )
        r = self._run_hook(str(repo), self._env())
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("[agentm] New project", r.stdout)
        self.assertIn("skipped", r.stderr)

    def test_silent_for_non_git_dir(self) -> None:
        plaindir = self.root / "notarepo"
        plaindir.mkdir()
        r = self._run_hook(str(plaindir), self._env())
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("[agentm] New project", r.stdout)
        self.assertIn("skipped", r.stderr)


if __name__ == "__main__":
    unittest.main()
