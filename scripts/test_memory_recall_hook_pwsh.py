#!/usr/bin/env python3
"""Hook-firing tests for harness/hooks/memory-recall-session-start/memory-recall-session-start.ps1 (GH #72).

pwsh twin of test_memory_recall_hook.py. Real, load-bearing difference from
the bash hook found while writing this: memory-recall-session-start.ps1
resolves recall.py via a RELATIVE path from the invocation cwd
(.claude/skills/memory/scripts/recall.py), not the source_clones.agentm
config-bridge the bash hook and its test fixture use. The fixture here
copies the real memory scripts tree into the fixture project's
.claude/skills/memory/scripts/ instead of faking HOME, so recall.py finds
its sibling modules the same way a real
install lays them out.

Skipped when pwsh isn't on PATH (runs on macOS CI too, not Windows-only).

Run: python3 scripts/test_memory_recall_hook_pwsh.py
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
_HOOK = _REPO / "harness" / "hooks" / "memory-recall-session-start" / "memory-recall-session-start.ps1"
_MEMORY_SCRIPTS_SRC = _REPO / "harness" / "skills" / "memory" / "scripts"
_PWSH = shutil.which("pwsh")

_SENTINEL = "ALWAYS_LOAD_SENTINEL_BODY"


@unittest.skipIf(_PWSH is None, "pwsh not on PATH")
class TestMemoryRecallSessionStartHookPwsh(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls._cls_tmp = tempfile.TemporaryDirectory()
        cls._skills_dst = Path(cls._cls_tmp.name) / "skills-fixture" / ".claude" / "skills" / "memory" / "scripts"
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
        (self.vault / "projects").mkdir(parents=True)
        self.proj = self.root / "proj"
        (self.proj / ".harness").mkdir(parents=True)
        # Lay out .claude/skills/memory/scripts/ the way a real install does
        # (memory-recall-session-start.ps1 resolves recall.py relative to cwd).
        dst = self.proj / ".claude" / "skills" / "memory" / "scripts"
        shutil.copytree(self._skills_dst, dst)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_always_load(self) -> None:
        al = self.vault / "personal" / "_always-load"
        al.mkdir(parents=True, exist_ok=True)
        (al / "conv.md").write_text(
            "---\nname: conv\ndescription: a test convention\nmetadata:\n  type: reference\n---\n\n"
            + _SENTINEL + "\n",
            encoding="utf-8",
        )

    def _env(self, with_vault: bool = True, **over) -> dict:
        env = {**os.environ}
        env.pop("AGENTM_INSTALL_PREFIX", None)
        if with_vault:
            env["MEMORY_VAULT_PATH"] = str(self.vault)
        else:
            env.pop("MEMORY_VAULT_PATH", None)
        env.update(over)
        return env

    def _run_hook(self, env: dict, sid: str = "sess-1", raw_payload: str | None = None,
                  transcript_path: str | None = None, source: str | None = None):
        if raw_payload is None:
            payload = {"session_id": sid, "cwd": str(self.proj)}
            if transcript_path is not None:
                payload["transcript_path"] = transcript_path
            if source is not None:
                payload["source"] = source
            raw_payload = json.dumps(payload)
        return subprocess.run(
            [_PWSH, "-NoProfile", "-File", str(_HOOK)],
            input=raw_payload, env=env, cwd=str(self.proj), capture_output=True, text=True,
        )

    def test_fires_recall_emits_always_load_entry(self) -> None:
        self._seed_always_load()
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(_SENTINEL, r.stdout)

    def test_writes_session_marker_on_sessionstart(self) -> None:
        r = self._run_hook(self._env(), sid="abc123")
        self.assertEqual(r.returncode, 0, r.stderr)
        marker = self.proj / ".harness" / "session-id-abc123.start"
        self.assertTrue(marker.is_file(), "crash-recovery marker not written")
        body = marker.read_text(encoding="utf-8")
        self.assertIn("session_id: abc123", body)
        self.assertIn("transcript:", body)

    def test_marker_records_the_payload_transcript_path(self) -> None:
        # A path no slug could produce, so this only passes if the hook read the
        # payload. Parity with the bash twin's test of the same name.
        elsewhere = str(self.root / "elsewhere" / "conversation.jsonl")
        r = self._run_hook(self._env(), sid="p1", transcript_path=elsewhere)
        self.assertEqual(r.returncode, 0, r.stderr)
        body = (self.proj / ".harness" / "session-id-p1.start").read_text(encoding="utf-8")
        self.assertIn(f"transcript: {elsewhere}", body)

    def test_marker_records_the_sessionstart_source(self) -> None:
        r = self._run_hook(self._env(), sid="p2", source="compact")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = (self.proj / ".harness" / "session-id-p2.start").read_text(encoding="utf-8")
        self.assertIn("source: compact", body)

    def test_marker_source_defaults_when_the_payload_omits_it(self) -> None:
        r = self._run_hook(self._env(), sid="p3")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = (self.proj / ".harness" / "session-id-p3.start").read_text(encoding="utf-8")
        self.assertIn("source: unknown", body)

    def test_no_vault_recall_silent_but_marker_written(self) -> None:
        self._seed_always_load()
        r = self._run_hook(self._env(with_vault=False))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(_SENTINEL, r.stdout)
        self.assertTrue((self.proj / ".harness" / "session-id-sess-1.start").is_file())

    def test_malformed_stdin_is_nonblocking(self) -> None:
        r = self._run_hook(self._env(), raw_payload="this is not json{")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(any((self.proj / ".harness").glob("session-id-*.start")))

    def test_empty_stdin_is_nonblocking(self) -> None:
        r = self._run_hook(self._env(), raw_payload="")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
