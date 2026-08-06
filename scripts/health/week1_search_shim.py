#!/usr/bin/env python3
"""week1_search_shim.py — the MCP stdio server `claude -p` spawns, once per question.

Backs `agentm-rescope-week1-experiment.md`. Holds no state and does no work: it
speaks MCP over stdio to Claude Code and forwards every tool call to the
long-lived `week1_search_daemon.py` over a Unix socket.

Why the tools reach the driver as MCP rather than an allowed bash wrapper — the
one real design call in this build:

  **A closed toolset is the experiment's control.** `--mcp-config` plus
  `--strict-mcp-config` plus an `--allowedTools` list naming only these tools
  fixes exactly what an arm can do. Exposing search as a bash wrapper would mean
  allowing the `Bash` tool, and an agent holding `Bash` can `grep -r` the vault
  directly. It would, too — that is what a capable agent does when search
  disappoints. Arm A would then be measuring grep, Arm B would be measuring grep
  plus vectors, and the delta the decision rule turns on would be noise. The
  arms have to differ by exactly one tool, and MCP is what makes that
  enforceable rather than hoped for.

  **The 6-call ceiling has to be a gate, not a request.** Counting lives in the
  daemon, where calls land. A ceiling stated only in the system prompt is a
  behavior of the model under test, and measuring a model against a limit it is
  also responsible for enforcing measures neither.

  **Startup cost decides the implementation.** This process is spawned once per
  question — 120 times across a full run — so it imports `json`, `socket`, and
  `sys`, and nothing else. The repo's existing FastMCP shim
  (`scripts/memory_mcp_shim.py`) would pull pydantic and httpx into every one of
  those spawns, and it is bound to the memory daemon's vault-writing concern,
  which this experiment has no business touching.

Stdout carries the protocol and nothing else; all logging goes to stderr.

Environment:
    WEEK1_SOCKET       path to the daemon's Unix socket (required)
    WEEK1_QUESTION_ID  question id, echoed in daemon logs (optional)
"""
from __future__ import annotations

import json
import os
import socket
import sys

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "week1"

_LEXICAL_DESC = (
    "Full-text search over every note in the vault, ranked with BM25. Matches "
    "words as written — the note has to contain terms you searched for. Supports "
    "FTS5 syntax: bare words are OR-ed, \"quoted phrases\" match in order, AND/OR/NOT "
    "combine terms, and NEAR(a b, 10) finds terms close together. Returns note "
    "paths with a matching snippet."
)
_VECTOR_DESC = (
    "Semantic search over the same notes, ranked by meaning rather than wording. "
    "Finds notes that are about what you asked even when they share no words with "
    "your query. Phrase the query as the idea you are looking for, in a full "
    "sentence, rather than as keywords. Returns note paths without snippets."
)


def _log(msg):
    print(f"[week1-shim] {msg}", file=sys.stderr, flush=True)


def _daemon(payload, timeout=180.0):
    sock_path = os.environ.get("WEEK1_SOCKET")
    if not sock_path:
        return {"ok": False, "error": "WEEK1_SOCKET is not set"}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        s.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        chunks = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
            if b.endswith(b"\n"):
                break
        return json.loads(b"".join(chunks).decode("utf-8").strip())
    except Exception as e:
        return {"ok": False, "error": f"daemon unreachable: {type(e).__name__}: {e}"}
    finally:
        s.close()


def _tool_defs():
    info = _daemon({"op": "ping"})
    available = info.get("tools", ["search_lexical"]) if info.get("ok") else ["search_lexical"]
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "k": {"type": "integer", "description": "How many results to return (default 5).",
                  "default": 5},
        },
        "required": ["query"],
    }
    defs = []
    for name, desc in (("search_lexical", _LEXICAL_DESC), ("search_vector", _VECTOR_DESC)):
        if name in available:
            defs.append({"name": name, "description": desc, "inputSchema": schema})
    return defs


def _format_results(resp):
    if not resp.get("ok"):
        if resp.get("error") == "budget_exhausted":
            return resp.get("message", "Tool-call budget exhausted."), True
        return f"Search error: {resp.get('error')}", True
    lines = []
    if resp.get("note"):
        lines.append(f"note: {resp['note']}")
    results = resp.get("results") or []
    if not results:
        lines.append("No matching notes.")
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['path']}  (score {r['score']})")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
    lines.append(f"[{resp.get('calls_remaining', '?')} tool calls remaining for this question]")
    return "\n".join(lines), False


def _handle(req):
    """Return a response dict, or None for notifications (which take no reply)."""
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                # Echo the client's version when it offers one; a shim that
                # insists on its own is a shim that breaks on the next release.
                "protocolVersion": (req.get("params") or {}).get(
                    "protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
            },
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _tool_defs()}}
    if method == "tools/call":
        params = req.get("params") or {}
        args = params.get("arguments") or {}
        resp = _daemon({
            "op": "search", "tool": params.get("name"),
            "query": args.get("query", ""), "k": args.get("k") or 5,
        })
        text, is_error = _format_results(resp)
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
        }
    if req_id is None:
        return None
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            _log(f"bad json from client: {e}")
            continue
        try:
            resp = _handle(req)
        except Exception as e:
            _log(f"handler error: {type(e).__name__}: {e}")
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
