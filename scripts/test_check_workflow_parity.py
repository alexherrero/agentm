#!/usr/bin/env python3
"""Positive + negative tests for scripts/check-vendored-parity.sh's `workflow` mode.

Was scripts/check-workflow-parity.sh (a standalone script) — CONS-1 merged it into
one of check-vendored-parity.sh's five modes. Same invariant, same fixture shapes,
just invoked as `check-vendored-parity.sh workflow [--root DIR]` instead of a bare
script.

The gate locks the dogfood self-consumption invariant: every workflow agentm ships
as a template under templates/.github/workflows/ must be active at .github/workflows/,
byte-identical. It is the local mirror of the Linux-only `dogfood-workflows` CI job —
the gap that let a one-sided wiki-sync.yml edit pass the whole local battery and only
fail after a push.

These tests drive the real gate via subprocess: it passes against the live repo,
**fails** on a drifted twin (the mandatory negative test), fails when a templated
workflow has no active copy, leaves a template-less active workflow alone (the invariant
is template→active, not the reverse), and raises a setup error (exit 2) when there are
no templated workflows to check. The `--root` flag points the gate at a throwaway
fixture tree so the negative cases never touch the repo.

Skipped on non-POSIX (the gate is a bash script; Windows has no POSIX bash), matching
the other bash-driving test suites in this directory.

Run directly:

    python3 scripts/test_check_workflow_parity.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_GATE = _HERE / "check-vendored-parity.sh"
_MODE = "workflow"

# Two synthetic workflow bodies — the gate only byte-compares, so the content need not
# be valid YAML; distinct bodies just make the multi-file loop and the diff meaningful.
_WF_A = (
    "name: A\n"
    "on: [push]\n"
    "jobs:\n"
    "  a:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - run: echo a\n"
)
_WF_B = (
    "name: B\n"
    "on: [push]\n"
    "jobs:\n"
    "  b:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - run: echo b\n"
)


def _run_gate(root: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["bash", str(_GATE), _MODE]
    if root is not None:
        cmd += ["--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


class _FixtureRoot:
    """A throwaway ROOT carrying two templated workflows, each active at the repo root
    byte-identical (the clean state). Tests mutate one twin to exercise each branch."""

    def __init__(self, base: Path) -> None:
        self.root = base
        self.tmpl = self.root / "templates" / ".github" / "workflows"
        self.active = self.root / ".github" / "workflows"
        self.tmpl.mkdir(parents=True)
        self.active.mkdir(parents=True)
        for name, body in (("a.yml", _WF_A), ("b.yml", _WF_B)):
            (self.tmpl / name).write_text(body, encoding="utf-8")
            (self.active / name).write_text(body, encoding="utf-8")


@unittest.skipIf(os.name == "nt", "bash gate — POSIX only")
class CheckWorkflowParity(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-workflow-parity-gate-")
        self.fx = _FixtureRoot(Path(self._tmp) / "root")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- positive: the real repo satisfies the invariant ---

    def test_gate_passes_on_repo(self) -> None:
        proc = _run_gate()  # no --root → the live repo
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_clean_fixture_passes(self) -> None:
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    # --- negative: a drifted twin (the mandatory one) ---

    def test_drifted_twin_fails(self) -> None:
        # Active copy edited without the template — the exact failure mode the gate
        # exists to catch (the wiki-sync.yml drift that bit a push).
        (self.fx.active / "a.yml").write_text(_WF_A + "# drift\n", encoding="utf-8")
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("a.yml", proc.stderr)
        self.assertIn("drifted", proc.stderr)

    def test_template_only_missing_active_fails(self) -> None:
        # A templated workflow with no active copy at the repo root.
        (self.fx.active / "b.yml").unlink()
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("b.yml", proc.stderr)
        self.assertIn("missing", proc.stderr)

    # --- direction: an active workflow WITHOUT a template twin is out of scope ---

    def test_template_less_active_is_ignored(self) -> None:
        # ci-all.yml et al. are active-only — the invariant is template→active, so an
        # active workflow lacking a template must NOT trip the gate.
        (self.fx.active / "ci-all.yml").write_text(_WF_A, encoding="utf-8")
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    # --- setup error: nothing to check → exit 2 (distinct from a drift) ---

    def test_no_templated_workflows_is_setup_error(self) -> None:
        shutil.rmtree(self.fx.tmpl)
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("no templated workflows", proc.stderr)

    def test_missing_root_is_setup_error(self) -> None:
        proc = _run_gate(self.fx.root / "does-not-exist")
        self.assertEqual(proc.returncode, 2, proc.stdout)


if __name__ == "__main__":
    unittest.main()
