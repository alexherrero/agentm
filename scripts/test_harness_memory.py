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


if __name__ == "__main__":
    unittest.main(verbosity=2)
