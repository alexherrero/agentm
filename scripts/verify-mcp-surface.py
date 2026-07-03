#!/usr/bin/env python3
"""verify-mcp-surface.py — the MCP memory-tool round-trip + the dead-surface
fate (R1.6 / agentmEngine#2).

An in-process FastMCP client (FastMCPTransport — no HTTP binding, no daemon)
against a scratch vault, driving the REAL server object from
memory_mcp_server.py:

  A. memory_append -> memory_search -> memory_forget round-trip.
  B. Soft-delete semantics: a forgotten entry stays at its ORIGINAL path with
     status: deleted in frontmatter (never unlinked, never moved to
     _archive/ -- that was an earlier design iteration; the shipped contract
     is an in-place status flip, per memory_mcp_tools.py's own docstring:
     "the backing file is NEVER unlinked ... hard-delete is an explicit
     non-goal"). Excluded from memory_search by default; included with
     include_deleted=True.
  C. The three dead-recall-surface fates, verified against what R0.9 actually
     shipped (not the plan's original either/or framing, which predates the
     decision):
       - memory_recall (MCP tool): RETIRED. Not in tools/list; calling it
         raises ToolError("Unknown tool").
       - process_seam.recall_here: RETIRED. hasattr() is False.
       - harness_memory.phase_recall() / documenter_context(): NEITHER wired
         nor retired -- a PERMANENT STUB by V5-3 design (context now comes
         from these same MCP tools instead). This is the locked, tested
         contract in test_harness_memory_documenter.py
         (test_documenter_context_returns_rc1_v5_3) -- this script asserts
         the same contract holds, it does not re-litigate the V5-3 decision.

VERIFY_MCP_SURFACE_FAULT=1 deletes the scratch vault directory between
append and search and asserts memory_search raises (ToolError) rather than
silently returning empty/stale results — _require_vault() already fails
loud here (harness_memory.vault_path() returns None for a missing env-var
path), so this mode validates that invariant holds, not a new fix.

Requires the `fastmcp` package. Graceful-skip (exit 0, SKIP note) if it
isn't installed — matches test_memory_mcp_tools.py's existing convention,
which is currently the ONLY thing in this repo that exercises the MCP tool
surface (and is itself always-skipped without this dependency present).

Usage:   python3 scripts/verify-mcp-surface.py
         VERIFY_MCP_SURFACE_FAULT=1 python3 scripts/verify-mcp-surface.py
Exit:    0 iff every check passes (or the whole suite gracefully skips).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

try:
    from fastmcp.client import Client, FastMCPTransport  # noqa: F401
except ImportError:
    print("verify-mcp-surface: SKIP — fastmcp not installed (pip install fastmcp)")
    sys.exit(0)

sys.path.insert(0, str(HERE))
import memory_mcp_server as srv  # noqa: E402

# process_seam's own retirement check runs out-of-process (subprocess), not a
# top-level `import process_seam` here — a verify script statically importing
# the seam would itself trip check-process-seam-import-direction.sh (LC-4:
# the seam's only legitimate in-repo importer is its own test suite; this
# script is seam-adjacent but not one of those tests). Checking recall_here's
# absence from the outside, black-box, is architecturally the right shape for
# a verify script anyway.
def _recall_here_is_retired() -> bool:
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c", "import process_seam; print(hasattr(process_seam, 'recall_here'))"],
        cwd=str(HERE), capture_output=True, text=True,
    )
    return result.stdout.strip() == "False"

PASS = 0
FAIL = 0
RESULTS: list[str] = []


def ok(desc: str) -> None:
    global PASS
    RESULTS.append(f"  PASS  {desc}")
    PASS += 1


def bad(desc: str, detail: str) -> None:
    global FAIL
    RESULTS.append(f"  FAIL  {desc}\n          ↳ {detail}")
    FAIL += 1


def make_vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / "personal" / "_always-load").mkdir(parents=True)
    return vault


async def run_normal(vault: Path) -> None:
    transport = FastMCPTransport(srv.mcp)
    async with Client(transport) as client:
        # ── A. append -> search -> forget round-trip ────────────────────────
        appended = await client.call_tool("memory_append", {
            "content": "deployment runbook staging gate lives at ops/deploy.md",
            "kind": "reference",
            "title": "deploy-runbook",
        })
        entry_id = appended.data["id"]
        if entry_id:
            ok("A. memory_append: returns an entry id")
        else:
            bad("A. memory_append: returns an entry id", f"got: {appended.data}")

        searched = await client.call_tool("memory_search", {"query": "deployment runbook staging gate"})
        hits = [r["id"] for r in searched.data["results"]]
        if entry_id in hits:
            ok("A. memory_search: the appended entry is surfaced")
        else:
            bad("A. memory_search: the appended entry is surfaced", f"hits={hits}")

        forgotten = await client.call_tool("memory_forget", {"id": entry_id, "reason": "test cleanup"})
        if forgotten.data.get("status") == "deleted":
            ok("A. memory_forget: status flips to deleted")
        else:
            bad("A. memory_forget: status flips to deleted", f"got: {forgotten.data}")

        # ── B. soft-delete semantics ─────────────────────────────────────────
        entry_path = vault / entry_id
        if entry_path.is_file():
            ok("B. soft-delete: backing file still exists at its original path")
        else:
            bad("B. soft-delete: backing file still exists at its original path", f"missing: {entry_path}")
        archive_dir = vault / "_archive"
        if not archive_dir.exists() or not any(archive_dir.rglob("*")):
            ok("B. soft-delete: nothing moved to _archive/ (in-place status flip, not a relocation)")
        else:
            bad("B. soft-delete: nothing moved to _archive/", f"found: {list(archive_dir.rglob('*'))}")

        excluded = await client.call_tool("memory_search", {"query": "deployment runbook staging gate"})
        excluded_hits = [r["id"] for r in excluded.data["results"]]
        if entry_id not in excluded_hits:
            ok("B. memory_search excludes the deleted entry by default")
        else:
            bad("B. memory_search excludes the deleted entry by default", f"hits={excluded_hits}")

        included = await client.call_tool("memory_search", {"query": "deployment runbook staging gate", "include_deleted": True})
        included_hits = [r["id"] for r in included.data["results"]]
        if entry_id in included_hits:
            ok("B. memory_search finds it again with include_deleted=True")
        else:
            bad("B. memory_search finds it again with include_deleted=True", f"hits={included_hits}")

        # ── C. dead-surface fate ─────────────────────────────────────────────
        tools = sorted(t.name for t in await client.list_tools())
        if "memory_recall" not in tools:
            ok("C. memory_recall: retired — absent from tools/list")
        else:
            bad("C. memory_recall: retired — absent from tools/list", f"tools={tools}")
        try:
            await client.call_tool("memory_recall", {"query": "x"})
            bad("C. memory_recall: calling it raises (Unknown tool)", "did not raise")
        except Exception as e:  # ToolError from fastmcp
            if "Unknown tool" in str(e):
                ok("C. memory_recall: calling it raises (Unknown tool)")
            else:
                bad("C. memory_recall: calling it raises (Unknown tool)", f"raised {type(e).__name__}: {e}")

    if _recall_here_is_retired():
        ok("C. process_seam.recall_here: retired (AttributeError)")
    else:
        bad("C. process_seam.recall_here: retired (AttributeError)", "attribute still present")

    import harness_memory as hm
    out, rc = hm.documenter_context("agentm", fmt="text")
    if out == "" and rc == 1:
        ok("C. documenter_context: locked V5-3 stub contract holds (empty, rc=1)")
    else:
        bad("C. documenter_context: locked V5-3 stub contract holds (empty, rc=1)", f"got: ({out!r}, {rc})")


async def run_fault(vault: Path) -> None:
    transport = FastMCPTransport(srv.mcp)
    async with Client(transport) as client:
        await client.call_tool("memory_append", {
            "content": "fault-mode fixture entry", "kind": "reference", "title": "fault-fixture",
        })
        shutil.rmtree(vault)
        try:
            result = await client.call_tool("memory_search", {"query": "fault-mode fixture entry"})
            bad(
                "fault: memory_search raises when the vault has vanished mid-operation",
                f"returned instead of raising: {result.data}",
            )
        except Exception:
            ok("fault: memory_search raises when the vault has vanished mid-operation")


async def main() -> int:
    tmp = tempfile.mkdtemp()
    vault = make_vault(Path(tmp))
    os.environ["MEMORY_VAULT_PATH"] = str(vault)
    print(f"verify-mcp-surface: scratch vault = {vault}")
    try:
        if os.environ.get("VERIFY_MCP_SURFACE_FAULT") == "1":
            await run_fault(vault)
        else:
            await run_normal(vault)
    finally:
        os.environ.pop("MEMORY_VAULT_PATH", None)
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    for line in RESULTS:
        print(line)
    print()
    print(f"verify-mcp-surface: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
