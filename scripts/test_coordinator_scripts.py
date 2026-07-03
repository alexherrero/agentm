#!/usr/bin/env python3
"""CLI-firing smoke tests for the three V5-11 coordinator scripts (R0.9-adjacent
task, agentmEngine#4).

All three (`readiness.py`, `standup.py`, `merge_order.py`) crashed with
`TypeError: harness_state_dir() missing 1 required positional argument:
'resolution'` on their default (no `--harness-dir`) invocation —
`harness_state_dir()` has required a `resolution` dict since at least V5, but
each CLI's `_main()` called it with zero arguments. The existing per-script
test files (`test_readiness.py` etc.) only exercise the internal report
builders directly, never `_main()` — 100% logic coverage, 0% CLI-wiring
coverage, exactly why this went unnoticed.

Drives each script as a REAL subprocess (not an in-process function call)
with `cwd` pointed at a bare fixture project dir (a `.harness/` with no
`vault_project`, so `resolve_project` resolves the device-local fallback) —
the exact code path that crashed. Asserts no `TypeError` traceback and a
clean exit.

Run: python3 scripts/test_coordinator_scripts.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent

_SCRIPTS = ["readiness.py", "standup.py", "merge_order.py"]


class TestCoordinatorScriptsDefaultInvocation(unittest.TestCase):
    """R0.9 / agentmEngine#4: default (no --harness-dir) invocation must not
    crash with a TypeError from a missing harness_state_dir() argument."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "bare-project"
        (self.project / ".harness").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, script: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_HERE / script)],
            cwd=str(self.project),
            capture_output=True, text=True, timeout=30,
        )

    def test_no_scripts_crash_with_typeerror(self) -> None:
        for script in _SCRIPTS:
            with self.subTest(script=script):
                r = self._run(script)
                self.assertNotIn("TypeError", r.stderr, msg=r.stderr)
                self.assertNotIn("Traceback", r.stderr, msg=r.stderr)
                self.assertEqual(r.returncode, 0, msg=f"stdout={r.stdout!r} stderr={r.stderr!r}")

    def test_explicit_harness_dir_still_works(self) -> None:
        """The pre-existing --harness-dir override path is unaffected."""
        for script in _SCRIPTS:
            with self.subTest(script=script):
                r = subprocess.run(
                    [sys.executable, str(_HERE / script), "--harness-dir", str(self.project / ".harness")],
                    cwd=str(self.project),
                    capture_output=True, text=True, timeout=30,
                )
                self.assertNotIn("TypeError", r.stderr, msg=r.stderr)
                self.assertEqual(r.returncode, 0, msg=f"stdout={r.stdout!r} stderr={r.stderr!r}")


if __name__ == "__main__":
    unittest.main()
