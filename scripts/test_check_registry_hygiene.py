#!/usr/bin/env python3
"""Unit tests for scripts/check-registry-hygiene.py.

The gate exists because tests that build a fixture repo in a temp directory and
register it leave an entry in the operator's live `Agent/_meta/repos.json` whose
`root_path` died with the test. Three accumulated by 2026-08-10. The registry is
then permanently modified-but-uncommitted, and since the daemon commits markdown
only, nothing clears it and `agentmd gate corpus-write` stays shut.

Every fixture below is a hand-written literal. None is built by asking the
implementation what it would produce — a check that computes its expectation
with the implementation's own logic proves only that they agree with each other.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
GATE = _HERE / "check-registry-hygiene.py"

CLEAN = {
    "version": 1,
    "repos": [
        {"slug": "agentm", "root_path": "/srv/code/agentm"},
        {"slug": "crickets", "root_path": "/srv/code/crickets"},
    ],
}

# The exact shape the three real leaks had on 2026-08-10.
LEAKED_MACOS = {
    "version": 1,
    "repos": [
        {"slug": "agentm", "root_path": "/srv/code/agentm"},
        {"slug": "redetect-demo",
         "root_path": "/var/folders/2y/q80rcfrs6gd_z18jlh17k9wm0000gn/T/tmpnrbwfs7z/repo"},
    ],
}

LEAKED_PRIVATE_VAR = {
    "version": 1,
    "repos": [
        {"slug": "novault-marker",
         "root_path": "/private/var/folders/2y/abc/T/tmpo_sz91ml/repo"},
    ],
}

LEAKED_TMP = {
    "version": 1,
    "repos": [{"slug": "redetect-cli", "root_path": "/tmp/tmpqhvvzds9/repo"}],
}


def run_gate(registry_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--registry", str(registry_path)],
        capture_output=True, text=True,
    )


class TestRegistryHygiene(unittest.TestCase):
    def _write(self, td: str, payload) -> Path:
        p = Path(td) / "repos.json"
        if isinstance(payload, str):
            p.write_text(payload, encoding="utf-8")
        else:
            p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return p

    def test_clean_registry_passes(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_gate(self._write(td, CLEAN))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("clean", res.stdout)

    def test_var_folders_path_fails(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_gate(self._write(td, LEAKED_MACOS))
        self.assertEqual(res.returncode, 1)
        self.assertIn("redetect-demo", res.stderr)
        # The real repo alongside it must not be reported.
        self.assertNotIn("slug=agentm", res.stderr)

    def test_private_var_folders_path_fails(self):
        """macOS resolves /var -> /private/var; both spellings must be caught."""
        with tempfile.TemporaryDirectory() as td:
            res = run_gate(self._write(td, LEAKED_PRIVATE_VAR))
        self.assertEqual(res.returncode, 1)
        self.assertIn("novault-marker", res.stderr)

    def test_tmp_path_fails(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_gate(self._write(td, LEAKED_TMP))
        self.assertEqual(res.returncode, 1)
        self.assertIn("redetect-cli", res.stderr)

    def test_missing_registry_is_not_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_gate(Path(td) / "does-not-exist.json")
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_malformed_json_exits_two(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_gate(self._write(td, "{not json"))
        self.assertEqual(res.returncode, 2)

    def test_missing_repos_key_exits_two(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_gate(self._write(td, {"version": 1}))
        self.assertEqual(res.returncode, 2)

    def test_bare_list_registry_is_accepted(self):
        """Some readers write the list directly rather than under `repos`."""
        with tempfile.TemporaryDirectory() as td:
            res = run_gate(self._write(
                td, [{"slug": "agentm", "root_path": "/srv/code/agentm"}]))
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_failure_message_names_the_helper_to_use(self):
        """The remedy has to say what to do, not just that something is wrong."""
        with tempfile.TemporaryDirectory() as td:
            res = run_gate(self._write(td, LEAKED_MACOS))
        self.assertIn("no_vault_configured", res.stderr)
        self.assertIn("AGENTM_INSTALL_PREFIX", res.stderr)


class TestGateIsWiredIntoBattery(unittest.TestCase):
    """The both-places rule: the battery must actually run this gate."""

    def test_check_all_runs_check_registry_hygiene(self):
        text = (_HERE / "check-all.sh").read_text(encoding="utf-8")
        self.assertIn("check-registry-hygiene.py", text)


if __name__ == "__main__":
    unittest.main()
