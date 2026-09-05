#!/usr/bin/env python3
"""test_dream_skip_ported.py — the retirement switch (filing v2 part 6, task 6).

`dream.py --skip-ported` hands the three lanes the dreaming binary carries —
the suffix-backlog drain (`copies`), the calendar rollups (`calendar`) and
the lifecycle policy (`lifecycle`) — to the binary: the cycle no longer runs
them and its digest says so. Every other stage is untouched. The flag is
flipped together with `agentmdream run -apply`, after the overlap window.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import dream  # noqa: E402

# The copies family the parity recording already proves the Python drain
# collapses (copy-canon survives; copy-1 and copy-2 go superseded).
_FIXTURE = _HERE / "fixtures" / "dreaming-parity" / "vault" / "memory" / "procedural"
_FAMILY = ("copy-canon.md", "copy-1.md", "copy-2.md")


class SkipPortedTests(unittest.TestCase):
    """One vault and one state dir per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.vault = root / "vault"
        (self.vault / "memory" / "procedural").mkdir(parents=True)
        for name in _FAMILY:
            shutil.copy(_FIXTURE / name, self.vault / "memory" / "procedural" / name)
        self._env = {k: os.environ.get(k) for k in ("AGENTM_STATE_DIR", "MEMORY_VAULT_PATH")}
        os.environ["AGENTM_STATE_DIR"] = str(root / "state")
        os.environ.pop("MEMORY_VAULT_PATH", None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _status(self, name: str) -> str:
        text = (self.vault / "memory" / "procedural" / name).read_text(encoding="utf-8")
        return next(l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("status:"))

    def _cycle(self, **kw):
        return dream.run_dream_and_auto_apply(
            self.vault, run_id="skip-ported",
            log_root=self.vault.parent / "revert-log", lock_root=self.vault.parent / "locks", **kw,
        )

    def test_without_the_switch_the_cycle_runs_the_three_lanes(self) -> None:
        digest, _batch = self._cycle()
        self.assertTrue(any(p.stage == "suffix_backlog_drain" for p in digest.proposals), "the drain proposed the family")
        self.assertEqual(self._status("copy-canon.md"), "active")
        self.assertEqual({self._status("copy-1.md"), self._status("copy-2.md")}, {"superseded"})
        # The rollups ran and found no register; the policy ran and described the corpus.
        self.assertEqual(digest.rollups.get("skipped"), "no Calendar/ space")
        self.assertFalse(digest.lifecycle["summary"].startswith("ported:"), digest.lifecycle["summary"])

    def test_the_switch_hands_the_three_lanes_to_the_binary(self) -> None:
        digest, _batch = self._cycle(skip_ported=True)
        self.assertEqual([p for p in digest.proposals if p.stage == "suffix_backlog_drain"], [])
        for name in _FAMILY:
            self.assertEqual(self._status(name), "active", f"{name}: the drain must not run under --skip-ported")
        self.assertEqual(digest.rollups,
                         {"written": [], "refreshed": 0, "skipped": "ported: the dreaming binary owns the rollups"})
        self.assertEqual(digest.lifecycle["summary"], "ported: the dreaming binary owns the lifecycle policy")
        self.assertEqual(digest.lifecycle["considered"], 0)
        text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("ported: the dreaming binary owns the rollups", text)
        self.assertIn("ported: the dreaming binary owns the lifecycle policy", text)

    def test_the_cli_flag_reaches_the_cycle(self) -> None:
        rc = dream.main(["--vault-path", str(self.vault), "--run-id", "cli-skip", "--skip-ported",
                         "--log-root", str(self.vault.parent / "revert-log"),
                         "--lock-root", str(self.vault.parent / "locks")])
        self.assertEqual(rc, 0)
        for name in _FAMILY:
            self.assertEqual(self._status(name), "active")
        digest = (dream.engine_state.engine_state_dir() / "dream-runs" / "cli-skip" / "digest.md").read_text(encoding="utf-8")
        self.assertIn("ported: the dreaming binary owns the rollups", digest)
        self.assertIn("ported: the dreaming binary owns the lifecycle policy", digest)


if __name__ == "__main__":
    unittest.main()
