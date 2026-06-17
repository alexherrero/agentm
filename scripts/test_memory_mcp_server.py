#!/usr/bin/env python3
"""Contract tests for scripts/memory_mcp_server.py — no live daemon required.

Uses FastMCP's in-memory transport (FastMCPTransport) so these run on any
platform without a running daemon, port binding, or launchd.  This is the
deterministic CI baseline for Part 1; live-host tests are manual gates.

Run directly:
  cd scripts && python3 -m unittest test_memory_mcp_server
Or via check-all.sh:
  cd scripts && python3 -m unittest discover -p 'test_*.py'
"""
from __future__ import annotations

import sys
import unittest

# Skip the whole module if fastmcp is not installed under this interpreter.
# On Python 3.9 / CI without fastmcp, these tests are simply not runnable.
try:
    from fastmcp.client import Client, FastMCPTransport
    import memory_mcp_server as _srv

    _HAS_FASTMCP = True
except ImportError:
    _HAS_FASTMCP = False


@unittest.skipUnless(_HAS_FASTMCP, "fastmcp not installed — skip MCP server tests")
class TestMemoryMcpServerSkeleton(unittest.IsolatedAsyncioTestCase):
    """In-memory FastMCP contract tests for the Part 1 server skeleton."""

    async def test_mcp_instance_is_fastmcp(self):
        """mcp is a FastMCP instance named 'agentm-memory'."""
        from fastmcp import FastMCP

        self.assertIsInstance(_srv.mcp, FastMCP)
        self.assertEqual(_srv.mcp.name, "agentm-memory")

    async def test_tools_list_is_empty_at_skeleton_stage(self):
        """tools/list returns an empty list — no tools registered in Part 1."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            tools = await client.list_tools()
        self.assertEqual(tools, [], f"Expected [], got {tools}")

    async def test_mcp_server_round_trip(self):
        """In-memory client completes a full initialize + list_tools round-trip."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            tools = await client.list_tools()
            # Skeleton: 0 tools.  Response must still be a list (not an error).
            self.assertIsInstance(tools, list)

    def test_build_app_returns_starlette_app(self):
        """build_app() produces a Starlette application with a /health route."""
        from starlette.applications import Starlette

        app = _srv.build_app()
        self.assertIsInstance(app, Starlette)
        # Confirm /health route is present.
        paths = [
            getattr(r, "path", None)
            for r in app.router.routes
        ]
        self.assertIn("/health", paths, f"/health missing from routes: {paths}")

    def test_default_port_constant(self):
        """Default port is 7821 as specified in the design."""
        self.assertEqual(_srv._DEFAULT_PORT, 7821)

    def test_parse_args_defaults(self):
        """_parse_args() defaults to host=127.0.0.1 and port=7821."""
        args = _srv._parse_args([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 7821)

    def test_parse_args_custom_port(self):
        """--port flag overrides the default."""
        args = _srv._parse_args(["--port", "8888"])
        self.assertEqual(args.port, 8888)

    def test_parse_args_help_exits_zero(self):
        """--help exits 0 (SystemExit with code 0)."""
        with self.assertRaises(SystemExit) as cm:
            _srv._parse_args(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_probe_check_liveness_returns_bool(self):
        """check_liveness() returns False when no daemon is running on an unused port."""
        import memory_mcp_probe as probe

        # Use an unlikely-to-be-bound port so the test never blocks.
        result = probe.check_liveness(host="127.0.0.1", port=19999)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
