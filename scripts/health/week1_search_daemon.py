#!/usr/bin/env python3
"""week1_search_daemon.py — the one process in the run that holds the embedding model.

Backs `agentm-rescope-week1-experiment.md`. Lives for the whole experiment,
across every question and both arms, and owns three things:

  1. **The warm embedder.** This is the reason the daemon exists at all. The
     premise under test is that the agent *iterates* — it reads a result set and
     writes a new query — so query text is not known ahead of time and has to be
     embedded at call time. `claude -p` is one process per question, so an
     embedder living inside it pays a ~4,385ms checkpoint load per question.
     That per-prompt cold load is the exact defect that made this system return
     nothing for four months; reproducing it inside the experiment meant to
     settle the design would be its own kind of joke. Loaded once, here, before
     the first question runs.
  2. **The built indexes.** FTS5 connection and the chunk-embedding matrix, both
     built once and shared.
  3. **The tool-call budget.** The 6-call ceiling is enforced here, where the
     calls actually land, not in the system prompt where it would be a request.
     Call seven gets a refusal no matter what the model decided to do.

Transport is newline-delimited JSON over a Unix domain socket, one request per
connection. `week1_search_shim.py` is the client. Deliberately not HTTP and not
the repo's existing FastMCP daemon at `scripts/memory_mcp_shim.py`: that one
proxies vault *writes* and pulls pydantic and httpx into a process the
experiment spawns once per question, where startup cost is the whole point.

Requests
--------
    {"op": "begin",  "question_id": str, "arm": str}   -> resets the call budget
    {"op": "search", "tool": str, "query": str, "k": int}
    {"op": "stats"}                                    -> calls used this question
    {"op": "ping"} / {"op": "shutdown"}
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import week1_corpus  # noqa: E402

DEFAULT_CALL_BUDGET = 6
TOOL_LEXICAL = "search_lexical"
TOOL_VECTOR = "search_vector"


class SearchDaemon:
    def __init__(self, vault, work_dir, *, arm="A", call_budget=DEFAULT_CALL_BUDGET,
                 exclude_dirs=None, embed_mode="local", verbose=True,
                 variant=week1_corpus.DEFAULT_VARIANT, penalty=None,
                 query_mode="as-is"):
        self.vault = Path(vault)
        self.work_dir = Path(work_dir)
        self.arm = arm
        self.call_budget = call_budget
        self.verbose = verbose
        self.variant = variant
        self.penalty = penalty or None
        self.query_mode = query_mode
        self.weights = week1_corpus.variant_spec(variant)["weights"]
        self.lock = threading.Lock()
        self.question_id = None
        self.calls_used = 0
        self.call_log = []

        paths = week1_corpus.iter_markdown_paths(self.vault, exclude_dirs=exclude_dirs)
        self._log(f"corpus: {len(paths)} .md files under {self.vault}")
        self.conn, self.n_docs = week1_corpus.build_lexical_index(
            self.vault, self.work_dir / week1_corpus.lexical_db_name(variant),
            paths=paths, variant=variant, verbose=verbose
        )
        # Loaded whatever the penalty setting, so an unpenalized run still
        # reports how much of what it served was flagged.
        self.doc_flags = week1_corpus.load_doc_flags(self.conn)
        self._log(
            f"lexical variant {variant} (weights {self.weights}), "
            f"query-mode {query_mode}, "
            f"{len(self.doc_flags)} flagged notes, penalty "
            + (f"{self.penalty}" if self.penalty else "off")
        )
        # SQLite connections are not shareable across threads by default and the
        # accept loop is single-threaded, so this stays on the serving thread.
        self.vector = None
        if arm == "B":
            t0 = time.monotonic()
            encode, doc_paths, vectors = week1_corpus.build_vector_index(
                self.vault, self.work_dir / f"vectors-{embed_mode}.npz",
                paths=paths, mode=embed_mode, verbose=verbose,
            )
            self.vector = (encode, doc_paths, vectors)
            # Prove the warm load actually took, rather than trusting that it
            # did. A second query that still costs seconds means the model is
            # being reloaded and every number this run produces is suspect.
            t1 = time.monotonic()
            week1_corpus.search_vector(encode, doc_paths, vectors, "warm probe", k=1)
            probe_ms = (time.monotonic() - t1) * 1000
            self._log(
                f"vector surface ready in {t1 - t0:.1f}s; warm query probe "
                f"{probe_ms:.0f}ms ({vectors.shape[0]} chunks)"
            )
            if probe_ms > 1000:
                self._log(
                    f"WARNING: a warm query took {probe_ms:.0f}ms. The embedding "
                    f"model is being reloaded per query — results are not "
                    f"measuring what this experiment thinks they measure."
                )

    def _log(self, msg):
        if self.verbose:
            print(f"[week1-daemon] {msg}", file=sys.stderr, flush=True)

    def tools(self):
        return [TOOL_LEXICAL] + ([TOOL_VECTOR] if self.vector else [])

    def handle(self, req):
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "arm": self.arm, "tools": self.tools(),
                    "n_docs": self.n_docs, "call_budget": self.call_budget,
                    "variant": self.variant, "penalty": self.penalty,
                    "query_mode": self.query_mode,
                    "n_flagged": len(self.doc_flags)}
        if op == "begin":
            with self.lock:
                self.question_id = req.get("question_id")
                self.calls_used = 0
                self.call_log = []
            return {"ok": True}
        if op == "stats":
            with self.lock:
                return {"ok": True, "question_id": self.question_id,
                        "calls_used": self.calls_used, "call_log": list(self.call_log)}
        if op == "search":
            return self._search(req)
        return {"ok": False, "error": f"unknown op {op!r}"}

    def _search(self, req):
        tool = req.get("tool")
        query = req.get("query", "")
        k = int(req.get("k") or 5)
        if tool not in self.tools():
            return {"ok": False, "error": f"unknown tool {tool!r} for arm {self.arm}"}

        with self.lock:
            if self.calls_used >= self.call_budget:
                # Refusal, not an exception: the agent should now answer from
                # what it already has, which is the behavior being measured.
                return {
                    "ok": False,
                    "error": "budget_exhausted",
                    "calls_used": self.calls_used,
                    "calls_remaining": 0,
                    "message": (
                        f"Tool-call budget exhausted ({self.call_budget} calls used). "
                        f"No further searches are available for this question. "
                        f"Answer now with the note paths you already believe are "
                        f"correct, or say no answer found."
                    ),
                }
            self.calls_used += 1
            used = self.calls_used

        started = time.monotonic()
        if tool == TOOL_LEXICAL:
            results, note = week1_corpus.search_lexical(
                self.conn, query, k=k, weights=self.weights,
                doc_flags=self.doc_flags, penalty=self.penalty,
                query_mode=self.query_mode,
            )
        else:
            results, note = week1_corpus.search_vector(*self.vector, query=query, k=k)
        elapsed_ms = (time.monotonic() - started) * 1000

        with self.lock:
            self.call_log.append({
                "n": used, "tool": tool, "query": query,
                "n_results": len(results), "ms": round(elapsed_ms, 1),
                # What the agent was shown, so the scorecard can answer "how much
                # of the reading surface was junk" without a second run.
                #
                # The classes come from `doc_flags`, not from each result's own
                # `penalty` key, and that distinction is load-bearing: the key is
                # only attached when a penalty is active, so reading it made an
                # unpenalized run report a junk share of zero — which is what the
                # first control run did, claiming a clean reading surface while
                # serving 5.9% fragments. A control that looks clean by
                # construction is worse than no number at all.
                "result_paths": [r["path"] for r in results],
                "result_penalties": [
                    ",".join(sorted(self.doc_flags.get(r["path"], ()))) for r in results],
            })
        return {
            "ok": True, "results": results, "note": note,
            "calls_used": used, "calls_remaining": self.call_budget - used,
            "elapsed_ms": round(elapsed_ms, 1),
        }

    def serve(self, socket_path, ready_path=None):
        socket_path = str(socket_path)
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(socket_path)
        srv.listen(16)
        self._log(f"listening on {socket_path} (arm {self.arm}, tools {self.tools()})")
        if ready_path:
            Path(ready_path).write_text("ready", encoding="utf-8")
        try:
            while True:
                conn, _ = srv.accept()
                try:
                    data = _recv_line(conn)
                    if not data:
                        continue
                    try:
                        req = json.loads(data)
                    except json.JSONDecodeError as e:
                        resp = {"ok": False, "error": f"bad json: {e}"}
                    else:
                        if req.get("op") == "shutdown":
                            conn.sendall(json.dumps({"ok": True}).encode() + b"\n")
                            return
                        try:
                            resp = self.handle(req)
                        except Exception as e:  # never let one bad call kill the run
                            resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                    conn.sendall(json.dumps(resp).encode("utf-8") + b"\n")
                finally:
                    conn.close()
        finally:
            srv.close()
            if os.path.exists(socket_path):
                os.unlink(socket_path)
            if ready_path and os.path.exists(str(ready_path)):
                os.unlink(str(ready_path))


def _recv_line(conn):
    chunks = []
    while True:
        b = conn.recv(65536)
        if not b:
            break
        chunks.append(b)
        if b.endswith(b"\n"):
            break
    return b"".join(chunks).decode("utf-8").strip()


def request(socket_path, payload, timeout=180.0):
    """Send one request to a running daemon and return its response dict."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(socket_path))
        s.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        return json.loads(_recv_line(s))
    finally:
        s.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vault-path", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--socket", required=True)
    ap.add_argument("--ready-file", default=None)
    ap.add_argument("--arm", default="A", choices=("A", "B"))
    ap.add_argument("--call-budget", type=int, default=DEFAULT_CALL_BUDGET)
    ap.add_argument("--exclude-dir", action="append", default=[])
    ap.add_argument("--embed-mode", default="local", choices=("local", "stub"))
    ap.add_argument("--variant", default=week1_corpus.DEFAULT_VARIANT,
                    choices=sorted(week1_corpus.LEXICAL_VARIANTS))
    ap.add_argument("--query-mode", default="as-is",
                    choices=week1_corpus.QUERY_MODES)
    ap.add_argument("--penalty", default=None,
                    help="JSON object of per-class weights, e.g. "
                         '\'{"fragment": 0.3, "status": 0.6}\'. Omit for none.')
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    daemon = SearchDaemon(
        args.vault_path, args.work_dir, arm=args.arm, call_budget=args.call_budget,
        exclude_dirs=args.exclude_dir, embed_mode=args.embed_mode,
        variant=args.variant, query_mode=args.query_mode,
        penalty=json.loads(args.penalty) if args.penalty else None,
    )
    daemon.serve(args.socket, ready_path=args.ready_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
