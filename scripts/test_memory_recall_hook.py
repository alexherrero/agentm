#!/usr/bin/env python3
"""Hook-firing tests for harness/hooks/memory-recall-session-start (Hardening I #45 task 6).

Drives the bash hook as a subprocess with a synthetic SessionStart event JSON on
stdin + a fixture vault, proving it ACTUALLY FIRES (the V4 #39 class of bug — a
hook that lands but silently no-ops): it must emit the always-load entries,
graceful-skip (exit 0) when the memory skill / vault is absent, and NEVER block
session boot. It writes no `.harness/session-id-*.start` marker: the markers
retired with task 199, when mining moved to the night.

Both state modes are covered via the vault axis: vault-present (recall emits) vs
no-vault / repo-local (recall silent, exit 0).

Hermetic: a fake `HOME` whose `.claude/.agentm-config.json` resolves the memory
scripts to THIS repo via `source_clones.agentm` (CI has no `~/.claude` nor a
`~/Antigravity/agentm` clone, so without this the hook would correctly skip and
the fires-case couldn't be exercised). The fixture vault is selected via
MEMORY_VAULT_PATH (env wins in the resolver).

Run: python3 scripts/test_memory_recall_hook.py
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
_HOOK = _REPO / "harness" / "hooks" / "memory-recall-session-start" / "memory-recall-session-start.sh"

_SENTINEL = "ALWAYS_LOAD_SENTINEL_BODY"


@unittest.skipIf(os.name == "nt", "bash hook — POSIX only")
class TestMemoryRecallSessionStartHook(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "desk/projects").mkdir(parents=True)
        self.proj = self.root / "proj"
        (self.proj / ".harness").mkdir(parents=True)
        # Fake HOME → .agentm-config.json points the memory-script resolver at THIS repo.
        self.fake_home = self.root / "home"
        (self.fake_home / ".claude").mkdir(parents=True)
        (self.fake_home / ".claude" / ".agentm-config.json").write_text(
            json.dumps({"schema_version": 2, "source_clones": {"agentm": str(_REPO)}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_always_load(self) -> None:
        al = self.vault / "memory" / "_always-load"
        al.mkdir(parents=True, exist_ok=True)
        (al / "conv.md").write_text(
            "---\nname: conv\ndescription: a test convention\nmetadata:\n  type: reference\n---\n\n"
            + _SENTINEL + "\n",
            encoding="utf-8",
        )

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

    def _run_hook(self, env: dict, sid: str = "sess-1", cwd: Path | None = None,
                  raw_payload: str | None = None, transcript_path: str | None = None,
                  source: str | None = None):
        cwd = cwd or self.proj
        if raw_payload is None:
            payload = {"session_id": sid, "cwd": str(cwd)}
            if transcript_path is not None:
                payload["transcript_path"] = transcript_path
            if source is not None:
                payload["source"] = source
            raw_payload = json.dumps(payload)
        return subprocess.run(
            ["bash", str(_HOOK)], input=raw_payload, env=env,
            cwd=str(cwd), capture_output=True, text=True,
        )

    # ── fires ────────────────────────────────────────────────────────────────

    def test_fires_recall_emits_always_load_entry(self) -> None:
        self._seed_always_load()
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(_SENTINEL, r.stdout)              # recall actually ran (not silently skipped)
        self.assertIn("Loaded 1", r.stderr)             # transparency line

    def test_writes_no_session_marker(self) -> None:
        # Task 199: nothing reads a session marker any more, so SessionStart
        # writes none — on startup, resume or compact, with or without a
        # payload transcript path.
        for sid, src, tp in (("abc123", None, None), ("p1", "compact", str(self.root / "t.jsonl")),
                             ("p2", "resume", None)):
            kwargs = {"sid": sid}
            if src:
                kwargs["source"] = src
            if tp:
                kwargs["transcript_path"] = tp
            r = self._run_hook(self._env(), **kwargs)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list((self.proj / ".harness").glob("session-id-*")), [])

    # ── graceful-skip / non-blocking (both modes) ─────────────────────────────

    def test_no_vault_recall_silent(self) -> None:
        # Repo-local / no-vault mode: recall has nothing to load, and the hook
        # never blocks.
        self._seed_always_load()  # present, but unreachable without MEMORY_VAULT_PATH
        r = self._run_hook(self._env(with_vault=False))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(_SENTINEL, r.stdout)           # no vault → nothing emitted

    def test_graceful_skip_when_resolver_unavailable(self) -> None:
        # Bare HOME (no .agentm-config.json) + project has no .claude/skills/ →
        # recall.py is unresolvable → hook exits 0 before recall, emits nothing.
        bare = self.root / "barehome"
        bare.mkdir()
        env = self._env()
        env["HOME"] = str(bare)
        r = self._run_hook(env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_malformed_stdin_is_nonblocking(self) -> None:
        r = self._run_hook(self._env(), raw_payload="this is not json{")
        self.assertEqual(r.returncode, 0, r.stderr)
        # Unparseable payload → the hook still exits 0.

    def test_empty_stdin_is_nonblocking(self) -> None:
        r = self._run_hook(self._env(), raw_payload="")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
