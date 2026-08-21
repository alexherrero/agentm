#!/usr/bin/env python3
"""Retrieval eval against the ranker that actually ships.

`eval_v6_retrieval.py` is the eval this arc inherited, and auditing it before
relying on it found three reasons it cannot carry a promotion decision. All three
were verified on this machine rather than read off its docstring:

  1. **It measures the wrong ranker.** It calls Python `recall.query`. Search
     ships from the Go daemon, and whatever the Python path scores is not
     evidence about what a question actually hits.
  2. **Its dense arm is off.** `sqlite3.Connection.enable_load_extension` does
     not exist on the Python this repo runs, so `sqlite-vec` cannot load and the
     comparison is lexical-only — while the daemon's embedder is warm. It is
     blind to precisely the arm that ranks differently.
  3. **It runs a 22-question v0 set**, not the frozen 84-entry gold set.

This harness is the replacement, and its one job is to run the shipped
competition. It shells `agentmd search` with the same flags, the same mode and
the same term extraction the recall hook uses, because a query built differently
is a different query and scoring it answers a question nobody asked.

Determinism, not replicates. There is no model judgment in the scoring path —
fixed corpus, fixed weights, fixed query — so repeated runs are identical and
replicates would measure nothing. `--verify-determinism` runs twice and compares,
so that is checked rather than assumed. The variation that matters is across
questions, which is what the paired test reads.

Usage:
  python3 scripts/health/eval_retrieval_shipped.py --baseline out.json
  python3 scripts/health/eval_retrieval_shipped.py --compare baseline.json
  python3 scripts/health/eval_retrieval_shipped.py --verify-determinism

Exit:
  0  the run completed (and, with --compare, cleared its bar)
  1  the comparison failed its bar, or determinism did not hold
  2  setup error — no daemon, no gold set, or a cold embedder
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

# The term extraction the hook uses, imported rather than reimplemented. A
# reimplementation would drift, and a drifted query means the eval and the
# shipped path are asking different things while reporting one number.
import recall  # noqa: E402

GOLD_SET = _HERE / "fixtures" / "week1-gold" / "gold-set-v3.json"

# The field the fixture actually uses. The output rows elsewhere in this repo
# call it `expected`, and reading the wrong one scores a silent, total null that
# reads as a finding rather than as a bug.
EXPECTED_FIELD = "expected_note_paths"

# `negative` entries are questions the corpus is not supposed to answer. They are
# scored separately: counting them in R@5 would reward a ranker for finding
# nothing, and hiding them would lose the only check on false confidence.
NEGATIVE_STRATUM = "negative"

DEFAULT_K = 5


class Setup(Exception):
    """The environment cannot produce a trustworthy measurement."""


def daemon_binary() -> str:
    import os
    return os.environ.get("AGENTMD", "").strip() or "agentmd"


def require_warm_embedder(binary: str) -> str:
    """Refuse to score with a cold or partial dense arm.

    This is the guard the inherited eval lacked. A lexical-only run reported as a
    hybrid result is not a weaker measurement, it is a different one — and the
    arm that is missing is the one a paraphrase question depends on.
    """
    proc = subprocess.run([binary, "status", "--json"], capture_output=True, text=True)
    if proc.returncode not in (0, 3):
        raise Setup(f"{binary} status failed: {(proc.stderr or '').strip()[:200]}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise Setup(f"{binary} status was not JSON: {exc}") from exc

    emb = (payload.get("health") or {}).get("embedder") or {}
    state = emb.get("state")
    vectors, in_scope, stale = emb.get("vectors", 0), emb.get("in_scope", 0), emb.get("stale", 0)
    if state != "warm":
        raise Setup(
            f"the embedder is {state!r}, so this would be a lexical-only run "
            f"reported as a hybrid one. Start the daemon and let the model load.")
    if in_scope and stale > in_scope * 0.05:
        raise Setup(
            f"{stale} of {in_scope} in-scope notes carry stale vectors "
            f"({stale / in_scope:.0%}). Scoring now measures a half-embedded "
            f"corpus rather than the ranker. Run `agentmd embed` first.")
    return f"{vectors}/{in_scope} embedded, {stale} stale"


def load_gold() -> list:
    if not GOLD_SET.is_file():
        raise Setup(f"the frozen gold set is missing: {GOLD_SET}")
    entries = json.loads(GOLD_SET.read_text(encoding="utf-8")).get("entries") or []
    if not entries:
        raise Setup("the gold set carries no entries")
    return entries


def search(binary: str, question: str, k: int) -> list:
    """One query, exactly as the recall hook issues it.

    Same mode, same `-question` for the dense arm, same extracted terms for the
    lexical arm. Reproducing the shape is the entire point of this harness.
    """
    terms = recall._daemon_query_terms(question)
    if not terms:
        return []
    argv = [binary, "search", "-json", "-k", str(k),
            "-mode", recall.DAEMON_SEARCH_MODE, "-question", question, terms]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise Setup(f"search failed for {question[:60]!r}: "
                    f"{(proc.stderr or '').strip()[:200]}")
    payload = json.loads(proc.stdout or "{}")
    return [r.get("path", "") for r in (payload.get("results") or [])]


def score(binary: str, entries: list, k: int) -> dict:
    """Run every question and return the per-question outcomes plus the summary."""
    per_question = {}
    hits = 0
    scored = 0
    rank_sum = 0
    ranked = 0
    false_positives = 0
    negatives = 0

    for e in entries:
        question = e["question"]
        expected = [p for p in (e.get(EXPECTED_FIELD) or []) if p]
        got = search(binary, question, k)

        if e.get("stratum") == NEGATIVE_STRATUM:
            negatives += 1
            # A negative is answered correctly by returning nothing the fixture
            # named. With no expected paths, any confident hit is the failure.
            hit = False
            if expected:
                hit = any(p in got for p in expected)
                if hit:
                    false_positives += 1
            per_question[e["id"]] = {"hit": not hit, "negative": True, "rank": None}
            continue

        if not expected:
            per_question[e["id"]] = {"hit": None, "negative": False, "rank": None,
                                     "note": "no expected paths in the fixture"}
            continue

        scored += 1
        rank = None
        for i, path in enumerate(got, start=1):
            if path in expected:
                rank = i
                break
        if rank is not None:
            hits += 1
            rank_sum += rank
            ranked += 1
        per_question[e["id"]] = {"hit": rank is not None, "negative": False, "rank": rank}

    return {
        "k": k,
        "scored": scored,
        "hits": hits,
        "r_at_k": (hits / scored) if scored else 0.0,
        "avg_rank_to_first_hit": (rank_sum / ranked) if ranked else None,
        "negatives": negatives,
        "false_positives": false_positives,
        "per_question": per_question,
    }


def mcnemar_exact(flips_for: int, flips_against: int) -> float:
    """Two-sided exact binomial p for a paired hit/miss comparison.

    The right test for this shape: the corpus, the weights and the queries are
    all fixed, so the variation lives across questions rather than across runs.
    Only the questions that *flipped* carry information — the ones that agree say
    nothing about which ranker is better.
    """
    n = flips_for + flips_against
    if n == 0:
        return 1.0
    from math import comb
    smaller = min(flips_for, flips_against)
    tail = sum(comb(n, i) for i in range(smaller + 1))
    return min(1.0, 2.0 * tail / (2 ** n))


def compare(baseline: dict, current: dict) -> dict:
    """Paired comparison over the per-question outcomes."""
    b, c = baseline["per_question"], current["per_question"]
    shared = [q for q in b if q in c and not b[q].get("negative")
              and b[q].get("hit") is not None and c[q].get("hit") is not None]

    for_current = [q for q in shared if c[q]["hit"] and not b[q]["hit"]]
    against = [q for q in shared if b[q]["hit"] and not c[q]["hit"]]
    p = mcnemar_exact(len(for_current), len(against))

    return {
        "compared": len(shared),
        "flips_for": len(for_current),
        "flips_against": len(against),
        "flipped_for_ids": sorted(for_current),
        "flipped_against_ids": sorted(against),
        "p": p,
        "r_at_k_before": baseline["r_at_k"],
        "r_at_k_after": current["r_at_k"],
        "regressed": len(against) > len(for_current) and p < 0.05,
    }


def render(result: dict, provenance: str) -> str:
    lines = [
        f"corpus: {provenance}",
        f"scored {result['scored']} question(s) at k={result['k']}",
        f"  R@{result['k']}              : {result['r_at_k']:.3f} ({result['hits']} hits)",
    ]
    if result["avg_rank_to_first_hit"] is not None:
        lines.append(f"  rank to first hit  : {result['avg_rank_to_first_hit']:.2f}")
    lines.append(f"  negatives          : {result['negatives']}, "
                 f"{result['false_positives']} answered wrongly")
    return "\n".join(lines)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="retrieval eval against the shipped ranker")
    ap.add_argument("--baseline", metavar="OUT", help="score and write the result here")
    ap.add_argument("--compare", metavar="BASELINE", help="score and compare against this")
    ap.add_argument("--verify-determinism", action="store_true",
                    help="run twice and assert the two runs agree")
    ap.add_argument("-k", type=int, default=DEFAULT_K)
    args = ap.parse_args(argv)

    binary = daemon_binary()
    try:
        provenance = require_warm_embedder(binary)
        entries = load_gold()
    except Setup as exc:
        print(f"eval-retrieval-shipped: {exc}", file=sys.stderr)
        return 2

    try:
        first = score(binary, entries, args.k)
    except Setup as exc:
        print(f"eval-retrieval-shipped: {exc}", file=sys.stderr)
        return 2

    print(render(first, provenance))

    if args.verify_determinism:
        second = score(binary, entries, args.k)
        differing = [q for q in first["per_question"]
                     if first["per_question"][q] != second["per_question"].get(q)]
        if differing:
            print(f"\nNOT DETERMINISTIC: {len(differing)} question(s) differ between "
                  f"two consecutive runs: {differing[:8]}\n"
                  f"Every conclusion from this harness assumes a fixed input gives a "
                  f"fixed answer. It does not.", file=sys.stderr)
            return 1
        print(f"\ndeterministic: two consecutive runs agree on all "
              f"{len(first['per_question'])} question(s)")

    if args.baseline:
        Path(args.baseline).write_text(json.dumps(first, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
        print(f"\nbaseline written to {args.baseline}")

    if args.compare:
        baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        cmp = compare(baseline, first)
        print(f"\npaired comparison over {cmp['compared']} question(s):")
        print(f"  R@{args.k}            : {cmp['r_at_k_before']:.3f} -> {cmp['r_at_k_after']:.3f}")
        print(f"  flipped to a hit  : {cmp['flips_for']} {cmp['flipped_for_ids'][:6]}")
        print(f"  flipped to a miss : {cmp['flips_against']} {cmp['flipped_against_ids'][:6]}")
        print(f"  exact paired p    : {cmp['p']:.4f}")
        if cmp["regressed"]:
            print("\nREGRESSION: more questions got worse than better, and the flip "
                  "count is unlikely under chance. The bar is not met.", file=sys.stderr)
            return 1
        print("\nno significant regression")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
