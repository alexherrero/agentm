#!/usr/bin/env python3
"""Tests for scripts/health/session_email.py — the opt-in daily digest email
(`wiki/designs/agentm-autonomy.md` Delivery → daily email channel)."""
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

import session_email as se  # noqa: E402

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


class EmailConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.prefix = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, **fields):
        (self.prefix / ".agentm-config.json").write_text(json.dumps(fields), encoding="utf-8")

    def test_absent_config_is_none(self):
        self.assertIsNone(se.email_config(self.prefix))

    def test_only_email_to_is_none(self):
        self._write(**{"plugins.autonomy.email_to": "me@example.com"})
        self.assertIsNone(se.email_config(self.prefix))

    def test_only_smtp_url_is_none(self):
        self._write(**{"plugins.autonomy.email_smtp_url": "smtp://localhost:25"})
        self.assertIsNone(se.email_config(self.prefix))

    def test_both_present_returns_tuple(self):
        self._write(**{
            "plugins.autonomy.email_to": "me@example.com",
            "plugins.autonomy.email_smtp_url": "smtp://relay@localhost:587",
        })
        self.assertEqual(
            se.email_config(self.prefix), ("me@example.com", "smtp://relay@localhost:587", None))

    def test_from_absent_by_default(self):
        self._write(**{
            "plugins.autonomy.email_to": "me@example.com",
            "plugins.autonomy.email_smtp_url": "smtp://relay@localhost:587",
        })
        _, _, from_addr = se.email_config(self.prefix)
        self.assertIsNone(from_addr)

    def test_from_present_returned(self):
        self._write(**{
            "plugins.autonomy.email_to": "me@example.com",
            "plugins.autonomy.email_smtp_url": "smtp://relay@localhost:587",
            "plugins.autonomy.email_from": "digest@example.com",
        })
        _, _, from_addr = se.email_config(self.prefix)
        self.assertEqual(from_addr, "digest@example.com")

    def test_empty_string_email_to_is_none(self):
        self._write(**{
            "plugins.autonomy.email_to": "   ",
            "plugins.autonomy.email_smtp_url": "smtp://localhost:25",
        })
        self.assertIsNone(se.email_config(self.prefix))

    def test_malformed_json_is_none_not_raise(self):
        (self.prefix / ".agentm-config.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(se.email_config(self.prefix))


class SendSmtpTests(unittest.TestCase):
    def test_sends_via_smtp(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            ok = se._send_smtp("smtp://relay@localhost:587", "me@example.com", "subj", "body")
        self.assertTrue(ok)
        smtp_cls.assert_called_once_with("localhost", 587, timeout=10)
        server.send_message.assert_called_once()

    def test_default_port_25_when_unspecified(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            se._send_smtp("smtp://localhost", "me@example.com", "subj", "body")
        smtp_cls.assert_called_once_with("localhost", 25, timeout=10)

    def test_false_on_smtp_exception(self):
        import smtplib
        with mock.patch("smtplib.SMTP", side_effect=smtplib.SMTPException("boom")):
            ok = se._send_smtp("smtp://localhost:25", "me@example.com", "subj", "body")
        self.assertFalse(ok)

    def test_false_on_connection_error(self):
        with mock.patch("smtplib.SMTP", side_effect=OSError("connection refused")):
            ok = se._send_smtp("smtp://localhost:25", "me@example.com", "subj", "body")
        self.assertFalse(ok)

    def test_false_when_host_unparseable(self):
        ok = se._send_smtp("not-a-url", "me@example.com", "subj", "body")
        self.assertFalse(ok)

    def test_starttls_attempted_on_non_465_port(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            se._send_smtp("smtp://relay@localhost:587", "me@example.com", "subj", "body")
        server.starttls.assert_called_once()

    def test_starttls_unsupported_is_swallowed(self):
        import smtplib
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            server.starttls.side_effect = smtplib.SMTPNotSupportedError("no TLS")
            ok = se._send_smtp("smtp://relay@localhost:587", "me@example.com", "subj", "body")
        self.assertTrue(ok)
        server.send_message.assert_called_once()

    def test_login_refused_when_starttls_unsupported_and_password_present(self):
        # Regression: STARTTLS-stripping downgrade must never fall through to
        # a plaintext login() — credentials refuse to go out unencrypted.
        import smtplib
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            server.starttls.side_effect = smtplib.SMTPNotSupportedError("no TLS")
            ok = se._send_smtp(
                "smtp://resend:re_apikey123@example.com:587", "me@example.com", "subj", "body")
        self.assertFalse(ok)
        server.login.assert_not_called()
        server.send_message.assert_not_called()

    def test_login_refused_when_starttls_raises_other_smtp_error(self):
        # A STARTTLS attempt that fails for a reason OTHER than "unsupported"
        # (e.g. a stripped/corrupted response) must not be swallowed into a
        # plaintext-login path either — any non-success STARTTLS outcome with
        # a password present is refuse-to-send, not just the NotSupported case.
        import smtplib
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            server.starttls.side_effect = smtplib.SMTPException("connection reset")
            ok = se._send_smtp(
                "smtp://resend:re_apikey123@example.com:587", "me@example.com", "subj", "body")
        self.assertFalse(ok)
        server.login.assert_not_called()

    def test_ssl_used_on_port_465_no_starttls(self):
        with mock.patch("smtplib.SMTP_SSL") as ssl_cls, mock.patch("smtplib.SMTP") as plain_cls:
            server = ssl_cls.return_value.__enter__.return_value
            ok = se._send_smtp("smtp://relay@localhost:465", "me@example.com", "subj", "body")
        self.assertTrue(ok)
        ssl_cls.assert_called_once_with("localhost", 465, timeout=10)
        plain_cls.assert_not_called()
        server.starttls.assert_not_called()

    def test_login_called_when_password_present(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            se._send_smtp("smtp://resend:re_apikey123@example.com:587", "me@example.com", "subj", "body")
        server.login.assert_called_once_with("resend", "re_apikey123")

    def test_no_login_when_password_absent(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            se._send_smtp("smtp://localhost:587", "me@example.com", "subj", "body")
        server.login.assert_not_called()

    def test_from_addr_used_as_sender_when_given(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            se._send_smtp(
                "smtp://resend:re_apikey123@example.com:587", "me@example.com", "subj", "body",
                from_addr="digest@example.com",
            )
        sent_msg = server.send_message.call_args[0][0]
        self.assertEqual(sent_msg["From"], "digest@example.com")

    def test_from_addr_falls_back_to_to_addr_when_absent(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            se._send_smtp("smtp://localhost:587", "me@example.com", "subj", "body")
        sent_msg = server.send_message.call_args[0][0]
        self.assertEqual(sent_msg["From"], "me@example.com")

    def test_false_on_auth_failure(self):
        import smtplib
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
            ok = se._send_smtp("smtp://resend:wrong@example.com:587", "me@example.com", "subj", "body")
        self.assertFalse(ok)


class EmailBodyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_none_when_no_digest_ever_ran(self):
        self.assertIsNone(se.email_body(self.vault))

    def test_subject_and_body_from_latest_digest(self):
        _write_digest(self.vault, "20260717", "daily", spend=12.5, events=3)
        built = se.email_body(self.vault)
        self.assertIsNotNone(built)
        subject, body = built
        self.assertIn("$12.50", subject)
        self.assertIn("Observability digest", body)


class RunEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.prefix = self.root / "prefix"
        self.vault = self.root / "vault"
        self.prefix.mkdir()
        self.vault.mkdir()
        self.state_path = self.root / "email-state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _configure(self):
        (self.prefix / ".agentm-config.json").write_text(json.dumps({
            "plugins.autonomy.email_to": "me@example.com",
            "plugins.autonomy.email_smtp_url": "smtp://relay@localhost:587",
        }), encoding="utf-8")

    def _run(self, now):
        return se.run(install_prefix=self.prefix, vault=self.vault, now=now, state_path=self.state_path)

    def test_unconfigured_never_sends(self):
        _write_digest(self.vault, "20260717", "daily", spend=1.0, events=1)
        with mock.patch("smtplib.SMTP") as smtp_cls:
            sent = self._run(_NOW)
        self.assertFalse(sent)
        smtp_cls.assert_not_called()

    def test_configured_sends(self):
        self._configure()
        _write_digest(self.vault, "20260717", "daily", spend=1.0, events=1)
        with mock.patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value = mock.Mock()
            sent = self._run(_NOW)
        self.assertTrue(sent)
        smtp_cls.assert_called_once()

    def test_no_digest_ever_run_skips(self):
        self._configure()
        with mock.patch("smtplib.SMTP") as smtp_cls:
            sent = self._run(_NOW)
        self.assertFalse(sent)
        smtp_cls.assert_not_called()

    def test_same_day_rerun_does_not_resend(self):
        self._configure()
        _write_digest(self.vault, "20260717", "daily", spend=1.0, events=1)
        with mock.patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value = mock.Mock()
            sent1 = self._run(_NOW)
            sent2 = self._run(_NOW)
        self.assertTrue(sent1)
        self.assertFalse(sent2)
        self.assertEqual(smtp_cls.call_count, 1)

    def test_new_day_resends(self):
        self._configure()
        _write_digest(self.vault, "20260717", "daily", spend=1.0, events=1)
        tomorrow = datetime(2026, 7, 18, 18, 0, 0, tzinfo=timezone.utc)
        with mock.patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value = mock.Mock()
            sent1 = self._run(_NOW)
            sent2 = self._run(tomorrow)
        self.assertTrue(sent1)
        self.assertTrue(sent2)
        self.assertEqual(smtp_cls.call_count, 2)

    def test_smtp_failure_does_not_record_sent(self):
        self._configure()
        _write_digest(self.vault, "20260717", "daily", spend=1.0, events=1)
        with mock.patch("smtplib.SMTP", side_effect=OSError("refused")):
            sent1 = self._run(_NOW)
        with mock.patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value = mock.Mock()
            sent2 = self._run(_NOW)
        self.assertFalse(sent1)
        self.assertTrue(sent2)

    def test_never_raises_on_internal_error(self):
        self._configure()
        with mock.patch.object(se, "email_body", side_effect=RuntimeError("boom")):
            sent = self._run(_NOW)
        self.assertFalse(sent)


if __name__ == "__main__":
    unittest.main()
