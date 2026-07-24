#!/usr/bin/env python3
"""Hook-firing tests for harness/hooks/memory-reflect-idle/memory-reflect-idle.ps1 (GH #72).

pwsh twin of test_memory_reflect_idle_hook.py. Same relative-path resolution
divergence as the other three pwsh hooks tested this plan: reflect.py /
orchestration_idle.py / vec_index.py are all resolved relative to cwd
(.claude/skills/memory/scripts/...), not the source_clones.agentm bridge the
bash hook's own fixture uses — so this fixture copies the real memory
scripts tree into place instead of faking HOME's .agentm-config.json.

Marker ages are set deterministically via os.utime() against the hook's own
default thresholds (idle 1h, GC 30d) — no reliance on wall-clock timing. The
vault's auto-orchestration-config.md disables the detached idle chain so it
can't race test teardown, matching the bash fixture's own neutralization.

Skipped when pwsh isn't on PATH (runs on macOS CI too, not Windows-only).

Run: python3 scripts/test_memory_reflect_idle_hook_pwsh.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_HOOK = _REPO / "harness" / "hooks" / "memory-reflect-idle" / "memory-reflect-idle.ps1"
_MEMORY_SCRIPTS_SRC = _REPO / "harness" / "skills" / "memory" / "scripts"
_PWSH = shutil.which("pwsh")

_IDLE = 3600       # default MEMORY_IDLE_THRESHOLD_SEC (1h)
_GC = 2592000      # default MEMORY_REFLECTED_GC_SEC (30d)


@unittest.skipIf(_PWSH is None, "pwsh not on PATH")
class TestMemoryReflectIdleHookPwsh(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls._cls_tmp = tempfile.TemporaryDirectory()
        cls._skills_dst = Path(cls._cls_tmp.name) / "skills-fixture"
        cls._skills_dst.mkdir(parents=True)
        for item in _MEMORY_SCRIPTS_SRC.iterdir():
            if item.is_file():
                shutil.copy2(item, cls._skills_dst / item.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._cls_tmp.cleanup()

    def setUp(self) -> None:
        # rmtree(ignore_errors=True), not TemporaryDirectory: the hook launches
        # detached vec-index/orchestration processes (neutralized below, but a
        # strict cleanup could still race a slow spawn on a loaded CI box).
        self.root = Path(tempfile.mkdtemp(prefix="agentm-idle-hook-pwsh-test-"))
        self.vault = self.root / "vault"
        (self.vault / "personal").mkdir(parents=True)
        (self.vault / "_inbox").mkdir(parents=True)
        (self.vault / "personal" / "auto-orchestration-config.md").write_text(
            "```settings\nenable_idle_chain = false\n```\n", encoding="utf-8",
        )
        self.proj = self.root / "proj"
        self.hdir = self.proj / ".harness"
        self.hdir.mkdir(parents=True)
        dst = self.proj / ".claude" / "skills" / "memory" / "scripts"
        shutil.copytree(self._skills_dst, dst)
        self.fake_home = self.root / "home"
        (self.fake_home / ".claude").mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _env(self, with_vault: bool = True, **over) -> dict:
        env = {**os.environ, "HOME": str(self.fake_home)}
        env.pop("AGENTM_INSTALL_PREFIX", None)
        if with_vault:
            env["MEMORY_VAULT_PATH"] = str(self.vault)
        else:
            env.pop("MEMORY_VAULT_PATH", None)
        env.update(over)
        return env

    def _make_marker(self, sid: str, transcript: str, kind: str = ".start",
                      age_sec: int = 0) -> Path:
        m = self.hdir / f"session-id-{sid}{kind}"
        m.write_text(
            f"session_id: {sid}\nstarted_at: 2026-01-01T00:00:00Z\ntranscript: {transcript}\n",
            encoding="utf-8",
        )
        if age_sec:
            t = time.time() - age_sec
            os.utime(m, (t, t))
        return m

    def _transcript(self) -> str:
        tp = self.root / "t.jsonl"
        tp.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n', encoding="utf-8")
        return str(tp)

    def _run_hook(self, env: dict, cwd: Path | None = None):
        return subprocess.run(
            [_PWSH, "-NoProfile", "-File", str(_HOOK)],
            input="{}", env=env, cwd=str(cwd or self.proj), capture_output=True, text=True,
        )

    def test_orphan_recovery_reflects_aged_marker(self) -> None:
        m = self._make_marker("orphan1", self._transcript(), ".start", age_sec=_IDLE + 1000)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(m.exists(), "aged .start marker not consumed")
        self.assertTrue((self.hdir / "session-id-orphan1.reflected").is_file(),
                         "orphan not renamed to .reflected after reflection")

    def test_fresh_marker_is_left_alone(self) -> None:
        m = self._make_marker("fresh1", self._transcript(), ".start", age_sec=0)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(m.is_file(), "fresh marker wrongly consumed")
        self.assertFalse((self.hdir / "session-id-fresh1.reflected").exists())

    def test_aged_marker_missing_transcript_is_skipped(self) -> None:
        m = self._make_marker("gone1", str(self.root / "absent.jsonl"), ".start",
                               age_sec=_IDLE + 1000)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(m.is_file())
        self.assertIn("transcript not found", r.stderr)

    def test_gc_deletes_old_reflected_marker(self) -> None:
        old = self._make_marker("done1", "x", ".reflected", age_sec=_GC + 86400)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(old.exists(), "old .reflected marker not GC'd")

    def test_gc_keeps_recent_reflected_marker(self) -> None:
        recent = self._make_marker("done2", "x", ".reflected", age_sec=86400)  # 1d < 30d
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(recent.is_file(), "recent .reflected marker wrongly GC'd")

    def test_no_markers_is_nonblocking(self) -> None:
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_graceful_skip_when_resolver_unavailable(self) -> None:
        bare_proj = self.root / "bareproj"
        (bare_proj / ".harness").mkdir(parents=True)
        m = bare_proj / ".harness" / "session-id-orphan2.start"
        m.write_text("session_id: orphan2\ntranscript: x\n", encoding="utf-8")
        t = time.time() - (_IDLE + 1000)
        os.utime(m, (t, t))
        r = self._run_hook(self._env(), cwd=bare_proj)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(m.is_file(), "marker touched despite unresolvable reflect.py")

    def test_drain_call_is_wired_and_detached(self) -> None:
        source = _HOOK.read_text(encoding="utf-8")
        self.assertIn("VecIndexPy", source)
        drain_line = next(
            (ln for ln in source.splitlines()
             if "VecIndexPy" in ln and re.search(r"\bdrain\b", ln)),
            None,
        )
        self.assertIsNotNone(drain_line, "no line invokes vec_index.py drain")
        self.assertIn("Start-Process", drain_line)

    def test_drain_call_does_not_block_hook_exit(self) -> None:
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
