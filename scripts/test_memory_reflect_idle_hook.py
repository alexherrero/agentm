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
        (self.vault / "memory").mkdir(parents=True)
        (self.vault / "_inbox").mkdir(parents=True)
        # Neutralize the detached idle chain (enable_idle_chain=false) so it exits
        # fast without corpus-mining writes that would race tearDown.
        (self.vault / "memory" / "auto-orchestration-config.md").write_text(
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

    # ── skips: already reflected, nothing new ─────────────────────────────────

    def _processed(self, r) -> int:
        m = re.search(r"processed (\d+) orphans", r.stderr)
        self.assertIsNotNone(m, f"no orphan summary in stderr: {r.stderr!r}")
        return int(m.group(1))

    def test_already_reflected_and_transcript_unchanged_does_not_re_mine(self) -> None:
        # The re-mine loop. `SessionStart` rewrites `.start` whenever it is
        # absent, so a resumed session gets a fresh marker beside the
        # `.reflected` one an earlier pass left — and this hook used to mine the
        # whole transcript again on the strength of the `.start` alone, without
        # ever looking at its sibling. Thirteen passes over one transcript is
        # how one operator sentence became thirteen inbox files.
        tp = self._transcript()
        self._make_marker("done1", tp, ".reflected", age_sec=10)
        m = self._make_marker("done1", tp, ".start", age_sec=_IDLE + 1000)
        # Transcript last written before the reflection → nothing new to mine.
        old = time.time() - 5000
        os.utime(tp, (old, old))
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._processed(r), 0, "re-mined an already-reflected session")
        self.assertFalse(m.exists(), "stale .start left behind to fire again next pass")

    def test_already_reflected_but_transcript_grew_still_re_mines(self) -> None:
        # The other half, and the reason the skip cannot be unconditional: a
        # resumed session keeps appending turns after its first reflection, and
        # those turns are exactly what orphan recovery exists to catch. Skipping
        # on the marker alone would silently drop them.
        tp = self._transcript()
        self._make_marker("grew1", tp, ".reflected", age_sec=5000)
        m = self._make_marker("grew1", tp, ".start", age_sec=_IDLE + 1000)
        os.utime(tp, None)  # transcript touched now → newer than the reflection
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._processed(r), 1, "new turns after a resume went unmined")
        self.assertFalse(m.exists())

    def test_rename_stamps_the_reflected_marker_with_the_reflection_time(self) -> None:
        # `mv` preserves mtime, so before this the `.reflected` marker carried
        # the time the SESSION STARTED, not the time it was reflected — which
        # makes it useless as the "have we already done this?" comparison the
        # two tests above depend on.
        tp = self._transcript()
        m = self._make_marker("stamp1", tp, ".start", age_sec=_IDLE + 1000)
        before = time.time() - 60
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        reflected = self.hdir / "session-id-stamp1.reflected"
        self.assertTrue(reflected.is_file())
        self.assertGreater(reflected.stat().st_mtime, before,
                           "reflected marker still carries the .start mtime")

    def test_fresh_marker_is_left_alone(self) -> None:
        # Age 0 < 1h threshold → the session may still be active; don't reflect.
        m = self._make_marker("fresh1", self._transcript(), ".start", age_sec=0)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(m.is_file(), "fresh marker wrongly consumed")
        self.assertFalse((self.hdir / "session-id-fresh1.reflected").exists())

    def test_aged_marker_missing_transcript_is_cleared_as_a_dead_pointer(self) -> None:
        # Aged past the idle threshold with its transcript gone → nothing can ever
        # be reflected from this marker, so it is deleted rather than skipped.
        #
        # This test previously asserted the opposite (`assertTrue(m.is_file())` —
        # stays .start). That behavior was the leak: every such marker was skipped
        # on every session forever, and 200 accumulated in this repo. The contract
        # changed deliberately, so the assertion tracks it; the same intent is
        # still being checked — what happens to an aged marker whose transcript is
        # unresolvable — and the hook stays non-blocking either way.
        m = self._make_marker("gone1", str(self.root / "absent.jsonl"), ".start",
                              age_sec=_IDLE + 1000)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(m.exists(), "dead pointer not cleared — the leak is back")
        self.assertFalse(m.with_suffix(".reflected").exists(),
                         "an unreflectable marker must not be recorded as reflected")
        self.assertIn("transcript not found", r.stderr)

    def test_fresh_marker_missing_transcript_is_left_alone(self) -> None:
        # The guard on the above: a marker still INSIDE the idle threshold may be a
        # live session whose transcript has not been written yet. Deleting it would
        # destroy an active session's pending reflection, so the age gate — not the
        # missing transcript alone — is what licenses the delete.
        m = self._make_marker("fresh1", str(self.root / "absent.jsonl"), ".start",
                              age_sec=0)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(m.is_file(), "a fresh marker was deleted — active session at risk")

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
