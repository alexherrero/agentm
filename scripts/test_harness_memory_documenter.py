#!/usr/bin/env python3
"""Unit tests for the V4 #35 documenter vault-context resolver.

Covers `harness_memory.py`'s `documenter` recall-phase additions:

  - `_PHASE_PROJECT_DIRS["documenter"]` read-list shape
  - `_DEFAULT_BUDGETS["documenter"]` + HARNESS_RECALL_BUDGET_DOCUMENTER override
  - `resolve_documenter_context(slug)` structured bundle (registered / not /
    vault-unavailable)
  - `documenter-context` CLI subcommand exit codes (0 / 1 / 2) + --format json

Run directly:

    python3 scripts/test_harness_memory_documenter.py

Discovered by CI via `(cd scripts && python3 -m unittest discover -p 'test_*.py')`.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402


# Sandbox AGENTM_INSTALL_PREFIX module-wide so vault_path()'s config-file
# fallback can't read the operator's real ~/.claude/.agentm-config.json during
# the vault-unavailable tests (mirrors test_harness_memory.py's setUpModule).
_TEST_INSTALL_PREFIX = tempfile.mkdtemp(prefix="agentm-test-doc-prefix-")


def setUpModule() -> None:  # noqa: N802 — unittest convention
    os.environ["AGENTM_INSTALL_PREFIX"] = _TEST_INSTALL_PREFIX


def tearDownModule() -> None:  # noqa: N802
    os.environ.pop("AGENTM_INSTALL_PREFIX", None)
    shutil.rmtree(_TEST_INSTALL_PREFIX, ignore_errors=True)


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

class _ClearEnv:
    """Set env vars + explicitly unset listed keys on enter; restore on exit."""

    def __init__(self, set_vars: Optional[dict] = None, unset_keys: Optional[list] = None):
        self.set_vars = set_vars or {}
        self.unset_keys = unset_keys or []
        self._saved: dict = {}

    def __enter__(self):
        for k in list(self.set_vars.keys()) + self.unset_keys:
            self._saved[k] = os.environ.get(k)
        for k in self.unset_keys:
            os.environ.pop(k, None)
        for k, v in self.set_vars.items():
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _make_documenter_vault(root: Path, *, project: str = "agentm") -> Path:
    """Build a vault on the NEW (post-V4 #26) `projects/` layout with the dirs
    the documenter resolver reads: `_always-load/`, `_index.md`, `decisions/`,
    `wiki-style/`. Returns the vault path."""
    vault = root / "vault"

    al = vault / "personal" / "_always-load"
    al.mkdir(parents=True)
    (al / "diataxis-conventions.md").write_text(
        "# diataxis\nfour modes; never mix on one page.\n", encoding="utf-8",
    )
    (al / "writing-style.md").write_text(
        "# writing style\nlead with the why; short sentences.\n", encoding="utf-8",
    )

    base = vault / "projects" / project
    (base / "decisions").mkdir(parents=True)
    (base / "_index.md").write_text(
        f"# {project} index\nstate: active.\n", encoding="utf-8",
    )
    (base / "decisions" / "2026-05-20-stdlib-only.md").write_text(
        "# stdlib only\nrationale: no new deps per ADR 0001.\n", encoding="utf-8",
    )
    (base / "wiki-style").mkdir(parents=True)
    (base / "wiki-style" / "page-length.md").write_text(
        "# page length\nhow-to soft ceiling 600 words; keep worked scenarios.\n",
        encoding="utf-8",
    )

    # Global on-demand wiki conventions — the reserved `_global` pseudo-project
    # (the relocation target; read slug-independently, like _always-load).
    gws = vault / "projects" / "_global" / "wiki-style"
    gws.mkdir(parents=True)
    (gws / "house-voice.md").write_text(
        "---\ntrigger: house-voice\n---\nsecond person; cut peacock words.\n",
        encoding="utf-8",
    )
    return vault


def _run_cli(argv: list) -> tuple:
    """Invoke hm.main(argv) in-process, capturing stdout. Returns (rc, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = hm.main(argv)
    return rc, buf.getvalue()


# -----------------------------------------------------------------------------
# Constants / phase wiring (V4 #35 task 1)
# -----------------------------------------------------------------------------

class TestDocumenterPhaseWiring(unittest.TestCase):

    def test_phase_project_dirs_shape(self) -> None:
        # (a) read-list is _index.md anchor + decisions + wiki-style.
        self.assertEqual(
            hm._PHASE_PROJECT_DIRS["documenter"],
            ("_index.md", "decisions", "wiki-style"),
        )

    def test_documenter_is_a_valid_recall_phase(self) -> None:
        self.assertIn("documenter", hm._VALID_PHASES)

    def test_default_budget_is_10k(self) -> None:
        # Raised from 4k after the V4 #35 task-5 dogfood (4k truncated away the
        # project decisions). Overrideable via HARNESS_RECALL_BUDGET_DOCUMENTER.
        self.assertEqual(hm._DEFAULT_BUDGETS["documenter"], 10000)

    def test_budget_env_override(self) -> None:
        # Locked DC-3: HARNESS_RECALL_BUDGET_DOCUMENTER overrides the 4k default.
        with _ClearEnv(set_vars={"HARNESS_RECALL_BUDGET_DOCUMENTER": "1500"}):
            self.assertEqual(hm.phase_budget("documenter"), 1500)

    def test_budget_arg_beats_env(self) -> None:
        with _ClearEnv(set_vars={"HARNESS_RECALL_BUDGET_DOCUMENTER": "1500"}):
            self.assertEqual(hm.phase_budget("documenter", 9000), 9000)

    def test_documenter_context_returns_rc1_v5_3(self) -> None:
        # V5-3: resolve_documenter_context always returns None → rc=1 (vault unavailable).
        # Context is now served by the V5-9 MCP memory server.
        out, rc = hm.documenter_context("agentm", fmt="text")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_project_first_always_empty_v5_3(self) -> None:
        # V5-3: phase_recall("documenter", ...) always returns "" regardless of vault.
        full = hm.phase_recall("documenter", "agentm", project_first=True)
        tight = hm.phase_recall("documenter", "agentm", budget=40, project_first=True)
        self.assertEqual(full, "")
        self.assertEqual(tight, "")

    def test_phase_recall_documenter_does_not_raise_v5_3(self) -> None:
        # V5-3: phase_recall("documenter", ...) returns "" (never raises).
        out = hm.phase_recall("documenter", "agentm")
        self.assertEqual(out, "")


# -----------------------------------------------------------------------------
# resolve_documenter_context (V4 #35 task 1)
# -----------------------------------------------------------------------------

class TestResolveDocumenterContext(unittest.TestCase):

    def test_b_resolve_documenter_context_always_none_v5_3(self) -> None:
        # V5-3: vault backend removed → resolve_documenter_context always returns None.
        # Context is provided by the V5-9 MCP memory server.
        self.assertIsNone(hm.resolve_documenter_context("agentm"))
        self.assertIsNone(hm.resolve_documenter_context("no-such-project"))

    def test_c_resolve_any_slug_returns_none_v5_3(self) -> None:
        # V5-3: vault reachable or not — always None. rc=1 from documenter_context.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                bundle = hm.resolve_documenter_context("agentm")
                bundle_missing = hm.resolve_documenter_context("no-such-project")
        self.assertIsNone(bundle)
        self.assertIsNone(bundle_missing)

    def test_d_vault_unavailable_returns_none(self) -> None:
        # (d) MEMORY_VAULT_PATH unset + sandboxed (empty) config prefix → None.
        # This test's behavior is unchanged by V5-3 (was already testing None).
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            self.assertIsNone(hm.resolve_documenter_context("agentm"))

    def test_registered_project_returns_none_v5_3(self) -> None:
        # V5-3: even a fully-registered project returns None.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "projects" / "bare").mkdir(parents=True)
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                bundle = hm.resolve_documenter_context("bare")
        self.assertIsNone(bundle)

    def test_legacy_layout_returns_none_v5_3(self) -> None:
        # V5-3: even legacy layout projects return None.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            base = vault / "personal-projects" / "agentm"
            (base / "decisions").mkdir(parents=True)
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                bundle = hm.resolve_documenter_context("agentm")
        self.assertIsNone(bundle)


# -----------------------------------------------------------------------------
# documenter-context CLI (V4 #35 task 2)
# -----------------------------------------------------------------------------

class TestDocumenterContextCLI(unittest.TestCase):

    def test_a_always_rc1_v5_3(self) -> None:
        # V5-3: resolve_documenter_context always None → rc=1 for any slug.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                rc, out = _run_cli(["documenter-context", "--slug", "agentm"])
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_b_vault_unavailable_returns_rc1(self) -> None:
        # This test's behavior is unchanged by V5-3 (rc=1 was already expected).
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            rc, out = _run_cli(["documenter-context", "--slug", "agentm"])
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_c_unregistered_slug_also_rc1_v5_3(self) -> None:
        # V5-3: unregistered slug → rc=1 (same as registered; vault backend gone).
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                rc, out = _run_cli(["documenter-context", "--slug", "no-such-project"])
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_d_format_json_rc1_empty_v5_3(self) -> None:
        # V5-3: JSON format also rc=1, empty output (no bundle to serialize).
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                rc, out = _run_cli(
                    ["documenter-context", "--slug", "agentm", "--format", "json"]
                )
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_json_unregistered_also_rc1_v5_3(self) -> None:
        # V5-3: unregistered slug JSON → rc=1, empty output.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                rc, out = _run_cli(
                    ["documenter-context", "--slug", "ghost", "--format", "json"]
                )
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_budget_flag_accepted_exits_cleanly_v5_3(self) -> None:
        # V5-3: budget flag is accepted (no crash), but output is always "".
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                rc_big, big = _run_cli(
                    ["documenter-context", "--slug", "agentm", "--budget", "100000"]
                )
                rc_small, small = _run_cli(
                    ["documenter-context", "--slug", "agentm", "--budget", "1"]
                )
        self.assertEqual(rc_big, 1)
        self.assertEqual(rc_small, 1)
        self.assertEqual(big, "")
        self.assertEqual(small, "")


if __name__ == "__main__":
    unittest.main()
