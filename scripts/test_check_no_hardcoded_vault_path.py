#!/usr/bin/env python3
"""Tests for scripts/check-no-hardcoded-vault-path.py.

The gate prevents agents from caching absolute MemoryVault path literals in
repo-tracked files. Two patterns are checked:

  (A) /Library/CloudStorage/ without a tilde/variable prefix — an absolute
      machine-specific vault path baked in as a constant or config value.

  (B) /Obsidian/AgentMemory — the retired pre-V5-3 vault root name used as a
      path component (the slash-prefixed form only; prose backtick references
      without a leading slash are not literals and are not flagged).

Positive tests confirm that the live repo passes and that legitimate uses
(shell tilde expansions, example placeholder notation, test fixtures) are not
flagged. Negative tests confirm that a real violation fails the gate.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Load the gate module via importlib (hyphen in filename prevents direct import)
_SPEC = importlib.util.spec_from_file_location(
    "check_no_hardcoded_vault_path",
    _HERE / "check-no-hardcoded-vault-path.py",
)
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore[union-attr]
_main = _mod.main


def _run(tmp: str, filename: str, content: str) -> int:
    """Write content to tmp/scripts/<filename>, run gate against tmp, return exit code."""
    scripts = Path(tmp) / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / filename).write_text(content, encoding="utf-8")
    return _main(["--root", tmp])


# ── positive: things that MUST pass ──────────────────────────────────────────

class TestClean(unittest.TestCase):

    def test_real_repo_passes(self) -> None:
        """The live repo must pass the gate — this is the primary health check."""
        repo_root = str(_HERE.parent)
        rc = _main(["--root", repo_root])
        self.assertEqual(rc, 0, "Live repo failed check-no-hardcoded-vault-path — check scripts/")

    def test_empty_file_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "config.py", "vault = None\n")
            self.assertEqual(rc, 0)

    def test_tilde_expansion_passes(self) -> None:
        """~/Library/CloudStorage is a shell tilde-expansion, not a literal path."""
        content = (
            "# vault probe\n"
            "PROBE_ROOT = os.path.expanduser('~/Library/CloudStorage')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "vault_probe.py", content)
            self.assertEqual(rc, 0)

    def test_dollar_home_expansion_passes(self) -> None:
        """$HOME/Library/CloudStorage is a shell variable expansion, not a literal."""
        content = 'echo "==> scanning $HOME/Library/CloudStorage"\n'
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "install.sh", content)
            self.assertEqual(rc, 0)

    def test_placeholder_angle_bracket_passes(self) -> None:
        """Example paths using <account> placeholder notation are not literals."""
        content = (
            "--vault-path ~/Library/CloudStorage/GoogleDrive-<account>/My Drive/Obsidian/Agent\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "SKILL.md", content)
            self.assertEqual(rc, 0)

    def test_placeholder_ellipsis_passes(self) -> None:
        """Paths using … (Unicode ellipsis) placeholder notation are not literals."""
        content = "e.g. ~/Library/CloudStorage/GoogleDrive-…/Obsidian/Agent\n"
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "how-to.md", content)
            self.assertEqual(rc, 0)

    def test_triple_dot_retired_name_passes(self) -> None:
        """Docstring reference using .../Obsidian/AgentMemory is documentation."""
        content = (
            '"""Probe for ``.../Obsidian/AgentMemory`` nested under Obsidian."""\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "vault_probe.py", content)
            self.assertEqual(rc, 0)

    def test_tilde_retired_name_passes(self) -> None:
        """Example using ~/Obsidian/AgentMemory is a shell expansion, not a literal."""
        content = "vault root (e.g. ~/Obsidian/AgentMemory)\n"
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "how-to.md", content)
            self.assertEqual(rc, 0)

    def test_test_file_excluded(self) -> None:
        """test_*.py files are fixtures — violations inside them are ignored."""
        content = (
            '_OBSIDIAN = "/Users/x/Library/CloudStorage/GoogleDrive-y/.../Obsidian"\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "test_vault_probe.py", content)
            self.assertEqual(rc, 0)


# ── negative: things that MUST fail ──────────────────────────────────────────

class TestViolations(unittest.TestCase):

    def test_absolute_cloudStorage_path_fails(self) -> None:
        """Baked-in /Library/CloudStorage/ absolute path literal must be flagged."""
        content = (
            "# Hardcoded — this must NOT pass the gate\n"
            'VAULT = "/Users/x/Library/CloudStorage/GoogleDrive-y/My Drive/Obsidian/Agent"\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "config.py", content)
            self.assertEqual(rc, 1)

    def test_retired_vault_name_in_path_fails(self) -> None:
        """Absolute path component /Obsidian/AgentMemory (retired name) must be flagged."""
        content = (
            "# Stale pre-V5-3 path — must NOT pass the gate\n"
            'VAULT = "/Users/x/Library/CloudStorage/GoogleDrive-y/My Drive/Obsidian/AgentMemory"\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "config.py", content)
            self.assertEqual(rc, 1)

    def test_retired_name_without_cloud_storage_fails(self) -> None:
        """/Obsidian/AgentMemory is flagged even without /Library/CloudStorage/."""
        content = "vault_path = os.path.join(home, 'Obsidian', '/Obsidian/AgentMemory')\n"
        with tempfile.TemporaryDirectory() as tmp:
            rc = _run(tmp, "setup.py", content)
            self.assertEqual(rc, 1)

    def test_stderr_names_file_and_line(self) -> None:
        """Violation output must include the filename and line number."""
        import io
        content = 'ROOT = "/Library/CloudStorage/GoogleDrive-x/My Drive/Obsidian/Agent"\n'
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "scripts"
            scripts.mkdir()
            (scripts / "config.py").write_text(content, encoding="utf-8")

            old_stderr = sys.stderr
            sys.stderr = buf = io.StringIO()
            try:
                rc = _main(["--root", tmp])
            finally:
                sys.stderr = old_stderr

            self.assertEqual(rc, 1)
            output = buf.getvalue()
            self.assertIn("config.py", output)
            self.assertIn(":1", output)


# ── setup errors ──────────────────────────────────────────────────────────────

class TestSetupErrors(unittest.TestCase):

    def test_missing_root_is_setup_error(self) -> None:
        rc = _main(["--root", "/nonexistent/path/that/does/not/exist"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
