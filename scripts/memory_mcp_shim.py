#!/usr/bin/env python3
"""stdio shim for Claude Desktop — proxies MCP stdio to the agentm-memory HTTP daemon.

The shim is NOT a vault writer.  It creates a FastMCP proxy backed by
StreamableHttpTransport (bearer header from env) and runs it as a stdio server.
Claude Desktop spawns this process; the existing daemon on 127.0.0.1:7821 is
the actual writer (vault_lock writer #2).  Writer count stays at two.

Stdout is sacred — all log output goes to stderr (MCP stdio protocol uses stdout).

Usage:
    # Run as Claude Desktop's stdio shim (requires AGENTM_MCP_TOKEN in env):
    AGENTM_MCP_TOKEN=<token> python3 memory_mcp_shim.py

    # Print MCP host config snippets for Claude Desktop / Claude Code / Cursor:
    python3 memory_mcp_shim.py --print-configs

    # Dev/test: disable bearer auth requirement (not for production):
    python3 memory_mcp_shim.py --no-auth

Environment variables:
    AGENTM_MCP_URL    Daemon URL  (default: http://127.0.0.1:7821/mcp)
    AGENTM_MCP_TOKEN  Bearer token (required unless --no-auth is passed)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import textwrap

# All log output goes to stderr — stdout is sacred (MCP stdio protocol uses it).
logging.basicConfig(
    stream=sys.stderr,
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("agentm.shim")

_DEFAULT_URL = "http://127.0.0.1:7821/mcp"


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="memory_mcp_shim",
        description=(
            "stdio→HTTP shim: proxies MCP stdio to the agentm-memory daemon.\n"
            "Intended for Claude Desktop, which speaks stdio transport."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--print-configs",
        action="store_true",
        help="Print MCP host config snippets for all supported hosts and exit.",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Disable bearer auth requirement (dev/test only; never use in production).",
    )
    return parser.parse_args(argv)


def _print_configs(url: str) -> None:
    """Print MCP host config snippets to stdout.

    Token is NEVER a literal in any output — configs use ${AGENTM_MCP_TOKEN} as
    the placeholder.  Set AGENTM_MCP_TOKEN in your shell profile; processes inherit
    it at runtime.
    """
    script_path = os.path.abspath(__file__)
    python_bin = sys.executable

    # Claude Desktop: stdio shim (AGENTM_MCP_TOKEN inherited from shell env).
    claude_desktop_cfg = {
        "mcpServers": {
            "agentm-memory": {
                "command": python_bin,
                "args": [script_path],
                "env": {"AGENTM_MCP_URL": url},
            }
        }
    }

    # Claude Code + Cursor: native streamable-HTTP (bearer via ${AGENTM_MCP_TOKEN}).
    native_http_cfg = {
        "mcpServers": {
            "agentm-memory": {
                "url": url,
                "headers": {"Authorization": "Bearer ${AGENTM_MCP_TOKEN}"},
            }
        }
    }

    header = textwrap.dedent("""\
        # agentm-memory MCP host configs
        # Set AGENTM_MCP_TOKEN in your shell profile (~/.zshrc or ~/.bashrc).
        # The token is NEVER a literal in any config file — always env-injected.
        # Run `launchctl setenv AGENTM_MCP_TOKEN <token>` so GUI apps inherit it.
    """)
    sys.stdout.write(header)
    sys.stdout.write("\n# --- Claude Desktop (stdio shim) ---\n")
    sys.stdout.write(
        "# ~/Library/Application Support/Claude/claude_desktop_config.json\n"
    )
    sys.stdout.write(
        "# AGENTM_MCP_TOKEN is inherited from your shell/launchctl environment.\n"
    )
    sys.stdout.write(json.dumps(claude_desktop_cfg, indent=2))
    sys.stdout.write("\n\n# --- Claude Code (native streamable-HTTP) ---\n")
    sys.stdout.write("# .claude/settings.json  (merge under top-level mcpServers key)\n")
    sys.stdout.write(json.dumps(native_http_cfg, indent=2))
    sys.stdout.write("\n\n# --- Cursor (native streamable-HTTP) ---\n")
    sys.stdout.write("# ~/.cursor/mcp.json  (same shape as Claude Code)\n")
    sys.stdout.write(json.dumps(native_http_cfg, indent=2))
    sys.stdout.write("\n")


def main(argv=None) -> None:
    args = _parse_args(argv)

    url = os.environ.get("AGENTM_MCP_URL", _DEFAULT_URL).rstrip("/")
    token = os.environ.get("AGENTM_MCP_TOKEN", "").strip()

    if args.print_configs:
        _print_configs(url)
        return

    if not token and not args.no_auth:
        logger.error(
            "AGENTM_MCP_TOKEN is not set. "
            "Set this env var to the daemon bearer token, "
            "or pass --no-auth for dev/test use (not for production)."
        )
        sys.exit(1)

    try:
        from fastmcp.client import Client
        from fastmcp.client.transports import StreamableHttpTransport
        from fastmcp.server import create_proxy
    except ImportError as exc:
        logger.error("Import error: %s — install: pip install 'fastmcp>=3,<4'", exc)
        sys.exit(1)

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    transport = StreamableHttpTransport(url=url, headers=headers or None)
    client = Client(transport)
    proxy = create_proxy(client, name="agentm-memory-shim")

    logger.info("agentm-memory shim connecting to %s (auth=%s)", url, bool(token))
    proxy.run(transport="stdio")


if __name__ == "__main__":
    main()
