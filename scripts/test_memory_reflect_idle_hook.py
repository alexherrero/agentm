#!/usr/bin/env python3
"""Hook-firing tests for harness/hooks/memory-reflect-idle (Hardening I #45 task 6).

Drives the bash idle hook as a subprocess, proving it ACTUALLY FIRES (the V4
#39 class of bug). Until task 199 it mined orphaned sessions from `.start`
markers; mining moved to the night's lesson compile and the markers retired, so
it now deletes every `.harness/session-id-*.start` and `.reflected` it finds,
whatever its age, and mines nothing — not even the transcript an aged orphan
marker names. It touches nothing else in `.harness/` and NEVER blocks session
start.

Hermetic: a fake `HOME` whose `.claude/.agentm-config.json` resolves the memory
scripts to THIS repo via `source_clones.agentm`.

Run: python3 scripts/test_memory_reflect_idle_hook.py
Skipped on non-POSIX (bash hook).
"""
from __future__ import annotations

import json
import os
import re
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
    '{"type":"user","message":{"role":"user","content":"Always put the summary at the top of the report."}}\n'
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Done."}]}}\n'
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
        (self.vault / "memory").mkdir(parents=True)
        (self.vault / "_inbox").mkdir(parents=True)
        # Neutralize the detached idle chain (enable_idle_chain=false) so it exits
        # fast without corpus-mining writes that would race tearDown.
        (self.vault / "projects" / "agentm" / "desk").mkdir(parents=True, exist_ok=True)
        (self.vault / "projects" / "agentm" / "desk" / "auto-orchestration-config.md").write_text(
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
            env.pop("MEMORY_ROOT", None)
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

    # ── retired markers ──────────────────────────────────────────────────────

    def test_clears_every_retired_marker_whatever_its_age(self) -> None:
        markers = [
            self._make_marker("fresh", "x", ".start"),
            self._make_marker("aged", self._transcript(), ".start", age_sec=_IDLE + 1000),
            self._make_marker("recent", "x", ".reflected", age_sec=86400),
            self._make_marker("old", "x", ".reflected", age_sec=_GC + 86400),
        ]
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        for m in markers:
            self.assertFalse(m.exists(), f"{m.name} not cleared")
        self.assertIn("cleared 4 retired session markers", r.stderr)

    def test_never_mines_an_orphan(self) -> None:
        # The retired sweep mined the transcript an aged `.start` named and
        # filed a card for its always-directive. Nothing is mined now.
        self._make_marker("orphan1", self._transcript(), ".start", age_sec=_IDLE + 1000)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list((self.vault / "memory").rglob("*.md")), [])
        self.assertNotIn("processed", r.stderr)

    def test_leaves_the_rest_of_harness_alone(self) -> None:
        keep = [self.hdir / "PLAN.md", self.hdir / "progress.md", self.hdir / "session-id-notes.txt"]
        for k in keep:
            k.write_text("x", encoding="utf-8")
        self._make_marker("s", "x", ".start")
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        for k in keep:
            self.assertTrue(k.is_file(), k.name)

    # ── graceful-skip / non-blocking ─────────────────────────────────────────

    def test_no_markers_is_nonblocking(self) -> None:
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("cleared", r.stderr)

    def test_no_harness_dir_is_nonblocking(self) -> None:
        shutil.rmtree(self.hdir)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_graceful_when_resolver_unavailable(self) -> None:
        bare = self.root / "barehome"
        bare.mkdir()
        env = self._env()
        env["HOME"] = str(bare)
        m = self._make_marker("orphan2", self._transcript(), ".start", age_sec=_IDLE + 1000)
        r = self._run_hook(env)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(m.exists(), "a retired marker is cleared without the memory skill too")

    # ── vector-stack removal (was: vec-index drain wiring) ───────────────────

    def test_hook_does_not_invoke_the_removed_vector_index(self) -> None:
        """This guarded the opposite condition until the vector stack was
        removed: pre-R0.2 nothing ever called `drain_queue`, so the hook was
        made to fire one and this pinned the wiring. `vec_index.py` no longer
        exists, so the same line would now resolve to nothing and fail
        silently on every idle pass — exactly the class of quiet breakage this
        test was written to prevent. It pins the removal instead."""
        source = _HOOK.read_text(encoding="utf-8")
        self.assertNotIn("VEC_INDEX_PY", source)
        self.assertNotIn("vec_index", source)

    def test_hook_still_exits_clean_without_the_drain(self) -> None:
        # A real (empty) vault: removing the backgrounded drain must not have
        # disturbed the hook's own exit path.
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
