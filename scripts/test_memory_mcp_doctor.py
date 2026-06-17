"""Unit tests for scripts/memory_mcp_doctor.py.

All tests run without a live daemon — HTTP calls are stubbed via
unittest.mock so the battery runs offline inside check-all.sh.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── path setup ────────────────────────────────────────────────────────────────

_SCRIPTS = Path(__file__).parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import memory_mcp_doctor as _doc  # noqa: E402


# ── liveness ──────────────────────────────────────────────────────────────────

class TestLiveness(unittest.TestCase):

    def test_liveness_false_when_down(self):
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            passed, msg = _doc.check_liveness()
        self.assertFalse(passed)
        self.assertIn("launchctl bootstrap", msg)

    def test_liveness_true_when_up(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b'{"status": "ok"}'
        with patch("urllib.request.urlopen", return_value=mock_resp):
            passed, msg = _doc.check_liveness()
        self.assertTrue(passed)

    def test_liveness_false_on_unexpected_body(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b'{"status": "error"}'
        with patch("urllib.request.urlopen", return_value=mock_resp):
            passed, msg = _doc.check_liveness()
        self.assertFalse(passed)
        self.assertIn("unexpected health body", msg)


# ── token_env ─────────────────────────────────────────────────────────────────

class TestTokenEnv(unittest.TestCase):

    def test_token_env_unset(self):
        passed, msg = _doc.check_token_env(env={})
        self.assertFalse(passed)
        self.assertIn("AGENTM_MCP_TOKEN", msg)
        self.assertIn("launchctl setenv", msg)

    def test_token_env_set(self):
        passed, msg = _doc.check_token_env(env={"AGENTM_MCP_TOKEN": "mytoken"})
        self.assertTrue(passed)

    def test_token_env_whitespace_only(self):
        passed, msg = _doc.check_token_env(env={"AGENTM_MCP_TOKEN": "   "})
        self.assertFalse(passed)


# ── origin_guard ──────────────────────────────────────────────────────────────

class TestOriginGuard(unittest.TestCase):

    def _stub_liveness_up(self):
        return patch.object(_doc, "check_liveness", return_value=(True, "up"))

    def _stub_liveness_down(self):
        return patch.object(_doc, "check_liveness", return_value=(False, "down"))

    def test_origin_guard_skips_when_daemon_down(self):
        with self._stub_liveness_down():
            passed, msg = _doc.check_origin_guard()
        self.assertIsNone(passed)
        self.assertIn("skipped", msg)

    def test_origin_guard_passes_on_403(self):
        err = urllib.error.HTTPError(
            url="http://127.0.0.1:7821/mcp",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )
        with self._stub_liveness_up(), \
             patch("urllib.request.urlopen", side_effect=err):
            passed, msg = _doc.check_origin_guard()
        self.assertTrue(passed)
        self.assertIn("403", msg)

    def test_origin_guard_fails_on_non_403(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        with self._stub_liveness_up(), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            passed, msg = _doc.check_origin_guard()
        self.assertFalse(passed)
        self.assertIn("Origin-validation NOT active", msg)


# ── index_root_safe ───────────────────────────────────────────────────────────

class TestIndexRootSafe(unittest.TestCase):

    def test_index_root_safe_passes_when_local(self):
        fake_root = Path("/tmp/agentm/locks")
        with patch("memory_mcp_doctor._get_vault_lock_root", return_value=fake_root):
            passed, msg = _doc.check_index_root_safe()
        self.assertTrue(passed)

    def test_index_root_safe_fails_when_inside_vault(self):
        fake_root = Path(
            "/tmp/CloudStorage/GoogleDrive-foo/Obsidian/AgentMemory/locks"
        )
        with patch("memory_mcp_doctor._get_vault_lock_root", return_value=fake_root):
            passed, msg = _doc.check_index_root_safe()
        self.assertFalse(passed)
        self.assertIn("synced/cloud path", msg)
        self.assertIn("~/.cache/agentm/locks", msg)


# ── run_checks integration ────────────────────────────────────────────────────

class TestRunChecks(unittest.TestCase):

    def test_run_checks_default_returns_liveness_and_token(self):
        with patch.object(_doc, "check_liveness", return_value=(False, "down")), \
             patch.object(_doc, "check_token_env", return_value=(True, "set")):
            results = _doc.run_checks()
        names = [r[0] for r in results]
        self.assertIn("liveness", names)
        self.assertIn("token_env", names)
        self.assertNotIn("origin_guard", names)

    def test_run_checks_all_returns_four(self):
        with patch.object(_doc, "check_liveness", return_value=(True, "up")), \
             patch.object(_doc, "check_token_env", return_value=(True, "set")), \
             patch.object(_doc, "check_origin_guard", return_value=(None, "skipped")), \
             patch.object(_doc, "check_index_root_safe", return_value=(True, "ok")):
            results = _doc.run_checks(checks=_doc._ALL_CHECKS)
        self.assertEqual(len(results), 4)


if __name__ == "__main__":
    unittest.main()
