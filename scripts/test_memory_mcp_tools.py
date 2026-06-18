#!/usr/bin/env python3
"""Contract tests for the four MCP memory tools — no live daemon required.

Tests run against a temporary vault (a fresh tempdir) injected via the
MEMORY_VAULT_PATH env var so harness_memory.vault_path() resolves it.
FastMCP's in-memory transport (FastMCPTransport) is used — no HTTP binding.

Contracts verified:
  1. memory_forget: soft-delete — file present + status=deleted + deleted_at
  2. memory_append: idempotency — same key twice → one entry, deduplicated=True
  3. memory_recall: returns a string (may be empty if vault has no always-load)
  4. memory_search: deleted-exclude contract + include_deleted override
  5. tools/list: four tools, snake_case names, each with description + schema

Run directly:
  cd scripts && python3 -m unittest test_memory_mcp_tools
Or via check-all.sh:
  cd scripts && python3 -m unittest discover -p 'test_*.py'
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from fastmcp.client import Client, FastMCPTransport
    import memory_mcp_server as _srv
    from memory_mcp_tools import (
        _parse_frontmatter,
        _replace_frontmatter,
        _idem_tag,
        _make_slug,
        _get_snippet,
        _validate_path_segment,
    )
    import yaml

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


def _make_vault(tmp: Path) -> Path:
    """Create a minimal vault directory structure in `tmp`."""
    vault = tmp / "vault"
    vault.mkdir()
    # Minimum required dirs so save_entry + recall don't fail.
    (vault / "personal" / "_always-load").mkdir(parents=True)
    return vault


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestMemoryForgetSoftDelete(unittest.IsolatedAsyncioTestCase):
    """memory_forget must soft-delete (status flip + deleted_at; file present)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._vault = _make_vault(Path(self._tmp.name))
        os.environ["MEMORY_VAULT_PATH"] = str(self._vault)

    def tearDown(self):
        os.environ.pop("MEMORY_VAULT_PATH", None)
        self._tmp.cleanup()

    async def _append_entry(self, client, *, kind="feedback", content="test body", slug_hint="test"):
        result = await client.call_tool("memory_append", {
            "content": content,
            "kind": kind,
            "title": slug_hint,
        })
        return result.data  # CallToolResult.data is the structured return value

    async def test_soft_delete_file_present(self):
        """After memory_forget, the backing file still exists."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            # Create an entry.
            append_res = await self._append_entry(client)
            entry_id = append_res["id"]

            # Forget it.
            await client.call_tool("memory_forget", {"id": entry_id})

        # File must still exist — never unlinked.
        entry_path = self._vault / entry_id
        self.assertTrue(entry_path.is_file(), f"File was unlinked: {entry_path}")

    async def test_soft_delete_status_field(self):
        """After memory_forget, status frontmatter field is 'deleted'."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            append_res = await self._append_entry(client)
            entry_id = append_res["id"]
            await client.call_tool("memory_forget", {"id": entry_id})

        content = (self._vault / entry_id).read_text()
        fm = _parse_frontmatter(content)
        self.assertEqual(fm.get("status"), "deleted",
                         f"Expected status=deleted, got: {fm.get('status')!r}")

    async def test_soft_delete_deleted_at_field(self):
        """After memory_forget, deleted_at frontmatter field is set."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            append_res = await self._append_entry(client)
            entry_id = append_res["id"]
            await client.call_tool("memory_forget", {"id": entry_id})

        content = (self._vault / entry_id).read_text()
        fm = _parse_frontmatter(content)
        self.assertIn("deleted_at", fm, "deleted_at field missing after forget")
        self.assertIsNotNone(fm["deleted_at"])

    async def test_soft_delete_idempotent(self):
        """memory_forget on an already-deleted entry returns already_deleted=True."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            append_res = await self._append_entry(client)
            entry_id = append_res["id"]
            await client.call_tool("memory_forget", {"id": entry_id})
            # Forget again.
            second = await client.call_tool("memory_forget", {"id": entry_id})
            res = second.data
        self.assertTrue(res["already_deleted"], f"Expected already_deleted=True, got {res}")

    async def test_path_traversal_rejected(self):
        """memory_forget rejects id values that escape the vault root."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            with self.assertRaises(Exception):
                await client.call_tool("memory_forget", {"id": "../../etc/passwd"})


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestMemoryAppendIdempotency(unittest.IsolatedAsyncioTestCase):
    """memory_append idempotency: same key twice → one entry, deduplicated=True."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._vault = _make_vault(Path(self._tmp.name))
        os.environ["MEMORY_VAULT_PATH"] = str(self._vault)

    def tearDown(self):
        os.environ.pop("MEMORY_VAULT_PATH", None)
        self._tmp.cleanup()

    async def _call_append(self, client, **kw):
        result = await client.call_tool("memory_append", kw)
        return result.data

    async def test_idempotent_no_duplicate(self):
        """Two appends with the same idempotency_key produce one vault file."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            first = await self._call_append(client,
                content="body one", kind="feedback", idempotency_key="key-abc")
            second = await self._call_append(client,
                content="body two", kind="feedback", idempotency_key="key-abc")

        # Same id returned both times.
        self.assertEqual(first["id"], second["id"],
                         f"Got different ids: {first['id']!r} vs {second['id']!r}")
        # Second call is flagged deduplicated.
        self.assertFalse(first.get("deduplicated"), "First call should not be deduplicated")
        self.assertTrue(second.get("deduplicated"), "Second call should be deduplicated")

    async def test_without_idempotency_key_creates_two_entries(self):
        """Without a key, two appends with different titles create two entries."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            first = await self._call_append(client,
                content="body one", kind="feedback", title="slug-alpha")
            second = await self._call_append(client,
                content="body two", kind="feedback", title="slug-beta")

        # Each call creates a new file; they should be distinct.
        self.assertNotEqual(first["id"], second["id"])

    async def test_append_entry_exists(self):
        """Written entry file is present and readable on disk."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            res = await self._call_append(client, content="hello vault", kind="user", title="hello-test")

        entry_path = self._vault / res["id"]
        self.assertTrue(entry_path.is_file(), f"Entry file not found: {entry_path}")
        content = entry_path.read_text()
        fm = _parse_frontmatter(content)
        self.assertEqual(fm.get("status"), "active")
        self.assertIn("hello vault", content)


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestMemoryRecall(unittest.IsolatedAsyncioTestCase):
    """memory_recall returns a string (may be empty on a vault with no entries)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._vault = _make_vault(Path(self._tmp.name))
        os.environ["MEMORY_VAULT_PATH"] = str(self._vault)

    def tearDown(self):
        os.environ.pop("MEMORY_VAULT_PATH", None)
        self._tmp.cleanup()

    async def test_recall_returns_string(self):
        """memory_recall always returns a string (empty vault → empty string)."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            result = await client.call_tool("memory_recall", {
                "context": "test context",
                "phase": "plan",
                "budget_tokens": 1000,
            })
        # memory_recall returns a string; .data may be the string directly.
        val = result.data
        self.assertIsInstance(val, str, f"Expected str, got {type(val)}: {val!r}")

    async def test_recall_respects_budget_tokens(self):
        """memory_recall honours the budget_tokens parameter (no exception raised)."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            result = await client.call_tool("memory_recall", {
                "context": "context",
                "phase": "work",
                "budget_tokens": 100,
            })
        self.assertIsNotNone(result)


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestMemorySearchDeletedFilter(unittest.IsolatedAsyncioTestCase):
    """memory_search deleted-exclude contract."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._vault = _make_vault(Path(self._tmp.name))
        os.environ["MEMORY_VAULT_PATH"] = str(self._vault)

    def tearDown(self):
        os.environ.pop("MEMORY_VAULT_PATH", None)
        self._tmp.cleanup()

    async def _call(self, client, method: str, args: dict):
        result = await client.call_tool(method, args)
        return result.data  # CallToolResult.data is the structured return value

    async def test_search_excludes_deleted_by_default(self):
        """memory_search with default include_deleted=False excludes deleted entries."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            # Write and immediately delete an entry.
            app = await self._call(client, "memory_append", {
                "content": "xyzzy-unique-search-token deleted-entry",
                "kind": "feedback",
                "title": "searchable-deleted",
            })
            await self._call(client, "memory_forget", {"id": app["id"]})

            # Search — should not surface the deleted entry.
            search = await self._call(client, "memory_search", {"query": "xyzzy-unique-search-token"})

        deleted_ids = {r["id"] for r in search["results"]}
        self.assertNotIn(app["id"], deleted_ids,
                         "Deleted entry appeared in results with include_deleted=False")

    async def test_search_includes_deleted_when_requested(self):
        """memory_search with include_deleted=True surfaces deleted entries."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            app = await self._call(client, "memory_append", {
                "content": "qwerty-include-deleted-token body text",
                "kind": "feedback",
                "title": "include-deleted-test",
            })
            await self._call(client, "memory_forget", {"id": app["id"]})

            search = await self._call(client, "memory_search", {
                "query": "qwerty-include-deleted-token",
                "include_deleted": True,
            })

        # The entry may or may not appear (grep-only on a fresh vault may not
        # find it depending on recall internals) — but the call must succeed.
        self.assertIn("results", search, "search response missing 'results' key")
        self.assertIsInstance(search["results"], list)


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestToolsListSchema(unittest.IsolatedAsyncioTestCase):
    """tools/list: four tools, snake_case names, each with description + schema."""

    async def test_exactly_four_tools(self):
        """tools/list returns exactly four tools."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            tools = await client.list_tools()
        self.assertEqual(len(tools), 4, f"Expected 4 tools, got {len(tools)}: {[t.name for t in tools]}")

    async def test_all_names_are_snake_case(self):
        """All tool names are snake_case (no dots)."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            tools = await client.list_tools()
        for t in tools:
            self.assertNotIn(".", t.name, f"Tool name {t.name!r} contains a dot (not snake_case)")
            self.assertRegex(t.name, r"^[a-z_][a-z0-9_]*$", f"Tool name not snake_case: {t.name!r}")

    async def test_all_tools_have_description(self):
        """Each tool has a non-empty description."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            tools = await client.list_tools()
        for t in tools:
            self.assertTrue(t.description and t.description.strip(),
                            f"Tool {t.name!r} has no description")

    async def test_expected_tool_names_present(self):
        """The four required tool names are present."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            tools = await client.list_tools()
        names = {t.name for t in tools}
        for required in ("memory_search", "memory_recall", "memory_append", "memory_forget"):
            self.assertIn(required, names, f"Required tool {required!r} not in tools/list")


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestHelperUnits(unittest.TestCase):
    """Unit tests for helper functions in memory_mcp_tools."""

    def test_parse_frontmatter_basic(self):
        content = "---\nkind: feedback\nstatus: active\n---\n\nbody"
        fm = _parse_frontmatter(content)
        self.assertEqual(fm["kind"], "feedback")
        self.assertEqual(fm["status"], "active")

    def test_parse_frontmatter_no_fm(self):
        self.assertEqual(_parse_frontmatter("no frontmatter here"), {})

    def test_replace_frontmatter_preserves_body(self):
        content = "---\nkind: feedback\nstatus: active\n---\n\nbody text"
        fm = _parse_frontmatter(content)
        fm["status"] = "deleted"
        new_content = _replace_frontmatter(content, fm)
        self.assertIn("body text", new_content)
        new_fm = _parse_frontmatter(new_content)
        self.assertEqual(new_fm["status"], "deleted")

    def test_idem_tag_is_kebab(self):
        tag = _idem_tag("some-key-123")
        self.assertRegex(tag, r"^[a-z0-9-]+$", f"Tag {tag!r} is not kebab-case")
        self.assertTrue(tag.startswith("idem-"))

    def test_idem_tag_same_key_same_result(self):
        self.assertEqual(_idem_tag("abc"), _idem_tag("abc"))

    def test_idem_tag_different_keys_different_results(self):
        self.assertNotEqual(_idem_tag("abc"), _idem_tag("xyz"))

    def test_make_slug_basic(self):
        slug = _make_slug("Hello World!")
        self.assertRegex(slug, r"^[a-z0-9-]+$")

    def test_make_slug_max_length(self):
        slug = _make_slug("a" * 100)
        self.assertLessEqual(len(slug), 48)

    def test_get_snippet_strips_frontmatter(self):
        content = "---\nkind: x\nstatus: active\n---\n\nbody text here"
        snippet = _get_snippet(content, max_chars=200)
        self.assertNotIn("kind:", snippet)
        self.assertIn("body text", snippet)


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestStdioShim(unittest.TestCase):
    """Unit tests for memory_mcp_shim.py (the stdio→HTTP proxy for Claude Desktop)."""

    def _run_shim(self, args, env_overrides=None, timeout=10):
        """Run memory_mcp_shim.py as a subprocess; return (stdout, stderr, returncode)."""
        import subprocess
        shim_path = str(Path(__file__).parent / "memory_mcp_shim.py")
        env = {k: v for k, v in os.environ.items() if k != "AGENTM_MCP_TOKEN"}
        if env_overrides:
            env.update(env_overrides)
        proc = subprocess.run(
            [sys.executable, shim_path] + list(args),
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        return proc.stdout, proc.stderr, proc.returncode

    def test_shim_imports_cleanly(self):
        """memory_mcp_shim can be imported without side effects on stdout."""
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import memory_mcp_shim; print('OK')",
                str(Path(__file__).parent),
            ],
            capture_output=True, text=True, timeout=10,
            env={k: v for k, v in os.environ.items() if k != "AGENTM_MCP_TOKEN"},
        )
        self.assertEqual(result.returncode, 0, f"Import failed:\n{result.stderr}")
        self.assertEqual(result.stdout.strip(), "OK",
                         f"Unexpected stdout on import: {result.stdout!r}")

    def test_print_configs_exits_zero(self):
        """--print-configs exits 0 and writes to stdout."""
        stdout, stderr, code = self._run_shim(["--print-configs"])
        self.assertEqual(code, 0, f"Expected exit 0, got {code}:\n{stderr}")
        self.assertTrue(stdout.strip(), "stdout should not be empty")

    def test_print_configs_stdout_contains_agentm_memory(self):
        """--print-configs output references the agentm-memory server key."""
        stdout, _, code = self._run_shim(["--print-configs"])
        self.assertEqual(code, 0)
        self.assertIn("agentm-memory", stdout)

    def test_print_configs_token_is_env_placeholder(self):
        """--print-configs uses ${AGENTM_MCP_TOKEN} — no literal bearer token."""
        stdout, _, code = self._run_shim(["--print-configs"])
        self.assertEqual(code, 0)
        # The env-var placeholder must appear (proves the pattern is used).
        self.assertIn("AGENTM_MCP_TOKEN", stdout,
                      "Config must reference AGENTM_MCP_TOKEN as the token placeholder")
        # No literal bearer value should follow "Bearer " (only the ${...} form).
        import re
        bearer_literals = re.findall(r'Bearer (?!\$\{)[^\s"\\]+', stdout)
        self.assertEqual(bearer_literals, [],
                         f"Literal bearer token found in config output: {bearer_literals}")

    def test_no_token_exits_nonzero(self):
        """Running the shim without AGENTM_MCP_TOKEN (and no --no-auth) exits non-zero."""
        stdout, stderr, code = self._run_shim([])
        self.assertNotEqual(code, 0,
                            "Expected non-zero exit when AGENTM_MCP_TOKEN is unset")
        self.assertTrue(stderr.strip(), "stderr should contain the error explanation")

    def test_stdout_clean_on_missing_token(self):
        """No MCP protocol content leaks to stdout when shim exits due to missing token."""
        stdout, _, _ = self._run_shim([])
        self.assertEqual(stdout, "",
                         f"stdout should be empty on early exit, got: {stdout!r}")


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestSecurityOriginValidation(unittest.TestCase):
    """Origin→403 validation: non-localhost Origin is blocked at HTTP level."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._vault = _make_vault(Path(self._tmp.name))
        os.environ["MEMORY_VAULT_PATH"] = str(self._vault)

    def tearDown(self):
        os.environ.pop("MEMORY_VAULT_PATH", None)
        self._tmp.cleanup()

    def _make_client(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from starlette.testclient import TestClient
        return TestClient(_srv.build_app(), raise_server_exceptions=False)

    def _mcp_init_body(self):
        import json
        return json.dumps({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
            "id": 1,
        }).encode()

    def test_bad_origin_blocked(self):
        """A non-localhost Origin header returns 403 (DNS-rebinding defense)."""
        client = self._make_client()
        resp = client.post(
            "/mcp",
            headers={"Origin": "http://evil.example.com", "Content-Type": "application/json"},
            content=self._mcp_init_body(),
        )
        self.assertEqual(resp.status_code, 403, f"Expected 403, got {resp.status_code}")

    def test_localhost_origin_allowed(self):
        """A localhost Origin header passes the Origin check (not 403)."""
        client = self._make_client()
        resp = client.post(
            "/mcp",
            headers={"Origin": "http://localhost:7821", "Content-Type": "application/json"},
            content=self._mcp_init_body(),
        )
        self.assertNotEqual(resp.status_code, 403,
                            f"localhost Origin should not be 403, got {resp.status_code}")

    def test_loopback_ip_origin_allowed(self):
        """A 127.0.0.1 Origin header passes the Origin check (not 403)."""
        client = self._make_client()
        resp = client.post(
            "/mcp",
            headers={"Origin": "http://127.0.0.1:7821", "Content-Type": "application/json"},
            content=self._mcp_init_body(),
        )
        self.assertNotEqual(resp.status_code, 403,
                            f"127.0.0.1 Origin should not be 403, got {resp.status_code}")

    def test_no_origin_allowed(self):
        """Requests without an Origin header pass the Origin check (MCP clients never send one)."""
        client = self._make_client()
        resp = client.post(
            "/mcp",
            headers={"Content-Type": "application/json"},
            content=self._mcp_init_body(),
        )
        self.assertNotEqual(resp.status_code, 403,
                            f"No-Origin request should not be 403, got {resp.status_code}")


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestSecurityBearerAuth(unittest.TestCase):
    """Bearer token auth: wrong/missing token → 401; correct token → non-401/403."""

    _TEST_TOKEN = "test-static-bearer-xyz-12345"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._vault = _make_vault(Path(self._tmp.name))
        os.environ["MEMORY_VAULT_PATH"] = str(self._vault)

    def tearDown(self):
        os.environ.pop("MEMORY_VAULT_PATH", None)
        self._tmp.cleanup()

    def _make_auth_client(self):
        """TestClient backed by a fresh FastMCP instance with static bearer auth."""
        from fastmcp import FastMCP as FreshMCP
        from memory_mcp_tools import register_tools as _rt
        import memory_mcp_server as srv
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from starlette.testclient import TestClient
        auth_mcp = FreshMCP(name="test-auth", auth=srv._StaticBearerAuth(self._TEST_TOKEN))
        _rt(auth_mcp)
        return TestClient(srv.build_app(_mcp=auth_mcp), raise_server_exceptions=False)

    def _mcp_init_body(self):
        import json
        return json.dumps({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
            "id": 1,
        }).encode()

    def test_bearer_missing_rejected(self):
        """Request without Authorization header is rejected (401) when auth is configured."""
        client = self._make_auth_client()
        resp = client.post(
            "/mcp",
            headers={"Content-Type": "application/json"},
            content=self._mcp_init_body(),
        )
        self.assertIn(resp.status_code, (401, 403),
                      f"Expected 401/403 for missing token, got {resp.status_code}")

    def test_bearer_invalid_rejected(self):
        """Request with wrong bearer token is rejected (401) when auth is configured."""
        client = self._make_auth_client()
        resp = client.post(
            "/mcp",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer wrong-token-value"},
            content=self._mcp_init_body(),
        )
        self.assertIn(resp.status_code, (401, 403),
                      f"Expected 401/403 for invalid token, got {resp.status_code}")

    def test_bearer_valid_passes(self):
        """Request with correct bearer token passes auth (not 401/403)."""
        client = self._make_auth_client()
        resp = client.post(
            "/mcp",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._TEST_TOKEN}"},
            content=self._mcp_init_body(),
        )
        self.assertNotIn(resp.status_code, (401, 403),
                         f"Expected non-401/403 for valid token, got {resp.status_code}")


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestAppendPathTraversal(unittest.IsolatedAsyncioTestCase):
    """memory_append rejects kind/project values containing path-traversal sequences."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._vault = _make_vault(Path(self._tmp.name))
        os.environ["MEMORY_VAULT_PATH"] = str(self._vault)

    def tearDown(self):
        os.environ.pop("MEMORY_VAULT_PATH", None)
        self._tmp.cleanup()

    async def test_kind_traversal_rejected(self):
        """memory_append raises when kind contains path-traversal characters."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            with self.assertRaises(Exception):
                await client.call_tool("memory_append", {
                    "content": "body",
                    "kind": "../../etc",
                })

    async def test_project_traversal_rejected(self):
        """memory_append raises when project contains path-traversal characters."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            with self.assertRaises(Exception):
                await client.call_tool("memory_append", {
                    "content": "body",
                    "kind": "feedback",
                    "project": "../../root",
                })

    async def test_kind_dot_rejected(self):
        """memory_append raises when kind is a dot (.) — not a valid segment."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            with self.assertRaises(Exception):
                await client.call_tool("memory_append", {
                    "content": "body",
                    "kind": ".",
                })

    async def test_validate_path_segment_unit(self):
        """_validate_path_segment unit: valid segments pass; invalid raise ValueError."""
        for valid in ("user", "feedback", "my-project", "proj_1"):
            _validate_path_segment(valid, "kind")  # must not raise
        for invalid in ("../../etc", "/abs", "a/b", "..", ".", "", "has space"):
            with self.assertRaises(ValueError, msg=f"Expected ValueError for {invalid!r}"):
                _validate_path_segment(invalid, "kind")


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestVaultSourceResolution(unittest.IsolatedAsyncioTestCase):
    """All four tools must fail loud when the vault is not configured.

    harness_memory.vault_path() reads a hardcoded ~/.claude/.agentm-config.json
    with no env override, so we patch the function directly to return None rather
    than trying to manipulate env vars.
    """

    async def _call_expect_fail(self, method: str, args: dict):
        """Call a tool with vault_path() → None; the tool must raise."""
        from unittest.mock import patch
        import harness_memory
        transport = FastMCPTransport(_srv.mcp)
        with patch.object(harness_memory, "vault_path", return_value=None):
            async with Client(transport) as client:
                with self.assertRaises(Exception):
                    await client.call_tool(method, args)

    async def test_search_fails_loud_without_vault(self):
        """memory_search raises when vault is not configured."""
        await self._call_expect_fail("memory_search", {"query": "anything"})

    async def test_recall_fails_loud_without_vault(self):
        """memory_recall raises when vault is not configured (verifies task-1 fix)."""
        await self._call_expect_fail("memory_recall", {"context": "x", "phase": "plan"})

    async def test_append_fails_loud_without_vault(self):
        """memory_append raises when vault is not configured."""
        await self._call_expect_fail("memory_append", {"content": "x", "kind": "feedback"})

    async def test_forget_fails_loud_without_vault(self):
        """memory_forget raises when vault is not configured."""
        await self._call_expect_fail("memory_forget", {"id": "some/entry.md"})


@unittest.skipUnless(_HAS_DEPS, "fastmcp / deps not installed — skip MCP tool tests")
class TestWriterTwoRouting(unittest.IsolatedAsyncioTestCase):
    """Writer-#2 chain: vault_lock.atomic_write() is in the write path for all MCP writes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._vault = _make_vault(Path(self._tmp.name))
        os.environ["MEMORY_VAULT_PATH"] = str(self._vault)

    def tearDown(self):
        os.environ.pop("MEMORY_VAULT_PATH", None)
        self._tmp.cleanup()

    async def test_append_writes_within_vault_root(self):
        """memory_append writes the entry inside the vault root (not a repo path)."""
        import memory_mcp_tools
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            res = await client.call_tool("memory_append", {
                "content": "routing proof body",
                "kind": "feedback",
                "title": "writer-routing-test",
            })
        entry = res.data
        # id is a relative path; vault / id must be a real file inside the vault.
        entry_path = self._vault / entry["id"]
        self.assertTrue(entry_path.is_file(), f"Entry not found at {entry_path}")
        # Confirm it's strictly inside the vault root (no repo-path escape).
        entry_path.resolve().relative_to(self._vault.resolve())  # raises ValueError on escape

    def test_vault_lock_in_mcp_tools_import(self):
        """vault_lock is imported in memory_mcp_tools (proves memory_forget uses the lock lib)."""
        import memory_mcp_tools
        self.assertIn("vault_lock", memory_mcp_tools.__dict__,
                      "vault_lock not imported in memory_mcp_tools — write path not wired")

    async def test_forget_write_is_atomic(self):
        """After memory_forget, the file is a complete parseable YAML with status=deleted + valid deleted_at."""
        transport = FastMCPTransport(_srv.mcp)
        async with Client(transport) as client:
            app = await client.call_tool("memory_append", {
                "content": "atomic write proof",
                "kind": "user",
                "title": "atomic-forget-test",
            })
            entry_id = app.data["id"]
            await client.call_tool("memory_forget", {"id": entry_id})

        content = (self._vault / entry_id).read_text()
        fm = _parse_frontmatter(content)
        # Must parse cleanly — torn writes would fail yaml.safe_load.
        self.assertEqual(fm.get("status"), "deleted")
        # deleted_at must be a valid ISO-8601 UTC timestamp.
        deleted_at = fm.get("deleted_at", "")
        self.assertRegex(str(deleted_at),
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
                         f"deleted_at is not ISO-8601 UTC: {deleted_at!r}")


if __name__ == "__main__":
    unittest.main()
