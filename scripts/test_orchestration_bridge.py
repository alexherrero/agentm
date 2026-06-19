#!/usr/bin/env python3
"""Contract tests for the V5-5 orchestration bridge (harness_memory.phase_dispatch).

Pins the bridge contract — the importable ``phase_dispatch()`` function is the
stated API, not just the ``phase-dispatch`` CLI verb:

  - **Non-blocking.** Always returns 0; a phase is never wedged.
  - **Graceful-skip.** Returns 0 when vault absent or toolkit not installed.
  - **Kernel-single-writer.** Bridge invokes ``orchestration_phase.py`` (the
    kernel script writes state); the bridge never touches the state file directly.
  - **Write-capable sibling.** Can trigger writes through the kernel, unlike
    V5-4's read-only ``process_seam``.
  - ``ValueError`` on unknown phase values (the CLI argparse ``choices=`` layer
    mirrors ``_BRIDGE_PHASES`` so this guard only fires on direct Python calls).

Run: python3 scripts/test_orchestration_bridge.py
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402

# Sandbox AGENTM_INSTALL_PREFIX module-wide so vault_path()'s config-file
# fallback never reads the operator's real ~/.claude/.agentm-config.json.
_TEST_INSTALL_PREFIX = tempfile.mkdtemp(prefix="agentm-test-bridge-prefix-")


def setUpModule() -> None:  # noqa: N802
    os.environ["AGENTM_INSTALL_PREFIX"] = _TEST_INSTALL_PREFIX


def tearDownModule() -> None:  # noqa: N802
    os.environ.pop("AGENTM_INSTALL_PREFIX", None)
    shutil.rmtree(_TEST_INSTALL_PREFIX, ignore_errors=True)


class _ClearEnv:
    """Context manager: set env vars, also explicitly unset listed keys."""

    def __init__(self, set_vars: dict | None = None, unset_keys: list | None = None):
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


def _make_vault(root: Path) -> Path:
    vault = root / "vault"
    vault.mkdir(parents=True)
    return vault


def _make_toolkit(root: Path, *, with_dispatcher: bool = True) -> Path:
    tk = root / "toolkit"
    tk.mkdir(parents=True)
    if with_dispatcher:
        (tk / "orchestration_phase.py").write_text("# stub\n", encoding="utf-8")
    return tk


# -----------------------------------------------------------------------------
# Importability
# -----------------------------------------------------------------------------

class TestBridgeImportable(unittest.TestCase):
    """The module API is the contract — must be importable, not just CLI-callable."""

    def test_phase_dispatch_importable(self) -> None:
        self.assertTrue(callable(hm.phase_dispatch))

    def test_bridge_phases_constant_accessible(self) -> None:
        self.assertIn("post-work", hm._BRIDGE_PHASES)
        self.assertIn("post-release", hm._BRIDGE_PHASES)
        self.assertEqual(len(hm._BRIDGE_PHASES), 2)

    def test_bridge_phases_constant_matches_argparse_choices(self) -> None:
        parser = hm._build_parser()
        # Find the phase-dispatch subparser's 'phase' argument choices.
        pd_actions = None
        for action in parser._subparsers._group_actions:
            for name, sub in action.choices.items():
                if name == "phase-dispatch":
                    pd_actions = sub._actions
        self.assertIsNotNone(pd_actions, "phase-dispatch subparser not found")
        choices = None
        for action in pd_actions:
            if action.dest == "phase":
                choices = set(action.option_strings or [action.dest])
                # choices attribute is on the action directly
                choices = set(action.choices) if hasattr(action, "choices") and action.choices else None
        self.assertEqual(choices, set(hm._BRIDGE_PHASES))


# -----------------------------------------------------------------------------
# Graceful-skip contract
# -----------------------------------------------------------------------------

class TestBridgeGracefulSkip(unittest.TestCase):
    """Non-blocking + graceful-skip — always returns 0, never raises."""

    def test_returns_0_when_vault_absent(self) -> None:
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            rc = hm.phase_dispatch(phase="post-work")
        self.assertEqual(rc, 0)

    def test_returns_0_when_vault_dir_missing(self) -> None:
        with _ClearEnv(set_vars={"MEMORY_VAULT_PATH": "/nonexistent-vault-path-for-bridge-test"}):
            rc = hm.phase_dispatch(phase="post-work")
        self.assertEqual(rc, 0)

    def test_returns_0_when_toolkit_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            with _ClearEnv(
                set_vars={
                    "MEMORY_VAULT_PATH": str(vault),
                    "HARNESS_MEMORY_TOOLKIT_PATH": str(Path(tmp) / "missing-toolkit"),
                }
            ):
                rc = hm.phase_dispatch(phase="post-work")
        self.assertEqual(rc, 0)

    def test_returns_0_when_dispatcher_script_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(Path(tmp))
            tk = _make_toolkit(Path(tmp), with_dispatcher=False)
            with _ClearEnv(
                set_vars={
                    "MEMORY_VAULT_PATH": str(vault),
                    "HARNESS_MEMORY_TOOLKIT_PATH": str(tk),
                }
            ):
                rc = hm.phase_dispatch(phase="post-work")
        self.assertEqual(rc, 0)


# -----------------------------------------------------------------------------
# Subprocess invocation contract
# -----------------------------------------------------------------------------

class TestBridgeSubprocessInvocation(unittest.TestCase):
    """Bridge invokes orchestration_phase.py with the correct argv."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vault = _make_vault(self.root)
        self.toolkit = _make_toolkit(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, phase: str, **kwargs) -> tuple[int, list]:
        captured = []

        def fake_run(cmd, **_):
            captured.append(cmd)
            r = mock.MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        env = {
            "MEMORY_VAULT_PATH": str(self.vault),
            "HARNESS_MEMORY_TOOLKIT_PATH": str(self.toolkit),
        }
        with _ClearEnv(set_vars=env):
            with mock.patch("subprocess.run", side_effect=fake_run):
                rc = hm.phase_dispatch(phase=phase, **kwargs)
        return rc, captured

    def test_invokes_orchestration_phase_script(self) -> None:
        rc, calls = self._run("post-work")
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        cmd = calls[0]
        self.assertTrue(any("orchestration_phase.py" in str(a) for a in cmd))

    def test_phase_arg_at_end_of_argv(self) -> None:
        rc, calls = self._run("post-work")
        self.assertEqual(calls[0][-1], "post-work")

    def test_vault_path_passed(self) -> None:
        rc, calls = self._run("post-release")
        cmd = calls[0]
        self.assertIn("--vault-path", cmd)
        idx = cmd.index("--vault-path")
        self.assertEqual(cmd[idx + 1], str(self.vault))

    def test_project_root_passed_when_provided(self) -> None:
        rc, calls = self._run("post-work", project_root="/my/project")
        cmd = calls[0]
        self.assertIn("--project-root", cmd)
        idx = cmd.index("--project-root")
        self.assertEqual(cmd[idx + 1], "/my/project")

    def test_project_root_defaults_to_dot_when_none(self) -> None:
        rc, calls = self._run("post-work", project_root=None)
        cmd = calls[0]
        idx = cmd.index("--project-root")
        self.assertEqual(cmd[idx + 1], ".")

    def test_dry_run_flag_propagates(self) -> None:
        rc, calls = self._run("post-work", dry_run=True)
        self.assertIn("--dry-run", calls[0])

    def test_dry_run_false_omits_flag(self) -> None:
        rc, calls = self._run("post-work", dry_run=False)
        self.assertNotIn("--dry-run", calls[0])

    def test_returns_0_on_subprocess_nonzero(self) -> None:
        def failing_run(cmd, **_):
            r = mock.MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "something broke\n"
            return r

        env = {
            "MEMORY_VAULT_PATH": str(self.vault),
            "HARNESS_MEMORY_TOOLKIT_PATH": str(self.toolkit),
        }
        with _ClearEnv(set_vars=env):
            with mock.patch("subprocess.run", side_effect=failing_run):
                rc = hm.phase_dispatch(phase="post-work")
        self.assertEqual(rc, 0)

    def test_returns_0_on_oserror(self) -> None:
        def oserror_run(cmd, **_):
            raise OSError("exec failed")

        env = {
            "MEMORY_VAULT_PATH": str(self.vault),
            "HARNESS_MEMORY_TOOLKIT_PATH": str(self.toolkit),
        }
        with _ClearEnv(set_vars=env):
            with mock.patch("subprocess.run", side_effect=oserror_run):
                rc = hm.phase_dispatch(phase="post-work")
        self.assertEqual(rc, 0)

    def test_returns_0_on_timeout(self) -> None:
        def timeout_run(cmd, **_):
            raise subprocess.TimeoutExpired(cmd, 180)

        env = {
            "MEMORY_VAULT_PATH": str(self.vault),
            "HARNESS_MEMORY_TOOLKIT_PATH": str(self.toolkit),
        }
        with _ClearEnv(set_vars=env):
            with mock.patch("subprocess.run", side_effect=timeout_run):
                rc = hm.phase_dispatch(phase="post-work")
        self.assertEqual(rc, 0)


# -----------------------------------------------------------------------------
# Phase validation
# -----------------------------------------------------------------------------

class TestBridgePhaseValidation(unittest.TestCase):
    """ValueError on unknown phase — the argparse layer mirrors _BRIDGE_PHASES."""

    def test_unknown_phase_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            hm.phase_dispatch(phase="not-a-real-phase")
        self.assertIn("not-a-real-phase", str(ctx.exception))
        self.assertIn("post-work", str(ctx.exception))

    def test_post_work_is_valid(self) -> None:
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            rc = hm.phase_dispatch(phase="post-work")
        self.assertEqual(rc, 0)

    def test_post_release_is_valid(self) -> None:
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            rc = hm.phase_dispatch(phase="post-release")
        self.assertEqual(rc, 0)


# -----------------------------------------------------------------------------
# Default argument contract
# -----------------------------------------------------------------------------

class TestBridgeDefaults(unittest.TestCase):
    """Keyword defaults mean the function is callable with phase= alone."""

    def test_callable_with_phase_only(self) -> None:
        with _ClearEnv(unset_keys=["MEMORY_VAULT_PATH"]):
            rc = hm.phase_dispatch(phase="post-work")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
