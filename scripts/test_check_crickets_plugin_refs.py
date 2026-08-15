#!/usr/bin/env python3
"""Unit tests for check-crickets-plugin-refs.py.

The gate catches the two shapes a crickets plugin rename actually breaks, and
deliberately ignores a third that it does not. These tests pin all three, using
a synthetic crickets checkout so they never depend on the real sibling's
current plugin set.

The negative case matters as much as the positives: crickets keeps old plugin
names alive as declared capability aliases, so a bare old name in prose still
resolves. A gate that flagged those would fight that mechanism and bury the two
real failures in noise.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_GATE = _HERE / "check-crickets-plugin-refs.py"

# A retired plugin name, built by join so this source file does not itself
# contain the literal needles — the gate scans every tracked text file, this one
# included, and would otherwise flag its own fixtures. Same idiom the retire
# guards use (test_diataxis_author_retired.py), and better than allowlisting
# this file: an allowlist entry is exactly the silent rot those guards exist to
# prevent.
_OLD = "wiki-" + "maintenance"


def _load():
    spec = importlib.util.spec_from_file_location("crickets_ref_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


_MOD = _load() if _GATE.is_file() else None


@unittest.skipIf(_MOD is None, "gate script missing")
class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # synthetic crickets: two current plugins, no `wiki-maintenance`
        self.crickets = self.root / "crickets"
        (self.crickets / "src" / "wiki").mkdir(parents=True)
        (self.crickets / "src" / "development-lifecycle").mkdir(parents=True)
        mp = self.crickets / ".claude-plugin" / "marketplace.json"
        mp.parent.mkdir(parents=True)
        mp.write_text(json.dumps({"plugins": [
            {"name": "wiki"}, {"name": "development-lifecycle"},
        ]}), encoding="utf-8")
        # synthetic agentm: a git repo so `git ls-files` works
        self.repo = self.root / "agentm"
        self.repo.mkdir()
        for args in (["init", "-q"], ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", str(self.repo), *args],
                           check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _scan(self, body: str) -> list[str]:
        (self.repo / "doc.md").write_text(body, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"],
                       check=True, capture_output=True)
        return _MOD.scan(self.crickets, root=self.repo)

    # ── shape 1: dead source links ──────────────────────────────────────
    def test_dead_source_link_flagged(self):
        f = self._scan(
            f"see https://github.com/alexherrero/crickets/tree/main/src/{_OLD}\n"
        )
        self.assertEqual(len(f), 1, f)
        self.assertIn("dead crickets source link", f[0])

    def test_live_source_link_clean(self):
        self.assertEqual(
            self._scan(
                "see https://github.com/alexherrero/crickets/blob/main/src/wiki/agents/documenter.md\n"
            ),
            [],
        )

    # ── shape 2: unresolvable plugin-qualified dispatch ─────────────────
    def test_stale_qualifier_flagged(self):
        f = self._scan(f"dispatch `{_OLD}:documenter` at the boundary\n")
        self.assertEqual(len(f), 1, f)
        self.assertIn("unresolvable dispatch", f[0])

    def test_current_qualifier_clean(self):
        self.assertEqual(self._scan("dispatch `wiki:documenter` here\n"), [])

    def test_former_plugin_name_covers_every_known_rename(self):
        for old in ("developer-workflows", "wiki-maintenance", "releasing-conventions"):
            with self.subTest(old=old):
                f = self._scan(f"run `{old}:something` now\n")
                self.assertEqual(len(f), 1, f)

    # ── the deliberate non-check ────────────────────────────────────────
    def test_bare_old_name_in_prose_is_NOT_flagged(self):
        """Old names survive as declared capability aliases and still resolve.

        Flagging them would fight crickets' backward-compatibility mechanism.
        """
        self.assertEqual(
            self._scan(
                "The phase loop ships in the crickets developer-workflows plugin,\n"
                "and " + _OLD + " owns the documenter.\n"
            ),
            [],
        )

    def test_unrelated_colon_token_is_not_a_dispatch(self):
        """A qualifier crickets never shipped belongs to someone else."""
        self.assertEqual(
            self._scan("note: see http://example.com and key: value\n"), []
        )


@unittest.skipIf(_MOD is None, "gate script missing")
class EndToEndInvocationTests(unittest.TestCase):
    """Subprocess-invoke check-crickets-plugin-refs.py exactly as check-all.sh
    does. This is the UNIT_WRAPPED contract (test_ci_consistency.py): the gate
    has no direct CI step, because it needs the crickets sibling as ground
    truth, so this wrapper is what proves it still runs and still exits 0 on a
    clean tree."""

    def test_gate_runs_clean_against_this_repo(self):
        result = subprocess.run(
            [sys.executable, str(_GATE)], capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(
            result.returncode, 0,
            "check-crickets-plugin-refs.py must exit 0 on a clean tree:\n"
            + result.stdout + result.stderr,
        )
        # Either it resolved crickets and found nothing, or it graceful-skipped.
        self.assertRegex(result.stdout, r"clean \(against \d+ crickets plugins\)|skipping")


@unittest.skipIf(_MOD is None, "gate script missing")
class ResolutionTests(unittest.TestCase):
    def test_skips_when_no_crickets_reachable(self):
        """Graceful-skip (exit 0) rather than guessing the plugin set."""
        with tempfile.TemporaryDirectory() as td:
            orig = _MOD.sibling_repo_root.sibling_layout_root
            _MOD.sibling_repo_root.sibling_layout_root = lambda *a, **k: Path(td)
            try:
                self.assertIsNone(_MOD.find_crickets_root())
            finally:
                _MOD.sibling_repo_root.sibling_layout_root = orig


if __name__ == "__main__":
    unittest.main()
