#!/usr/bin/env python3
"""Delivery, and the distinction it exists to draw.

Mail that was never configured is a skip. Mail that *is* configured and did not
arrive is a channel the operator believes is working and is not. The seam
underneath returns False for both, which is right for its own caller and wrong
here — so every test in this file is ultimately checking that those two stay
apart.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts/health"))

import scorecard_delivery as sd  # noqa: E402


def configured(prefix: Path, **extra):
    """Write a kernel config with mail set up."""
    body = {
        "plugins.autonomy.email_to": "operator@example.com",
        "plugins.autonomy.email_smtp_url": "smtp://user:secret@example.com:587",
    }
    body.update(extra)
    (prefix / ".agentm-config.json").write_text(json.dumps(body), encoding="utf-8")
    return prefix


def a_scorecard(d: Path) -> Path:
    p = d / "2026-08-22-health-scorecard.md"
    p.write_text("# Corpus health — 2026-08-22\n\nSome rows.\n", encoding="utf-8")
    return p


class OutcomeTests(unittest.TestCase):
    def test_unconfigured_mail_is_a_skip_and_says_how_to_configure_it(self):
        """The design's own words: unconfigured mail is a skip, never a failure."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            result = sd.deliver(a_scorecard(tmp), install_prefix=tmp)

        self.assertEqual(result.outcome, sd.SKIPPED)
        self.assertTrue(result.ok, "an unconfigured channel counted as a failure")
        self.assertIn("email_to", result.reason,
                      "the skip does not say what would make it send")
        self.assertIn("written either way", result.reason,
                      "the skip does not say the artifact still exists")

    def test_a_relay_that_refuses_is_a_failure_not_a_skip(self):
        """The distinction the whole module exists for.

        `session_email.run()` returns False here *and* for the unconfigured case,
        so a channel that is configured and silently not delivering would look
        exactly like one nobody set up.
        """
        with tempfile.TemporaryDirectory() as d:
            tmp = configured(Path(d))
            result = sd.deliver(a_scorecard(tmp), install_prefix=tmp,
                                send=lambda *a, **k: False)

        self.assertEqual(result.outcome, sd.FAILED)
        self.assertFalse(result.ok)
        self.assertIn("looks like it works and does not", result.reason)

    def test_a_relay_that_raises_is_a_failure_with_the_reason(self):
        def explode(*a, **k):
            raise TimeoutError("the relay never answered")

        with tempfile.TemporaryDirectory() as d:
            tmp = configured(Path(d))
            result = sd.deliver(a_scorecard(tmp), install_prefix=tmp, send=explode)

        self.assertEqual(result.outcome, sd.FAILED)
        self.assertIn("TimeoutError", result.reason)
        self.assertIn("never answered", result.reason)

    def test_a_successful_send_says_where_it_went(self):
        seen = {}

        def record(smtp_url, to_addr, subject, body, *, from_addr=None):
            seen.update(url=smtp_url, to=to_addr, subject=subject, body=body)
            return True

        with tempfile.TemporaryDirectory() as d:
            tmp = configured(Path(d))
            card = a_scorecard(tmp)
            result = sd.deliver(card, install_prefix=tmp, send=record)

        self.assertEqual(result.outcome, sd.SENT)
        self.assertTrue(result.ok)
        self.assertIn("operator@example.com", result.reason)
        self.assertEqual(seen["to"], "operator@example.com")
        self.assertIn("2026-08-22", seen["subject"])
        self.assertIn("Some rows.", seen["body"],
                      "the scorecard's own text was not what got sent")

    def test_a_missing_scorecard_with_mail_configured_is_a_failure(self):
        """Configured mail with nothing to send is the step before this one
        having failed, and calling it a skip would file it under harmless."""
        with tempfile.TemporaryDirectory() as d:
            tmp = configured(Path(d))
            result = sd.deliver(tmp / "nothing-here.md", install_prefix=tmp,
                                send=lambda *a, **k: True)

        self.assertEqual(result.outcome, sd.FAILED)
        self.assertIn("no scorecard at", result.reason)

    def test_a_missing_scorecard_with_no_mail_is_still_only_a_skip(self):
        """Nothing to send and nowhere to send it is not a delivery problem."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            result = sd.deliver(tmp / "nothing-here.md", install_prefix=tmp)

        self.assertEqual(result.outcome, sd.SKIPPED)

    def test_the_optional_from_address_is_passed_through(self):
        """Some relays require a domain-verified From distinct from the auth
        user, which is why the seam carries it at all."""
        seen = {}

        def record(smtp_url, to_addr, subject, body, *, from_addr=None):
            seen["from"] = from_addr
            return True

        with tempfile.TemporaryDirectory() as d:
            tmp = configured(Path(d),
                             **{"plugins.autonomy.email_from": "digest@example.com"})
            sd.deliver(a_scorecard(tmp), install_prefix=tmp, send=record)

        self.assertEqual(seen["from"], "digest@example.com")


class SecrecyTests(unittest.TestCase):
    """A log line is the wrong place for the operator's relay credentials."""

    def test_the_failure_message_names_the_host_and_not_the_password(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = configured(Path(d))
            result = sd.deliver(a_scorecard(tmp), install_prefix=tmp,
                                send=lambda *a, **k: False)

        self.assertIn("example.com", result.reason)
        self.assertNotIn("secret", result.reason,
                         "the relay password went into a message meant for a log")
        self.assertNotIn("user:", result.reason)

    def test_the_host_is_extracted_from_every_url_shape(self):
        for url, want in [
            ("smtp://user:pw@example.com:587", "example.com:587"),
            ("smtp://example.com", "example.com"),
            ("smtp://user@example.com:25/path", "example.com:25"),
            ("", "the configured relay"),
        ]:
            self.assertEqual(sd._host(url), want, f"for {url!r}")


class ExitCodeTests(unittest.TestCase):
    """The exit code is what a nightly runner branches on."""

    def test_a_skip_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            code = sd.main([str(a_scorecard(tmp)), "--install-prefix", str(tmp)])
        self.assertEqual(code, 0)

    def test_a_failure_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = configured(Path(d))
            # No `send` override reaches the real SMTP path, which cannot
            # connect to example.com and returns False — a genuine refusal
            # rather than a stubbed one.
            code = sd.main([str(tmp / "absent.md"), "--install-prefix", str(tmp)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
