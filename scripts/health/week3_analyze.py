#!/usr/bin/env python3
"""week3_analyze.py — AL against NO, exactly, over the week-3 replicates.

The week-3 retest runs the same 60 questions against two copies of one frozen
corpus that differ by exactly the alias backfill and nothing else. Six Opus
replicates per copy, because a single run of this harness cannot be read as an
effect — six same-configuration runs spread 2.5 points of R@5 on the 2026-08-07
campaign, and a variant with provably zero retrieval effect swung one stratum 36
points in a single run.

So the test is an exact permutation test over all 924 rearrangements of the
twelve runs, the same one the rank-penalty result used, applied to:

- overall R@5 — the headline
- each stratum, paraphrase above all
- the correct-rejection rate on the negative stratum, which is the guard.
  Aliases add matchable surface to every note that has them. If that talks the
  driver out of concluding "no such memory exists", the alias backfill is
  buying paraphrase recall the same way the OR rewrite did, and paying in the
  one stratum that tests whether the system knows what it does not know.

Fable runs are reported alongside and excluded from every test: one run per
copy is a sensitivity check, not a measurement.

    python3 scripts/health/week3_analyze.py --raw-dir ~/.agentm/week3-retest/raw \\
        --out scripts/health/results/week3-retest/week3-retest.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from itertools import combinations
from pathlib import Path

STRATA = ["distinctive-token", "pure-paraphrase", "episodic-temporal",
          "research-density", "negative"]
SHARED_MISSES = ["dt12", "ep09", "ep12", "ng02", "pp05", "pp07"]


def exact_permutation_p(a, b):
    """Two-sided exact permutation test on the difference of means.

    Pools the two arms and enumerates every way of splitting the pool into
    groups of the original sizes — 924 of them for six against six — counting
    how often a rearrangement produces a mean difference at least as extreme as
    the observed one. No distributional assumption, and at these sample sizes it
    is cheaper than the approximation.
    """
    a, b = list(a), list(b)
    if not a or not b:
        return None, None
    observed = statistics.mean(a) - statistics.mean(b)
    pool = a + b
    idx = range(len(pool))
    total = extreme = 0
    for pick in combinations(idx, len(a)):
        left = [pool[i] for i in pick]
        rest = [pool[i] for i in idx if i not in set(pick)]
        total += 1
        if abs(statistics.mean(left) - statistics.mean(rest)) >= abs(observed) - 1e-12:
            extreme += 1
    return round(observed, 4), round(extreme / total, 4)


def exact_paired_p(pairs):
    """Two-sided exact sign-flip test over paired rounds. Secondary, by design.

    AL and NO run concurrently inside each round, so round r sees the same
    machine and the same API conditions for both copies. That makes the design
    genuinely paired, and a paired test is the more powerful one. It is reported
    second and never as the verdict: week 1's rank-penalty ruling rests on the
    unpaired 924-rearrangement test, and switching to whichever test reads
    better once both numbers are visible is the move pre-registration exists to
    block.

    2^6 = 64 sign assignments for six rounds.
    """
    diffs = [a - b for a, b in pairs]
    if not diffs:
        return None, None
    observed = statistics.mean(diffs)
    n = len(diffs)
    total = extreme = 0
    for mask in range(1 << n):
        flipped = [d if (mask >> i) & 1 else -d for i, d in enumerate(diffs)]
        total += 1
        if abs(statistics.mean(flipped)) >= abs(observed) - 1e-12:
            extreme += 1
    return round(observed, 4), round(extreme / total, 4)


def arm_stats(values):
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 4) if values else None,
        "sd": round(statistics.stdev(values), 4) if len(values) > 1 else None,
        "min": round(min(values), 4) if values else None,
        "max": round(max(values), 4) if values else None,
        "values": [round(v, 4) for v in values],
    }


def compare(al_values, no_values, pairs=None):
    delta, p = exact_permutation_p(al_values, no_values)
    out = {"AL": arm_stats(al_values), "NO": arm_stats(no_values),
           "delta_al_minus_no": delta, "p_exact": p}
    if pairs and len(pairs) > 1:
        pd, pp = exact_paired_p(pairs)
        out["paired_secondary"] = {"delta": pd, "p_exact": pp, "n_pairs": len(pairs)}
    return out


def round_of(run):
    """The replicate index a run belongs to, for pairing. None for Fable."""
    label = run.get("run_label", "")
    return label.rsplit("-r", 1)[-1] if "-r" in label else None


def paired_values(al, no, getter):
    """AL/NO value pairs, matched by round. Rounds missing either side drop."""
    a = {round_of(r): getter(r) for r in al}
    b = {round_of(r): getter(r) for r in no}
    return [(a[k], b[k]) for k in sorted(set(a) & set(b))
            if a[k] is not None and b[k] is not None]


def load_runs(raw_dir):
    runs = []
    for path in sorted(Path(raw_dir).glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        d["_file"] = path.name
        runs.append(d)
    return runs


def stratum_value(run, stratum, key="r_at_5"):
    s = (run.get("per_stratum") or {}).get(stratum)
    return None if not s else s.get(key)


def analyze(runs):
    opus = [r for r in runs if r.get("model") == "opus"]
    fable = [r for r in runs if r.get("model") == "fable"]
    al = [r for r in opus if r["copy"] == "week3-AL"]
    no = [r for r in opus if r["copy"] == "week3-NO"]

    out = {
        "n_runs": len(runs),
        "opus_runs": {"AL": len(al), "NO": len(no)},
        "fable_runs": {"AL": sum(1 for r in fable if r["copy"] == "week3-AL"),
                       "NO": sum(1 for r in fable if r["copy"] == "week3-NO")},
        "corpus": {
            "n_docs": sorted({r["corpus"]["n_docs"] for r in runs}),
            "daemon_version": sorted({(r.get("daemon") or {}).get("version") for r in runs}),
        },
    }

    # --- the headline and every stratum -----------------------------------
    out["overall_r_at_5"] = compare(
        [r["overall"]["r_at_5"] for r in al], [r["overall"]["r_at_5"] for r in no],
        paired_values(al, no, lambda r: r["overall"]["r_at_5"]))
    out["per_stratum_r_at_5"] = {}
    for s in STRATA:
        a = [v for v in (stratum_value(r, s) for r in al) if v is not None]
        b = [v for v in (stratum_value(r, s) for r in no) if v is not None]
        if a and b:
            out["per_stratum_r_at_5"][s] = compare(
                a, b, paired_values(al, no, lambda r, s=s: stratum_value(r, s)))

    # --- the negatives guard ----------------------------------------------
    a = [v for v in (stratum_value(r, "negative", "correct_rejection_rate") for r in al)
         if v is not None]
    b = [v for v in (stratum_value(r, "negative", "correct_rejection_rate") for r in no)
         if v is not None]
    out["correct_rejection_rate"] = compare(
        a, b, paired_values(
            al, no, lambda r: stratum_value(r, "negative", "correct_rejection_rate"))
    ) if a and b else None

    # --- accuracy, cost, calls, latency -----------------------------------
    out["accuracy"] = compare([r["overall"]["accuracy"] for r in al],
                              [r["overall"]["accuracy"] for r in no])
    out["mean_tool_calls"] = compare([r["overall"]["mean_tool_calls"] for r in al],
                                     [r["overall"]["mean_tool_calls"] for r in no])
    out["wall_s_per_question"] = compare(
        [round(r["wall_s_total"] / r["n_questions"], 2) for r in al],
        [round(r["wall_s_total"] / r["n_questions"], 2) for r in no])
    out["daemon_ms_per_call"] = compare(
        [r["daemon_latency"]["mean_ms"] for r in al],
        [r["daemon_latency"]["mean_ms"] for r in no])
    out["cost_usd_total"] = round(sum(r["cost_usd_total"] for r in runs), 2)

    # --- the six shared misses --------------------------------------------
    shared = {}
    for qid in SHARED_MISSES:
        row = {}
        for name, group in (("AL", al), ("NO", no)):
            hits = [1 if _question(r, qid)["correct"] else 0 for r in group
                    if _question(r, qid)]
            row[name] = {"correct_of_n": f"{sum(hits)}/{len(hits)}",
                         "hit_rate": round(sum(hits) / len(hits), 4) if hits else None}
        row["fable"] = {
            r["copy"].replace("week3-", ""): bool(_question(r, qid)["correct"])
            for r in fable if _question(r, qid)
        }
        shared[qid] = row
    out["shared_misses"] = shared

    # --- every question, both copies, so a flip is findable ---------------
    flips = []
    ids = [q["id"] for q in al[0]["per_question"]] if al else []
    for qid in ids:
        a_hits = sum(1 for r in al if _question(r, qid) and _question(r, qid)["correct"])
        b_hits = sum(1 for r in no if _question(r, qid) and _question(r, qid)["correct"])
        if a_hits != b_hits:
            q = _question(al[0], qid)
            flips.append({"id": qid, "stratum": q["stratum"],
                          "AL": f"{a_hits}/{len(al)}", "NO": f"{b_hits}/{len(no)}",
                          "delta": a_hits - b_hits})
    out["per_question_flips"] = sorted(flips, key=lambda f: -abs(f["delta"]))

    # --- integrity, pooled ------------------------------------------------
    out["integrity"] = {
        "hook_violations": sum(len(r["integrity"]["hook_violations"]) for r in runs),
        "tool_escapes": sum(len(r["integrity"]["tool_escapes"]) for r in runs),
        "tool_call_count_mismatches": sum(
            len(r["integrity"]["tool_call_count_mismatches"]) for r in runs),
        "budget_leaks": sum(len(r["integrity"]["budget_leaks"]) for r in runs),
        "driver_errors": sum(len(r["integrity"]["driver_errors"]) for r in runs),
        "questions_that_wanted_more_calls": sum(
            r["integrity"]["questions_that_wanted_more_calls"] for r in runs),
        "refused_tool_attempts": sum(
            len(r["integrity"]["refused_tool_attempts"]) for r in runs),
    }

    # --- fable, reported and not tested -----------------------------------
    out["fable"] = [
        {"copy": r["copy"], "r_at_5": r["overall"]["r_at_5"],
         "accuracy": r["overall"]["accuracy"],
         "correct_rejection_rate": stratum_value(r, "negative", "correct_rejection_rate"),
         "mean_tool_calls": r["overall"]["mean_tool_calls"],
         "per_stratum": {s: stratum_value(r, s) for s in STRATA}}
        for r in sorted(fable, key=lambda r: r["copy"])
    ]

    # --- the reading surface ----------------------------------------------
    out["reading_surface"] = {
        name: {
            "results_served": sum(r["surface"]["results_served"] for r in group),
            "flagged_share": round(
                sum(r["surface"]["flagged_results_served"] for r in group)
                / max(sum(r["surface"]["results_served"] for r in group), 1), 4),
        }
        for name, group in (("AL", al), ("NO", no))
    }
    out["per_run"] = [
        {"run": r["run_label"], "copy": r["copy"], "model": r["model"],
         "r_at_5": r["overall"]["r_at_5"], "accuracy": r["overall"]["accuracy"],
         "mean_tool_calls": r["overall"]["mean_tool_calls"],
         "wall_s_per_q": round(r["wall_s_total"] / r["n_questions"], 2),
         "daemon_mean_ms": r["daemon_latency"]["mean_ms"],
         "cost_usd": r["cost_usd_total"],
         "n_docs": r["corpus"]["n_docs"]}
        for r in sorted(runs, key=lambda r: (r["model"], r["copy"], r["run_label"]))
    ]
    # Every run's configuration, integrity block, scores, and one line per
    # question. Raw scorecards are ~200KB each and stay on the machine that ran
    # them; this is what makes the file above recomputable without them, which
    # is the convention the 2026-08-07 campaign settled on.
    out["runs"] = [
        {"run": r["run_label"], "copy": r["copy"], "model": r["model"],
         "vault_name": r.get("vault_name"), "corpus": r["corpus"],
         "daemon": r.get("daemon"), "call_budget": r["call_budget"],
         "n_questions": r["n_questions"], "cost_usd_total": r["cost_usd_total"],
         "wall_s_total": r["wall_s_total"], "daemon_latency": r["daemon_latency"],
         "surface": r["surface"], "overall": r["overall"],
         "per_stratum": r["per_stratum"], "integrity": r["integrity"],
         "per_question": [
             {"id": q["id"], "stratum": q["stratum"], "correct": q["correct"],
              "r_at_5": q["r_at_5"], "p_at_5": q["p_at_5"],
              "tool_calls": q["tool_calls"], "n_expected": len(q["expected"]),
              "said_no_answer": q["said_no_answer"],
              "wall_s": q["wall_s"], "daemon_ms_total": q["daemon_ms_total"]}
             for q in r["per_question"]
         ]}
        for r in sorted(runs, key=lambda r: (r["model"], r["copy"], r["run_label"]))
    ]
    return out


def _question(run, qid):
    for q in run["per_question"]:
        if q["id"] == qid:
            return q
    return None


def render(a):
    L = []
    L.append(f"week-3 retest — alias backfill as the isolated variable")
    L.append(f"{a['n_runs']} runs · Opus AL={a['opus_runs']['AL']} NO={a['opus_runs']['NO']}"
             f" · corpus {a['corpus']['n_docs']} notes · ${a['cost_usd_total']:.2f}")
    L.append("")
    o = a["overall_r_at_5"]
    L.append(f"OVERALL R@5   AL {o['AL']['mean']:.4f} (sd {o['AL']['sd']:.4f}, "
             f"{o['AL']['min']:.3f}–{o['AL']['max']:.3f})   "
             f"NO {o['NO']['mean']:.4f} (sd {o['NO']['sd']:.4f}, "
             f"{o['NO']['min']:.3f}–{o['NO']['max']:.3f})")
    L.append(f"              delta {o['delta_al_minus_no']:+.4f}   p = {o['p_exact']}"
             + (f"   (paired, secondary: {o['paired_secondary']['delta']:+.4f}, "
                f"p = {o['paired_secondary']['p_exact']})"
                if o.get("paired_secondary") else ""))
    L.append("")
    hdr = f"{'stratum':<22} {'AL':>8} {'NO':>8} {'delta':>8} {'p':>8}"
    L.append(hdr)
    L.append("-" * len(hdr))
    for s in STRATA:
        c = a["per_stratum_r_at_5"].get(s)
        if not c:
            continue
        L.append(f"{s:<22} {c['AL']['mean']:>8.3f} {c['NO']['mean']:>8.3f} "
                 f"{c['delta_al_minus_no']:>+8.3f} {c['p_exact']:>8.4f}")
    L.append("-" * len(hdr))
    cr = a["correct_rejection_rate"]
    if cr:
        L.append(f"{'correct rejections':<22} {cr['AL']['mean']:>8.3f} "
                 f"{cr['NO']['mean']:>8.3f} {cr['delta_al_minus_no']:>+8.3f} "
                 f"{cr['p_exact']:>8.4f}   <- the negatives guard")
    L.append("")
    for key, label in (("mean_tool_calls", "tool calls / question"),
                       ("wall_s_per_question", "wall seconds / question"),
                       ("daemon_ms_per_call", "daemon ms / call")):
        c = a[key]
        L.append(f"{label:<24} AL {c['AL']['mean']:>8.2f}   NO {c['NO']['mean']:>8.2f}"
                 f"   delta {c['delta_al_minus_no']:>+7.2f}  p = {c['p_exact']}")
    L.append("")
    L.append("THE SIX SHARED MISSES (correct across replicates)")
    for qid, row in a["shared_misses"].items():
        L.append(f"  {qid:<6} AL {row['AL']['correct_of_n']:>5}   "
                 f"NO {row['NO']['correct_of_n']:>5}   fable {row['fable']}")
    L.append("")
    L.append("QUESTIONS THAT MOVED (AL correct-count vs NO correct-count)")
    for f in a["per_question_flips"][:20]:
        L.append(f"  {f['id']:<6} [{f['stratum']:<18}] AL {f['AL']:>5}  NO {f['NO']:>5}  "
                 f"{f['delta']:+d}")
    L.append("")
    L.append("FABLE (sensitivity only, excluded from every test)")
    for f in a["fable"]:
        L.append(f"  {f['copy']:<10} R@5 {f['r_at_5']:.4f}  acc {f['accuracy']:.4f}  "
                 f"rejections {f['correct_rejection_rate']}  calls {f['mean_tool_calls']}")
    L.append("")
    i = a["integrity"]
    hard = i["hook_violations"] + i["tool_escapes"] + i["tool_call_count_mismatches"] \
        + i["budget_leaks"]
    L.append(f"INTEGRITY (pooled over every run): {'FAIL' if hard else 'clean'}")
    for k, v in i.items():
        L.append(f"  {k:<34} {v}")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    runs = load_runs(args.raw_dir)
    if not runs:
        raise SystemExit(f"[week3] no scorecards under {args.raw_dir}")
    a = analyze(runs)
    print(render(a))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(a, indent=2) + "\n", encoding="utf-8")
        print(f"\n[week3] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
