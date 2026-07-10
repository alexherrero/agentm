#!/usr/bin/env python3
"""check-kind-taxonomy.sh — the report-only V6-15 advisory gate (check-all.sh
task 4, PLAN-v6-15-v6-18-typed-object-moc).

Drives the real script via subprocess. Asserts the one property that makes
this gate report-only rather than blocking: its exit code is always 0,
whether MEMORY_VAULT_PATH is unset, points at a clean vault, or points at a
vault with real kind-taxonomy violations.

Run directly:
    cd scripts && python3 -m unittest test_check_kind_taxonomy
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check-kind-taxonomy.sh"


def _run(env_overrides: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("MEMORY_VAULT_PATH", None)
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(_SCRIPT)], cwd=_REPO_ROOT,
        capture_output=True, text=True, env=env,
    )


@unittest.skipIf(os.name == "nt", "bash gate — POSIX only")
class TestCheckKindTaxonomyAlwaysExitsZero(unittest.TestCase):
    def test_unset_vault_path_skips_and_exits_zero(self):
        result = _run({})
        self.assertEqual(result.returncode, 0)
        self.assertIn("skipping", result.stdout)

    def test_nonexistent_vault_path_skips_and_exits_zero(self):
        result = _run({"MEMORY_VAULT_PATH": "/nonexistent/path/xyz"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("skipping", result.stdout)

    def test_clean_vault_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal").mkdir()
            (vault / "personal" / "a.md").write_text(
                "---\nkind: fix\nstatus: active\ncreated: 2026-07-10\n---\n\nbody\n",
                encoding="utf-8",
            )
            result = _run({"MEMORY_VAULT_PATH": str(vault)})
            self.assertEqual(result.returncode, 0)

    def test_vault_with_violations_still_exits_zero(self):
        # The whole point of this gate: real, known-messy real-vault data
        # (malformed / unrecognized kind values) must never fail the battery.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal").mkdir()
            (vault / "personal" / "a.md").write_text(
                "---\nkind: handoff-artifact (verdict memo)\nstatus: active\ncreated: 2026-07-10\n---\n\nbody\n",
                encoding="utf-8",
            )
            result = _run({"MEMORY_VAULT_PATH": str(vault)})
            self.assertEqual(result.returncode, 0)
            self.assertIn("malformed", result.stdout)


if __name__ == "__main__":
    unittest.main()
