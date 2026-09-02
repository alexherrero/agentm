#!/usr/bin/env python3
"""The 2a migration script's apply semantics, pinned.

The live apply surfaced defect classes a dry run structurally cannot show
(the dry run never executes the collision or cleanup branches), and review
reproduced three more. This suite runs the real script against a fixture
vault + pre-populated engine dir and pins the doctrine:

  - vault-wins on file collisions;
  - directory collisions MERGE per-entry (never nest the vault dir inside
    the destination one);
  - report history (vault-lint-*.md) routes to vault diagnostics/lint,
    not into machine state;
  - staged run dirs (proposals.json marker) route to dream-runs, not the
    scratch sweep;
  - the forward-learning whitelist always lands somewhere load_sources()
    probes, creating the flat standards/ fallback if nothing exists;
  - a second apply is a clean no-op.

Run: python3 scripts/test_structural_2a_migration.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "migrate" / "structural_2a.sh"

# The migration is a POSIX operator-machine artifact (bash, mv, rmdir); the
# Windows runner's `bash` is the WSL stub with no distribution installed —
# there is no production execution path to test there.
if sys.platform == "win32":
    raise unittest.SkipTest("structural_2a.sh is POSIX-only; no bash on Windows CI")


def _build_fixture(root: Path) -> tuple[Path, Path]:
    vault = root / "Vault" / "Agent"
    state = root / "engine-state"
    meta = vault / "_meta"
    meta.mkdir(parents=True)
    state.mkdir(parents=True)

    # File collision: engine holds scratch leakage, vault holds production.
    (meta / "needs-your-eye.json").write_text('{"items": ["real"]}')
    (state / "needs-your-eye.json").write_text('{"items": []}')

    # Directory collision: pre-migration code run created the engine dir
    # with fresh state; the vault dir holds the production watermark.
    (meta / "forward-learning-cache").mkdir()
    (meta / "forward-learning-cache" / "state.json").write_text('{"watermark": "production"}')
    (state / "forward-learning-cache").mkdir()
    (state / "forward-learning-cache" / "state.json").write_text('{"watermark": "fresh-empty"}')

    # Report history is operator diagnostics, not machine state.
    (meta / "vault-lint-2026-08-27.md").write_text("# report\n")

    # The whitelist must land where load_sources() probes.
    (meta / "forward-learning-sources.json").write_text('{"sources": ["a"]}')

    # Scratch: one plain note, one staged run dir.
    scratch = vault / "desk" / "scratch"
    (scratch / "old-run").mkdir(parents=True)
    (scratch / "old-run" / "proposals.json").write_text("{}")
    (scratch / "note.md").write_text("scratch\n")
    return vault, state


def _apply(vault: Path, state: Path) -> str:
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--apply"],
        env={
            "MEMORY_VAULT_PATH": str(vault),
            "AGENTM_STATE_DIR": str(state),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(vault.parent.parent),
        },
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


class Structural2aApply(unittest.TestCase):
    def test_apply_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vault, state = _build_fixture(root)
            _apply(vault, state)

            # File collision: vault copy won.
            self.assertEqual(
                json.loads((state / "needs-your-eye.json").read_text())["items"],
                ["real"], "vault-wins on file collisions")

            # Directory collision: merged, vault file won, nothing nested.
            merged = state / "forward-learning-cache" / "state.json"
            self.assertEqual(json.loads(merged.read_text())["watermark"], "production")
            self.assertFalse(
                (state / "forward-learning-cache" / "forward-learning-cache").exists(),
                "a dir-dir collision must merge, never nest")

            # Report history landed in vault diagnostics, not machine state.
            self.assertTrue((vault / "diagnostics" / "lint" / "vault-lint-2026-08-27.md").exists())
            self.assertFalse((state / "vault-lint-2026-08-27.md").exists())

            # Staged run routed to dream-runs; plain scratch swept.
            self.assertTrue((state / "dream-runs" / "old-run" / "proposals.json").exists())
            sweeps = list(state.glob("scratch-sweep-*"))
            self.assertEqual(len(sweeps), 1)
            self.assertTrue((sweeps[0] / "note.md").exists())
            self.assertFalse((sweeps[0] / "old-run").exists(),
                             "a staged run must not disappear into the sweep")

            # The whitelist is where load_sources() probes (flat fallback
            # created — the fixture has no sibling standards dir).
            self.assertTrue((vault / "standards" / "forward-learning-sources.json").exists())

            # Second apply: clean no-op (idempotence survived the mv -f change).
            out2 = _apply(vault, state)
            self.assertIn("moved=0", out2.replace(" ", " "))

    def test_dry_run_names_the_whitelist_hop_and_staged_runs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vault, state = _build_fixture(root)
            proc = subprocess.run(
                ["bash", str(_SCRIPT)],
                env={"MEMORY_VAULT_PATH": str(vault),
                     "AGENTM_STATE_DIR": str(state),
                     "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                     "HOME": str(root)},
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("forward-learning-sources.json", proc.stdout,
                          "the dry run must show the whitelist's standards hop")
            self.assertIn("staged run dir", proc.stdout,
                          "the dry run must name the staged-run routing")


if __name__ == "__main__":
    unittest.main()
