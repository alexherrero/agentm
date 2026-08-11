#!/usr/bin/env python3
"""Hook-firing tests for harness/hooks/memory-recall-prompt-submit/memory-recall-prompt-submit.ps1 (GH #92).

pwsh twin of test_memory_recall_prompt_submit_hook.py, following the same
convention as the memory-recall-session-start pair. Windows CI runs the .ps1
half of every hook and never the .sh half, so without this the prompt-submit
hook has firing coverage on exactly two of the three supported platforms.

Two fixture differences from the bash twin, both load-bearing:

1. memory-recall-prompt-submit.ps1 resolves recall.py via a RELATIVE path from
   the invocation cwd (.claude/skills/memory/scripts/recall.py), not the
   source_clones.agentm config bridge the bash hook uses. So the fixture copies
   the real memory scripts tree into the fixture project the way a real install
   lays it out, rather than faking a config — same as the session-start pwsh
   twin.

2. It still sets a fake HOME, which the session-start pwsh twin does not need.
   That hook's recall path never embeds; this one's does, and on a developer
   machine with `sentence-transformers` installed the embed costs seconds and
   blows the recall deadline (see the bash twin's docstring). Pointing HOME at
   an empty directory drops the user site-packages, which is the state CI runs
   in — so the test measures the hook's logic rather than whichever optional
   packages the machine happens to carry.

   Note this lever is POSIX-only: Windows resolves user site-packages from
   %APPDATA%, not $HOME. It happens not to matter, because Windows CI never
   installs `sentence-transformers` — but do not read a green Windows run as
   evidence that the fake HOME did anything there.

3. It sets an explicit RECALL_BUDGET_MS. The recall engine's 300ms
   prompt-submit budget is a production tuning constant, not the behavior these
   tests assert, and driving the hook end-to-end raced it: recall pays a FIXED
   setup cost before the corpus walk starts (opening
   the vec index, and where sqlite-vec actually loads — Windows, not macOS
   system Python — creating the index DB and loading a native extension). That
   cost is independent of corpus size, so a two-entry fixture vault does not
   escape it. Measured at 25-42ms of the 300ms budget on an idle M-series Mac.
   On a loaded Windows runner it can consume the whole budget, at which point
   `_bm25_search` sees the deadline already elapsed, reports `walked 0 of 2`,
   and `query()` discards the stream — the hook then exits 0 having printed
   nothing, and `assertIn(_SENTINEL, r.stdout)` fails against ''. That is the
   observed flake (PR #417, commit 1cb2a886, on a docs-only diff).

   The budget is deliberately below VEC_COLD_EMBED_MIN_BUDGET_MS so the vec
   half is still declined on affordability exactly as in production; widening
   past it would buy a real cold model load on any developer machine that has
   `sentence-transformers`. So this makes the lexical path deterministic
   without changing which streams run.

Skipped when pwsh isn't on PATH (runs on macOS CI too, not Windows-only).

Run: python3 scripts/test_memory_recall_prompt_submit_hook_pwsh.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_HOOK = (_REPO / "harness" / "hooks" / "memory-recall-prompt-submit"
         / "memory-recall-prompt-submit.ps1")
_MEMORY_SCRIPTS_SRC = _REPO / "harness" / "skills" / "memory" / "scripts"
_PWSH = shutil.which("pwsh")

# Ten times the production prompt-submit budget, and two orders of magnitude
# above the fixed pre-walk setup cost measured on an idle Mac — but still under
# the engine's VEC_COLD_EMBED_MIN_BUDGET_MS (5000ms), so the vec half is
# declined on affordability here exactly as it is in production. See docstring
# point 3.
_TEST_BUDGET_MS = 3000

_SENTINEL = "PROMPT_RECALL_SENTINEL_BODY"
_QUERY_TERM = "quokkaflange"
_PROMPT = f"how does the {_QUERY_TERM} protocol handle widget alignment?"
_DISTRACTOR_SLUG = "unrelated-entry"


@unittest.skipIf(_PWSH is None, "pwsh not on PATH")
class TestMemoryRecallPromptSubmitHookPwsh(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls._cls_tmp = tempfile.TemporaryDirectory()
        cls._skills_dst = (Path(cls._cls_tmp.name) / "skills-fixture" / ".claude"
                           / "skills" / "memory" / "scripts")
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
        self.proj = self.root / "proj"
        self.proj.mkdir(parents=True)
        # Lay out .claude/skills/memory/scripts/ the way a real install does
        # (the hook resolves recall.py relative to cwd).
        shutil.copytree(self._skills_dst, self.proj / ".claude" / "skills" / "memory" / "scripts")
        self.fake_home = self.root / "home"
        self.fake_home.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _entry(self, body: str, *, name: str) -> str:
        return (
            f"---\nname: {name}\nkind: convention\n"
            f"description: a fixture entry\n---\n\n{body}\n"
        )

    def _seed_recallable(self, *, always_load: bool = False) -> None:
        rel = Path("personal") / "_always-load" if always_load else Path("personal")
        target = self.vault / rel
        target.mkdir(parents=True, exist_ok=True)
        (target / "matching-entry.md").write_text(
            self._entry(
                f"{_SENTINEL} the {_QUERY_TERM} protocol governs widget alignment.",
                name="matching-entry",
            ),
            encoding="utf-8",
        )
        (self.vault / "personal" / f"{_DISTRACTOR_SLUG}.md").write_text(
            self._entry(
                "Sourdough hydration ratios for a cold winter kitchen.",
                name=_DISTRACTOR_SLUG,
            ),
            encoding="utf-8",
        )

    def _env(self, with_vault: bool = True, **over) -> dict:
        env = {**os.environ, "HOME": str(self.fake_home)}
        env.pop("AGENTM_INSTALL_PREFIX", None)
        env["AGENTM_RECALL_HISTORY"] = str(self.root / "recall-history.jsonl")
        env["RECALL_BUDGET_MS"] = str(_TEST_BUDGET_MS)
        if with_vault:
            env["MEMORY_VAULT_PATH"] = str(self.vault)
        else:
            env.pop("MEMORY_VAULT_PATH", None)
        env.update(over)
        return env

    def _run_hook(self, env: dict, prompt: str | None = _PROMPT,
                  raw_payload: str | None = None):
        if raw_payload is None:
            payload: dict = {"hookEventName": "UserPromptSubmit", "session_id": "sess-1",
                             "cwd": str(self.proj)}
            if prompt is not None:
                payload["prompt"] = prompt
            raw_payload = json.dumps(payload)
        return subprocess.run(
            [_PWSH, "-NoProfile", "-File", str(_HOOK)],
            input=raw_payload, env=env, cwd=str(self.proj),
            capture_output=True, text=True,
        )

    # ── fires ────────────────────────────────────────────────────────────────

    def test_fires_recall_injects_the_matching_entry(self) -> None:
        self._seed_recallable()
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(_SENTINEL, r.stdout)
        self.assertIn("### matching-entry", r.stdout)
        self.assertIn("Loaded 1 relevant entries: matching-entry", r.stderr)

    def test_the_budget_env_reaches_the_engine_through_the_hook(self) -> None:
        # The generous budget in _env() is worth nothing unless pwsh actually
        # forwards the variable, and a hook that silently stopped doing so
        # would just restore the flake rather than fail anything. So starve it
        # deliberately and require the starvation to bite: at 1ms the corpus
        # walk is discarded before it reaches entry 0 and the hook prints
        # nothing — the exact shape of the flake. If the environment ever stops
        # reaching the engine, this run falls back to the 300ms default, the
        # entry gets injected, and this test fails.
        #
        # Not timing-dependent in the way the injection assertions were: the
        # fixed pre-walk setup is tens of milliseconds on any machine, so a 1ms
        # deadline is always already elapsed. Starving is robust; racing is not.
        self._seed_recallable()
        r = self._run_hook(self._env(RECALL_BUDGET_MS="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_recall_is_scoped_to_the_prompt(self) -> None:
        self._seed_recallable()
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(_DISTRACTOR_SLUG, r.stdout)

    def test_always_load_entries_are_deduped_not_reinjected(self) -> None:
        self._seed_recallable(always_load=True)
        r = self._run_hook(self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(_SENTINEL, r.stdout)
        self.assertIn("Loaded 0 relevant entries", r.stderr)

    # ── silent + non-blocking ────────────────────────────────────────────────

    def test_no_vault_is_silent_but_exits_zero(self) -> None:
        self._seed_recallable()
        r = self._run_hook(self._env(with_vault=False))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_missing_vault_path_is_silent_but_exits_zero(self) -> None:
        r = self._run_hook(self._env(MEMORY_VAULT_PATH=str(self.root / "gone")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("vault path not found", r.stderr)

    def test_malformed_stdin_is_nonblocking(self) -> None:
        self._seed_recallable()
        r = self._run_hook(self._env(), raw_payload="this is not json{")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("no prompt on stdin", r.stderr)

    def test_payload_without_a_prompt_field_is_nonblocking(self) -> None:
        self._seed_recallable()
        r = self._run_hook(self._env(), prompt=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("no prompt on stdin", r.stderr)

    def test_empty_stdin_is_nonblocking(self) -> None:
        r = self._run_hook(self._env(), raw_payload="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_graceful_skip_when_the_memory_skill_is_absent(self) -> None:
        # No .claude/skills/ under cwd → the hook can't resolve recall.py and
        # exits 0 before doing anything. The bash twin's equivalent goes through
        # a bare HOME; this hook has no config bridge to strip, so the skill
        # tree itself is the thing that has to be missing.
        bare = self.root / "bare-proj"
        bare.mkdir()
        r = subprocess.run(
            [_PWSH, "-NoProfile", "-File", str(_HOOK)],
            input=json.dumps({"prompt": _PROMPT}), env=self._env(),
            cwd=str(bare), capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
