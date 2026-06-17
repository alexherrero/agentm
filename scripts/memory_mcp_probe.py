#!/usr/bin/env python3
"""agentm memory MCP server — liveness probe.

Checks whether the memory MCP daemon is reachable.  Used as the
SessionStart probe (Part 6 / doctor) and by check-all.sh contract tests.

Exit codes:
  0 — daemon is up and /health returned {"status": "ok"}
  1 — daemon is unreachable or returned an unexpected response

Usage:
  python scripts/memory_mcp_probe.py --check
  python scripts/memory_mcp_probe.py --check --port 7821
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request

_DEFAULT_PORT: int = 7821
_TIMEOUT: float = 3.0  # seconds


def check_liveness(host: str = "127.0.0.1", port: int = _DEFAULT_PORT) -> bool:
    """Return True if the daemon's /health endpoint returns status ok."""
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            import json

            body = json.loads(resp.read())
            return body.get("status") == "ok"
    except Exception:
        return False


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory_mcp_probe",
        description="agentm memory MCP server liveness probe",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        required=True,
        help="Run the liveness check (exits 0 = up, 1 = down)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Daemon host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AGENTM_MCP_PORT", _DEFAULT_PORT)),
        help=f"Daemon port (default: {_DEFAULT_PORT}; override: AGENTM_MCP_PORT env)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    up = check_liveness(host=args.host, port=args.port)
    if up:
        print("agentm-memory: up", file=sys.stderr)
        sys.exit(0)
    else:
        print(
            f"agentm-memory: not reachable at {args.host}:{args.port}",
            file=sys.stderr,
        )
        sys.exit(1)
