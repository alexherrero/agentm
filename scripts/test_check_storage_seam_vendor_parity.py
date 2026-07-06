#!/usr/bin/env python3
"""Unit test for check-storage-seam-vendor-parity.sh — subprocess-invokes
the gate against the real repo tree (mirrors test_check_workflow_parity.py's
shape for a shell-script gate)."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "check-storage-seam-vendor-parity.sh"


class TestStorageSeamVendorParity(unittest.TestCase):
    def test_real_vendored_pair_is_clean(self):
        proc = subprocess.run(["bash", str(GATE)], capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("clean", proc.stdout)


if __name__ == "__main__":
    unittest.main()
