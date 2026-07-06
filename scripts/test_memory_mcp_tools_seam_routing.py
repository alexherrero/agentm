#!/usr/bin/env python3
"""memory_mcp_tools.py's V5-14 seam-routing changes (agentm-memory-index.md
/ agentm-memory-system.md), exercised WITHOUT fastmcp — the full existing
test_memory_mcp_tools.py suite is entirely `@skipUnless(_HAS_DEPS, ...)`-gated
and fastmcp isn't installed in every environment, so it can't be relied on
alone to catch a regression here. This file registers the real tool closures
against a minimal stub `mcp` object (a `.tool()` method that's the identity
decorator — no FastMCP transport, no network) to reach the actual
`memory_forget` / `memory_search` logic directly.

Run directly:
    cd scripts && python3 -m unittest test_memory_mcp_tools_seam_routing
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class _StubMCP:
    """The minimal surface `register_tools` needs: `.tool()` as an identity
    decorator, capturing each registered function by its `__name__`."""

    def __init__(self):
        self.tools: dict[str, object] = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


class TestMemoryForgetSeamRouting(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        self._old_env = os.environ.get("MEMORY_VAULT_PATH")
        os.environ["MEMORY_VAULT_PATH"] = str(self.vault)

        import memory_mcp_tools
        # harness_memory caches nothing across calls for vault_path() (it
        # reads the env var fresh); no reload needed.
        self.memory_mcp_tools = memory_mcp_tools
        self.stub = _StubMCP()
        memory_mcp_tools.register_tools(self.stub)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("MEMORY_VAULT_PATH", None)
        else:
            os.environ["MEMORY_VAULT_PATH"] = self._old_env
        self._tmp.cleanup()

    def _seed_entry(self, rel: str, tags: str = "[]") -> Path:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            f"---\nkind: reference\nstatus: active\ncreated: 2026-07-06\n"
            f"tags: {tags}\ngroup: personal\nslug: {p.stem}\nalways_load: false\n"
            f"---\nbody content\n",
            encoding="utf-8",
        )
        return p

    def test_forget_soft_deletes_through_the_seam(self):
        entry = self._seed_entry("personal/reference/note.md")
        memory_forget = self.stub.tools["memory_forget"]
        result = memory_forget(id="personal/reference/note.md")
        self.assertEqual(result["status"], "deleted")
        self.assertFalse(result["already_deleted"])
        # NEVER unlinked — soft-delete only.
        self.assertTrue(entry.is_file())
        content = entry.read_text(encoding="utf-8")
        self.assertIn("status: deleted", content)
        self.assertIn("deleted_at:", content)

    def test_forget_is_idempotent_on_an_already_deleted_entry(self):
        self._seed_entry("personal/reference/note.md")
        memory_forget = self.stub.tools["memory_forget"]
        memory_forget(id="personal/reference/note.md")
        second = memory_forget(id="personal/reference/note.md")
        self.assertTrue(second["already_deleted"])

    def test_forget_rejects_path_traversal(self):
        memory_forget = self.stub.tools["memory_forget"]
        with self.assertRaises(ValueError):
            memory_forget(id="../../etc/passwd")

    def test_forget_write_is_lf_only_no_tmp_remnant(self):
        """The seam-routed write must preserve atomic_write's guarantees:
        no .tmp remnant, no CRLF translation."""
        self._seed_entry("personal/reference/note.md")
        memory_forget = self.stub.tools["memory_forget"]
        memory_forget(id="personal/reference/note.md")
        tmp_remnants = list(self.vault.rglob("*.tmp"))
        self.assertEqual(tmp_remnants, [])
        raw = (self.vault / "personal" / "reference" / "note.md").read_bytes()
        self.assertNotIn(b"\r\n", raw)

    def test_device_local_backend_is_the_wired_write_path(self):
        """Proves the module actually imports the seam backend (the
        equivalent, post-V5-14 check to the retired bare `vault_lock`
        import assertion in test_memory_mcp_tools.py)."""
        self.assertIn("DeviceLocalBackend", self.memory_mcp_tools.__dict__)


class TestFindByIdemTagSeamRouting(unittest.TestCase):
    """_find_by_idem_tag — no fastmcp dependency at all (a plain helper)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "personal" / "reference").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_finds_entry_by_idempotency_tag(self):
        import memory_mcp_tools as mmt
        tag = mmt._idem_tag("my-idempotency-key")
        p = self.vault / "personal" / "reference" / "note.md"
        p.write_text(
            f"---\nkind: reference\nstatus: active\ntags: [{tag}]\nslug: note\n---\nbody\n",
            encoding="utf-8",
        )
        result = mmt._find_by_idem_tag(self.vault, tag)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "personal/reference/note.md")

    def test_no_match_returns_none(self):
        import memory_mcp_tools as mmt
        self.assertIsNone(mmt._find_by_idem_tag(self.vault, mmt._idem_tag("nonexistent")))

    def test_skips_archived_entries(self):
        """Deliberate behavior refinement from the raw rglob this replaced:
        an idempotency lookup has no reason to match an archived entry."""
        import memory_mcp_tools as mmt
        tag = mmt._idem_tag("archived-key")
        archive_dir = self.vault / "personal" / "_archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "old.md").write_text(
            f"---\nkind: reference\nstatus: superseded\ntags: [{tag}]\nslug: old\n---\nbody\n",
            encoding="utf-8",
        )
        self.assertIsNone(mmt._find_by_idem_tag(self.vault, tag))


if __name__ == "__main__":
    unittest.main()
