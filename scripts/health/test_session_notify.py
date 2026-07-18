#!/usr/bin/env python3
"""Tests for scripts/health/session_notify.py — the opt-in daily on-device
notification (`wiki/designs/agentm-autonomy.md` Delivery → on-device
notification channel)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import session_notify as sn  # noqa: E402

_NOW = datetime(2026, 7, 17, 18, 0, 0, tzinfo=timezone.utc)


def _write_digest(vault: Path, date: str, cadence: str, *, spend=None, events=None):
    briefs = vault / "_briefs"
    briefs.mkdir(parents=True, exist_ok=True)
    slug = f"{date}-digest-{cadence}"
    lines = ["---", "kind: brief", "status: active", f"slug: {slug}",
             f"digest_cadence: {cadence}", "---", "", f"# Observability digest — {cadence}", ""]
    if spend is not None:
        lines.append(f"- Spend: ${spend:.4f}")
    if events is not None:
        lines.append(f"- Events: {events}")
    (briefs / f"{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


class NotifyEnabledTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.prefix = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_absent_config_is_false(self):
        self.assertFalse(sn.notify_enabled(self.prefix))

    def test_config_without_key_is_false(self):
        (self.prefix / ".agentm-config.json").write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
        self.assertFalse(sn.notify_enabled(self.prefix))

    def test_config_true_is_true(self):
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"plugins.autonomy.notify_enabled": True}), encoding="utf-8")
        self.assertTrue(sn.notify_enabled(self.prefix))

    def test_config_false_is_false(self):
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"plugins.autonomy.notify_enabled": False}), encoding="utf-8")
        self.assertFalse(sn.notify_enabled(self.prefix))

    def test_malformed_json_is_false_not_raise(self):
        (self.prefix / ".agentm-config.json").write_text("{not json", encoding="utf-8")
        self.assertFalse(sn.notify_enabled(self.prefix))

    def test_non_dict_json_is_false(self):
        (self.prefix / ".agentm-config.json").write_text("[1, 2, 3]", encoding="utf-8")
        self.assertFalse(sn.notify_enabled(self.prefix))


class OsascriptFiringTests(unittest.TestCase):
    def test_fires_when_osascript_present(self):
        with mock.patch("shutil.which", return_value="/usr/bin/osascript"), \
             mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0)
            fired = sn._fire_osascript("test body")
            self.assertTrue(fired)
            run_mock.assert_called_once()
            argv = run_mock.call_args[0][0]
            self.assertEqual(argv[0], "/usr/bin/osascript")
            self.assertEqual(argv[1], "-e")
            self.assertIn("test body", argv[2])

    def test_skips_when_osascript_absent(self):
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("subprocess.run") as run_mock:
            fired = sn._fire_osascript("test body")
            self.assertFalse(fired)
            run_mock.assert_not_called()

    def test_false_when_osascript_fails(self):
        with mock.patch("shutil.which", return_value="/usr/bin/osascript"), \
             mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=1)
            self.assertFalse(sn._fire_osascript("test body"))

    def test_false_on_subprocess_exception(self):
        with mock.patch("shutil.which", return_value="/usr/bin/osascript"), \
             mock.patch("subprocess.run", side_effect=OSError("boom")):
            self.assertFalse(sn._fire_osascript("test body"))

    def test_quoting_escapes_double_quotes(self):
        quoted = sn._applescript_quote('say "hi" \\ done')
        self.assertEqual(quoted, '"say \\"hi\\" \\\\ done"')


class NotifyBodyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        # Isolate from the operator's real ~/.cache/agentm/telemetry/ —
        # mirrors test_session_brief.py's own isolation pattern.
        self.park = Path(self._tmp.name) / "park"
        self.hist = Path(self._tmp.name) / "digest-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_none_when_no_digest_ever_ran(self):
        self.assertIsNone(sn.notify_body(self.vault, now=_NOW, park_dir=self.park, history_path=self.hist))

    def test_strips_agentm_prefix(self):
        _write_digest(self.vault, "20260717", "daily", spend=12.5, events=3)
        body = sn.notify_body(self.vault, now=_NOW, park_dir=self.park, history_path=self.hist)
        self.assertIsNotNone(body)
        self.assertFalse(body.startswith("[agentm]"))
        self.assertIn("$12.50", body)


class RunEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.prefix = self.root / "prefix"
        self.vault = self.root / "vault"
        self.prefix.mkdir()
        self.vault.mkdir()
        self.state_path = self.root / "notify-state.json"
        # Isolate from the operator's real ~/.cache/agentm/telemetry/.
        self.park = self.root / "park"
        self.hist = self.root / "digest-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _enable(self):
        (self.prefix / ".agentm-config.json").write_text(
            json.dumps({"plugins.autonomy.notify_enabled": True}), encoding="utf-8")

    def _run(self, now):
        return sn.run(
            install_prefix=self.prefix, vault=self.vault, now=now,
            state_path=self.state_path, park_dir=self.park, history_path=self.hist,
        )

    def test_disabled_by_default_never_fires(self):
        _write_digest(self.vault, "20260717", "daily", spend=1.0, events=1)
        with mock.patch("shutil.which", return_value="/usr/bin/osascript"), \
             mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0)
            fired = self._run(_NOW)
        self.assertFalse(fired)
        run_mock.assert_not_called()

    def test_enabled_with_osascript_fires(self):
        self._enable()
        _write_digest(self.vault, "20260717", "daily", spend=1.0, events=1)
        with mock.patch("shutil.which", return_value="/usr/bin/osascript"), \
             mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0)
            fired = self._run(_NOW)
        self.assertTrue(fired)
        run_mock.assert_called_once()

    def test_enabled_without_osascript_skips(self):
        self._enable()
        _write_digest(self.vault, "20260717", "daily", spend=1.0, events=1)
        with mock.patch("shutil.which", return_value=None):
            fired = self._run(_NOW)
        self.assertFalse(fired)

    def test_no_digest_ever_run_skips(self):
        self._enable()
        with mock.patch("shutil.which", return_value="/usr/bin/osascript"), \
             mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0)
            fired = self._run(_NOW)
        self.assertFalse(fired)
        run_mock.assert_not_called()

    def test_same_day_rerun_does_not_refire(self):
        self._enable()
        _write_digest(self.vault, "20260717", "daily", spend=1.0, events=1)
        with mock.patch("shutil.which", return_value="/usr/bin/osascript"), \
             mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0)
            fired1 = self._run(_NOW)
            fired2 = self._run(_NOW)
        self.assertTrue(fired1)
        self.assertFalse(fired2)
        self.assertEqual(run_mock.call_count, 1)

    def test_new_day_with_new_digest_refires(self):
        self._enable()
        _write_digest(self.vault, "20260717", "daily", spend=1.0, events=1)
        tomorrow = datetime(2026, 7, 18, 18, 0, 0, tzinfo=timezone.utc)
        with mock.patch("shutil.which", return_value="/usr/bin/osascript"), \
             mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0)
            fired1 = self._run(_NOW)
            _write_digest(self.vault, "20260718", "daily", spend=2.0, events=2)
            fired2 = self._run(tomorrow)
        self.assertTrue(fired1)
        self.assertTrue(fired2)
        self.assertEqual(run_mock.call_count, 2)

    def test_never_raises_on_internal_error(self):
        self._enable()
        with mock.patch.object(sn, "notify_body", side_effect=RuntimeError("boom")):
            fired = self._run(_NOW)
        self.assertFalse(fired)


if __name__ == "__main__":
    unittest.main()
