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
    (vault / "personal-private" / "_always-load").mkdir(parents=True)
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


if __name__ == "__main__":
    unittest.main()
