#!/usr/bin/env python3
"""test_dream_retired_lanes.py — the lanes the dreaming binary owns are gone
from the Python cycle (filing v2 part 6, the takeover of 2026-09-05).

The suffix-backlog drain, the calendar rollups and the lifecycle policy's
sinking and lifting left `dream.py`; the cycle reads the lifecycle axis,
stages the archive proposals for the confirm surface, and runs the stages the
binary does not carry. A full cycle over a copies family, a register with
closed weeks and a silent memory must touch none of them.
"""
from __future__ import annotations

import datetime as dt
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

# The copies family the parity recording collapses (copy-canon survives;
# copy-1 and copy-2 go superseded) — in the binary's hands now.
_FIXTURE = _HERE / "fixtures" / "dreaming-parity" / "vault" / "memory" / "procedural"
_FAMILY = ("copy-canon.md", "copy-1.md", "copy-2.md")


class RetiredLanesTests(unittest.TestCase):
    """One vault and one state dir per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.vault = root / "vault"
        (self.vault / "memory" / "procedural").mkdir(parents=True)
        for name in _FAMILY:
            shutil.copy(_FIXTURE / name, self.vault / "memory" / "procedural" / name)
        # A register with closed weeks behind it: the rollups would have
        # written reviews here.
        (self.vault / "Calendar").mkdir()
        # A memory silent for 500 days: the policy would have sunk it.
        created = (dt.date.today() - dt.timedelta(days=500)).isoformat()
        self.silent = self.vault / "memory" / "procedural" / "silent.md"
        self.silent.write_text(f"---\ntitle: silent\nkind: workflow\nstatus: active\nslug: silent\n"
                               f"lifecycle: active\ncreated: {created}\n---\n\nnobody asked\n", encoding="utf-8")
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

    def _field(self, path: Path, key: str) -> str:
        text = path.read_text(encoding="utf-8")
        return next(l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith(key + ":"))

    def test_a_full_cycle_leaves_the_binarys_lanes_alone(self) -> None:
        digest, batch = dream.run_dream_and_auto_apply(
            self.vault, run_id="retired", log_root=self.vault.parent / "revert-log", lock_root=self.vault.parent / "locks",
        )
        # The drain: no proposal, no collapse.
        self.assertEqual([p for p in digest.proposals if p.stage == "suffix_backlog_drain"], [])
        for name in _FAMILY:
            self.assertEqual(self._field(self.vault / "memory" / "procedural" / name, "status"), "active", name)
        # The rollups: no review written into the register.
        self.assertEqual(sorted(p.name for p in (self.vault / "Calendar").rglob("*-review.md")), [])
        # The policy: the silent memory is still active, and nothing was journaled.
        self.assertEqual(self._field(self.silent, "lifecycle"), "active")
        import lifecycle_transitions as lt
        self.assertEqual(lt.journal_entries(), [])
        # The cycle still reads the axis and says whose the moves are.
        self.assertIn("active", digest.lifecycle["summary"])
        text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("sinking and lifting are the dreaming binary's", text)
        self.assertNotIn("Calendar rollups:", text)

    def test_the_retirement_switch_is_gone(self) -> None:
        with self.assertRaises(SystemExit):
            dream.main(["--vault-path", str(self.vault), "--run-id", "cli-gone", "--skip-ported"])


if __name__ == "__main__":
    unittest.main()
