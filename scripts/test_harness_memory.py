#!/usr/bin/env python3
"""Unit tests for scripts/harness_memory.py — stdlib unittest, cross-platform.

Run directly:

    python3 scripts/test_harness_memory.py

Covers:
  - available exit codes (vault present / absent)
  - recall graceful-skip + fixture-vault content + budget cap + permanent-only
  - offer-save mode envelope (off / silent / ask) + confidence threshold edges
  - offer-save non-TTY skip default + toolkit-absent graceful path
  - plan-done-promotion: empty / first run / idempotent re-run / cursor advance
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402


# -----------------------------------------------------------------------------
# Fixture helpers
# -----------------------------------------------------------------------------

def _make_vault(root: Path, *, project: str = "fixture-project") -> Path:
    """Build a minimal MemoryVault under `root`. Returns the vault path."""
    vault = root / "vault"
    (vault / "personal-private" / "_always-load").mkdir(parents=True)
    (vault / "personal-private" / "_always-load" / "coding-style.md").write_text(
        "# coding style\nuse stdlib; kebab-case slugs.\n",
        encoding="utf-8",
    )
    (vault / "personal-projects" / project / "decisions").mkdir(parents=True)
    (vault / "personal-projects" / project / "_index.md").write_text(
        f"# {project} index\ncurrent state: in-progress\n",
        encoding="utf-8",
    )
    (vault / "personal-projects" / project / "decisions" / "2026-05-20-pick-stdlib.md").write_text(
        "# pick stdlib\nrationale: no new deps per ADR 0007 D7.\n",
        encoding="utf-8",
    )
    (vault / "personal-projects" / project / "open-questions").mkdir(parents=True)
    (vault / "personal-projects" / project / "open-questions" / "2026-05-22-budget-tuning.md").write_text(
        "# budget tuning\nq: what's the right per-phase budget?\n",
        encoding="utf-8",
    )
    (vault / "personal-projects" / project / "known-issues").mkdir(parents=True)
    (vault / "personal-projects" / project / "known-issues" / "2026-05-15-crlf-windows.md").write_text(
        "# CRLF on windows\nfix: write_bytes instead of write_text.\n",
        encoding="utf-8",
    )
    return vault


def _make_toolkit_stub(root: Path, *, save_exit: int = 0, save_log: Path | None = None) -> Path:
    """Build a toolkit stub directory with a stub save.py.

    The stub script optionally writes a JSON log of args + stdin to `save_log`.
    """
    tk = root / "toolkit-stub"
    tk.mkdir(parents=True)
    log_arg = repr(str(save_log)) if save_log else "None"
    stub = textwrap.dedent(
        f"""
        import json, sys
        log_path = {log_arg}
        record = {{
            "argv": sys.argv[1:],
            "stdin": sys.stdin.read(),
        }}
        if log_path:
            with open(log_path, "w", encoding="utf-8") as fh:
                json.dump(record, fh)
        sys.exit({save_exit})
        """
    ).lstrip()
    (tk / "save.py").write_text(stub, encoding="utf-8")
    return tk


def _set_env(**kwargs: str | None) -> mock.patch.dict:
    """Context manager: temporarily set env vars (None = unset)."""
    to_set = {k: v for k, v in kwargs.items() if v is not None}
    to_unset = [k for k, v in kwargs.items() if v is None]
    patcher = mock.patch.dict(os.environ, to_set, clear=False)
    # We can't unset via patch.dict; emulate via setting empty + checking helpers.
    # Tests should explicitly use _ClearEnv when they want to remove a var.
    return patcher


class _ClearEnv:
    """Context manager: set env vars, also explicitly unset listed keys on enter."""

    def __init__(self, set_vars: dict | None = None, unset_keys: list[str] | None = None):
        self.set_vars = set_vars or {}
        self.unset_keys = unset_keys or []
        self._saved: dict[str, str | None] = {}

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


# -----------------------------------------------------------------------------
# is_available / vault_path
# -----------------------------------------------------------------------------

class TestAvailable(unittest.TestCase):

    def test_available_false_when_env_unset(self) -> None:
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            self.assertFalse(hm.is_available())
            self.assertIsNone(hm.vault_path())

    def test_available_false_when_dir_missing(self) -> None:
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": "/definitely/not/a/real/path"}):
            self.assertFalse(hm.is_available())

    def test_available_true_when_dir_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                self.assertTrue(hm.is_available())
                self.assertEqual(hm.vault_path(), vault)


# -----------------------------------------------------------------------------
# recall
# -----------------------------------------------------------------------------

class TestRecall(unittest.TestCase):

    def test_recall_empty_when_vault_absent(self) -> None:
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            self.assertEqual(hm.phase_recall("plan", "any-slug"), "")

    def test_recall_loads_always_load_and_project_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                out = hm.phase_recall("plan", "agentm")
        self.assertIn("coding style", out)
        self.assertIn("pick stdlib", out)
        self.assertIn("budget tuning", out)
        # plan phase doesn't include known-issues:
        self.assertNotIn("CRLF on windows", out)

    def test_recall_work_phase_includes_known_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                out = hm.phase_recall("work", "agentm")
        self.assertIn("CRLF on windows", out)
        self.assertIn("pick stdlib", out)
        # work doesn't include open-questions:
        self.assertNotIn("budget tuning", out)

    def test_recall_budget_env_caps_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp), project="agentm")
            with _ClearEnv(
                set_vars={
                    "MEMORY_VAULT_PATH": str(vault),
                    # Brutally tight budget — should drop trailing entries.
                    "HARNESS_RECALL_BUDGET_PLAN": "30",
                }
            ):
                out = hm.phase_recall("plan", "agentm")
        # Output should be small. Approx budget=30 tokens = ~120 chars.
        # Allow for the header + at least one entry; the rest must be truncated.
        self.assertLess(len(out), 600)

    def test_recall_unknown_phase_raises(self) -> None:
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": "/tmp"}):
            with self.assertRaises(ValueError):
                hm.phase_recall("bogus", "x")

    def test_recall_review_phase_no_project_entries(self) -> None:
        """Review phase reads only always-load (no per-project, by spec)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp), project="agentm")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                out = hm.phase_recall("review", "agentm")
        self.assertIn("coding style", out)
        self.assertNotIn("pick stdlib", out)


# -----------------------------------------------------------------------------
# offer-save: confidence / mode / non-TTY
# -----------------------------------------------------------------------------

class TestOfferSaveDecision(unittest.TestCase):
    """should_prompt() pure decision logic — no I/O."""

    def test_silent_mode_never_prompts(self) -> None:
        self.assertFalse(hm.should_prompt(0.1, mode="silent", threshold=0.8))
        self.assertFalse(hm.should_prompt(None, mode="silent", threshold=0.8))

    def test_off_mode_never_prompts(self) -> None:
        self.assertFalse(hm.should_prompt(0.99, mode="off", threshold=0.8))

    def test_ask_mode_high_confidence_skips_prompt(self) -> None:
        self.assertFalse(hm.should_prompt(0.9, mode="ask", threshold=0.8))

    def test_ask_mode_low_confidence_prompts(self) -> None:
        self.assertTrue(hm.should_prompt(0.5, mode="ask", threshold=0.8))

    def test_ask_mode_no_confidence_prompts(self) -> None:
        self.assertTrue(hm.should_prompt(None, mode="ask", threshold=0.8))

    def test_ask_mode_at_threshold_skips(self) -> None:
        self.assertFalse(hm.should_prompt(0.8, mode="ask", threshold=0.8))


class TestOfferSaveBehavior(unittest.TestCase):
    """offer_save() end-to-end with stub toolkit."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.vault = _make_vault(self.tmp_root, project="fixture-project")
        self.save_log = self.tmp_root / "save_log.json"
        self.toolkit = _make_toolkit_stub(self.tmp_root, save_log=self.save_log)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *, confidence=None, mode="ask", threshold=None, stdin_data=""):
        env = {
            "MEMORY_VAULT_PATH": str(self.vault),
            "HARNESS_AUTO_SAVE_MODE": mode,
            "HARNESS_MEMORY_TOOLKIT_PATH": str(self.toolkit),
        }
        if threshold is not None:
            env["HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD"] = str(threshold)
        stdout = io.StringIO()
        stderr = io.StringIO()
        # Non-TTY stdin (StringIO doesn't have isatty=True).
        stdin = io.StringIO(stdin_data)
        with _ClearEnv(set_vars=env):
            rc = hm.offer_save(
                phase="work",
                project="fixture-project",
                kind="decision",
                slug="example-call",
                body="this is the entry body\n",
                confidence=confidence,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_off_mode_no_save(self) -> None:
        rc, out, err = self._run(mode="off")
        self.assertEqual(rc, 0)
        self.assertIn("HARNESS_AUTO_SAVE_MODE=off", err)
        self.assertFalse(self.save_log.exists())

    def test_silent_mode_saves_without_prompt(self) -> None:
        rc, out, err = self._run(mode="silent")
        self.assertEqual(rc, 0)
        # No preview headers in silent mode.
        self.assertNotIn("offer-save preview", out)
        self.assertIn("silent save", err)
        self.assertTrue(self.save_log.exists())
        log = json.loads(self.save_log.read_text(encoding="utf-8"))
        self.assertIn("--group", log["argv"])
        idx = log["argv"].index("--group")
        self.assertEqual(log["argv"][idx + 1], "personal-projects/fixture-project")
        self.assertEqual(log["argv"][-2:], ["decision", "example-call"])
        self.assertIn("this is the entry body", log["stdin"])

    def test_ask_high_confidence_auto_saves(self) -> None:
        rc, out, err = self._run(mode="ask", confidence=0.9)
        self.assertEqual(rc, 0)
        self.assertNotIn("offer-save preview", out)
        self.assertIn("[auto-saved high-confidence]", err)
        self.assertTrue(self.save_log.exists())

    def test_ask_low_confidence_non_tty_skips(self) -> None:
        rc, out, err = self._run(mode="ask", confidence=0.5)
        self.assertEqual(rc, 0)
        self.assertIn("offer-save preview", out)
        self.assertIn("non-TTY", err)
        self.assertFalse(self.save_log.exists())

    def test_ask_no_confidence_non_tty_skips(self) -> None:
        rc, out, err = self._run(mode="ask", confidence=None)
        self.assertEqual(rc, 0)
        self.assertIn("offer-save preview", out)
        self.assertFalse(self.save_log.exists())

    def test_ask_high_confidence_but_higher_threshold_prompts(self) -> None:
        # confidence=0.9 with threshold=0.95 → still below → prompt fires
        # (non-TTY default skip).
        rc, out, err = self._run(mode="ask", confidence=0.9, threshold=0.95)
        self.assertEqual(rc, 0)
        self.assertIn("offer-save preview", out)
        self.assertFalse(self.save_log.exists())


class TestOfferSaveToolkitAbsent(unittest.TestCase):
    """When toolkit isn't installed, offer-save records intent + exits 0."""

    def test_toolkit_absent_graceful_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            # Point toolkit override at a non-existent dir.
            env = {
                "MEMORY_VAULT_PATH": str(vault),
                "HARNESS_AUTO_SAVE_MODE": "silent",
                "HARNESS_MEMORY_TOOLKIT_PATH": str(Path(tmp) / "missing-toolkit"),
            }
            stdout = io.StringIO()
            stderr = io.StringIO()
            with _ClearEnv(set_vars=env):
                rc = hm.offer_save(
                    phase="work",
                    project="fixture-project",
                    kind="decision",
                    slug="x",
                    body="body",
                    confidence=0.9,
                    stdin=io.StringIO(""),
                    stdout=stdout,
                    stderr=stderr,
                )
            self.assertEqual(rc, 0)
            self.assertIn("toolkit not installed", stderr.getvalue())


# -----------------------------------------------------------------------------
# plan-done-promotion: cursor + tail
# -----------------------------------------------------------------------------

class TestPlanDonePromotion(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vault = _make_vault(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_progress(self, content: str) -> None:
        h = self.root / ".harness"
        h.mkdir(parents=True, exist_ok=True)
        (h / "progress.md").write_bytes(content.encode("utf-8"))

    def test_no_progress_returns_empty(self) -> None:
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(self.vault)}):
            self.assertEqual(hm.plan_done_promotion(self.root), "")

    def test_vault_absent_returns_empty(self) -> None:
        self._write_progress("some content\n")
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            self.assertEqual(hm.plan_done_promotion(self.root), "")

    def test_first_run_returns_full_tail_and_advances_cursor(self) -> None:
        content = "entry A\n\nentry B\n\nentry C\n"
        self._write_progress(content)
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(self.vault)}):
            tail = hm.plan_done_promotion(self.root)
        self.assertEqual(tail, content)
        cursor = (self.root / ".harness" / ".promoted-progress-cursor").read_text(encoding="utf-8")
        self.assertEqual(int(cursor.strip()), len(content.encode("utf-8")))

    def test_idempotent_re_run_returns_empty(self) -> None:
        content = "entry A\n\nentry B\n"
        self._write_progress(content)
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(self.vault)}):
            first = hm.plan_done_promotion(self.root)
            second = hm.plan_done_promotion(self.root)
        self.assertEqual(first, content)
        self.assertEqual(second, "")

    def test_appended_content_after_cursor_returned_next_run(self) -> None:
        self._write_progress("entry A\n")
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(self.vault)}):
            hm.plan_done_promotion(self.root)
            # Now append more progress entries
            (self.root / ".harness" / "progress.md").write_bytes(
                b"entry A\nentry B\n"
            )
            second = hm.plan_done_promotion(self.root)
        self.assertEqual(second, "entry B\n")

    def test_dry_run_does_not_advance_cursor(self) -> None:
        self._write_progress("entry A\n")
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(self.vault)}):
            tail = hm.plan_done_promotion(self.root, advance_cursor=False)
            self.assertEqual(tail, "entry A\n")
            # Cursor file should not exist (no advance happened).
            self.assertFalse((self.root / ".harness" / ".promoted-progress-cursor").is_file())
            # Re-running (without dry-run) should still return the full tail.
            again = hm.plan_done_promotion(self.root)
            self.assertEqual(again, "entry A\n")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

class TestCLI(unittest.TestCase):

    def _run(self, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("MEMORY_VAULT_PATH", None)
        env.pop("HARNESS_AUTO_SAVE_MODE", None)
        env.pop("HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD", None)
        env.pop("HARNESS_MEMORY_TOOLKIT_PATH", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(_HERE / "harness_memory.py"), *args],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )

    def test_cli_available_no_vault_exits_1(self) -> None:
        result = self._run("available")
        self.assertEqual(result.returncode, 1)

    def test_cli_available_with_vault_exits_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            result = self._run("available", env_extra={"MEMORY_VAULT_PATH": str(vault)})
        self.assertEqual(result.returncode, 0)

    def test_cli_recall_no_vault_empty_zero(self) -> None:
        result = self._run("recall", "--phase", "work", "--project", "x")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_cli_recall_with_vault_emits_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp), project="agentm")
            result = self._run(
                "recall", "--phase", "plan", "--project", "agentm",
                env_extra={"MEMORY_VAULT_PATH": str(vault)},
            )
        self.assertEqual(result.returncode, 0)
        self.assertIn("coding style", result.stdout)
        self.assertIn("pick stdlib", result.stdout)

    def test_cli_plan_done_promotion_empty_when_no_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            result = self._run(
                "plan-done-promotion", "--project-root", tmp,
                env_extra={"MEMORY_VAULT_PATH": str(vault)},
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    # V4 #37 task 7: dispatcher CLI subcommands.

    def test_cli_vault_state_path_resolves_post_v37_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            vault = _make_vault_new_layout(Path(tmp), project="fixture")
            result = self._run(
                "vault-state-path", "PLAN.md",
                "--project-root", str(project_root),
                env_extra={"MEMORY_VAULT_PATH": str(vault)},
            )
        self.assertEqual(result.returncode, 0)
        expected = str(vault / "projects" / "fixture" / "_harness" / "PLAN.md")
        self.assertEqual(result.stdout.strip(), expected)

    def test_cli_vault_state_path_exits_1_when_no_resolution(self) -> None:
        """No slug + no vault → empty stdout + exit 1 (caller graceful-skips)."""
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                "vault-state-path", "PLAN.md",
                "--project-root", tmp,
            )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_cli_read_state_returns_vault_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            vault = _make_vault_new_layout(Path(tmp), project="fixture")
            (vault / "projects" / "fixture" / "_harness").mkdir(parents=True)
            (vault / "projects" / "fixture" / "_harness" / "PLAN.md").write_text(
                "vault PLAN content\n", encoding="utf-8"
            )
            result = self._run(
                "read-state", "PLAN.md",
                "--project-root", str(project_root),
                env_extra={"MEMORY_VAULT_PATH": str(vault)},
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "vault PLAN content\n")

    def test_cli_read_state_falls_back_to_legacy(self) -> None:
        """Vault file absent → falls back to legacy <project>/.harness/<file>."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            (project_root / ".harness" / "PLAN.md").write_text(
                "legacy PLAN content\n", encoding="utf-8"
            )
            vault = _make_vault_new_layout(Path(tmp), project="fixture")
            result = self._run(
                "read-state", "PLAN.md",
                "--project-root", str(project_root),
                env_extra={"MEMORY_VAULT_PATH": str(vault)},
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "legacy PLAN content\n")
        # Warn-once notice on stderr.
        self.assertIn("legacy", result.stderr.lower())

    def test_cli_write_state_writes_to_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            vault = _make_vault_new_layout(Path(tmp), project="fixture")
            content_file = Path(tmp) / "input.md"
            content_file.write_text("new vault content\n", encoding="utf-8")
            result = self._run(
                "write-state", "PLAN.md",
                "--project-root", str(project_root),
                "--content-file", str(content_file),
                env_extra={"MEMORY_VAULT_PATH": str(vault)},
            )
            self.assertEqual(result.returncode, 0)
            target = vault / "projects" / "fixture" / "_harness" / "PLAN.md"
            self.assertEqual(result.stdout.strip(), str(target))
            self.assertEqual(target.read_text(encoding="utf-8"), "new vault content\n")


# -----------------------------------------------------------------------------
# resolve_project / vault_state_path / _vault_projects_dir  (V4 #26)
# -----------------------------------------------------------------------------

def _make_vault_new_layout(root: Path, *, project: str = "fixture-project") -> Path:
    """Build a vault using the post-V4 #26 `projects/` layout (no legacy dir)."""
    vault = root / "vault"
    (vault / "personal-private" / "_always-load").mkdir(parents=True)
    (vault / "projects" / project / "decisions").mkdir(parents=True)
    (vault / "projects" / project / "_index.md").write_text(
        f"# {project} index\nv4.1.0+ layout\n",
        encoding="utf-8",
    )
    return vault


class TestVaultProjectsDir(unittest.TestCase):
    """Covers the dual-path helper that prefers `projects/` over `personal-projects/`."""

    def test_prefers_new_projects_dir_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "projects").mkdir(parents=True)
            result = hm._vault_projects_dir(vault)
            self.assertEqual(result, vault / "projects")

    def test_falls_back_to_legacy_personal_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "personal-projects").mkdir(parents=True)
            result = hm._vault_projects_dir(vault)
            self.assertEqual(result, vault / "personal-projects")

    def test_prefers_new_when_both_present(self) -> None:
        """Locked semantics: if both dirs exist, new layout wins (legacy is stale)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "projects").mkdir(parents=True)
            (vault / "personal-projects").mkdir(parents=True)
            result = hm._vault_projects_dir(vault)
            self.assertEqual(result, vault / "projects")

    def test_returns_new_path_when_neither_present(self) -> None:
        """Empty vault: return the new path (so write callers target post-V4 layout)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            result = hm._vault_projects_dir(vault)
            self.assertEqual(result, vault / "projects")


class TestResolveProject(unittest.TestCase):
    """Covers resolve_project() → {slug, vault_path, project_root, layout}."""

    def test_no_slug_returns_none_fields(self) -> None:
        """No git origin + no project.json = no slug → layout='none'."""
        with tempfile.TemporaryDirectory() as tmp:
            resolution = hm.resolve_project({"cwd": Path(tmp)})
        self.assertIsNone(resolution["slug"])
        self.assertIsNone(resolution["vault_path"])
        self.assertEqual(resolution["layout"], "none")

    def test_slug_present_but_vault_unset(self) -> None:
        """Slug from .harness/project.json, but MEMORY_VAULT_PATH unset → vault_path=None."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "my-project"}', encoding="utf-8"
            )
            with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
                resolution = hm.resolve_project({"cwd": project_root})
        self.assertEqual(resolution["slug"], "my-project")
        self.assertIsNone(resolution["vault_path"])
        self.assertEqual(resolution["layout"], "none")

    def test_resolves_new_layout(self) -> None:
        """Vault has projects/<slug>/ → layout='new'."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            vault = _make_vault_new_layout(Path(tmp), project="fixture")
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                resolution = hm.resolve_project({"cwd": project_root})
        self.assertEqual(resolution["slug"], "fixture")
        self.assertEqual(resolution["vault_path"], vault / "projects" / "fixture")
        self.assertEqual(resolution["layout"], "new")

    def test_resolves_legacy_layout(self) -> None:
        """Vault has personal-projects/<slug>/ (no projects/) → layout='legacy'."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "fixture"}', encoding="utf-8"
            )
            vault = _make_vault(Path(tmp), project="fixture")  # legacy layout
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                resolution = hm.resolve_project({"cwd": project_root})
        self.assertEqual(resolution["slug"], "fixture")
        self.assertEqual(
            resolution["vault_path"], vault / "personal-projects" / "fixture"
        )
        self.assertEqual(resolution["layout"], "legacy")

    def test_returns_new_path_when_neither_layout_has_project(self) -> None:
        """Slug + vault present but no project dir → layout='new', path=projects/<slug>."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            (project_root / ".harness").mkdir()
            (project_root / ".harness" / "project.json").write_text(
                '{"vault_project": "new-project"}', encoding="utf-8"
            )
            vault = Path(tmp) / "vault"
            vault.mkdir()
            with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": str(vault)}):
                resolution = hm.resolve_project({"cwd": project_root})
        self.assertEqual(resolution["slug"], "new-project")
        self.assertEqual(resolution["vault_path"], vault / "projects" / "new-project")
        self.assertEqual(resolution["layout"], "new")


class TestVaultStatePath(unittest.TestCase):
    """Covers vault_state_path(resolution, filename) — path construction only."""

    def test_returns_none_when_no_vault_path(self) -> None:
        result = hm.vault_state_path({"vault_path": None}, "PLAN.md")
        self.assertIsNone(result)

    def test_returns_none_when_missing_field(self) -> None:
        result = hm.vault_state_path({}, "PLAN.md")
        self.assertIsNone(result)

    def test_returns_harness_subpath(self) -> None:
        resolution = {"vault_path": Path("/tmp/vault/projects/agentm")}
        result = hm.vault_state_path(resolution, "PLAN.md")
        self.assertEqual(result, Path("/tmp/vault/projects/agentm/_harness/PLAN.md"))

    def test_handles_nested_filenames(self) -> None:
        resolution = {"vault_path": Path("/tmp/vault/projects/agentm")}
        result = hm.vault_state_path(resolution, "designs/v4-26/01-pre-flight.md")
        self.assertEqual(
            result,
            Path("/tmp/vault/projects/agentm/_harness/designs/v4-26/01-pre-flight.md"),
        )


# -----------------------------------------------------------------------------
# read_state_file / write_state_file / warn_once  (V4 #26 task 3)
# -----------------------------------------------------------------------------

class TestReadStateFile(unittest.TestCase):
    """Covers backward-compat read with vault-first, legacy-fallback semantics."""

    def setUp(self) -> None:
        hm._reset_warn_state()

    def test_returns_empty_when_neither_path_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolution = {
                "vault_path": Path(tmp) / "vault" / "projects" / "p",
                "project_root": Path(tmp) / "project",
            }
            (Path(tmp) / "project").mkdir()
            self.assertEqual(hm.read_state_file(resolution, "PLAN.md"), "")

    def test_reads_from_vault_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vp = Path(tmp) / "vault" / "projects" / "p"
            (vp / "_harness").mkdir(parents=True)
            (vp / "_harness" / "PLAN.md").write_text("vault content", encoding="utf-8")
            project = Path(tmp) / "project"
            project.mkdir()
            resolution = {"vault_path": vp, "project_root": project}
            self.assertEqual(hm.read_state_file(resolution, "PLAN.md"), "vault content")

    def test_falls_back_to_legacy_with_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / "PLAN.md").write_text("legacy content", encoding="utf-8")
            resolution = {
                "vault_path": Path(tmp) / "vault" / "projects" / "p",  # doesn't exist
                "project_root": project,
            }
            with io.StringIO() as buf:
                # Capture stderr via mock
                with mock.patch("sys.stderr", buf):
                    result = hm.read_state_file(resolution, "PLAN.md")
                self.assertEqual(result, "legacy content")
                stderr = buf.getvalue()
                self.assertIn("reading PLAN.md from legacy", stderr)
                self.assertIn("migrate-harness-to-vault.sh", stderr)

    def test_warn_only_once_per_session_per_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / "PLAN.md").write_text("legacy", encoding="utf-8")
            resolution = {"vault_path": None, "project_root": project}
            with io.StringIO() as buf:
                with mock.patch("sys.stderr", buf):
                    hm.read_state_file(resolution, "PLAN.md")
                    hm.read_state_file(resolution, "PLAN.md")
                    hm.read_state_file(resolution, "PLAN.md")
                # Single warning despite 3 reads.
                self.assertEqual(buf.getvalue().count("reading PLAN.md from legacy"), 1)

    def test_warns_separately_for_different_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / "PLAN.md").write_text("a", encoding="utf-8")
            (project / ".harness" / "progress.md").write_text("b", encoding="utf-8")
            resolution = {"vault_path": None, "project_root": project}
            with io.StringIO() as buf:
                with mock.patch("sys.stderr", buf):
                    hm.read_state_file(resolution, "PLAN.md")
                    hm.read_state_file(resolution, "progress.md")
                stderr = buf.getvalue()
                self.assertEqual(stderr.count("reading PLAN.md from legacy"), 1)
                self.assertEqual(stderr.count("reading progress.md from legacy"), 1)

    def test_project_mode_local_bypasses_vault_read(self) -> None:
        """DC-3: when .project-mode='local', read goes straight to legacy."""
        with tempfile.TemporaryDirectory() as tmp:
            vp = Path(tmp) / "vault" / "projects" / "p"
            (vp / "_harness").mkdir(parents=True)
            (vp / "_harness" / "PLAN.md").write_text("vault wins", encoding="utf-8")
            (vp / "_harness" / ".project-mode").write_text("local", encoding="utf-8")
            project = Path(tmp) / "project"
            (project / ".harness").mkdir(parents=True)
            (project / ".harness" / "PLAN.md").write_text("legacy wins", encoding="utf-8")
            resolution = {"vault_path": vp, "project_root": project}
            with io.StringIO() as buf:
                with mock.patch("sys.stderr", buf):
                    # Despite vault content existing, .project-mode=local skips it.
                    self.assertEqual(hm.read_state_file(resolution, "PLAN.md"), "legacy wins")


class TestWriteStateFile(unittest.TestCase):
    """Covers vault-only writes (with .project-mode=local override)."""

    def test_writes_to_vault_creating_harness_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vp = Path(tmp) / "vault" / "projects" / "p"
            resolution = {"vault_path": vp, "project_root": Path(tmp) / "project"}
            target = hm.write_state_file(resolution, "PLAN.md", "new content")
            self.assertEqual(target, vp / "_harness" / "PLAN.md")
            self.assertEqual(target.read_text(encoding="utf-8"), "new content")
            # _harness/ dir created.
            self.assertTrue((vp / "_harness").is_dir())

    def test_raises_when_no_vault_path(self) -> None:
        resolution = {"vault_path": None, "project_root": Path("/tmp/project")}
        with self.assertRaises(ValueError) as cm:
            hm.write_state_file(resolution, "PLAN.md", "x")
        self.assertIn("cannot write PLAN.md", str(cm.exception))

    def test_atomic_write_no_tmp_remnant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vp = Path(tmp) / "vault" / "projects" / "p"
            resolution = {"vault_path": vp, "project_root": Path(tmp)}
            hm.write_state_file(resolution, "PLAN.md", "content")
            # No .tmp file left behind.
            self.assertEqual(
                list((vp / "_harness").glob("PLAN.md.*")), []
            )

    def test_project_mode_local_writes_to_legacy(self) -> None:
        """DC-3: when .project-mode='local', write goes to legacy."""
        with tempfile.TemporaryDirectory() as tmp:
            vp = Path(tmp) / "vault" / "projects" / "p"
            (vp / "_harness").mkdir(parents=True)
            (vp / "_harness" / ".project-mode").write_text("local", encoding="utf-8")
            project = Path(tmp) / "project"
            project.mkdir()
            resolution = {"vault_path": vp, "project_root": project}
            target = hm.write_state_file(resolution, "PLAN.md", "legacy write")
            self.assertEqual(target, project / ".harness" / "PLAN.md")
            self.assertEqual(target.read_text(encoding="utf-8"), "legacy write")
            # Vault path NOT written.
            self.assertFalse((vp / "_harness" / "PLAN.md").exists())


# -----------------------------------------------------------------------------
# safe_write_replace_style / detect_conflict_files  (V4 #26 task 4)
# -----------------------------------------------------------------------------

class TestSafeWriteReplaceStyle(unittest.TestCase):
    """Covers atomic-write with optional mtime concurrent-modification check."""

    def test_plain_write_no_mtime_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            result = hm.safe_write_replace_style(path, "content")
            self.assertEqual(result, path)
            self.assertEqual(path.read_text(), "content")

    def test_overwrites_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            path.write_text("old")
            hm.safe_write_replace_style(path, "new")
            self.assertEqual(path.read_text(), "new")

    def test_creates_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "deep" / "PLAN.md"
            hm.safe_write_replace_style(path, "x")
            self.assertTrue(path.is_file())

    def test_mtime_check_passes_when_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            path.write_text("initial")
            mtime = path.stat().st_mtime
            # Write with matching expected_mtime succeeds.
            hm.safe_write_replace_style(path, "updated", expected_mtime=mtime)
            self.assertEqual(path.read_text(), "updated")

    def test_mtime_check_raises_when_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            path.write_text("initial")
            # Simulate "I read it earlier" with a stale mtime.
            stale_mtime = path.stat().st_mtime - 1000.0  # an hour ago
            with self.assertRaises(hm.ConcurrentModificationError) as cm:
                hm.safe_write_replace_style(path, "x", expected_mtime=stale_mtime)
            self.assertIn("modified since read", str(cm.exception))
            # File contents unchanged.
            self.assertEqual(path.read_text(), "initial")

    def test_mtime_check_passes_when_file_absent_originally(self) -> None:
        """First-write case: expected_mtime is provided but file doesn't exist
        yet — check should pass (nothing to conflict with) and proceed."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            hm.safe_write_replace_style(path, "x", expected_mtime=12345.0)
            self.assertEqual(path.read_text(), "x")

    def test_atomic_no_tmp_remnant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.md"
            hm.safe_write_replace_style(path, "x")
            self.assertEqual(list(Path(tmp).glob("PLAN.md.*")), [])


class TestDetectConflictFiles(unittest.TestCase):
    """Covers GDrive conflict-file detection + base-path inference."""

    def test_returns_empty_when_no_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "PLAN.md").write_text("clean")
            self.assertEqual(hm.detect_conflict_files(Path(tmp)), [])

    def test_returns_empty_for_nonexistent_vault(self) -> None:
        self.assertEqual(hm.detect_conflict_files(Path("/nonexistent/path")), [])

    def test_detects_basic_conflict_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "PLAN.md").write_text("base")
            conflict = Path(tmp) / "PLAN (conflicted copy 2026-05-27).md"
            conflict.write_text("conflict")
            result = hm.detect_conflict_files(Path(tmp))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["conflict"], conflict)
        self.assertEqual(result[0]["base"], Path(tmp) / "PLAN.md")

    def test_detects_with_device_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conflict = Path(tmp) / "PLAN (conflicted copy 2026-05-27) - Mac.md"
            conflict.write_text("x")
            result = hm.detect_conflict_files(Path(tmp))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["base"], Path(tmp) / "PLAN.md")

    def test_detects_nested_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "projects" / "agentm" / "_harness").mkdir(parents=True)
            conflict = Path(tmp) / "projects" / "agentm" / "_harness" / "PLAN (conflicted copy 2026-05-27).md"
            conflict.write_text("nested")
            result = hm.detect_conflict_files(Path(tmp))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["conflict"], conflict)
        # rel path computed against vault_root. Compare via Path() to keep
        # Windows + POSIX both happy (Path equality is platform-normalized).
        self.assertEqual(
            result[0]["rel"],
            Path("projects/agentm/_harness/PLAN (conflicted copy 2026-05-27).md"),
        )

    def test_detects_multiple_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "PLAN (conflicted copy 2026-05-27).md").write_text("a")
            (Path(tmp) / "FOLLOWUPS (conflicted copy 2026-05-27).md").write_text("b")
            result = hm.detect_conflict_files(Path(tmp))
        self.assertEqual(len(result), 2)

    def test_case_insensitive_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "PLAN (Conflicted Copy 2026-05-27).md").write_text("x")
            result = hm.detect_conflict_files(Path(tmp))
        self.assertEqual(len(result), 1)

    def test_ignores_files_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "PLAN.md").write_text("clean")
            (Path(tmp) / "progress.md").write_text("clean")
            (Path(tmp) / "ROADMAP-V4.md").write_text("clean")
            self.assertEqual(hm.detect_conflict_files(Path(tmp)), [])


# -----------------------------------------------------------------------------
# vec-index drift-detection schema migration (V4 #37 task 2)
# -----------------------------------------------------------------------------

# Load vec_index directly via importlib so we can test the schema-migration
# logic without needing sqlite-vec installed in the test env (the migration
# operates on the regular `entry_meta` sqlite table — vec0 virtual table
# not required for these test paths).
import importlib.util
import sqlite3
_VEC_INDEX_PATH = _HERE.parent / "harness" / "skills" / "memory" / "scripts" / "vec_index.py"
_vec_spec = importlib.util.spec_from_file_location("vec_index", _VEC_INDEX_PATH)
vec_index = importlib.util.module_from_spec(_vec_spec)
# Register in sys.modules so lazy-importing modules (e.g. recall.py) resolve
# to the SAME module instance the tests patch via mock.patch.object().
sys.modules["vec_index"] = vec_index
_vec_spec.loader.exec_module(vec_index)


def _make_pre_v37_entry_meta(db_path: Path) -> sqlite3.Connection:
    """Build a pre-#37-shaped sqlite db with `entry_meta` lacking `indexed_at`."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE entry_meta ("
        "  rowid INTEGER PRIMARY KEY,"
        "  path TEXT UNIQUE NOT NULL,"
        "  updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "INSERT INTO entry_meta(rowid, path, updated_at) VALUES (1, 'preferences/old-entry.md', '2026-04-01T12:00:00Z')"
    )
    conn.commit()
    return conn


def _make_post_v37_entry_meta(db_path: Path) -> sqlite3.Connection:
    """Build a post-#37-shaped sqlite db with `indexed_at` already present."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE entry_meta ("
        "  rowid INTEGER PRIMARY KEY,"
        "  path TEXT UNIQUE NOT NULL,"
        "  updated_at TEXT NOT NULL,"
        "  indexed_at INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    conn.execute(
        "INSERT INTO entry_meta(rowid, path, updated_at, indexed_at) VALUES (1, 'preferences/new-entry.md', '2026-05-27T18:00:00Z', 1748376000)"
    )
    conn.commit()
    return conn


class TestVecIndexSchemaMigration(unittest.TestCase):
    """V4 #37 task 2: pre-#37 → v37 schema migration via ALTER TABLE ADD COLUMN."""

    def test_has_column_detects_present_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_post_v37_entry_meta(db)
            try:
                self.assertTrue(vec_index._has_column(conn, "entry_meta", "indexed_at"))
                self.assertTrue(vec_index._has_column(conn, "entry_meta", "path"))
                self.assertFalse(vec_index._has_column(conn, "entry_meta", "nonexistent_column"))
            finally:
                conn.close()

    def test_has_column_detects_absent_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_pre_v37_entry_meta(db)
            try:
                self.assertFalse(vec_index._has_column(conn, "entry_meta", "indexed_at"))
            finally:
                conn.close()

    def test_migrate_pre_v37_adds_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_pre_v37_entry_meta(db)
            try:
                # Pre-migration: column absent.
                self.assertFalse(vec_index._has_column(conn, "entry_meta", "indexed_at"))
                # Run migration.
                with mock.patch("sys.stderr"):
                    migrated = vec_index._migrate_pre_v37(conn)
                conn.commit()
                self.assertTrue(migrated, "migration should have run")
                # Post-migration: column present.
                self.assertTrue(vec_index._has_column(conn, "entry_meta", "indexed_at"))
            finally:
                conn.close()

    def test_migrate_pre_v37_preserves_existing_rows(self) -> None:
        """ALTER TABLE preserves rows; existing entries get indexed_at=0 (default)."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_pre_v37_entry_meta(db)
            try:
                with mock.patch("sys.stderr"):
                    vec_index._migrate_pre_v37(conn)
                conn.commit()
                cursor = conn.execute(
                    "SELECT path, updated_at, indexed_at FROM entry_meta WHERE rowid = 1"
                )
                row = cursor.fetchone()
                self.assertEqual(row[0], "preferences/old-entry.md")
                self.assertEqual(row[1], "2026-04-01T12:00:00Z")
                self.assertEqual(row[2], 0, "default value should be 0 (pre-#37 rows appear drifted)")
            finally:
                conn.close()

    def test_migrate_pre_v37_idempotent(self) -> None:
        """Re-running migration on already-migrated table is a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_post_v37_entry_meta(db)
            try:
                with mock.patch("sys.stderr"):
                    migrated = vec_index._migrate_pre_v37(conn)
                conn.commit()
                self.assertFalse(migrated, "should be no-op on already-migrated schema")
                # Row data unchanged.
                cursor = conn.execute("SELECT indexed_at FROM entry_meta WHERE rowid = 1")
                self.assertEqual(cursor.fetchone()[0], 1748376000)
            finally:
                conn.close()

    def test_migrate_pre_v37_emits_one_line_stderr_notice(self) -> None:
        """First migration emits a clear one-line stderr notice; re-runs do not."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = _make_pre_v37_entry_meta(db)
            try:
                with io.StringIO() as buf:
                    with mock.patch("sys.stderr", buf):
                        vec_index._migrate_pre_v37(conn)
                    stderr = buf.getvalue()
                self.assertIn("migrated pre-v4.2 entry_meta schema to v37", stderr)
                self.assertIn("drift-detection enabled", stderr)
                # Re-run: no additional notice (already migrated).
                with io.StringIO() as buf:
                    with mock.patch("sys.stderr", buf):
                        vec_index._migrate_pre_v37(conn)
                    self.assertEqual(buf.getvalue(), "")
            finally:
                conn.close()


# -----------------------------------------------------------------------------
# Drift detection primitives (V4 #37 task 3)
# -----------------------------------------------------------------------------

def _seed_v37_index(db_path: Path, entries: dict[str, int]) -> None:
    """Build a v37-shaped sqlite db with seeded entry_meta rows.

    entries: {entry_relative_path: indexed_at_epoch}
    No vec0 virtual table — just the metadata side, which is what drift-
    detection reads.
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE entry_meta ("
        "  rowid INTEGER PRIMARY KEY,"
        "  path TEXT UNIQUE NOT NULL,"
        "  updated_at TEXT NOT NULL,"
        "  indexed_at INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    for i, (path, indexed_at) in enumerate(entries.items(), start=1):
        conn.execute(
            "INSERT INTO entry_meta(rowid, path, updated_at, indexed_at) VALUES (?, ?, '2026-05-27T18:00:00Z', ?)",
            (i, path, indexed_at),
        )
    conn.commit()
    conn.close()


class _MockConn:
    """Stand-in for sqlite_vec-loaded connection — exposes just the parts
    the drift-detection code touches (execute returns a real cursor).
    Used to bypass _open_index's sqlite-vec check + extension load."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()


class TestIsEntryDrifted(unittest.TestCase):
    """V4 #37 task 3: per-entry drift detection."""

    def _patch_open_index(self, db_path: Path):
        """Return a mock.patch context that makes _open_index return our test db.

        Uses side_effect (lazy) instead of return_value (eager) so the sqlite
        connection is only created when production code actually invokes
        _open_index(). Without this, early-return code paths (e.g. source-file
        missing) would never consume the pre-built connection, leaking a file
        handle that breaks tempdir cleanup on Windows (WinError 32).
        """
        return mock.patch.object(
            vec_index,
            "_open_index",
            side_effect=lambda *a, **kw: _MockConn(db_path),
        )

    def test_returns_true_when_entry_not_indexed(self) -> None:
        """Entry exists on disk but has no row in entry_meta → drifted (first-embed)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private" / "_always-load").mkdir(parents=True)
            entry = vault / "personal-private" / "_always-load" / "new-rule.md"
            entry.write_text("freshly authored", encoding="utf-8")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})  # empty index
            with self._patch_open_index(db):
                self.assertTrue(
                    vec_index.is_entry_drifted(vault, "personal-private/_always-load/new-rule.md")
                )

    def test_returns_false_when_indexed_and_unchanged(self) -> None:
        """Entry indexed AT or AFTER current mtime → not drifted."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private" / "_always-load").mkdir(parents=True)
            entry = vault / "personal-private" / "_always-load" / "stable.md"
            entry.write_text("indexed earlier", encoding="utf-8")
            future = int(entry.stat().st_mtime) + 60  # indexed 60s after mtime
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {"personal-private/_always-load/stable.md": future})
            with self._patch_open_index(db):
                self.assertFalse(
                    vec_index.is_entry_drifted(vault, "personal-private/_always-load/stable.md")
                )

    def test_returns_true_when_mtime_exceeds_indexed_at(self) -> None:
        """Entry's source mtime > indexed_at + tolerance → drifted."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private" / "_always-load").mkdir(parents=True)
            entry = vault / "personal-private" / "_always-load" / "stale-row.md"
            entry.write_text("freshly touched", encoding="utf-8")
            past = int(entry.stat().st_mtime) - 1000  # indexed 1000s before mtime
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {"personal-private/_always-load/stale-row.md": past})
            with self._patch_open_index(db):
                self.assertTrue(
                    vec_index.is_entry_drifted(vault, "personal-private/_always-load/stale-row.md")
                )

    def test_returns_true_when_source_file_missing(self) -> None:
        """Entry's source file is gone → drifted (caller handles delete)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {"deleted-entry.md": 12345})
            with self._patch_open_index(db):
                self.assertTrue(vec_index.is_entry_drifted(vault, "deleted-entry.md"))

    def test_returns_false_when_sqlite_vec_unavailable(self) -> None:
        """Graceful-skip: when index can't open, no signal → not drifted."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private" / "_always-load").mkdir(parents=True)
            entry = vault / "personal-private" / "_always-load" / "any.md"
            entry.write_text("x", encoding="utf-8")
            with mock.patch.object(vec_index, "_open_index", return_value=None):
                self.assertFalse(
                    vec_index.is_entry_drifted(vault, "personal-private/_always-load/any.md")
                )

    def test_tolerance_window_avoids_false_positive(self) -> None:
        """Sub-1-second mtime/indexed-at differences should NOT report drift."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private" / "_always-load").mkdir(parents=True)
            entry = vault / "personal-private" / "_always-load" / "same-second.md"
            entry.write_text("x", encoding="utf-8")
            # indexed_at = mtime - 0.5 (within tolerance window)
            mtime = entry.stat().st_mtime
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {"personal-private/_always-load/same-second.md": int(mtime)})
            with self._patch_open_index(db):
                # mtime == int(mtime) + (fractional); within 1s tolerance.
                self.assertFalse(
                    vec_index.is_entry_drifted(vault, "personal-private/_always-load/same-second.md")
                )


class TestFindDriftedEntries(unittest.TestCase):
    """V4 #37 task 3: vault-walk drift inventory."""

    def _patch_open_index(self, db_path: Path):
        return mock.patch.object(
            vec_index,
            "_open_index",
            return_value=_MockConn(db_path),
        )

    def test_returns_empty_for_nonexistent_vault(self) -> None:
        result = vec_index.find_drifted_entries(Path("/nonexistent/path"))
        self.assertEqual(result, {"drifted": [], "up_to_date": [], "not_indexed": []})

    def test_classifies_mixed_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private" / "_always-load").mkdir(parents=True)
            (vault / "projects" / "fixture").mkdir(parents=True)
            # 3 entries: 1 indexed-fresh, 1 indexed-stale, 1 not-indexed
            fresh = vault / "personal-private" / "_always-load" / "fresh.md"
            stale = vault / "personal-private" / "_always-load" / "stale.md"
            new_entry = vault / "projects" / "fixture" / "new.md"
            for f in (fresh, stale, new_entry):
                f.write_text("x", encoding="utf-8")
            fresh_indexed = int(fresh.stat().st_mtime) + 60
            stale_indexed = int(stale.stat().st_mtime) - 1000
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {
                "personal-private/_always-load/fresh.md": fresh_indexed,
                "personal-private/_always-load/stale.md": stale_indexed,
            })
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["up_to_date"], ["personal-private/_always-load/fresh.md"])
        self.assertEqual(result["drifted"], ["personal-private/_always-load/stale.md"])
        self.assertEqual(result["not_indexed"], ["projects/fixture/new.md"])

    def test_excludes_archive_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private" / "_archive").mkdir(parents=True)
            (vault / "personal-private" / "_archive" / "old.md").write_text("x")
            (vault / "personal-private" / "active.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["not_indexed"], ["personal-private/active.md"])

    def test_excludes_plan_archive_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "projects" / "agentm" / "_harness").mkdir(parents=True)
            (vault / "projects" / "agentm" / "_harness" / "PLAN.archive.20260420.md").write_text("x")
            (vault / "projects" / "agentm" / "_harness" / "PLAN.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["not_indexed"], ["projects/agentm/_harness/PLAN.md"])

    def test_excludes_meta_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "_meta").mkdir(parents=True)
            (vault / "_meta" / "seed-manifest.md").write_text("x")
            (vault / "personal-private").mkdir(parents=True)
            (vault / "personal-private" / "active.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["not_indexed"], ["personal-private/active.md"])

    def test_walks_idea_incubator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "_idea-incubator" / "foo").mkdir(parents=True)
            (vault / "_idea-incubator" / "foo" / "_index.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["not_indexed"], ["_idea-incubator/foo/_index.md"])

    def test_legacy_personal_projects_fallback(self) -> None:
        """When projects/ absent but personal-projects/ present, walk legacy layout."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-projects" / "fixture").mkdir(parents=True)
            (vault / "personal-projects" / "fixture" / "_index.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(result["not_indexed"], ["personal-projects/fixture/_index.md"])

    def test_returns_all_not_indexed_when_sqlite_vec_unavailable(self) -> None:
        """Graceful-skip: no index → all walkable entries appear not_indexed."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private").mkdir(parents=True)
            (vault / "personal-private" / "a.md").write_text("x")
            (vault / "personal-private" / "b.md").write_text("x")
            with mock.patch.object(vec_index, "_open_index", return_value=None):
                result = vec_index.find_drifted_entries(vault)
        self.assertEqual(sorted(result["not_indexed"]), ["personal-private/a.md", "personal-private/b.md"])
        self.assertEqual(result["drifted"], [])
        self.assertEqual(result["up_to_date"], [])


# -----------------------------------------------------------------------------
# full_sync subcommand + embed-text extraction (V4 #37 task 4)
# -----------------------------------------------------------------------------

class TestExtractEmbedTextFromFile(unittest.TestCase):
    """V4 #37 task 4: extract `{slug} [tags]\\n\\n{first_para}` from a .md file."""

    def test_extracts_slug_and_tags_from_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "entry.md"
            f.write_text(
                "---\n"
                "slug: my-pref\n"
                "tags: [convention, status-report]\n"
                "kind: preference\n"
                "---\n"
                "\nUse bullet points for status reports.\n",
                encoding="utf-8",
            )
            text = vec_index._extract_embed_text_from_file(f)
        self.assertIn("my-pref", text)
        self.assertIn("convention, status-report", text)
        self.assertIn("Use bullet points", text)

    def test_falls_back_to_file_stem_when_no_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "fallback-slug.md"
            f.write_text("plain markdown no frontmatter", encoding="utf-8")
            text = vec_index._extract_embed_text_from_file(f)
        self.assertIn("fallback-slug", text)
        self.assertIn("plain markdown", text)

    def test_truncates_body_at_500_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "long.md"
            long_body = "A" * 1000
            f.write_text(f"---\nslug: long\n---\n{long_body}", encoding="utf-8")
            text = vec_index._extract_embed_text_from_file(f)
        # Body portion should be ≤500 chars; total text includes slug + tags prefix.
        body_portion = text.split("\n\n", 1)[1] if "\n\n" in text else text
        self.assertLessEqual(len(body_portion), 500)

    def test_returns_empty_when_file_missing(self) -> None:
        result = vec_index._extract_embed_text_from_file(Path("/nonexistent/path.md"))
        self.assertEqual(result, "")


class TestFullSync(unittest.TestCase):
    """V4 #37 task 4: full-sync subcommand (default report + --rebuild enqueue)."""

    def _patch_open_index(self, db_path: Path):
        return mock.patch.object(
            vec_index,
            "_open_index",
            return_value=_MockConn(db_path),
        )

    def test_default_returns_summary_without_enqueueing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private").mkdir(parents=True)
            (vault / "personal-private" / "a.md").write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {})
            with self._patch_open_index(db):
                result = vec_index.full_sync(vault, rebuild=False)
            self.assertEqual(result["not_indexed_count"], 1)
            self.assertEqual(result["enqueued"], 0)
            # Queue file should NOT exist (no rebuild)
            self.assertFalse((vault / "_meta" / "embedding-queue.jsonl").exists())

    def test_rebuild_enqueues_drifted_and_not_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private" / "_always-load").mkdir(parents=True)
            stale = vault / "personal-private" / "_always-load" / "stale.md"
            stale.write_text("---\nslug: stale\n---\nstale body", encoding="utf-8")
            new_entry = vault / "personal-private" / "_always-load" / "new.md"
            new_entry.write_text("---\nslug: new\n---\nnew body", encoding="utf-8")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {
                "personal-private/_always-load/stale.md": int(stale.stat().st_mtime) - 1000,
            })
            with self._patch_open_index(db):
                result = vec_index.full_sync(vault, rebuild=True)
            self.assertEqual(result["drifted_count"], 1)
            self.assertEqual(result["not_indexed_count"], 1)
            self.assertEqual(result["enqueued"], 2)
            # Queue file should now exist with 2 records
            queue_path = vault / "_meta" / "embedding-queue.jsonl"
            self.assertTrue(queue_path.exists())
            lines = queue_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            # Each record is well-formed JSON with op=upsert + extracted text
            for ln in lines:
                rec = json.loads(ln)
                self.assertEqual(rec["op"], "upsert")
                self.assertIn(rec["path"], (
                    "personal-private/_always-load/stale.md",
                    "personal-private/_always-load/new.md",
                ))
                self.assertIn(rec["path"].split("/")[-1].rsplit(".", 1)[0], rec["text"])

    def test_rebuild_idempotent_on_clean_vault(self) -> None:
        """All entries up-to-date → no enqueueing."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private").mkdir(parents=True)
            fresh = vault / "personal-private" / "fresh.md"
            fresh.write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {
                "personal-private/fresh.md": int(fresh.stat().st_mtime) + 60,
            })
            with self._patch_open_index(db):
                result = vec_index.full_sync(vault, rebuild=True)
            self.assertEqual(result["drifted_count"], 0)
            self.assertEqual(result["not_indexed_count"], 0)
            self.assertEqual(result["up_to_date_count"], 1)
            self.assertEqual(result["enqueued"], 0)


# -----------------------------------------------------------------------------
# recall.py drift-check integration (V4 #37 task 5)
# -----------------------------------------------------------------------------

_RECALL_PATH = _HERE.parent / "harness" / "skills" / "memory" / "scripts" / "recall.py"
_recall_spec = importlib.util.spec_from_file_location("recall", _RECALL_PATH)
recall = importlib.util.module_from_spec(_recall_spec)
_recall_spec.loader.exec_module(recall)


class TestDriftCheckVecHits(unittest.TestCase):
    """V4 #37 task 5: per-hit drift check in the recall path."""

    def _patch_open_index(self, db_path: Path):
        return mock.patch.object(
            vec_index,
            "_open_index",
            return_value=_MockConn(db_path),
        )

    def test_empty_vec_results_returns_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = recall._drift_check_vec_hits(Path(tmp), {})
            self.assertEqual(result, {})

    def test_un_drifted_hits_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private").mkdir(parents=True)
            stable = vault / "personal-private" / "stable.md"
            stable.write_text("x")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            # indexed AFTER mtime — not drifted
            _seed_v37_index(db, {"personal-private/stable.md": int(stable.stat().st_mtime) + 60})
            vec_results = {"personal-private/stable.md": 0.85}
            with self._patch_open_index(db):
                with io.StringIO() as buf:
                    fresh = recall._drift_check_vec_hits(vault, vec_results, stderr=buf)
                    self.assertEqual(fresh, vec_results)
                    self.assertNotIn("flagged for re-embed", buf.getvalue())

    def test_drifted_hits_dropped_and_enqueued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private").mkdir(parents=True)
            stale = vault / "personal-private" / "stale.md"
            stale.write_text("---\nslug: stale\n---\nstale content", encoding="utf-8")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            # indexed BEFORE mtime — drifted
            _seed_v37_index(db, {"personal-private/stale.md": int(stale.stat().st_mtime) - 1000})
            vec_results = {"personal-private/stale.md": 0.85}
            with self._patch_open_index(db):
                with io.StringIO() as buf:
                    fresh = recall._drift_check_vec_hits(vault, vec_results, stderr=buf)
                    stderr_text = buf.getvalue()
            # Drifted entry dropped from results.
            self.assertNotIn("personal-private/stale.md", fresh)
            self.assertEqual(fresh, {})
            # Transparency line emitted.
            self.assertIn("1 entries flagged for re-embed", stderr_text)
            # Enqueued to queue file.
            queue = vault / "_meta" / "embedding-queue.jsonl"
            self.assertTrue(queue.exists())
            line = queue.read_text(encoding="utf-8").strip().splitlines()[0]
            rec = json.loads(line)
            self.assertEqual(rec["op"], "upsert")
            self.assertEqual(rec["path"], "personal-private/stale.md")
            self.assertIn("stale", rec["text"])

    def test_mixed_drifted_and_clean_hits(self) -> None:
        """Drifted entries dropped; clean entries retained."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "personal-private").mkdir(parents=True)
            stale = vault / "personal-private" / "stale.md"
            stale.write_text("---\nslug: stale\n---\nx", encoding="utf-8")
            clean = vault / "personal-private" / "clean.md"
            clean.write_text("y")
            db = vault / "_meta" / "vec-index.db"
            db.parent.mkdir(parents=True)
            _seed_v37_index(db, {
                "personal-private/stale.md": int(stale.stat().st_mtime) - 1000,
                "personal-private/clean.md": int(clean.stat().st_mtime) + 60,
            })
            vec_results = {
                "personal-private/stale.md": 0.85,
                "personal-private/clean.md": 0.72,
            }
            with self._patch_open_index(db):
                with io.StringIO() as buf:
                    fresh = recall._drift_check_vec_hits(vault, vec_results, stderr=buf)
            self.assertNotIn("personal-private/stale.md", fresh)
            self.assertEqual(fresh, {"personal-private/clean.md": 0.72})

    def test_vec_index_import_failure_returns_unchanged(self) -> None:
        """Defensive: if vec_index can't be imported, return input dict unchanged."""
        vec_results = {"any.md": 0.5}
        # Patch the lazy import by inserting a sentinel into sys.modules.
        with mock.patch.dict("sys.modules", {"vec_index": None}):
            # Importing None raises ImportError; the helper catches + returns unchanged.
            result = recall._drift_check_vec_hits(Path("/tmp"), vec_results)
        self.assertEqual(result, vec_results)


# -----------------------------------------------------------------------------
# V4 #30 plan #22 task 2 — repo_registry primitive
# -----------------------------------------------------------------------------

# importlib-load repo_registry from scripts/ dir without touching test PYTHONPATH
import importlib.util as _ilu

_REPO_REGISTRY_PATH = _HERE / "repo_registry.py"
_spec_rr = _ilu.spec_from_file_location("repo_registry", _REPO_REGISTRY_PATH)
assert _spec_rr is not None and _spec_rr.loader is not None
repo_registry = _ilu.module_from_spec(_spec_rr)
sys.modules["repo_registry"] = repo_registry
_spec_rr.loader.exec_module(repo_registry)


class TestRepoRegistry(unittest.TestCase):
    """V4 #30 task 2: vault-backed registry primitives."""

    def test_read_empty_returns_default_schema(self) -> None:
        """First-write semantics: missing registry file returns {version:1, repos:[]}."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            data = repo_registry.read_registry(vault)
            self.assertEqual(data, {"version": 1, "repos": []})
            # Read does NOT create the file (write_registry is responsible).
            self.assertFalse((vault / "_meta" / "repos.json").exists())

    def test_register_creates_file_and_entry(self) -> None:
        """First register_repo populates <vault>/_meta/repos.json."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            repo_registry.register_repo(
                vault, "agentm", "/tmp/fixture-agentm",
                wiki_path="/tmp/fixture-agentm/wiki",
                harness_state_mode="vault",
            )
            path = vault / "_meta" / "repos.json"
            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            self.assertEqual(len(data["repos"]), 1)
            entry = data["repos"][0]
            self.assertEqual(entry["slug"], "agentm")
            self.assertEqual(entry["root_path"], "/tmp/fixture-agentm")
            self.assertEqual(entry["wiki_path"], "/tmp/fixture-agentm/wiki")
            self.assertEqual(entry["harness_state_mode"], "vault")

    def test_register_upserts_existing_slug(self) -> None:
        """Re-registering the same slug updates the entry in-place (not appended)."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            repo_registry.register_repo(vault, "agentm", "/old/path")
            repo_registry.register_repo(
                vault, "agentm", "/new/path", wiki_path="/wiki",
            )
            repos = repo_registry.list_repos(vault)
            self.assertEqual(len(repos), 1)
            self.assertEqual(repos[0]["root_path"], "/new/path")
            self.assertEqual(repos[0]["wiki_path"], "/wiki")

    def test_unregister_removes_existing(self) -> None:
        """unregister_repo removes the matching slug; idempotent on absent."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            repo_registry.register_repo(vault, "agentm", "/a")
            repo_registry.register_repo(vault, "sherwood", "/s")
            removed = repo_registry.unregister_repo(vault, "agentm")
            self.assertTrue(removed)
            repos = repo_registry.list_repos(vault)
            self.assertEqual(len(repos), 1)
            self.assertEqual(repos[0]["slug"], "sherwood")
            # Idempotent: unregister of already-absent slug returns False, no-op.
            removed_again = repo_registry.unregister_repo(vault, "agentm")
            self.assertFalse(removed_again)

    def test_list_preserves_insertion_order(self) -> None:
        """list_repos returns entries in the order they were first registered."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            for slug in ("agentm", "sherwood", "dev-setup"):
                repo_registry.register_repo(vault, slug, f"/path/{slug}")
            repos = repo_registry.list_repos(vault)
            self.assertEqual(
                [r["slug"] for r in repos],
                ["agentm", "sherwood", "dev-setup"],
            )

    def test_concurrent_modification_raises(self) -> None:
        """write_registry with expected_mtime mismatch raises ConcurrentModificationError."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            repo_registry.register_repo(vault, "agentm", "/a")
            path = repo_registry.registry_path(vault)
            stale_mtime = path.stat().st_mtime
            # Simulate another writer by mutating the file (which bumps mtime).
            import time as _time
            _time.sleep(0.01)  # ensure mtime tick
            path.write_text(path.read_text() + " ", encoding="utf-8")
            data = repo_registry.read_registry(vault)
            with self.assertRaises(hm.ConcurrentModificationError):
                repo_registry.write_registry(vault, data, expected_mtime=stale_mtime)

    def test_atomic_write_no_tmp_remnant(self) -> None:
        """After successful write, no <path>.tmp lingers."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            repo_registry.register_repo(vault, "agentm", "/a")
            meta_dir = vault / "_meta"
            tmp_files = list(meta_dir.glob("*.tmp"))
            self.assertEqual(tmp_files, [])

    def test_vault_missing_raises(self) -> None:
        """Operating against a non-existent vault directory raises FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-such-vault"
            with self.assertRaises(FileNotFoundError):
                repo_registry.read_registry(missing)
            with self.assertRaises(FileNotFoundError):
                repo_registry.register_repo(missing, "agentm", "/a")


class TestRepoRegistryCLI(unittest.TestCase):
    """V4 #30 task 2: CLI subcommands (list / register / unregister)."""

    def _run(self, *argv: str, env: Optional[dict] = None) -> subprocess.CompletedProcess:
        e = os.environ.copy()
        if env is not None:
            e.update(env)
            # Allow caller to delete by passing empty string sentinel.
            for k, v in list(env.items()):
                if v == "":
                    e.pop(k, None)
        return subprocess.run(
            [sys.executable, str(_REPO_REGISTRY_PATH), *argv],
            capture_output=True, text=True, env=e,
        )

    def test_list_skipped_when_no_vault(self) -> None:
        """MEMORY_VAULT_PATH unset → CLI exits 1 with skip JSON."""
        res = self._run("list", env={"MEMORY_VAULT_PATH": ""})
        self.assertEqual(res.returncode, 1)
        out = json.loads(res.stdout)
        self.assertTrue(out["skipped"])
        self.assertIn("MEMORY_VAULT_PATH", out["reason"])

    def test_register_then_list_via_cli(self) -> None:
        """Register two repos via CLI; list returns both with correct fields."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            env = {"MEMORY_VAULT_PATH": str(vault)}

            reg1 = self._run(
                "register", "agentm",
                "--root", "/tmp/fixture-agentm",
                "--wiki", "/tmp/fixture-agentm/wiki",
                "--state-mode", "vault",
                env=env,
            )
            self.assertEqual(reg1.returncode, 0, reg1.stderr)
            self.assertEqual(reg1.stdout.strip(), "agentm")

            reg2 = self._run(
                "register", "sherwood",
                "--root", "/tmp/fixture-sherwood",
                env=env,
            )
            self.assertEqual(reg2.returncode, 0, reg2.stderr)

            ls = self._run("list", env=env)
            self.assertEqual(ls.returncode, 0, ls.stderr)
            data = json.loads(ls.stdout)
            self.assertEqual(len(data["repos"]), 2)
            slugs = [r["slug"] for r in data["repos"]]
            self.assertEqual(slugs, ["agentm", "sherwood"])
            # First repo carries all fields; second carries only required ones.
            self.assertEqual(data["repos"][0]["wiki_path"], "/tmp/fixture-agentm/wiki")
            self.assertEqual(data["repos"][0]["harness_state_mode"], "vault")
            self.assertNotIn("wiki_path", data["repos"][1])

    def test_unregister_via_cli(self) -> None:
        """Unregister an existing repo via CLI; re-running is a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            env = {"MEMORY_VAULT_PATH": str(vault)}

            self._run("register", "agentm", "--root", "/a", env=env)
            res = self._run("unregister", "agentm", env=env)
            self.assertEqual(res.returncode, 0)
            self.assertEqual(res.stdout.strip(), "removed")

            # Idempotent: second unregister is a no-op.
            res2 = self._run("unregister", "agentm", env=env)
            self.assertEqual(res2.returncode, 0)
            self.assertEqual(res2.stdout.strip(), "noop")

            ls = self._run("list", env=env)
            data = json.loads(ls.stdout)
            self.assertEqual(data["repos"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
