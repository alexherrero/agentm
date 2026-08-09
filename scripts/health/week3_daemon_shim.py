#!/usr/bin/env python3
"""week3_daemon_shim.py — the stdio MCP surface `claude -p` talks to, in front of the daemon.

The week-3 retest measures the Go daemon's own `memory_search`, so this process
does no searching. It forwards `initialize`, `tools/list` and `tools/call`
straight to `agentmd`'s HTTP `/mcp` endpoint and hands the answer back
unchanged. What it adds is the two things a scorecard cannot be written without
and the daemon deliberately does not do:

**A call ceiling.** Six per question, enforced here because the daemon has no
concept of a question. The count lives in the log file rather than in this
process, so a second shim instance — Claude Code is free to start one — shares
the same budget instead of doubling it.

**A record of what was served.** Every call is appended to `$WEEK3_CALL_LOG` as
one JSON line: the query, how many results came back, which of them the daemon
had demoted, and how long the daemon took. That file is the scorecard's
`tool_call_log`, and it is what the runner checks the driver transcript against.

`memory_capture` is filtered out of `tools/list`. The corpus under measurement
is a frozen snapshot, and a tool that can write to it does not belong in front
of a driver whose whole job is to search it.

Environment:

    WEEK3_DAEMON_URL    http://127.0.0.1:<port>/mcp
    WEEK3_CALL_LOG      where to append the per-call record
    WEEK3_CALL_BUDGET   how many calls to serve before refusing (default 6)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

DAEMON_URL = os.environ.get("WEEK3_DAEMON_URL", "")
CALL_LOG = os.environ.get("WEEK3_CALL_LOG", "")
CALL_BUDGET = int(os.environ.get("WEEK3_CALL_BUDGET", "6"))

SEARCH_TOOL = "memory_search"
_TIMEOUT = 120.0


def _post(payload, timeout=_TIMEOUT):
    """One JSON-RPC round trip to the daemon."""
    req = urllib.request.Request(
        DAEMON_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _served_calls():
    """How many calls this question has already spent, across every shim process."""
    if not CALL_LOG or not os.path.exists(CALL_LOG):
        return 0
    n = 0
    with open(CALL_LOG, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("served"):
                    n += 1
            except json.JSONDecodeError:
                continue
    return n


def _log(record):
    if not CALL_LOG:
        return
    record["pid"] = os.getpid()
    with open(CALL_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
        fh.flush()


def _tool_error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _handle(req):
    method = req.get("method")
    if method == "initialize":
        # Forwarded rather than synthesized: the daemon's own `instructions`
        # string is part of the surface under test.
        return _post(req).get("result", {})
    if method == "ping":
        return {}
    if method == "tools/list":
        result = _post(req).get("result", {}) or {}
        tools = [t for t in (result.get("tools") or []) if t.get("name") == SEARCH_TOOL]
        return {"tools": tools}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        if name != SEARCH_TOOL:
            _log({"served": False, "refused": "wrong_tool", "tool": name})
            return _tool_error(
                f"this run serves {SEARCH_TOOL} only; {name!r} is not available")
        used = _served_calls()
        if used >= CALL_BUDGET:
            _log({"served": False, "refused": "budget_exhausted",
                  "query": (params.get("arguments") or {}).get("query")})
            return _tool_error(
                f"tool-call budget exhausted: {CALL_BUDGET} calls already used "
                f"for this question. Answer with what you have.")
        started = time.monotonic()
        try:
            resp = _post(req)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            _log({"served": False, "refused": "daemon_error", "error": str(exc)})
            return _tool_error(f"search backend error: {exc}")
        elapsed_ms = (time.monotonic() - started) * 1000
        result = resp.get("result") or {}
        payload = (result.get("structuredContent") or {})
        results = payload.get("results") or []
        args = params.get("arguments") or {}
        _log({
            "served": True,
            "n": used + 1,
            "tool": SEARCH_TOOL,
            "query": args.get("query"),
            "k": args.get("k"),
            "after": args.get("after"),
            "before": args.get("before"),
            "n_results": len(results),
            "matched": payload.get("matched"),
            "note": payload.get("note"),
            "ms": round(elapsed_ms, 1),
            "result_paths": [r.get("path") for r in results],
            # The daemon's own verdict on each row, so "how much of the reading
            # surface was demoted junk" is answerable from the scorecard without
            # a second pass over the corpus.
            "result_penalties": [r.get("penalty", "") or "" for r in results],
        })
        return result
    return None


def main():
    if not DAEMON_URL:
        print("week3_daemon_shim: WEEK3_DAEMON_URL is unset", file=sys.stderr)
        return 2
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Notifications carry no id and get no reply.
        if "id" not in req:
            continue
        resp = {"jsonrpc": "2.0", "id": req["id"]}
        try:
            result = _handle(req)
        except Exception as exc:  # noqa: BLE001 — an unhandled error here would
            # hang the driver waiting on a reply that never comes, which reads as
            # a model failure rather than a shim failure.
            resp["error"] = {"code": -32603, "message": f"shim error: {exc}"}
        else:
            if result is None:
                resp["error"] = {"code": -32601,
                                 "message": f"unknown method {req.get('method')}"}
            else:
                resp["result"] = result
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
