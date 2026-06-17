#!/usr/bin/env python3
"""agentm memory MCP server — streamable-HTTP singleton daemon.

Exposes the agentm memory engine as an MCP server via FastMCP.
Transport: streamable-HTTP only (SSE is deprecated; stdio = shim in Part 5).
Bind:      127.0.0.1 only — LAN is flat; a routable bind exposes at L2.
Stdout:    sacred — all logs go to stderr (mandatory for the stdio-shim path).

Requires Python >=3.10 and fastmcp>=3,<4.  Run under Homebrew Python 3.13:
  /opt/homebrew/bin/python3.13 scripts/memory_mcp_server.py
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys

# All log output goes to stderr — stdout must stay clean for the stdio-shim path.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("agentm.mcp")

try:
    import uvicorn
    from fastmcp import FastMCP
    from fastmcp.server.auth import AccessToken, TokenVerifier
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route
except ImportError as exc:  # pragma: no cover
    sys.exit(
        f"Import error: {exc}\n"
        "Install: /opt/homebrew/bin/python3.13 -m pip install "
        '"fastmcp>=3,<4" "mcp<2" --user --break-system-packages'
    )

# Default port; override with AGENTM_MCP_PORT env var.
_DEFAULT_PORT: int = 7821

# ── security ─────────────────────────────────────────────────────────────────

class _StaticBearerAuth(TokenVerifier):
    """Loopback bearer auth: one static token, always env-injected (AGENTM_MCP_TOKEN).

    Subclasses TokenVerifier (resource-server-only interface) — not OAuthProvider.
    A loopback daemon needs token verification, not a full OAuth server.
    """

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    async def verify_token(self, token: str) -> "AccessToken | None":
        if token == self._token:
            return AccessToken(token=token, client_id="agentm-mcp", scopes=[])
        return None


# Origins that are safe for cross-origin browser requests to the daemon.
# MCP clients (Claude Code, Cursor) do not send an Origin header at all —
# those requests are allowed unconditionally.  Only browsers on cross-origin
# pages send Origin; this allowlist is the DNS-rebinding defense.
_SAFE_ORIGIN_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")


class _OriginValidator:
    """ASGI middleware: block requests whose Origin header is not localhost.

    Requests without an Origin header pass through (MCP clients don't send one).
    Any Origin that doesn't match http://localhost[:<port>] or
    http://127.0.0.1[:<port>] receives a 403 — the DNS-rebinding defense.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            origin = headers.get(b"origin", b"").decode()
            if origin and not _SAFE_ORIGIN_RE.match(origin):
                body = b'{"error":"forbidden","reason":"Origin not allowed"}'
                await send({
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


# ── server singleton ──────────────────────────────────────────────────────────

_mcp_token: str = os.environ.get("AGENTM_MCP_TOKEN", "").strip()
if not _mcp_token:
    logger.warning(
        "AGENTM_MCP_TOKEN is not set — HTTP transport is unauthenticated. "
        "Set this env var before connecting any MCP host."
    )

# The singleton MCP server instance.
mcp = FastMCP(
    name="agentm-memory",
    auth=_StaticBearerAuth(_mcp_token) if _mcp_token else None,
)

# Register the four memory tools (memory_search, memory_recall,
# memory_append, memory_forget) on the server.
try:
    from memory_mcp_tools import register_tools as _register_tools
    _register_tools(mcp)
except ImportError:
    pass  # tools module not yet available — bare skeleton mode (Part 1)


async def _health_endpoint(request):
    """Liveness probe — HTTP GET /health → {"status": "ok"}."""
    return JSONResponse({"status": "ok"})


def build_app(*, stateless_http: bool = True, _mcp=None):
    """Return a Starlette app with the MCP endpoint + /health route.

    _mcp: optional FastMCP instance override (for security tests that create
    a fresh server with a specific auth configuration).  When None, uses the
    module-level singleton.
    """
    server = _mcp if _mcp is not None else mcp
    app = server.http_app(
        transport="streamable-http",
        stateless_http=stateless_http,
        middleware=[Middleware(_OriginValidator)],
    )
    # Prepend /health so it resolves before the MCP POST endpoint.
    app.router.routes.insert(
        0, Route("/health", endpoint=_health_endpoint, methods=["GET"])
    )
    return app


def run(*, host: str = "127.0.0.1", port: int = _DEFAULT_PORT) -> None:
    """Start the uvicorn server.  Blocks until the process exits."""
    app = build_app()
    logger.info("agentm-memory MCP server starting on %s:%d", host, port)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_config=None,  # use our own stderr logger, not uvicorn's default
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory_mcp_server",
        description="agentm memory MCP server (streamable-HTTP singleton daemon)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            f"  AGENTM_MCP_PORT  Listen port (default: {_DEFAULT_PORT})\n\n"
            "This daemon runs on 127.0.0.1 by design — loopback-only binding\n"
            "is mandatory (LAN is flat; routable bind = L2 exposure)."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1; loopback-only by design)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AGENTM_MCP_PORT", _DEFAULT_PORT)),
        help=f"Listen port (default: {_DEFAULT_PORT}; override: AGENTM_MCP_PORT env)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    run(host=args.host, port=args.port)
