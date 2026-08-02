#!/usr/bin/env python3
"""Hook-firing tests for harness/hooks/memory-reflect-stop/memory-reflect-stop.ps1 (GH #72).

pwsh twin of test_memory_reflect_stop_hook.py. Like memory-recall-session-
start.ps1, this hook resolves reflect.py via a RELATIVE path from cwd
(.claude/skills/memory/scripts/reflect.py), not the source_clones.agentm
bridge the bash hook uses — the fixture copies the real memory scripts
tree into place instead of faking HOME for that purpose. HOME is still
faked here, though: the hook computes its transcript path as
$HOME/.claude/projects/<cwd-slug>/<sid>.jsonl, so the fixture needs a real
transcript file there.

Skipped when pwsh isn't on PATH (runs on macOS CI too, not Windows-only).

Run: python3 scripts/test_memory_reflect_stop_hook_pwsh.py
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
_HOOK = _REPO / "harness" / "hooks" / "memory-reflect-stop" / "memory-reflect-stop.ps1"
_MEMORY_SCRIPTS_SRC = _REPO / "harness" / "skills" / "memory" / "scripts"
_PWSH = shutil.which("pwsh")

_TRANSCRIPT = (
    '{"type":"user","message":{"role":"user","content":"do the thing"}}\n'
    '{"type":"assistant","message":{"role":"assistant","content":"done"}}\n'
)


@unittest.skipIf(_PWSH is None, "pwsh not on PATH")
class TestMemoryReflectStopHookPwsh(unittest.TestCase):

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
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "personal").mkdir(parents=True)
        (self.vault / "_inbox").mkdir(parents=True)
        self.proj = self.root / "proj"
        (self.proj / ".harness").mkdir(parents=True)
        dst = self.proj / ".claude" / "skills" / "memory" / "scripts"
        shutil.copytree(self._skills_dst, dst)
        self.fake_home = self.root / "home"
        (self.fake_home / ".claude").mkdir(parents=True)

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
        # The hook's FALLBACK path, reached only when the payload carries no
        # `transcript_path`: $CwdSlug = "-" + (($Cwd -replace '[\\/]', '-')
        # -replace ':', ''). Both \ and / become -, then any : is stripped
        # outright (Windows drive-letter colons) — this hook runs for real on
        # Windows CI, so the colon strip isn't a POSIX-only no-op here.
        #
        # This states the convention rather than reading the hook's line, so it
        # stays a mirror of a sort — but a mirror of a fallback the payload
        # branch now shadows, and the payload branch is pinned behaviorally
        # below against a path no slug can produce.
        slug = "-" + str(cwd).replace("\\", "-").replace("/", "-").replace(":", "")
        return self.fake_home / ".claude" / "projects" / slug / f"{sid}.jsonl"

    def _place_transcript(self, sid: str, cwd: Path) -> Path:
        tp = self._transcript_path(sid, cwd)
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text(_TRANSCRIPT, encoding="utf-8")
        return tp

    def _run_hook(self, env: dict, sid: str = "s1", raw_payload: str | None = None,
                  transcript_path: str | None = None):
        if raw_payload is None:
            payload = {"session_id": sid, "cwd": str(self.proj)}
            if transcript_path is not None:
                payload["transcript_path"] = transcript_path
            raw_payload = json.dumps(payload)
        return subprocess.run(
            [_PWSH, "-NoProfile", "-File", str(_HOOK)],
            input=raw_payload, env=env, cwd=str(self.proj), capture_output=True, text=True,
        )

    def test_fires_reflect_and_renames_marker(self) -> None:
        # No `transcript_path` on this payload — the computed-fallback branch.
        self._place_transcript("s1", self.proj)
        start = self.proj / ".harness" / "session-id-s1.start"
        start.write_text("session_id: s1\ntranscript: x\n", encoding="utf-8")
        r = self._run_hook(self._env(), sid="s1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"pass": "summary"', r.stdout)
        self.assertFalse(start.exists(), ".start marker not renamed")
        self.assertTrue((self.proj / ".harness" / "session-id-s1.reflected").is_file())

    def test_payload_transcript_path_is_used_verbatim(self) -> None:
        # Outside the fake HOME's projects tree entirely, so no slug — POSIX or
        # Windows — could reach it. The pwsh fallback slug was always a guess at
        # an unconfirmed Windows convention; this is the branch that retires it.
        elsewhere = self.root / "elsewhere" / "conversation.jsonl"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text(_TRANSCRIPT, encoding="utf-8")
        start = self.proj / ".harness" / "session-id-p1.start"
        start.write_text("session_id: p1\ntranscript: x\n", encoding="utf-8")
        r = self._run_hook(self._env(), sid="p1", transcript_path=str(elsewhere))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"pass": "summary"', r.stdout)
        self.assertTrue((self.proj / ".harness" / "session-id-p1.reflected").is_file())

    def test_absent_payload_transcript_path_skips_without_guessing(self) -> None:
        # The computed transcript is deliberately present and must stay unmined.
        self._place_transcript("p2", self.proj)
        ghost = self.root / "nope" / "gone.jsonl"
        r = self._run_hook(self._env(), sid="p2", transcript_path=str(ghost))
        self.assertEqual(r.returncode, 0)
        self.assertNotIn('"pass": "summary"', r.stdout)

    def test_graceful_skip_no_stdin(self) -> None:
        r = self._run_hook(self._env(), raw_payload="")
        self.assertEqual(r.returncode, 0)

    def test_graceful_skip_transcript_missing(self) -> None:
        r = self._run_hook(self._env(), sid="ghost")
        self.assertEqual(r.returncode, 0)
        self.assertIn("transcript not found", r.stderr)

    def test_dedup_guard_already_reflected(self) -> None:
        self._place_transcript("s2", self.proj)
        reflected = self.proj / ".harness" / "session-id-s2.reflected"
        reflected.write_text("session_id: s2\n", encoding="utf-8")
        r = self._run_hook(self._env(), sid="s2")
        self.assertEqual(r.returncode, 0)
        self.assertIn("already reflected", r.stderr)
        self.assertNotIn('"pass": "summary"', r.stdout)

    def test_no_vault_graceful_skip_marker_untouched(self) -> None:
        self._place_transcript("s3", self.proj)
        start = self.proj / ".harness" / "session-id-s3.start"
        start.write_text("session_id: s3\ntranscript: x\n", encoding="utf-8")
        r = self._run_hook(self._env(with_vault=False), sid="s3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(start.is_file(), "marker wrongly renamed without a vault")
        self.assertFalse((self.proj / ".harness" / "session-id-s3.reflected").exists())


if __name__ == "__main__":
    unittest.main()
