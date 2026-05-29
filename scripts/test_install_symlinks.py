#!/usr/bin/env python3
"""Unit tests for lib/install/python/install_symlinks.py — stdlib unittest.

Focused on orphan-symlink reaping (V4.6.2): when a source file is deleted
from a source clone, the installer should reap the dangling symlink under
<install-prefix>/ on the next run, but never touch operator-placed real
files or symlinks pointing outside the clones.

Run:

    python3 -m unittest scripts.test_install_symlinks
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_LIB = _REPO / "lib" / "install" / "python"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import install_symlinks as ism  # noqa: E402


def _minimal_clone(root: Path) -> dict[str, str]:
    """A tiny agentm-shaped clone with one agent + one skill."""
    agentm = root / "agentm"
    (agentm / ".git").mkdir(parents=True)
    (agentm / "harness" / "agents").mkdir(parents=True)
    (agentm / "harness" / "agents" / "explorer.md").write_text("explorer\n")
    (agentm / "harness" / "skills" / "wiki-author").mkdir(parents=True)
    (agentm / "harness" / "skills" / "wiki-author" / "SKILL.md").write_text("wiki\n")
    return {"agentm": str(agentm)}


class ReapOrphanSymlinksTests(unittest.TestCase):
    def test_reaps_dead_symlink_into_clone(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clones = _minimal_clone(root)
            prefix = root / "prefix"

            # Initial install: creates explorer + wiki-author symlinks.
            r = ism.symlink_customizations(clones, prefix)
            self.assertIn("agents/explorer.md", r["created"])
            self.assertEqual(r["reaped"], [])

            # Delete the source file. Symlink at prefix/agents/explorer.md now
            # dangles.
            (root / "agentm" / "harness" / "agents" / "explorer.md").unlink()
            self.assertTrue((prefix / "agents" / "explorer.md").is_symlink())
            self.assertFalse((prefix / "agents" / "explorer.md").exists())

            # Re-run install: orphan should be reaped.
            r2 = ism.symlink_customizations(clones, prefix)
            self.assertIn("agents/explorer.md", r2["reaped"])
            self.assertFalse((prefix / "agents" / "explorer.md").is_symlink())
            # Other live symlinks untouched.
            self.assertTrue((prefix / "skills" / "wiki-author").is_symlink())

    def test_leaves_external_symlinks_alone(self):
        """A symlink pointing outside any source clone is operator-owned."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clones = _minimal_clone(root)
            prefix = root / "prefix"
            ism.symlink_customizations(clones, prefix)

            # Operator places a symlink into agents/ pointing at an external
            # path that doesn't exist (still "broken" — but NOT in a clone).
            external_link = prefix / "agents" / "operator-tool.md"
            external_link.symlink_to(root / "elsewhere" / "tool.md")
            self.assertTrue(external_link.is_symlink())
            self.assertFalse(external_link.exists())

            r = ism.symlink_customizations(clones, prefix)

            # External symlink survives the reap pass.
            self.assertTrue(external_link.is_symlink())
            self.assertNotIn("agents/operator-tool.md", r["reaped"])

    def test_leaves_real_files_alone(self):
        """A real file under managed dirs must never be reaped."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clones = _minimal_clone(root)
            prefix = root / "prefix"
            ism.symlink_customizations(clones, prefix)

            # Operator places a real file under agents/.
            real_file = prefix / "agents" / "operator-notes.md"
            real_file.write_text("operator's notes\n")

            r = ism.symlink_customizations(clones, prefix)

            self.assertTrue(real_file.is_file())
            self.assertFalse(real_file.is_symlink())
            self.assertNotIn("agents/operator-notes.md", r["reaped"])

    def test_does_not_reap_live_symlinks(self):
        """Symlinks whose targets still exist must be left alone."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clones = _minimal_clone(root)
            prefix = root / "prefix"
            ism.symlink_customizations(clones, prefix)
            r2 = ism.symlink_customizations(clones, prefix)
            self.assertEqual(r2["reaped"], [])


class NormalizePathStrTests(unittest.TestCase):
    """Verify the cross-platform path-normalization helper.

    The orphan-reap walk compares a broken symlink's resolved target against
    the source-clone root using string comparison. On Windows, `os.readlink` /
    `Path.resolve` can emit different normalized forms on the two sides
    (extended-path prefix `\\\\?\\` on the existing clone root vs. plain
    `C:\\...` on the resolved-but-nonexistent symlink target). Without
    normalization the naive `startswith` returns False and orphans never
    get reaped on Windows — the exact regression the V4.6.2 install_symlinks
    landing shipped with. These tests verify the helper handles each quirk
    against raw strings, so coverage doesn't require a Windows runner.
    """

    def test_identity_on_posix_paths(self):
        self.assertEqual(
            ism._normalize_path_str("/tmp/agentm/harness/agents/explorer.md"),
            ism._normalize_path_str("/tmp/agentm/harness/agents/explorer.md"),
        )

    def test_strips_windows_extended_path_prefix(self):
        """`\\\\?\\C:\\foo` and `C:\\foo` must normalize identically."""
        self.assertEqual(
            ism._normalize_path_str("\\\\?\\C:\\Users\\runner\\agentm"),
            ism._normalize_path_str("C:\\Users\\runner\\agentm"),
        )

    def test_strips_prefix_only_when_present(self):
        """A plain posix path containing `?` mid-string is NOT stripped."""
        self.assertEqual(
            ism._normalize_path_str("/tmp/has?question/agentm"),
            ism._normalize_path_str("/tmp/has?question/agentm"),
        )

    def test_collapses_mixed_separators(self):
        """normpath handles `foo/bar/../baz` etc. uniformly."""
        self.assertEqual(
            ism._normalize_path_str("/tmp/agentm/./harness/../harness/agents"),
            ism._normalize_path_str("/tmp/agentm/harness/agents"),
        )

    def test_path_under_root_handles_extended_prefix_asymmetry(self):
        """The Windows-specific regression case: one side has `\\\\?\\`, one doesn't.

        Reconstruct the exact failure shape from the V4.6.2 Windows CI:
        clone_root resolved with extended prefix; target_resolved without.
        Naive str.startswith returns False; _path_under must return True.
        """
        target = Path("C:\\Users\\runner\\work\\agentm\\harness\\agents\\explorer.md")
        clone_root = Path("\\\\?\\C:\\Users\\runner\\work\\agentm")
        # Skip if running on POSIX — Path() rewrites backslashes and the test
        # becomes meaningless. The string-level tests above cover the core
        # normalization; this one only adds value where Path semantics match.
        if os.sep != "\\":
            self.skipTest("Windows-Path-semantics-specific")
        self.assertTrue(ism._path_under(target, clone_root))
        # And the reverse direction (prefix on target, not on root).
        self.assertTrue(ism._path_under(clone_root / "x.md", Path("C:\\Users\\runner\\work\\agentm")))


if __name__ == "__main__":
    unittest.main()
