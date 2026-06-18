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

    def test_documenter_text_is_project_first(self) -> None:
        # V4 #35 task-5 fix: the documenter text bundle emits project context
        # (decisions/_index) BEFORE always-load conventions, so the project's
        # settled decisions survive budget truncation instead of being cut first.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                out, rc = hm.documenter_context("agentm", fmt="text")
        self.assertEqual(rc, 0)
        first_heading = next(l for l in out.splitlines() if l.startswith("### "))
        self.assertTrue(
            first_heading.startswith("### agentm/"),
            f"expected a project entry first, got: {first_heading!r}",
        )
        # Explicit ordering: project decision precedes any always-load entry.
        self.assertLess(out.find("### agentm/"), out.find("### always-load:"))

    def test_project_first_keeps_project_when_budget_truncates(self) -> None:
        # With project_first + a budget too small for everything, what survives
        # is project context (always-load is dropped from the tail first).
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                full = hm.phase_recall("documenter", "agentm", project_first=True)
                tight = hm.phase_recall("documenter", "agentm", budget=40, project_first=True)
        # Full bundle has both; tight bundle keeps a project entry up front.
        self.assertIn("### always-load:", full)
        self.assertIn("### agentm/", tight)
        # Sanity: project_first=False would have led with always-load instead.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                default_order = hm.phase_recall("documenter", "agentm")
        self.assertLess(
            default_order.find("### always-load:"), default_order.find("### agentm/")
        )

    def test_phase_recall_documenter_does_not_raise(self) -> None:
        # Pre-V4 #35, phase_recall("documenter", ...) raised ValueError.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp))
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                out = hm.phase_recall("documenter", "agentm")
        self.assertIn("always-load", out)
        self.assertIn("agentm/decisions", out)
        self.assertIn("wiki-style", out)


# -----------------------------------------------------------------------------
# resolve_documenter_context (V4 #35 task 1)
# -----------------------------------------------------------------------------

class TestResolveDocumenterContext(unittest.TestCase):

    def test_b_registered_project_returns_structured_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                bundle = hm.resolve_documenter_context("agentm")

        self.assertIsNotNone(bundle)
        # Expected key shape.
        self.assertEqual(
            set(bundle.keys()),
            {"slug", "registered", "operator_conventions", "global_wiki_style",
             "project_decisions", "project_anchor", "wiki_style"},
        )
        self.assertEqual(bundle["slug"], "agentm")
        self.assertTrue(bundle["registered"])

        # operator conventions from _always-load/ (sorted by stem).
        conv_names = [c["name"] for c in bundle["operator_conventions"]]
        self.assertEqual(conv_names, ["diataxis-conventions", "writing-style"])
        self.assertIn("never mix", bundle["operator_conventions"][0]["body"])

        # global on-demand wiki conventions from projects/_global/wiki-style/ —
        # the relocation target the documenter resolver now reads (part 3 task 4).
        gws_names = [w["name"] for w in bundle["global_wiki_style"]]
        self.assertEqual(gws_names, ["house-voice"])
        self.assertIn("cut peacock words", bundle["global_wiki_style"][0]["body"])

        # project decisions from decisions/.
        dec_names = [d["name"] for d in bundle["project_decisions"]]
        self.assertEqual(dec_names, ["2026-05-20-stdlib-only"])

        # project anchor points at _index.md.
        self.assertIsNotNone(bundle["project_anchor"])
        self.assertTrue(bundle["project_anchor"].endswith("_index.md"))

        # wiki-style conventions.
        ws_names = [w["name"] for w in bundle["wiki_style"]]
        self.assertEqual(ws_names, ["page-length"])

    def test_c_unregistered_slug_returns_empty_project_bundle(self) -> None:
        # (c) vault reachable but slug not registered → registered=False,
        #     empty project lists, anchor None. Operator conventions still load
        #     (they're global). No exception fires.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                bundle = hm.resolve_documenter_context("no-such-project")

        self.assertIsNotNone(bundle)
        self.assertFalse(bundle["registered"])
        self.assertEqual(bundle["project_decisions"], [])
        self.assertIsNone(bundle["project_anchor"])
        self.assertEqual(bundle["wiki_style"], [])
        # Operator-global conventions still present.
        self.assertEqual(len(bundle["operator_conventions"]), 2)
        # Global `_global` wiki conventions are slug-independent — still loaded.
        self.assertEqual([w["name"] for w in bundle["global_wiki_style"]], ["house-voice"])

    def test_d_vault_unavailable_returns_none(self) -> None:
        # (d) MEMORY_VAULT_PATH unset + sandboxed (empty) config prefix → None.
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            self.assertIsNone(hm.resolve_documenter_context("agentm"))

    def test_registered_project_without_optional_dirs(self) -> None:
        # Registered project that lacks decisions/ + wiki-style/ + _index.md
        # still resolves: registered=True, empty lists, anchor None.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "projects" / "bare").mkdir(parents=True)
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                bundle = hm.resolve_documenter_context("bare")
        self.assertTrue(bundle["registered"])
        self.assertEqual(bundle["project_decisions"], [])
        self.assertEqual(bundle["wiki_style"], [])
        self.assertIsNone(bundle["project_anchor"])

    def test_legacy_personal_projects_layout_still_resolves(self) -> None:
        # Operators who haven't run the V4 #26 vault rename keep
        # personal-projects/<slug>/ — resolver falls back via _vault_projects_dir.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            base = vault / "personal-projects" / "agentm"
            (base / "decisions").mkdir(parents=True)
            (base / "decisions" / "d1.md").write_text("# d1\nx\n", encoding="utf-8")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                bundle = hm.resolve_documenter_context("agentm")
        self.assertTrue(bundle["registered"])
        self.assertEqual([d["name"] for d in bundle["project_decisions"]], ["d1"])


# -----------------------------------------------------------------------------
# documenter-context CLI (V4 #35 task 2)
# -----------------------------------------------------------------------------

class TestDocumenterContextCLI(unittest.TestCase):

    def test_a_registered_returns_rc0_with_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                rc, out = _run_cli(["documenter-context", "--slug", "agentm"])
        self.assertEqual(rc, 0)
        self.assertIn("always-load", out)
        self.assertIn("agentm/decisions", out)

    def test_b_vault_unavailable_returns_rc1(self) -> None:
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            rc, out = _run_cli(["documenter-context", "--slug", "agentm"])
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_c_unregistered_slug_returns_rc2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                rc, out = _run_cli(["documenter-context", "--slug", "no-such-project"])
        self.assertEqual(rc, 2)
        # Operator-global conventions still surface on rc=2 (text path).
        self.assertIn("always-load", out)

    def test_d_format_json_parses_with_expected_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                rc, out = _run_cli(
                    ["documenter-context", "--slug", "agentm", "--format", "json"]
                )
        self.assertEqual(rc, 0)
        parsed = json.loads(out)
        self.assertEqual(
            set(parsed.keys()),
            {"slug", "registered", "operator_conventions", "global_wiki_style",
             "project_decisions", "project_anchor", "wiki_style"},
        )
        self.assertTrue(parsed["registered"])
        self.assertEqual(parsed["slug"], "agentm")

    def test_json_on_unregistered_slug_is_rc2_but_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                rc, out = _run_cli(
                    ["documenter-context", "--slug", "ghost", "--format", "json"]
                )
        self.assertEqual(rc, 2)
        parsed = json.loads(out)
        self.assertFalse(parsed["registered"])
        self.assertEqual(parsed["project_decisions"], [])

    def test_budget_flag_caps_text_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_documenter_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                rc_big, big = _run_cli(
                    ["documenter-context", "--slug", "agentm", "--budget", "100000"]
                )
                rc_small, small = _run_cli(
                    ["documenter-context", "--slug", "agentm", "--budget", "1"]
                )
        self.assertEqual(rc_big, 0)
        self.assertEqual(rc_small, 0)
        # A 1-token budget drops project entries; output is strictly smaller.
        self.assertLess(len(small), len(big))


if __name__ == "__main__":
    unittest.main()
