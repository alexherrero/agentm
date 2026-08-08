#!/usr/bin/env python3
"""week1_rank_replay.py — replays a finished run's queries against a changed index.

Backs the rank-penalty follow-up to `agentm-rescope-week1-experiment.md`. A live
arm costs ~$7 and ~22 minutes and answers one question: did the agent's *answer*
change. That is the question the design hangs on, and it is a bad instrument for
tuning, because the driver is stochastic and every parameter sweep would cost
another run.

This replays the queries a completed run already issued — every `tool_call_log`
entry in its scorecard — against any index variant and any penalty setting, and
reports what the *tool* returned. It is deterministic, free, and answers a
narrower question honestly: for the queries the agent actually wrote, did the
right note move up.

Its ceiling is the same narrowness. A penalty that changes what the agent reads
changes what it asks next, and a replay cannot see that second-order effect — it
holds the queries fixed. So a replay result is a screen, never a verdict. The
live run is what goes on the scorecard.

Two modes:

    --sweep     score a grid of penalty weights, cheapest way to pick a shape
    (default)   score one configuration and print the per-question detail

Metrics, all at the tool level rather than the answer level:

    hit@5     the question's gold note appeared in some query's top 5
    hit@1     …at rank 1
    MRR       mean of 1/rank over the best rank any of the question's queries got
    junk@5    share of served top-5 rows that carry a penalty class
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for _p in (_HERE, _REPO / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import week1_corpus as wc  # noqa: E402

K = 5


def load_queries(report_path):
    """`[(question_id, stratum, [queries], [gold_paths])]` from a scorecard.

    Only lexical calls are replayed. A vector call in an Arm B report has no
    counterpart in an FTS5 index and replaying it as lexical would compare two
    different tools' queries.
    """
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    out = []
    for row in report["per_question"]:
        queries = [c["query"] for c in row.get("tool_call_log") or []
                   if c.get("tool", "search_lexical") == "search_lexical"]
        if not queries:
            continue
        out.append((row["id"], row["stratum"], queries, list(row["expected"])))
    return out, report


def _best_rank(conn, queries, gold, *, weights, doc_flags, penalty, query_mode="as-is"):
    """Best (lowest) rank any of a question's queries put a gold note at, plus junk count."""
    best = None
    served = flagged = 0
    per_query = []
    for q in queries:
        results, _ = wc.search_lexical(conn, q, k=K, weights=weights,
                                       doc_flags=doc_flags, penalty=penalty,
                                       query_mode=query_mode)
        paths = [r["path"] for r in results]
        served += len(paths)
        # Counted off `doc_flags`, not off the result's own `penalty` key, so an
        # unpenalized run still reports its junk share. Reading the key would
        # make the control look clean by construction.
        flagged += sum(1 for p in paths if doc_flags.get(p))
        rank = next((i + 1 for i, p in enumerate(paths) if p in gold), None)
        per_query.append({"query": q, "rank": rank, "top": paths})
        if rank is not None and (best is None or rank < best):
            best = rank
    return best, served, flagged, per_query


def replay(conn, questions, *, weights, doc_flags, penalty, query_mode="as-is"):
    rows = []
    served_total = flagged_total = 0
    for qid, stratum, queries, gold in questions:
        if not gold:  # negative stratum — no note to rank, nothing to measure here
            continue
        best, served, flagged, per_query = _best_rank(
            conn, queries, set(gold), weights=weights, doc_flags=doc_flags,
            penalty=penalty, query_mode=query_mode)
        served_total += served
        flagged_total += flagged
        rows.append({"id": qid, "stratum": stratum, "best_rank": best,
                     "n_queries": len(queries), "gold": gold, "detail": per_query})
    return rows, served_total, flagged_total


def summarize(rows, served, flagged):
    def agg(subset):
        n = len(subset) or 1
        return {
            "n": len(subset),
            "hit_at_5": round(sum(1 for r in subset if r["best_rank"]) / n, 4),
            "hit_at_1": round(sum(1 for r in subset if r["best_rank"] == 1) / n, 4),
            "mrr": round(sum(1.0 / r["best_rank"] for r in subset if r["best_rank"]) / n, 4),
        }
    strata = sorted({r["stratum"] for r in rows})
    return {
        "overall": agg(rows),
        "per_stratum": {s: agg([r for r in rows if r["stratum"] == s]) for s in strata},
        "junk_at_5": round(flagged / served, 4) if served else None,
        "results_served": served,
    }


def _prefetch(conn, questions, *, weights):
    """`[(qid, stratum, gold, [[(path, score), …] per query])]`, fetched once.

    The candidate window is `PENALTY_OVERFETCH` deep — the same window the live
    penalty re-ranks — so a sweep over it sees exactly what a live run would.
    """
    out = []
    for qid, stratum, queries, gold in questions:
        if not gold:
            continue
        windows = []
        for q in queries:
            results, _ = wc.search_lexical(conn, q, k=wc.PENALTY_OVERFETCH,
                                           weights=weights)
            windows.append([(r["path"], r["score"]) for r in results])
        out.append((qid, stratum, set(gold), list(gold), windows))
    return out


def _score_prefetched(fetched, doc_flags, penalty):
    """Re-rank prefetched windows under one penalty setting. Same shape as `replay`."""
    rows = []
    served_total = flagged_total = 0
    for qid, stratum, gold, gold_list, windows in fetched:
        best = None
        for window in windows:
            scored = sorted(
                ((s * wc.penalty_multiplier(doc_flags.get(p, frozenset()), penalty), p)
                 for p, s in window),
                key=lambda t: (-t[0], t[1]))[:K]
            paths = [p for _, p in scored]
            served_total += len(paths)
            flagged_total += sum(1 for p in paths if doc_flags.get(p))
            rank = next((i + 1 for i, p in enumerate(paths) if p in gold), None)
            if rank is not None and (best is None or rank < best):
                best = rank
        rows.append({"id": qid, "stratum": stratum, "best_rank": best,
                     "n_queries": len(windows), "gold": gold_list, "detail": []})
    return rows, served_total, flagged_total


def _open_index(vault, work_dir, variant, verbose=False):
    conn, n = wc.build_lexical_index(
        vault, Path(work_dir) / wc.lexical_db_name(variant), variant=variant,
        verbose=verbose)
    return conn, n, wc.load_doc_flags(conn), wc.variant_spec(variant)["weights"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", required=True,
                    help="a finished scorecard JSON to take the queries from")
    ap.add_argument("--vault-path", default=None)
    ap.add_argument("--work-dir", default=str(Path.home() / ".agentm" / "week1-experiment"))
    ap.add_argument("--variant", default=wc.DEFAULT_VARIANT,
                    choices=sorted(wc.LEXICAL_VARIANTS))
    ap.add_argument("--penalty", default=None,
                    help='JSON weights, or "default" / "as-measured"; omit for none')
    ap.add_argument("--sweep", action="store_true",
                    help="grid-search penalty weights instead of scoring one setting")
    ap.add_argument("--sweep-values", default="1.0,0.7,0.5,0.3,0.15,0.05")
    ap.add_argument("--all-variants", action="store_true",
                    help="score every variant, unpenalized, for attribution")
    ap.add_argument("--query-mode", default="as-is", choices=wc.QUERY_MODES,
                    help="'as-is' passes the agent's query straight to FTS5, which "
                         "ANDs its terms; 'or' OR-joins them, phrases intact")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    from week1_retrieval_experiment import resolve_vault  # noqa: E402
    vault = resolve_vault(args.vault_path)
    questions, source = load_queries(args.report)
    print(f"[replay] {len(questions)} questions, "
          f"{sum(len(q[2]) for q in questions)} lexical queries from "
          f"{Path(args.report).name}", file=sys.stderr)

    if args.all_variants:
        out = {}
        for variant in sorted(wc.LEXICAL_VARIANTS):
            conn, n, flags, weights = _open_index(vault, args.work_dir, variant)
            rows, served, flagged = replay(conn, questions, weights=weights,
                                           doc_flags=flags, penalty=None,
                                           query_mode=args.query_mode)
            out[variant] = summarize(rows, served, flagged)
            o = out[variant]["overall"]
            print(f"[replay] variant {variant:<9} n_docs={n} "
                  f"hit@5={o['hit_at_5']:.3f} hit@1={o['hit_at_1']:.3f} "
                  f"MRR={o['mrr']:.3f}", file=sys.stderr)
            conn.close()
        result = {"mode": "all-variants", "source_report": Path(args.report).name,
                  "variants": out}
    elif args.sweep:
        conn, n, flags, weights = _open_index(vault, args.work_dir, args.variant,
                                              verbose=True)
        values = [float(v) for v in args.sweep_values.split(",")]
        # Fetch each query's candidate window once and re-score it for every grid
        # point in memory. Re-querying SQLite per grid point is the obvious
        # implementation and turns a 40-second sweep into an hour-long one, for
        # identical numbers — the rows FTS5 returns do not depend on the penalty.
        fetched = _prefetch(conn, questions, weights=weights)
        grid = []
        for frag, stat, stag in itertools.product(values, repeat=3):
            penalty = {"fragment": frag, "status": stat, "staging": stag}
            rows, served, flagged = _score_prefetched(fetched, flags, penalty)
            s = summarize(rows, served, flagged)
            grid.append({"penalty": penalty, **s["overall"], "junk_at_5": s["junk_at_5"],
                         "per_stratum": s["per_stratum"]})
        grid.sort(key=lambda g: (-g["mrr"], -g["hit_at_5"]))
        print(f"\n{'fragment':>9} {'status':>7} {'staging':>8} {'hit@5':>7} "
              f"{'hit@1':>7} {'MRR':>7} {'junk@5':>7}", file=sys.stderr)
        for g in grid[:15]:
            p = g["penalty"]
            print(f"{p['fragment']:>9.2f} {p['status']:>7.2f} {p['staging']:>8.2f} "
                  f"{g['hit_at_5']:>7.3f} {g['hit_at_1']:>7.3f} {g['mrr']:>7.3f} "
                  f"{(g['junk_at_5'] or 0):>7.3f}", file=sys.stderr)
        result = {"mode": "sweep", "source_report": Path(args.report).name,
                  "variant": args.variant, "grid": grid}
    else:
        penalty = (dict(wc.DEFAULT_PENALTY_WEIGHTS) if args.penalty == "default"
                   else dict(wc.AS_MEASURED_PENALTY_WEIGHTS)
                   if args.penalty == "as-measured"
                   else json.loads(args.penalty) if args.penalty else None)
        conn, n, flags, weights = _open_index(vault, args.work_dir, args.variant,
                                              verbose=True)
        rows, served, flagged = replay(conn, questions, weights=weights,
                                       doc_flags=flags, penalty=penalty,
                                       query_mode=args.query_mode)
        summary = summarize(rows, served, flagged)
        result = {"mode": "single", "source_report": Path(args.report).name,
                  "variant": args.variant, "penalty": penalty,
                  "query_mode": args.query_mode,
                  "n_docs": n, "n_flagged_notes": len(flags),
                  **summary, "per_question": rows}
        o = summary["overall"]
        print(f"[replay] variant={args.variant} query-mode={args.query_mode} "
              f"penalty={penalty} "
              f"hit@5={o['hit_at_5']:.3f} hit@1={o['hit_at_1']:.3f} "
              f"MRR={o['mrr']:.3f} junk@5={summary['junk_at_5']}", file=sys.stderr)
        for s, v in sorted(summary["per_stratum"].items()):
            print(f"    {s:<22} n={v['n']:>3} hit@5={v['hit_at_5']:.3f} "
                  f"hit@1={v['hit_at_1']:.3f} MRR={v['mrr']:.3f}", file=sys.stderr)

    result["driver_of_source_run"] = source.get("model") or source.get("driver")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"[replay] -> {args.out}", file=sys.stderr)
    else:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
