#!/usr/bin/env python3
"""Hook-firing tests for harness/hooks/memory-reflect-idle (Hardening I #45 task 6).

Drives the bash idle/orphan-recovery hook as a subprocess, proving it ACTUALLY
FIRES (the V4 #39 class of bug): an aged `.start` marker (a crashed session whose
Stop never fired) is reflected retroactively and renamed `.start → .reflected`; a
fresh marker is left alone (session may still be active); a `.reflected` marker
past the GC window is deleted; and it graceful-skips (exit 0) when the memory
skill is absent or there's no orphan work. It NEVER blocks session start.

Hermetic: a fake `HOME` whose `.claude/.agentm-config.json` resolves reflect.py to
THIS repo via `source_clones.agentm`. Marker ages are set deterministically via
os.utime() against the DEFAULT idle (1h) + GC (30d) thresholds — no reliance on
wall-clock timing.

Run: python3 scripts/test_memory_reflect_idle_hook.py
Skipped on non-POSIX (bash hook).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_HOOK = _REPO / "harness" / "hooks" / "memory-reflect-idle" / "memory-reflect-idle.sh"

_TRANSCRIPT = (
    '{"type":"user","message":{"role":"user","content":"do the thing"}}\n'
    '{"type":"assistant","message":{"role":"assistant","content":"done"}}\n'
)
_IDLE = 3600       # default MEMORY_IDLE_THRESHOLD_SEC (1h)
_GC = 2592000      # default MEMORY_REFLECTED_GC_SEC (30d)


@unittest.skipIf(os.name == "nt", "bash hook — POSIX only")
class TestMemoryReflectIdleHook(unittest.TestCase):

    def setUp(self) -> None:
        # mkdtemp (not TemporaryDirectory) + ignore_errors rmtree: the hook fires
        # the DETACHED idle-orchestration chain, which we disable below but whose
        # spawn could still race a strict cleanup. We test the orphan/GC logic
        # that runs BEFORE the chain; the chain itself is covered by verify-v4.sh.
        self.root = Path(tempfile.mkdtemp(prefix="agentm-idle-hook-test-"))
        self.vault = self.root / "vault"
        (self.vault / "personal-private").mkdir(parents=True)
        (self.vault / "_inbox").mkdir(parents=True)
        # Neutralize the detached idle chain (enable_idle_chain=false) so it exits
        # fast without corpus-mining writes that would race tearDown.
        (self.vault / "personal-private" / "auto-orchestration-config.md").write_text(
            "```settings\nenable_idle_chain = false\n```\n", encoding="utf-8",
        )
        self.proj = self.root / "proj"
        self.hdir = self.proj / ".harness"
        self.hdir.mkdir(parents=True)
        self.fake_home = self.root / "home"
        (self.fake_home / ".claude").mkdir(parents=True)
        (self.fake_home / ".claude" / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "source_clones": {"agentm": str(_REPO)}}),
            encoding="utf-8",
        )

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
        tp.write_text(_TRANSCRIPT, encoding="utf-8")
        return str(tp)

    def _run_hook(self, env: dict):
        # The idle hook fires on SessionStart but doesn't consume the payload.
        return subprocess.run(
            ["bash", str(_HOOK)], input="{}", env=env,
            cwd=str(self.proj), capture_output=True, text=True,
        )

    # ── fires: orphan recovery ────────────────────────────────────────────────

    def test_orphan_recovery_reflects_aged_marker(self) -> None:
        m = self._make_marker("orphan1", self._transcript(), ".start", age_sec=_IDLE + 1000)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(m.exists(), "aged .start marker not consumed")
        self.assertTrue((self.hdir / "session-id-orphan1.reflected").is_file(),
                        "orphan not renamed to .reflected after reflection")

    def test_fresh_marker_is_left_alone(self) -> None:
        # Age 0 < 1h threshold → the session may still be active; don't reflect.
        m = self._make_marker("fresh1", self._transcript(), ".start", age_sec=0)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(m.is_file(), "fresh marker wrongly consumed")
        self.assertFalse((self.hdir / "session-id-fresh1.reflected").exists())

    def test_aged_marker_missing_transcript_is_skipped(self) -> None:
        # Aged, but its transcript is gone → skip (stays .start), non-blocking.
        m = self._make_marker("gone1", str(self.root / "absent.jsonl"), ".start",
                              age_sec=_IDLE + 1000)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(m.is_file())
        self.assertIn("transcript not found", r.stderr)

    # ── GC of old .reflected markers ──────────────────────────────────────────

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

    # ── graceful-skip / non-blocking ─────────────────────────────────────────

    def test_no_markers_is_nonblocking(self) -> None:
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_graceful_skip_when_resolver_unavailable(self) -> None:
        bare = self.root / "barehome"
        bare.mkdir()
        env = self._env()
        env["HOME"] = str(bare)
        # Even an aged orphan can't be processed without the memory skill → skip.
        m = self._make_marker("orphan2", self._transcript(), ".start", age_sec=_IDLE + 1000)
        r = self._run_hook(env)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(m.is_file(), "marker touched despite unresolvable reflect.py")


if __name__ == "__main__":
    unittest.main()
