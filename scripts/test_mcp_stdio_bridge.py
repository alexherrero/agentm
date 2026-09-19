#!/usr/bin/env python3
"""The stdio bridge carries JSON-RPC and adds nothing to it.

The bridge exists because Claude Desktop cannot point at `http://127.0.0.1:…`
— its custom-connector form requires https — so Desktop reaches the daemon over
stdio instead. Everything worth testing about it is a *negative*: that it does
not filter, rename, add or drop anything on the way through, because the
constraint it has to keep is that Desktop sees exactly the two tools the daemon
serves and never the deep search.

The daemon is faked here rather than run. A fake is the right call for once:
what these assert is what the bridge does to a message, and a real daemon would
make the two-tool assertions pass for the daemon's reasons instead of the
bridge's. `scripts/test_daemon_mcp.py` covers the daemon's own surface.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "harness" / "skills" / "memory" / "scripts" / "mcp_stdio_bridge.py"

spec = importlib.util.spec_from_file_location("_bridge", _SRC)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status = status

    def read(self, amt=None):
        # Mirrors http.client.HTTPResponse, which takes an optional size. The
        # first version of this fake took none, so it passed while the code
        # under test called `read(N)` — a fake that is easier to satisfy than
        # the real thing tests nothing about the real thing.
        return self._body if amt is None else self._body[:amt]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_returning(payload, status=200, *, record=None):
    def _open(req, timeout=None):
        if record is not None:
            record.append({
                "url": req.full_url,
                "body": json.loads(req.data.decode()),
                "headers": {k.lower(): v for k, v in req.headers.items()},
            })
        return FakeResponse(payload, status)
    return _open


class TheBridgeChangesNothing(unittest.TestCase):

    def test_the_request_arrives_byte_for_byte(self):
        """Anything this rewrote would be a second place the protocol lives."""
        sent = []
        raw = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
        bridge.forward(raw, bridge.DEFAULT_URL,
                       opener=opener_returning({"jsonrpc": "2.0", "id": 1}, record=sent))
        self.assertEqual(sent[0]["body"], json.loads(raw))
        self.assertEqual(sent[0]["url"], bridge.DEFAULT_URL)

    def test_the_two_tools_pass_through_unchanged(self):
        """The constraint: Desktop sees the daemon's surface, not a curated one.

        This holds because the bridge does not know what a tool is — so the
        assertion is that the reply is *identical*, not that it contains two
        named things. A bridge that filtered to an allowlist would pass a
        two-tool assertion and fail this one.
        """
        served = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [
            {"name": "memory_search"}, {"name": "memory_capture"}]}}
        got = bridge.forward('{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                             bridge.DEFAULT_URL, opener=opener_returning(served))
        self.assertEqual(got, served)

    def test_a_third_tool_would_pass_through_too(self):
        """Deliberately proving the bridge is *not* the enforcement point.

        If this failed, it would mean the bridge had opinions about the tool
        list — and the next person would have to check two places to know what
        Desktop can call. The daemon is the one place. Recorded here so the
        division stays visible rather than being rediscovered as a surprise.
        """
        served = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [
            {"name": "memory_search"}, {"name": "memory_capture"},
            {"name": "memory_deep_search"}]}}
        got = bridge.forward('{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                             bridge.DEFAULT_URL, opener=opener_returning(served))
        self.assertEqual(got, served)

    def test_initialize_carries_the_client_name(self):
        """`clientInfo.name` is what becomes the `mcp:<client>` surface in the
        ledger. A bridge that dropped it would make every Desktop recall
        anonymous, and the per-surface counts in the morning note would be
        wrong in a way nothing else would reveal."""
        sent = []
        raw = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                          "params": {"clientInfo": {"name": "claude-desktop",
                                                    "version": "2.2553.1"}}})
        bridge.forward(raw, bridge.DEFAULT_URL,
                       opener=opener_returning({"jsonrpc": "2.0", "id": 0}, record=sent))
        self.assertEqual(
            sent[0]["body"]["params"]["clientInfo"]["name"], "claude-desktop")


class NotificationsAndFailures(unittest.TestCase):

    def test_a_notification_gets_no_reply_written(self):
        """The daemon answers a notification 202 with no body. Writing anything
        back would be a reply to a message that carried no id."""
        got = bridge.forward(
            '{"jsonrpc":"2.0","method":"notifications/initialized"}',
            bridge.DEFAULT_URL, opener=opener_returning(b"", status=202))
        self.assertIsNone(got)

    def test_a_dead_daemon_becomes_an_error_not_a_hang(self):
        """The common case, and the one where silence is worst: the client would
        wait on a reply that never comes."""
        def _dead(req, timeout=None):
            raise urllib.error.URLError("Connection refused")
        got = bridge.forward('{"jsonrpc":"2.0","id":7,"method":"tools/list"}',
                             bridge.DEFAULT_URL, opener=_dead)
        self.assertEqual(got["id"], 7)
        self.assertEqual(got["error"]["code"], -32603)
        self.assertIn("agentmd status", got["error"]["message"])

    def test_a_dead_daemon_on_a_notification_stays_silent(self):
        """Still no id, so still nothing to answer under."""
        def _dead(req, timeout=None):
            raise urllib.error.URLError("Connection refused")
        got = bridge.forward('{"jsonrpc":"2.0","method":"notifications/x"}',
                             bridge.DEFAULT_URL, opener=_dead)
        self.assertIsNone(got)

    def test_an_unparseable_line_answers_under_a_null_id(self):
        got = bridge.forward("{not json", bridge.DEFAULT_URL,
                             opener=opener_returning({}))
        self.assertIsNone(got["id"])
        self.assertEqual(got["error"]["code"], -32700)

    def test_an_http_error_names_the_status(self):
        def _boom(req, timeout=None):
            raise urllib.error.HTTPError(bridge.DEFAULT_URL, 500, "kaboom", {},
                                         io.BytesIO(b"internal"))
        got = bridge.forward('{"jsonrpc":"2.0","id":3,"method":"ping"}',
                             bridge.DEFAULT_URL, opener=_boom)
        self.assertIn("500", got["error"]["message"])

class TheBridgeWillNotBeTalkedIntoReadingAFile(unittest.TestCase):
    """The replacement for a test that could not fail for the bug it named.

    The first version of this asserted that the bridge's *source* contained no
    `open(` and no `Path.home()`, and concluded from that it could hold no
    credential. It passed while the bridge would happily accept
    `--url file:///Users/…/.agentm-config.json` and write the SMTP credential to
    stdout — because `urllib` does the open, not the bridge, and neither token
    appears in a `file://` URL. The property is behavioural, so the test is too:
    these call `forward` with the URLs that used to work.
    """

    def test_a_file_url_is_refused(self):
        """The one that matters: the engine config holds an SMTP credential and
        will hold a mailbox one, and stdout here goes to Desktop and from there
        into a model's context."""
        got = bridge.forward('{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                             "file:///etc/passwd")
        self.assertEqual(got["error"]["code"], -32600)
        self.assertIn("http", got["error"]["message"])

    def test_a_data_url_is_refused(self):
        got = bridge.forward('{"jsonrpc":"2.0","id":1,"method":"ping"}',
                             'data:application/json,{"leak":"yes"}')
        self.assertEqual(got["error"]["code"], -32600)

    def test_an_ftp_url_is_refused(self):
        got = bridge.forward('{"jsonrpc":"2.0","id":1,"method":"ping"}',
                             "ftp://example.com/x")
        self.assertEqual(got["error"]["code"], -32600)

    def test_a_remote_http_host_is_refused(self):
        """Scheme alone is not enough — the daemon is a local process, and an
        endpoint somewhere else is a configuration nobody has reasoned about."""
        got = bridge.forward('{"jsonrpc":"2.0","id":1,"method":"ping"}',
                             "http://evil.example.net/mcp")
        self.assertEqual(got["error"]["code"], -32600)
        self.assertIn("on this machine", got["error"]["message"])

    def test_the_default_endpoint_passes_the_check(self):
        """The other half: a check that refused everything would also pass the
        four tests above."""
        self.assertIsNone(bridge.check_url(bridge.DEFAULT_URL))
        self.assertIsNone(bridge.check_url("http://localhost:9999/mcp"))

    def test_main_refuses_a_bad_url_before_reading_a_single_message(self):
        rc = bridge.main(["--url", "file:///etc/passwd"])
        self.assertEqual(rc, 2)

    def test_the_opener_carries_no_file_handler(self):
        """Belt and braces under the URL check: even reached with a `file:` URL
        directly, the opener has no handler that could serve it."""
        import urllib.request
        o = bridge.build_opener().__self__
        names = {type(h).__name__ for h in o.handlers}
        for forbidden in ("FileHandler", "FTPHandler", "DataHandler"):
            self.assertNotIn(forbidden, names)

    def test_a_redirect_is_not_followed(self):
        """Whatever answers the port could otherwise hand the tool list to a
        third party, which would move the two-tool guarantee off the daemon."""
        import urllib.request
        h = bridge._RefuseRedirects()
        self.assertIsNone(
            h.redirect_request(None, None, 302, "Found", {}, "http://elsewhere.example.net/"))

    def test_an_oversized_reply_is_refused_rather_than_read(self):
        big = b'{"jsonrpc":"2.0","id":1,"result":"' + b"A" * (bridge.MAX_REPLY_BYTES + 10) + b'"}'

        def _flood(req, timeout=None):
            return FakeResponse(big)

        got = bridge.forward('{"jsonrpc":"2.0","id":1,"method":"ping"}',
                             bridge.DEFAULT_URL, opener=_flood)
        self.assertEqual(got["error"]["code"], -32603)
        self.assertIn("exceeded", got["error"]["message"])


class ThePump(unittest.TestCase):

    def test_many_messages_one_line_each(self):
        lines = "\n".join([
            '{"jsonrpc":"2.0","id":1,"method":"ping"}',
            '{"jsonrpc":"2.0","method":"notifications/initialized"}',
            '{"jsonrpc":"2.0","id":2,"method":"ping"}',
        ])

        def _open(req, timeout=None):
            body = json.loads(req.data.decode())
            if "id" not in body:
                return FakeResponse(b"", status=202)
            return FakeResponse({"jsonrpc": "2.0", "id": body["id"], "result": {}})

        out = io.StringIO()
        rc = bridge.pump(io.StringIO(lines), out, bridge.DEFAULT_URL, opener=_open)
        self.assertEqual(rc, 0)
        written = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual([m["id"] for m in written], [1, 2],
                         "the notification should not have produced a line")

    def test_blank_lines_are_skipped(self):
        out = io.StringIO()
        rc = bridge.pump(io.StringIO("\n\n  \n"), out, bridge.DEFAULT_URL,
                         opener=opener_returning({}))
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
