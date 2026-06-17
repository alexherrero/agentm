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

# The singleton MCP server instance.
mcp = FastMCP(name="agentm-memory")

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


def build_app(*, stateless_http: bool = True):
    """Return a Starlette app with the MCP endpoint + /health route."""
    app = mcp.http_app(transport="streamable-http", stateless_http=stateless_http)
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
