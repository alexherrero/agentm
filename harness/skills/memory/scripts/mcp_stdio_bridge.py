#!/usr/bin/env python3
"""A stdio↔HTTP shim so Claude Desktop can reach the daemon's MCP surface.

The daemon already serves MCP, at `http://127.0.0.1:7821/mcp`, and Claude Code
points straight at it (`claude mcp add --transport http …`). Claude Desktop
cannot: its custom-connector form requires an `https` URL — the app's own
validator says so in as many words — and the daemon is plain HTTP on loopback,
where TLS would buy nothing but a certificate to manage. The other door into
Desktop is `claude_desktop_config.json`, and that one speaks stdio.

So this exists only to carry newline-delimited JSON-RPC from Desktop's stdin to
a POST, and the reply back out. It is not a proxy with opinions:

  * It never invents, filters, renames or adds a tool. The two tools Desktop
    sees are the two the daemon serves, because this forwards `tools/list`
    unread and hands back what came. The deep search is not on that surface and
    this is not where it could be added — a rule that holds because nothing here
    knows what a tool is.
  * It forwards `initialize` params verbatim, so `clientInfo.name` survives the
    hop and a recall from Desktop is recorded under its own surface rather than
    an anonymous one.
  * It reads no configuration file, and it will not be talked into reading one.
    That second half is not free: `urllib`'s default opener carries handlers for
    `file:`, `data:` and `ftp:`, so a bridge that passed `--url` straight
    through would read any local file and write it to stdout — into Desktop, and
    therefore into a model's context. `--url` is pinned to `http`/`https` on a
    loopback host, and the opener is built by hand with the HTTP handlers only.
  * It does not follow redirects. Whatever answers on the port could otherwise
    return a `302` and hand the reply — the tool list included — to a third
    party, which would move the two-tool guarantee off the daemon and onto
    whoever answered first.

Run it as `mcp_stdio_bridge.py [--url URL]`. Exits 0 on a clean EOF, which is
what Desktop does when it closes the server.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:7821/mcp"

# Long enough for a cold index and a dense arm, short enough that a wedged
# daemon surfaces as an error in the client rather than a conversation that
# never comes back.
TIMEOUT_S = 60

# A generous ceiling on one reply. `read()` with no argument allocates whatever
# the responder sends before any slice can trim it, so a hostile or broken
# process on the port could exhaust this subprocess by streaming. Five hit heads
# and their snippets are kilobytes; this is three orders of magnitude of room.
MAX_REPLY_BYTES = 8 * 1024 * 1024

ALLOWED_SCHEMES = ("http", "https")
# The daemon is a local process by design. A remote endpoint is not a
# configuration this has been thought about, so it is refused rather than
# allowed quietly.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Returning None from redirect_request makes urllib raise instead of follow."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener():
    """An opener with the HTTP handlers and nothing else.

    Built by hand rather than with `build_opener()`, which would add
    `FileHandler`, `FTPHandler` and `DataHandler` back. Those are what turn a
    URL flag into a local-file reader, and there is no use for them here.
    """
    o = urllib.request.OpenerDirector()
    o.add_handler(urllib.request.HTTPHandler())
    o.add_handler(urllib.request.HTTPSHandler())
    o.add_handler(urllib.request.HTTPErrorProcessor())
    o.add_handler(urllib.request.HTTPDefaultErrorHandler())
    o.add_handler(_RefuseRedirects())
    return o.open


def check_url(url: str) -> "str | None":
    """Return a refusal reason, or None when the URL is one we will POST to."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        return f"the endpoint does not parse: {exc}"
    if parsed.scheme not in ALLOWED_SCHEMES:
        return (f"the endpoint must be http or https, not "
                f"{parsed.scheme or 'a bare path'}")
    host = (parsed.hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        return (f"the endpoint must be on this machine "
                f"({', '.join(LOOPBACK_HOSTS[:2])}), not {host or 'nothing'}")
    return None


def _error(rpc_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def forward(raw: str, url: str, *, opener=None) -> "dict | None":
    """POST one JSON-RPC message, return the reply to write back, or None.

    None means "write nothing", which is correct for a notification: the daemon
    answers those `202` with no body, and a client that received a reply to a
    message it sent without an id would be entitled to complain.
    """
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        # No id to answer under — the line did not parse, so there is nothing to
        # read an id out of. -32700 with a null id is what the spec asks for.
        return _error(None, -32700, f"parse error: {exc}")

    rpc_id = message.get("id") if isinstance(message, dict) else None

    # Checked on every message, not only at startup, so a caller that reaches
    # `forward` directly cannot skip it.
    refusal = check_url(url)
    if refusal is not None:
        if rpc_id is None:
            return None
        return _error(rpc_id, -32600, f"refusing to use this endpoint: {refusal}")

    if opener is None:
        opener = build_opener()

    req = urllib.request.Request(
        url,
        data=raw.encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with opener(req, timeout=TIMEOUT_S) as resp:
            body = resp.read(MAX_REPLY_BYTES + 1)
            if len(body) > MAX_REPLY_BYTES:
                if rpc_id is None:
                    return None
                return _error(rpc_id, -32603,
                              f"the reply exceeded {MAX_REPLY_BYTES} bytes and "
                              f"was not read")
            status = getattr(resp, "status", None)
            if status == 202 or not body.strip():
                return None
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 — the error path must not raise
            pass
        if rpc_id is None:
            return None
        return _error(rpc_id, -32603, f"the daemon answered {exc.code}: {detail}".strip())
    except urllib.error.URLError as exc:
        if rpc_id is None:
            return None
        # The overwhelmingly common cause, and the one worth naming: the daemon
        # is not running. A client that gets this shows the operator a reason
        # rather than an empty tool list they have to guess at.
        return _error(
            rpc_id, -32603,
            f"cannot reach the agentm daemon at {url} ({exc.reason}). "
            f"It may not be running: `agentmd status`.",
        )
    except json.JSONDecodeError as exc:
        if rpc_id is None:
            return None
        return _error(rpc_id, -32603, f"the daemon's reply did not parse: {exc}")


def pump(stdin, stdout, url: str, *, opener=None) -> int:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        reply = forward(line, url, opener=opener)
        if reply is None:
            continue
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()
    return 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"the daemon's MCP endpoint (default: {DEFAULT_URL})")
    args = ap.parse_args(argv)
    refusal = check_url(args.url)
    if refusal is not None:
        # Loud and immediate. Desktop shows the operator a server that failed to
        # start, which is the honest outcome — better than a running bridge that
        # answers every call with the same error.
        print(f"mcp_stdio_bridge: {refusal}", file=sys.stderr)
        return 2
    try:
        return pump(sys.stdin, sys.stdout, args.url)
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        # Desktop closed the server. Not a failure.
        return 0


if __name__ == "__main__":
    sys.exit(main())
