#!/usr/bin/env python3
"""Hook-firing tests for harness/hooks/memory-reflect-stop (Hardening I #45 task 6).

Drives the bash Stop hook as a subprocess with a synthetic Stop event JSON on
stdin, proving it ACTUALLY FIRES (the V4 #39 class of bug): with a transcript at
the computed path + a vault it reflects and renames the session marker
`.start → .reflected`; it graceful-skips (exit 0) on every absent-input path
(no stdin / no session_id / transcript missing / dedup marker / no resolver /
no vault); and it NEVER blocks session end.

Both state modes are covered via the vault axis: vault-present (reflect routes +
renames the marker) vs no-vault / repo-local (reflect --route can't persist → the
hook graceful-skips, marker untouched, exit 0).

Hermetic: a fake `HOME` whose `.claude/.agentm-config.json` resolves reflect.py
to THIS repo via `source_clones.agentm`, and which doubles as the
`~/.claude/projects/<cwd-slug>/<sid>.jsonl` transcript root the hook computes.

Run: python3 scripts/test_memory_reflect_stop_hook.py
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
_HOOK = _REPO / "harness" / "hooks" / "memory-reflect-stop" / "memory-reflect-stop.sh"

_TRANSCRIPT = (
    '{"type":"user","message":{"role":"user","content":"do the thing"}}\n'
    '{"type":"assistant","message":{"role":"assistant","content":"done"}}\n'
)


@unittest.skipIf(os.name == "nt", "bash hook — POSIX only")
class TestMemoryReflectStopHook(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "personal-private").mkdir(parents=True)
        (self.vault / "_inbox").mkdir(parents=True)
        self.proj = self.root / "proj"
        (self.proj / ".harness").mkdir(parents=True)
        self.fake_home = self.root / "home"
        (self.fake_home / ".claude").mkdir(parents=True)
        (self.fake_home / ".claude" / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "source_clones": {"agentm": str(_REPO)}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _env(self, with_vault: bool = True, **over) -> dict:
        env = {**os.environ, "HOME": str(self.fake_home)}
        env.pop("AGENTM_INSTALL_PREFIX", None)
        if with_vault:
            env["MEMORY_VAULT_PATH"] = str(self.vault)
        else:
            env.pop("MEMORY_VAULT_PATH", None)
        env.update(over)
        return env

    def _transcript_path(self, sid: str, cwd: Path) -> Path:
        # Mirror the hook's formula: $HOME/.claude/projects/-<cwd-with-slashes-as-dashes>/<sid>.jsonl
        slug = "-" + str(cwd).replace("/", "-")
        return self.fake_home / ".claude" / "projects" / slug / f"{sid}.jsonl"

    def _place_transcript(self, sid: str, cwd: Path) -> Path:
        tp = self._transcript_path(sid, cwd)
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text(_TRANSCRIPT, encoding="utf-8")
        return tp

    def _run_hook(self, env: dict, sid: str = "s1", cwd: Path | None = None,
                  raw_payload: str | None = None):
        cwd = cwd or self.proj
        if raw_payload is None:
            raw_payload = json.dumps({"session_id": sid, "cwd": str(cwd)})
        return subprocess.run(
            ["bash", str(_HOOK)], input=raw_payload, env=env,
            cwd=str(cwd), capture_output=True, text=True,
        )

    # ── fires ────────────────────────────────────────────────────────────────

    def test_fires_reflect_and_renames_marker(self) -> None:
        self._place_transcript("s1", self.proj)
        start = self.proj / ".harness" / "session-id-s1.start"
        start.write_text("session_id: s1\ntranscript: x\n", encoding="utf-8")
        r = self._run_hook(self._env(), sid="s1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"pass": "summary"', r.stdout)                 # reflect.py output passed through
        self.assertIn("Mined", r.stderr)                            # transparency line
        # The hook-owned proof that reflection succeeded: .start → .reflected.
        self.assertFalse(start.exists(), ".start marker not renamed")
        self.assertTrue((self.proj / ".harness" / "session-id-s1.reflected").is_file())

    # ── graceful-skip / non-blocking ─────────────────────────────────────────

    def test_graceful_skip_no_stdin(self) -> None:
        r = self._run_hook(self._env(), raw_payload="")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no stdin payload", r.stderr)

    def test_graceful_skip_no_session_id(self) -> None:
        r = self._run_hook(self._env(), raw_payload=json.dumps({"cwd": str(self.proj)}))
        self.assertEqual(r.returncode, 0)
        self.assertIn("no session_id", r.stderr)

    def test_graceful_skip_transcript_missing(self) -> None:
        # Valid session_id but no transcript on disk → skip, non-blocking.
        r = self._run_hook(self._env(), sid="ghost")
        self.assertEqual(r.returncode, 0)
        self.assertIn("transcript not found", r.stderr)

    def test_dedup_guard_already_reflected(self) -> None:
        # A .reflected marker means the post-/work phase dispatch already mined
        # this session → the Stop hook must skip (avoid double-route).
        self._place_transcript("s2", self.proj)
        reflected = self.proj / ".harness" / "session-id-s2.reflected"
        reflected.write_text("session_id: s2\n", encoding="utf-8")
        r = self._run_hook(self._env(), sid="s2")
        self.assertEqual(r.returncode, 0)
        self.assertIn("already reflected", r.stderr)
        self.assertNotIn('"pass": "summary"', r.stdout)             # reflect.py not invoked
        self.assertTrue(reflected.is_file())

    def test_no_vault_graceful_skip_marker_untouched(self) -> None:
        # Repo-local / no-vault mode: reflect --route can't persist → the hook
        # reports + exits 0, and leaves the .start marker for a later pass.
        self._place_transcript("s3", self.proj)
        start = self.proj / ".harness" / "session-id-s3.start"
        start.write_text("session_id: s3\ntranscript: x\n", encoding="utf-8")
        r = self._run_hook(self._env(with_vault=False), sid="s3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(start.is_file(), "marker wrongly renamed without a vault")
        self.assertFalse((self.proj / ".harness" / "session-id-s3.reflected").exists())

    def test_graceful_skip_when_resolver_unavailable(self) -> None:
        bare = self.root / "barehome"
        bare.mkdir()
        env = self._env()
        env["HOME"] = str(bare)
        self._place_transcript("s4", self.proj)
        r = self._run_hook(env, sid="s4")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
