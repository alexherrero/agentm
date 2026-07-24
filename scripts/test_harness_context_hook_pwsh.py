#!/usr/bin/env python3
"""Unit tests for harness/hooks/harness-context-session-start/harness-context-session-start.ps1 (GH #72).

pwsh twin of test_harness_context_hook.py — drives the PowerShell hook as a
subprocess the same way, asserting the same core inject/skip behaviors. Not
a full 1:1 port of every edge case in the bash original: covers the
core inject/skip/graceful-degrade paths (the ones GH #72 named — "hook
actually fires and behaves"), not the full named-plan edge-case matrix.

Skipped when pwsh isn't on PATH (never assumes Windows specifically — this
also runs for real on macOS CI, which ships pwsh 7+ preinstalled).

Run: python3 scripts/test_harness_context_hook_pwsh.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_HOOK = _REPO / "harness" / "hooks" / "harness-context-session-start" / "harness-context-session-start.ps1"
_PWSH = shutil.which("pwsh")


@unittest.skipIf(_PWSH is None, "pwsh not on PATH")
class TestHarnessContextHookPwsh(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proj = self.root / "myfixtureproj"
        self.proj.mkdir()
        (self.proj / ".harness").mkdir()
        (self.proj / ".harness" / "project.json").write_text(
            json.dumps({"vault_project": "myfixtureproj"}), encoding="utf-8",
        )
        # Fake HOME so the hook resolves harness_memory.py from THIS repo,
        # mirroring the bash test's fixture exactly.
        self.fake_home = self.root / "home"
        (self.fake_home / ".claude").mkdir(parents=True)
        (self.fake_home / ".claude" / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "source_clones": {"agentm": str(_REPO)}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _env(self, **over) -> dict:
        env = {**os.environ, "HOME": str(self.fake_home)}
        env.pop("AGENTM_INSTALL_PREFIX", None)
        env.pop("MEMORY_VAULT_PATH", None)
        env.update(over)
        return env

    def _run_hook(self, cwd: str, env: dict):
        payload = json.dumps({"session_id": "doctor-probe", "cwd": cwd})
        return subprocess.run(
            [_PWSH, "-NoProfile", "-File", str(_HOOK)],
            input=payload, env=env, capture_output=True, text=True,
        )

    def test_injects_block_when_both_state_files_exist(self) -> None:
        hdir = self.proj / ".harness"
        plan = str(hdir / "PLAN.md")
        prog = str(hdir / "progress.md")
        Path(plan).write_text("# fixture\n", encoding="utf-8")
        Path(prog).write_text("# progress\n", encoding="utf-8")

        r = self._run_hook(str(self.proj), self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[agentm] Project state for this repo lives in .harness/", r.stdout)
        self.assertIn("Read PLAN.md before", r.stdout)
        self.assertIn("injected vault paths", r.stderr)

    def test_skips_when_state_files_absent(self) -> None:
        r = self._run_hook(str(self.proj), self._env())
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("[agentm] Project state", r.stdout)

    def test_named_plans_surfaced(self) -> None:
        hdir = self.proj / ".harness"
        (hdir / "PLAN-foo.md").write_text("# foo\n", encoding="utf-8")
        (hdir / "PLAN-bar.md").write_text("# bar\n", encoding="utf-8")
        r = self._run_hook(str(self.proj), self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Named-plan mode", r.stdout)
        self.assertIn("PLAN-foo.md", r.stdout)
        self.assertIn("PLAN-bar.md", r.stdout)

    def test_named_plan_dangling_marker_flagged(self) -> None:
        hdir = self.proj / ".harness"
        (hdir / "PLAN-foo.md").write_text("# foo\n", encoding="utf-8")
        (hdir / "active-plan").write_text("ghost\n", encoding="utf-8")
        r = self._run_hook(str(self.proj), self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DANGLING", r.stdout)
        self.assertIn("PLAN-ghost.md not found", r.stdout)

    def test_skips_when_event_cwd_missing(self) -> None:
        r = self._run_hook(str(self.root / "does-not-exist"), self._env())
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("[agentm] Project state", r.stdout)

    def test_graceful_skip_when_resolver_unavailable(self) -> None:
        bare_home = self.root / "barehome"
        bare_home.mkdir()
        env = self._env(HOME=str(bare_home))
        r = self._run_hook(str(self.proj), env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_nudge_emitted_for_unconfigured_git_repo(self) -> None:
        repo = self.root / "freshrepo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, capture_output=True)
        r = self._run_hook(str(repo), self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("[agentm] Project state", r.stdout)
        self.assertIn("[agentm] New project", r.stdout)


if __name__ == "__main__":
    unittest.main()
