#!/usr/bin/env python3
"""retrieval_scorecard — score a gold set against the daemon, no model in the loop.

The layer this measures, and the one it does not
-----------------------------------------------
`week1_retrieval_experiment.py` and `week3_daemon_retest.py` drive an *agent*
over `memory_search` and score what it concluded. That is the layer users live
in, and it is the layer where the alias backfill was convicted: the aliased
corpus was slightly better at the tool level and 3.85 points worse once an
agent read the results.

This scores the tool alone — query in, ranked list out, hit if any expected
path is in the top k. No driver, no retries, no judgment. That makes it
deterministic, so one run is exact and replicates buy nothing, and it makes it
cheap enough to run on every retrieval change. It is the harness shape AgentKV
used to price hybrid search, which is why it exists here: their numbers and
ours are not comparable until both are measured at the same layer.

Use both. A retrieval-layer win is necessary and not sufficient; we have one
documented case of the two layers disagreeing about the same treatment.

On correct rejection
--------------------
For a negative question the honest answer is "no such memory exists," and this
harness reports how often the tool returned nothing at all. That is a weaker
property than it sounds, and the report says so rather than dressing it up:
FTS5 ANDs its terms, so an empty result means a term was missing from the
index, not that anything judged the corpus unable to answer. There is no score
threshold in this stack today. Rejection is currently an agent-layer behavior,
and a retrieval-layer rejection number here is a floor, not a verdict.

Usage:
    python3 scripts/health/retrieval_scorecard.py --gold-set <path> [--k 5]
        [--json out.json] [--stratum NAME]
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_MEMORY_SCRIPTS = _HERE.parent.parent / "harness" / "skills" / "memory" / "scripts"
if str(_MEMORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SCRIPTS))
import recall  # noqa: E402  (production query-term extraction)


def to_query(question: str) -> str:
    """Reduce a question to the terms the production hook would search for.

    This is not a nicety, it is the difference between measuring the system and
    measuring a pipeline nobody runs. FTS5 ANDs its terms, so handing it a
    fifteen-word question demands all fifteen appear in one note; scored that
    way the whole gold set reads 3.1% and the number is an artifact of the
    harness. `recall.py` is imported rather than reimplemented so the scorecard
    cannot drift from the caller it claims to model — if the extraction changes,
    this moves with it.
    """
    return recall._daemon_query_terms(question)


def search(query: str, k: int) -> tuple[list[str], float]:
    """Top-k paths for a query, and the wall time in milliseconds."""
    started = time.monotonic()
    proc = subprocess.run(["agentmd", "search", "-json", "-k", str(k), query],
                          capture_output=True, text=True)
    elapsed = (time.monotonic() - started) * 1000.0
    out = proc.stdout
    brace = out.find("{")
    if brace < 0:
        return [], elapsed
    try:
        doc = json.loads(out[brace:])
    except json.JSONDecodeError:
        return [], elapsed
    return [r.get("path", "") for r in (doc.get("results") or [])], elapsed


def score(entries: list[dict], k: int) -> dict:
    rows = []
    for e in entries:
        expected = e.get("expected_note_paths") or []
        query = to_query(e["question"])
        ranked, ms = search(query, k) if query else ([], 0.0)
        hits = [p for p in expected if p in ranked]
        first = min((ranked.index(p) + 1 for p in hits), default=None)
        rows.append({
            "id": e["id"], "stratum": e["stratum"], "question": e["question"],
            "query": query,
            "is_negative": not expected,
            # A negative is "correct" when the tool returned nothing at all.
            # See the module docstring for why that is a floor, not a verdict.
            "correct_rejection": (not ranked) if not expected else None,
            "hit": bool(hits) if expected else None,
            "first_hit_rank": first,
            "returned": len(ranked),
            "expected": expected,
            "top": ranked[:k],
            "ms": round(ms, 1),
            "alias_overlap": e.get("alias_overlap"),
        })
    return {"rows": rows}


def render(result: dict, k: int) -> str:
    rows = result["rows"]
    by = collections.defaultdict(list)
    for r in rows:
        by[r["stratum"]].append(r)

    out = [f"retrieval scorecard — lexical (FTS5), k={k}, no model in the loop",
           "queries reduced by recall._daemon_query_terms, as the prompt-submit hook does", ""]
    out.append(f"{'stratum':<20}{'n':>4}{'hit@k':>9}{'rate':>9}")
    out.append("-" * 42)
    scored = [r for r in rows if not r["is_negative"]]
    for stratum in sorted(by):
        rs = by[stratum]
        pos = [r for r in rs if not r["is_negative"]]
        if pos:
            h = sum(1 for r in pos if r["hit"])
            out.append(f"{stratum:<20}{len(pos):>4}{h:>9}{h/len(pos):>9.1%}")
        else:
            c = sum(1 for r in rs if r["correct_rejection"])
            out.append(f"{stratum:<20}{len(rs):>4}{c:>9}{c/len(rs):>9.1%}  (returned nothing)")
    out.append("-" * 42)
    hits = sum(1 for r in scored if r["hit"])
    out.append(f"{'OVERALL R@' + str(k):<20}{len(scored):>4}{hits:>9}{hits/len(scored):>9.1%}")

    negs = [r for r in rows if r["is_negative"]]
    if negs:
        c = sum(1 for r in negs if r["correct_rejection"])
        out.append(f"{'rejection (floor)':<20}{len(negs):>4}{c:>9}{c/len(negs):>9.1%}")

    lat = sorted(r["ms"] for r in rows)
    out += ["", f"latency  p50 {lat[len(lat)//2]:.1f}ms   p90 "
                f"{lat[int(len(lat)*0.9)]:.1f}ms   max {lat[-1]:.1f}ms  (includes CLI startup)"]

    overlap = [r for r in rows if r.get("alias_overlap") is not None]
    if overlap:
        out.append("")
        for flag, label in ((True, "alias-overlapping"), (False, "alias-independent")):
            grp = [r for r in overlap if r["alias_overlap"] is flag and not r["is_negative"]]
            if grp:
                h = sum(1 for r in grp if r["hit"])
                out.append(f"  research-corpus, {label:<18} {h}/{len(grp)}  {h/len(grp):.1%}")
        out.append("  (split deliberately: the session that wrote those notes' aliases also")
        out.append("   wrote these questions, so the overlapping half grades its own author)")

    misses = [r for r in scored if not r["hit"]]
    if misses:
        out += ["", f"misses ({len(misses)}):"]
        for r in misses:
            out.append(f"  [{r['id']}] {r['question'][:66]}")
            out.append(f"        query  {r['query']}")
            out.append(f"        wanted {r['expected'][0].split('/')[-1][:56]}")
            got = r["top"][0].split("/")[-1][:56] if r["top"] else "(nothing returned)"
            out.append(f"        got    {got}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gold-set", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--stratum", default=None, help="score only this stratum")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)

    doc = json.loads(Path(args.gold_set).read_text(encoding="utf-8"))
    entries = doc["entries"] if isinstance(doc, dict) else doc
    if args.stratum:
        entries = [e for e in entries if e["stratum"] == args.stratum]
    if not entries:
        print("no entries to score", file=sys.stderr)
        return 2

    result = score(entries, args.k)
    result["gold_set"] = Path(args.gold_set).name
    result["corpus"] = (doc.get("$corpus") or {}).get("name") if isinstance(doc, dict) else None
    result["k"] = args.k
    print(render(result, args.k))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
