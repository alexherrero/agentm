#!/usr/bin/env python3
"""week3_miss_probe.py — do the six shared misses have vocabulary yet?

`dt12`, `ep09`, `ep12`, `ng02`, `pp05`, `pp07` missed under every ranking
configuration the 2026-08-07 campaign tried, and the diagnosis was that the
gold note never contained the words the operator would later ask with. Three of
them never surfaced the note at any depth; the other three sat at rank 42, 50
and 238, behind legitimately-matching documents rather than behind fragments.
Ranking could not reach them. `aliases` was the named fix.

This is the deterministic half of checking whether the backfill delivered one.
It asks each daemon directly, so it costs nothing and can be re-run:

1. Does the gold note carry aliases in the AL copy, and what are they?
2. Replaying the exact queries the 2026-08-06 Opus run wrote for that question,
   how deep does the gold note sit in each copy — at k=50, the daemon's ceiling?
3. Do the note's own aliases retrieve it, when searched as written?

It measures the tool, not the answer: it holds the agent's queries fixed, so it
cannot see how a changed result set changes the next query. That is what the
live replicates are for.

    python3 scripts/health/week3_miss_probe.py \\
        --al-url http://127.0.0.1:51468/mcp --no-url http://127.0.0.1:51469/mcp \\
        --week1-report scripts/health/results/week1/opus-arm-a.json
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

SHARED_MISSES = ["dt12", "ep09", "ep12", "ng02", "pp05", "pp07"]
PROBE_K = 50


def search(url, query, k=PROBE_K):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "memory_search",
                          "arguments": {"query": query, "k": k}}}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    sc = ((body.get("result") or {}).get("structuredContent") or {})
    return [r.get("path") for r in (sc.get("results") or [])], sc.get("matched")


def rank_of(paths, expected):
    for i, p in enumerate(paths, 1):
        if p in expected:
            return i
    return None


_NOTE_CACHE = {}


def _split_note(vault, rel):
    """`(alias line, body)` lowercased, cached — the attribution reads both."""
    if rel in _NOTE_CACHE:
        return _NOTE_CACHE[rel]
    p = Path(vault, rel)
    text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    alias_line, body = "", text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        fm = text[:end] if end > 0 else text[:2000]
        m = re.search(r"^aliases:\s*\[(.*)\]\s*$", fm, re.M)
        alias_line = m.group(1) if m else ""
        body = text[end + 4:] if end > 0 else text
    _NOTE_CACHE[rel] = (alias_line.lower(), body.lower())
    return _NOTE_CACHE[rel]


def read_aliases(vault, rel):
    p = Path(vault, rel)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    fm = text[:end] if end > 0 else text[:2000]
    m = re.search(r"^aliases:\s*\[(.*)\]\s*$", fm, re.M)
    if not m:
        return []
    return [a.strip() for a in m.group(1).split(",") if a.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--al-url", required=True)
    ap.add_argument("--no-url", required=True)
    ap.add_argument("--al-vault", required=True)
    ap.add_argument("--gold-set", required=True)
    ap.add_argument("--week1-report", required=True,
                    help="the 2026-08-06 Opus Arm A scorecard, for its recorded queries")
    ap.add_argument("--surface-replay", action="store_true",
                    help="also replay every recorded query against both copies, to "
                         "size how much of the reading surface the aliases moved")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    gold = json.loads(Path(args.gold_set).read_text(encoding="utf-8"))
    gold = gold["entries"] if isinstance(gold, dict) else gold
    by_id = {g["id"]: g for g in gold}

    w1 = json.loads(Path(args.week1_report).read_text(encoding="utf-8"))
    queries_by_id = {
        row["id"]: [c["query"] for c in (row.get("tool_call_log") or [])]
        for row in w1["per_question"]
    }

    out = []
    for qid in SHARED_MISSES:
        g = by_id[qid]
        expected = set(g["expected_note_paths"])
        entry = {
            "id": qid, "stratum": g["stratum"], "question": g["question"],
            "expected": sorted(expected),
            "aliases_in_AL": {rel: read_aliases(args.al_vault, rel)
                              for rel in sorted(expected)},
            "week1_queries": [],
            "alias_self_probe": [],
        }
        for q in queries_by_id.get(qid, []):
            al_paths, al_matched = search(args.al_url, q)
            no_paths, no_matched = search(args.no_url, q)
            entry["week1_queries"].append({
                "query": q,
                "AL": {"rank": rank_of(al_paths, expected), "n": len(al_paths),
                       "matched": al_matched},
                "NO": {"rank": rank_of(no_paths, expected), "n": len(no_paths),
                       "matched": no_matched},
            })
        # Do the note's own aliases retrieve it? If a phrasing written *onto* the
        # note does not find it, the column is not doing its job at all.
        for rel, aliases in entry["aliases_in_AL"].items():
            for alias in (aliases or [])[:3]:
                al_paths, _ = search(args.al_url, alias)
                entry["alias_self_probe"].append(
                    {"alias": alias, "rank_in_AL": rank_of(al_paths, {rel})})
        out.append(entry)

    for e in out:
        print(f"\n{'=' * 72}\n{e['id']}  [{e['stratum']}]  {e['question']}")
        for rel, al in e["aliases_in_AL"].items():
            print(f"  gold: {rel}")
            print(f"    aliases in AL: {al if al else '(none)'}")
        print("  the 2026-08-06 queries, replayed at k=50:")
        if not e["week1_queries"]:
            print("    (no recorded queries)")
        for q in e["week1_queries"]:
            print(f"    {q['query'][:58]:<60} AL rank {str(q['AL']['rank']):>5}"
                  f"   NO rank {str(q['NO']['rank']):>5}")
        if e["alias_self_probe"]:
            print("  searching the note's own aliases:")
            for p in e["alias_self_probe"]:
                print(f"    {p['alias'][:58]:<60} rank {p['rank_in_AL']}")

    payload = {"shared_misses": out}

    if args.surface_replay:
        # How much of the reading surface did the aliases actually move? Every
        # query the 2026-08-06 agent wrote, replayed against both copies. This
        # is the mechanism behind whatever the live runs show: an alias can only
        # change an answer by first changing what came back.
        queries = [q for qs in queries_by_id.values() for q in qs]
        same = diff = new_top = al_empty = no_empty = 0
        matched_al = matched_no = 0
        gained = dropped = alias_only = 0
        for q in queries:
            a, ma = search(args.al_url, q, k=5)
            b, mb = search(args.no_url, q, k=5)
            matched_al += ma or 0
            matched_no += mb or 0
            al_empty += not a
            no_empty += not b
            if a == b:
                same += 1
            else:
                diff += 1
                if a and (not b or a[0] != b[0]):
                    new_top += 1
            # Attribution: a row AL surfaced that NO did not, whose match rests
            # on a query term that appears in the note's alias line and nowhere
            # in its body. The `meta` column is weighted 3x above body, so an
            # alias-only match outranks a genuine body match — which is the
            # mechanism by which added surface can cost recall rather than buy it.
            terms = [w.lower() for w in re.findall(r"[A-Za-z0-9_]+", q) if len(w) > 3]
            for path in a:
                if path in b:
                    continue
                gained += 1
                al_line, body = _split_note(args.al_vault, path)
                if any(t in al_line and t not in body for t in terms):
                    alias_only += 1
            dropped += sum(1 for path in b if path not in a)
        n = len(queries) or 1
        replay = {
            "n_queries": len(queries),
            "identical_top_5": same, "different_top_5": diff,
            "different_rank_1": new_top,
            "share_top_5_changed": round(diff / n, 4),
            "share_rank_1_changed": round(new_top / n, 4),
            "empty_result_sets": {"AL": al_empty, "NO": no_empty},
            "rows_matched_before_ranking": {"AL": matched_al, "NO": matched_no},
            "rows_gained_by_AL": gained,
            "rows_dropped_by_AL": dropped,
            "gained_rows_matching_only_via_alias": alias_only,
            "alias_only_share_of_gained": round(alias_only / max(gained, 1), 4),
        }
        payload["surface_replay"] = replay
        print(f"\n{'=' * 72}\nSURFACE REPLAY — {len(queries)} recorded queries, both copies")
        print(f"  top-5 changed by the aliases : {diff} ({diff / n:.1%})")
        print(f"  rank-1 changed               : {new_top} ({new_top / n:.1%})")
        print(f"  queries returning nothing    : AL {al_empty}   NO {no_empty}")
        print(f"  rows matched before ranking  : AL {matched_al}   NO {matched_no}")
        print(f"  top-5 rows AL gained / dropped: {gained} / {dropped}")
        print(f"  gained rows matching ONLY via an alias: {alias_only} "
              f"({alias_only / max(gained, 1):.1%})")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\n[week3] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
