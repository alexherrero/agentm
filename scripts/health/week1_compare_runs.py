#!/usr/bin/env python3
"""week1_compare_runs.py — one table across several week-1 scorecards.

Every run of `week1_retrieval_experiment.py` writes a self-contained scorecard.
Comparing them by eye across five JSON files is how a two-point difference gets
read as a result, so this prints them side by side against a named baseline,
with the per-stratum breakdown and the per-question movement underneath.

    python3 scripts/health/week1_compare_runs.py \\
        --baseline scripts/health/results/week1/opus-arm-a-control.json \\
        scripts/health/results/week1/opus-arm-a-*.json

Two things it says out loud, because both have been misread on this experiment:

**P@5 and R@5 carry the same information here.** `score_at_k` computes
`P@5 = hits/5` and `R@5 = hits/len(expected)`, so for a fixed question the two
differ by a constant. A question with one expected note scores P@5 = 0.200 when
the answer is perfect. Reading a low P@5 as "the answer came back padded with
junk" is reading the size of the gold label, not the quality of the ranking.

**Runs are only comparable when the corpus matched.** The vault turns over
~1,500 notes a week, so a scorecard from a different day indexed a different
corpus. `n_docs` is printed for every run and a mismatch against the baseline is
called out rather than quietly averaged over.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STRATA = ["distinctive-token", "pure-paraphrase", "episodic-temporal",
          "research-density", "negative"]
SHARED_MISSES = ["dt12", "ep09", "ep12", "ng02", "pp05", "pp07"]


def load(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    d["_name"] = Path(path).stem
    return d


def recompute_junk_share(report, doc_flags):
    """Flagged share of the top-5 rows this run served, computed from the paths.

    The stored `surface.flagged_share` cannot be compared across runs: the
    daemon only annotates a result with its penalty classes when a penalty is
    active, so an unpenalized run reports 0.0 no matter how much junk it served.
    The call log records `result_paths` unconditionally, so the honest number is
    recoverable for every run by classifying those paths after the fact — which
    is what this does, against one flag map shared by all the runs being
    compared.
    """
    served = flagged = 0
    for row in report["per_question"]:
        for call in row.get("tool_call_log") or []:
            for path in call.get("result_paths") or []:
                served += 1
                if doc_flags.get(path):
                    flagged += 1
    return (round(flagged / served, 4) if served else None), served


def _label(r):
    bits = [r.get("lexical_variant") or "baseline"]
    if (r.get("query_mode") or "as-is") != "as-is":
        bits.append(f"query={r['query_mode']}")
    if r.get("penalty"):
        bits.append("penalty")
    return " + ".join(bits)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--baseline", required=True,
                    help="the run every delta is measured against")
    ap.add_argument("--vault-path", default=None,
                    help="recompute each run's junk share from its recorded result "
                         "paths, so penalized and unpenalized runs are comparable")
    ap.add_argument("--out", default=None, help="write the comparison as JSON too")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    base = load(args.baseline)
    runs = [load(p) for p in args.reports if Path(p).resolve() != Path(args.baseline).resolve()]
    runs.insert(0, base)

    doc_flags = None
    if args.vault_path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import week1_corpus as wc  # noqa: E402
        vault = Path(args.vault_path).expanduser()
        conn, _ = wc.build_lexical_index(vault, vault.parent / "compare-flags.db")
        doc_flags = wc.load_doc_flags(conn)
        for r in runs:
            share, served = recompute_junk_share(r, doc_flags)
            r["surface"] = dict(r.get("surface") or {},
                                flagged_share=share, results_served=served)

    out = []
    print(f"{'run':<26} {'config':<30} {'docs':>6} {'P@5':>7} {'R@5':>7} "
          f"{'acc':>7} {'junk':>6} {'calls':>6} {'$':>6}")
    print("-" * 110)
    for r in runs:
        o = r["overall"]
        surface = r.get("surface") or {}
        junk = surface.get("flagged_share")
        print(f"{r['_name']:<26} {_label(r):<30} {r['corpus']['n_docs']:>6} "
              f"{o['p_at_5']:>7.3f} {o['r_at_5']:>7.3f} {o['accuracy']:>7.3f} "
              f"{(junk if junk is not None else float('nan')):>6.3f} "
              f"{o['mean_tool_calls']:>6.2f} {r.get('cost_usd_total', 0):>6.2f}"
              + ("" if r is base else
                 f"   R@5 {o['r_at_5'] - base['overall']['r_at_5']:+.3f}"))
        if r["corpus"]["n_docs"] != base["corpus"]["n_docs"]:
            print(f"{'':<26} !! indexed {r['corpus']['n_docs']} notes, baseline indexed "
                  f"{base['corpus']['n_docs']} — the corpora differ, so this delta "
                  f"mixes a config change with a corpus change")
        out.append({"run": r["_name"], "config": _label(r), **o,
                    "n_docs": r["corpus"]["n_docs"], "junk_share": junk,
                    "cost_usd": r.get("cost_usd_total")})

    print(f"\nR@5 per stratum (baseline = {base['_name']})")
    header = f"{'stratum':<22} {'n':>3} " + " ".join(f"{r['_name'][-14:]:>16}" for r in runs)
    print(header)
    print("-" * len(header))
    for s in STRATA:
        if s not in base["per_stratum"]:
            continue
        cells = []
        for r in runs:
            v = r["per_stratum"].get(s)
            cells.append(f"{v['r_at_5']:>16.3f}" if v else f"{'—':>16}")
        print(f"{s:<22} {base['per_stratum'][s]['n']:>3} " + " ".join(cells))

    print(f"\nthe six misses shared by both drivers on 2026-08-06")
    print(f"{'id':<6} " + " ".join(f"{r['_name'][-14:]:>16}" for r in runs))
    for qid in SHARED_MISSES:
        cells = []
        for r in runs:
            row = next((q for q in r["per_question"] if q["id"] == qid), None)
            cells.append(f"{('hit' if row['correct'] else 'miss'):>16}" if row
                         else f"{'—':>16}")
        print(f"{qid:<6} " + " ".join(cells))

    base_ids = {q["id"]: q["correct"] for q in base["per_question"]}
    for r in runs[1:]:
        gained = [q["id"] for q in r["per_question"]
                  if q["correct"] and not base_ids.get(q["id"], False)]
        lost = [q["id"] for q in r["per_question"]
                if not q["correct"] and base_ids.get(q["id"], False)]
        print(f"\n{r['_name']}: gained {gained or 'none'}, lost {lost or 'none'}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(
            {"baseline": base["_name"], "runs": out}, indent=2), encoding="utf-8")
        print(f"\n-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
