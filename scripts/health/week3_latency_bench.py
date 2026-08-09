#!/usr/bin/env python3
"""week3_latency_bench.py — is the daemon actually faster than the Python shim?

The retest's in-run `ms` figures cannot answer this. The shim times a full HTTP
round trip while two Opus drivers are streaming on the same laptop; week 1's
daemon timed the search alone, inside its own process, and the socket transport
never appeared in the number. Comparing them directly would be comparing a
round trip under load against a function call at rest.

So this measures both the same way, on an idle machine, over the same queries —
every query the 2026-08-06 Opus run actually wrote — and decomposes the daemon's
number so the transport is visible rather than baked in:

    ping        a JSON-RPC round trip that does no searching. The floor: what
                any call costs before the index is touched.
    search      the full round trip for a real query.
    search-ping the daemon's own work, by subtraction.

The Python arm is week 1's `week1_corpus.search_lexical`, called in-process
against the same snapshot, which is exactly how week 1 measured it.

Index build time is reported too, because a resident daemon's real advantage is
not per-query microseconds — it is that the index already exists when the
session starts.

    python3 scripts/health/week3_latency_bench.py \\
        --daemon-url http://127.0.0.1:51468/mcp \\
        --vault ~/.agentm/corpus-snapshots/week3-AL \\
        --week1-report scripts/health/results/week1/opus-arm-a.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import week1_corpus as wc  # noqa: E402


def rpc(url, payload, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return (time.perf_counter() - t0) * 1000, out


def describe(name, samples):
    s = sorted(samples)
    return {
        "arm": name, "n": len(s),
        "mean_ms": round(statistics.mean(s), 2),
        "median_ms": round(statistics.median(s), 2),
        "p90_ms": round(s[int(0.9 * (len(s) - 1))], 2),
        "max_ms": round(max(s), 2),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--daemon-url", required=True)
    ap.add_argument("--vault", required=True, help="the corpus copy the daemon serves")
    ap.add_argument("--week1-report", required=True)
    ap.add_argument("--work-dir", default=None, help="where the Python index is built")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    w1 = json.loads(Path(args.week1_report).read_text(encoding="utf-8"))
    queries = [c["query"] for r in w1["per_question"]
               for c in (r.get("tool_call_log") or [])]
    print(f"[bench] {len(queries)} recorded queries")

    # --- the daemon, over HTTP ---------------------------------------------
    pings = [timed(rpc, args.daemon_url,
                   {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0]
             for _ in range(50)]
    daemon_ms = []
    for q in queries:
        ms, _ = timed(rpc, args.daemon_url, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "memory_search", "arguments": {"query": q, "k": 5}}})
        daemon_ms.append(ms)

    # --- week 1's Python lexical layer, in-process --------------------------
    work = Path(args.work_dir or (Path.home() / ".agentm" / "week3-retest" / "pybench"))
    work.mkdir(parents=True, exist_ok=True)
    vault = Path(args.vault)
    paths = wc.iter_markdown_paths(vault)
    db = work / wc.lexical_db_name(wc.DEFAULT_VARIANT)
    cold = db.exists()
    build_ms, (conn, n_docs) = timed(
        wc.build_lexical_index, vault, db, paths=paths,
        variant=wc.DEFAULT_VARIANT, verbose=False)
    doc_flags = wc.load_doc_flags(conn)
    weights = wc.variant_spec(wc.DEFAULT_VARIANT)["weights"]
    # Two Python configurations, because only one of them is a fair comparison.
    # The daemon's penalty is compiled in, and a penalty means fetching 200 rows
    # and re-ranking them instead of fetching five. Timing the daemon against a
    # Python call that fetches five would credit it with work it never did.
    python_plain, python_penalty = [], []
    for q in queries:
        ms, _ = timed(wc.search_lexical, conn, q, k=5, weights=weights,
                      doc_flags=doc_flags, penalty=None)
        python_plain.append(ms)
        ms, _ = timed(wc.search_lexical, conn, q, k=5, weights=weights,
                      doc_flags=doc_flags, penalty=dict(wc.DEFAULT_PENALTY_WEIGHTS))
        python_penalty.append(ms)
    conn.close()

    ping = describe("daemon ping (transport floor)", pings)
    dmn = describe("daemon memory_search (round trip)", daemon_ms)
    pyn = describe("week-1 Python, no penalty (what week 1 timed)", python_plain)
    pyp = describe("week-1 Python, penalty on (like-for-like)", python_penalty)
    net = round(dmn["median_ms"] - ping["median_ms"], 2)

    out = {
        "n_queries": len(queries),
        "corpus": {"vault_name": vault.name, "n_docs": n_docs},
        "arms": [ping, dmn, pyn, pyp],
        "daemon_work_median_ms": net,
        "python_index_build_ms": round(build_ms, 1),
        "python_index_was_cached": cold,
    }
    for a in (ping, dmn, pyn, pyp):
        print(f"  {a['arm']:<44} n={a['n']:<4} median {a['median_ms']:>8.2f}ms  "
              f"mean {a['mean_ms']:>8.2f}  p90 {a['p90_ms']:>8.2f}  max {a['max_ms']:>9.2f}")
    print(f"\n  daemon search work, transport subtracted: {net:.2f}ms median")
    print(f"  Python index build over {n_docs} notes: {build_ms / 1000:.1f}s"
          + ("  (reused a cached index — delete it for a cold number)" if cold else ""))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        print(f"\n[bench] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
