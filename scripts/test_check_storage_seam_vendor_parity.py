#!/usr/bin/env python3
"""Unit test for check-vendored-parity.sh's `storage-seam` mode — subprocess-invokes
the gate against the real repo tree (mirrors test_check_workflow_parity.py's
shape for a shell-script gate).

Was scripts/check-storage-seam-vendor-parity.sh (a standalone script) — CONS-1
merged it into one of check-vendored-parity.sh's five modes. Same invariant,
just invoked as `check-vendored-parity.sh storage-seam` instead of a bare script.

Skipped on non-POSIX (the gate is a bash script; a plain `subprocess.run(["bash",
...])` on Windows CI runners can resolve to the WSL launcher instead of Git
Bash, with no installed WSL distribution behind it — matching the other
bash-driving test suites in this directory, e.g. test_check_workflow_parity.py.
"""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "check-vendored-parity.sh"


@unittest.skipIf(os.name == "nt", "bash gate — POSIX only")
class TestStorageSeamVendorParity(unittest.TestCase):
    def test_real_vendored_pair_is_clean(self):
        proc = subprocess.run(["bash", str(GATE), "storage-seam"], capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("clean", proc.stdout)


if __name__ == "__main__":
    unittest.main()
