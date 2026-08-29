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
  2  setup error — no daemon, no gold set, or a cold embedder (gates SKIP)
  3  comparison refused — no provenance, moved corpus, or a different gold set
  4  instrument control fired — empty expectations, dead canary, or flat scores
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date
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


class Control(Exception):
    """An instrument-liveness control fired — the number must not print.

    Distinct from Setup (exit 2, the environment cannot measure → gates SKIP)
    and from Refused (exit 3, the sides are not comparable). A control firing
    means the environment looked fine and the instrument itself is broken: the
    gold set parsed into empty expectations, the planted canary did not come
    back, or every score came back identical. Exit 4, and the gate maps it to
    FAIL — this arc produced two clean false nulls from exactly these states,
    each indistinguishable from a real refutation until someone added the
    control by hand.
    """


# The canary: a note planted in the live corpus whose token appears nowhere
# else. A query for it that does not return it at rank 1 means the index is
# dead or detached, which is the state that produced the arc's "clean 0 of 5"
# false null. Checked before any question is scored.
CANARY_QUERY = "canary-eval-liveness-q7g3xz"
CANARY_PATH = "Agent/memory/2026/08/eval-canary.md"


class Refused(Exception):
    """A comparison that must not happen — distinct from a Setup skip.

    Setup means the environment cannot measure (exit 2, gates SKIP). Refused
    means the environment measured fine and the two sides are not comparable
    (exit 3): the baseline carries no provenance, names a different gold set, or
    was pinned on a corpus that has since moved. Sharing exit 2 would let the
    regression gate silently SKIP forever after the first drifted day — a
    tripwire that dies quietly the moment it matters.
    """


def gold_sha() -> str:
    """A content hash of the gold set, grouped so no ten-digit run survives.

    Grouped because the PII gate reads a long hex digest's digit runs as a US
    phone number — the third time this arc has hit that, so the format is now
    the habit rather than the retrofit.
    """
    h = hashlib.sha256(GOLD_SET.read_bytes()).hexdigest()[:12]
    return "-".join(h[i:i + 4] for i in range(0, 12, 4))


def corpus_fingerprint(binary: str) -> dict:
    """What corpus this measurement is about.

    The old baseline recorded seven scores and nothing else, which made two
    baselines from two different corpora silently comparable — 0.781 → 0.734
    nearly got read as a code regression when six of its nine flips were the
    corpus halving underneath the instrument (see goldv3/NOTES.md, task 1).
    """
    proc = subprocess.run([binary, "status", "--json"], capture_output=True, text=True)
    if proc.returncode not in (0, 3):
        raise Setup(f"{binary} status failed: {(proc.stderr or '').strip()[:200]}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise Setup(f"{binary} status was not JSON: {exc}") from exc
    emb = (payload.get("health") or {}).get("embedder") or {}
    return {
        "documents": (payload.get("index_detail") or {}).get("documents"),
        "embedded_in_scope": emb.get("in_scope"),
        "gold_sha": gold_sha(),
    }


def check_comparable(baseline: dict, current: dict, drifted_ok: bool):
    """Refuse a comparison the fingerprints cannot support; describe one they can.

    Returns None when the fingerprints match, or a drift description to print
    when they differ and the caller said `--drifted-ok`. Raises Refused
    otherwise. The gold-set check is never overridable: two gold sets are two
    different question papers, and no flag makes their scores one experiment.
    """
    pinned = baseline.get("corpus")
    if not pinned:
        raise Refused(
            "the baseline carries no corpus fingerprint, so nothing can say "
            "whether it was measured on this corpus or a different one. Re-pin "
            "with --baseline; comparing across unknown corpora is how a corpus "
            "change gets read as a code regression.")
    if pinned.get("gold_sha") != current.get("gold_sha"):
        raise Refused(
            f"the baseline was pinned against gold set {pinned.get('gold_sha')} "
            f"and this run scores {current.get('gold_sha')} — different question "
            "papers. No override exists for this one.")
    drift = [
        f"{name}: {pinned.get(name)} -> {current.get(name)}"
        for name in ("documents", "embedded_in_scope")
        if pinned.get(name) != current.get(name)
    ]
    if not drift:
        return None
    if not drifted_ok:
        raise Refused(
            "the corpus moved since the baseline was pinned ("
            + "; ".join(drift) +
            "). Flips on a moved corpus may be drift rather than code — pass "
            "--drifted-ok to compare anyway with the drift printed beside the "
            "verdict, or re-pin with --baseline.")
    return ("corpus drift since the baseline was pinned: " + "; ".join(drift) +
            " — flips below may be drift, not code")


def load_gold() -> list:
    if not GOLD_SET.is_file():
        raise Setup(f"the frozen gold set is missing: {GOLD_SET}")
    entries = json.loads(GOLD_SET.read_text(encoding="utf-8")).get("entries") or []
    if not entries:
        raise Setup("the gold set carries no entries")
    # The schema control. A scored entry whose expected set parses empty is the
    # field-name bug class — `expected` read where the fixture says
    # `expected_note_paths` — which produced two false nulls in this arc, each a
    # clean-looking "0 of N" that was really N comparisons against an empty
    # list. Named per entry, so a partial hole is as loud as a total one.
    for e in entries:
        if e.get("stratum") == NEGATIVE_STRATUM:
            continue
        if not [p for p in (e.get(EXPECTED_FIELD) or []) if p]:
            raise Control(
                f"entry {e.get('id')!r} parsed with an empty {EXPECTED_FIELD!r}"
                + (" (it carries only expected_note_prefixes, which this eval "
                   "does not score)" if e.get("expected_note_prefixes") else
                   " — the field-name bug class; check the fixture's key names")
            )
    return entries


def check_canary(binary: str) -> None:
    """The planted note must come back at rank 1, or nothing gets scored.

    Probed through the lexical arm, deliberately. The first live fire of this
    control found the hybrid competition burying the canary at rank 6 under four
    archive PLAN documents — fusion normalization drowning a one-token exact
    match under long-document dense mass, which is the corpus's known
    desk-outranks-memory behaviour, not an instrument fault. Liveness needs a
    deterministic answer: a unique token through FTS is rank 1 whenever the
    index is alive and attached, full stop. The dense arm's liveness is
    require_warm_embedder's job, and the hybrid path's sanity is the spread
    control's.
    """
    got = [path for path, _ in _search_rows(binary, CANARY_QUERY, 3,
                                            mode="and")]
    if not got or got[0] != CANARY_PATH:
        raise Control(
            f"the canary query returned {got[:2] or 'nothing'} instead of "
            f"{CANARY_PATH} at rank 1 — the index is dead, detached, or serving "
            "a corpus without the planted note. No number from this state is "
            "a measurement.")


def _search_rows(binary: str, question: str, k: int,
                 mode: str = None) -> list:
    """One query, as the recall hook issues it — including what it does after.

    Same mode, same `-question` for the dense arm, same extracted terms for the
    lexical arm, and the same three things the hook does around the call that
    this function used to skip:

    * **Over-fetch then filter.** The hook asks for `k * DAEMON_OVERFETCH` and
      truncates to `k` *after* filtering, so a rejected path is replaced from
      deeper in the ranking rather than leaving a hole. Asking for `k` directly
      measured a shorter list than the hook ever shows.
    * **Admissibility.** `_daemon_admissible` applies recall's directory rules
      to what the daemon returns. The daemon indexes `_inbox`, `scratch` and
      `_archive` by design and only rank-penalizes them; the hook drops them.
    * **Temporal bounds.** `_extract_temporal_bound` adds `-after` / `-before`
      when the question carries a date range, which the twelve-question
      episodic-temporal stratum exists to exercise.

    Reproducing the shape is the entire point of this harness, and for three
    questions (`dt01`, `ep10`, `ep12`) the difference was the whole result: the
    gold set marks them `hook_reachable: false` and the old baseline counted
    them as hits.

    Imported from `recall` rather than reimplemented. The module is already a
    dependency here for `_daemon_query_terms` and `DAEMON_SEARCH_MODE`, so this
    adds no new direction — and a second copy of the admissibility rules is a
    second thing to drift.
    """
    terms = recall._daemon_query_terms(question)
    if not terms:
        return []

    argv = [binary, "search", "-json",
            "-k", str(max(1, k) * recall.DAEMON_OVERFETCH),
            "-mode", mode or recall.DAEMON_SEARCH_MODE,
            "-question", question]
    temporal = recall._extract_temporal_bound(question)
    if temporal is not None:
        after, before = temporal
        if after:
            argv += ["-after", after]
        if before:
            argv += ["-before", before]
    argv.append(terms)

    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise Setup(f"search failed for {question[:60]!r}: "
                    f"{(proc.stderr or '').strip()[:200]}")
    payload = json.loads(proc.stdout or "{}")

    # `include_inbox` / `include_archive` are False here because that is what a
    # prompt-submit recall passes. A measurement run that admitted more than the
    # hook does would be scoring a system nobody uses.
    out = []
    for row in (payload.get("results") or []):
        if len(out) >= k:
            break
        path = row.get("path", "")
        if not path:
            continue
        if not recall._daemon_admissible(
            path, include_inbox=False, include_archive=False
        ):
            continue
        out.append((path, row.get("score")))
    return out


def search(binary: str, question: str, k: int) -> list:
    """The hook-shaped query, as paths. Scores stay internal to the controls."""
    return [path for path, _score in _search_rows(binary, question, k)]


def score(binary: str, entries: list, k: int) -> dict:
    """Run every question and return the per-question outcomes plus the summary."""
    per_question = {}
    hits = 0
    hits_at_1 = 0
    scored = 0
    rank_sum = 0
    ranked = 0
    false_positives = 0
    negatives = 0
    negatives_hard = 0
    false_positives_hard = 0

    all_scores = []
    for e in entries:
        question = e["question"]
        expected = [p for p in (e.get(EXPECTED_FIELD) or []) if p]
        rows = _search_rows(binary, question, k)
        got = [path for path, _score in rows]
        all_scores.extend(s for _path, s in rows if s is not None)

        if e.get("stratum") == NEGATIVE_STRATUM:
            negatives += 1
            hard = e.get("hardness") == "near-miss"
            if hard:
                negatives_hard += 1
            # A negative is answered correctly by not returning what the fixture
            # named. The original twenty carry empty banned lists, which made
            # `false_positives` structurally zero — a dial painted on, found and
            # documented in RULE-hard-negatives.md. The near-miss ten each ban
            # the ranker's own top hit at authoring, so this branch finally has
            # something to count.
            hit = False
            if expected:
                hit = any(p in got for p in expected)
                if hit:
                    false_positives += 1
                    if hard:
                        false_positives_hard += 1
            per_question[e["id"]] = {"hit": not hit, "negative": True,
                                     "rank": None, "hard": hard}
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
            if rank == 1:
                hits_at_1 += 1
        per_question[e["id"]] = {"hit": rank is not None, "negative": False, "rank": rank}

    # Rounded at the source, not for taste: a full-precision float carries a
    # ten-digit decimal run, and the PII gate reads that as a US phone number.
    # Fourth occurrence in this arc; the writer is where it stays fixed.
    # The spread control. A run whose every returned score is identical is a
    # flat or misattached scorer, not a ranking — the floorless-rerank rule
    # named this exact state as "what a dead or misattached reranker produces,
    # not a verdict", and checked it by hand. Now the eval checks it every run.
    if len(all_scores) >= 2 and min(all_scores) == max(all_scores):
        raise Control(
            f"all {len(all_scores)} returned scores are identical "
            f"({all_scores[0]}) — a flat scorer is not ranking anything, and "
            "hit/miss counts read off it are noise wearing a metric's clothes.")

    return {
        "k": k,
        "scored": scored,
        "hits": hits,
        "r_at_k": round(hits / scored, 4) if scored else 0.0,
        # Informational, deliberately: R@5 stays the product metric because the
        # hook injects five, and the gate compares on hit/miss at k. R@1 is the
        # sensitive ordering instrument — 31 questions of headroom against 9 at
        # R@5 on the audited baseline — reported so ordering work has a number
        # to read without a new harness.
        "hits_at_1": hits_at_1,
        "r_at_1": round(hits_at_1 / scored, 4) if scored else 0.0,
        "avg_rank_to_first_hit": round(rank_sum / ranked, 4) if ranked else None,
        "negatives": negatives,
        "false_positives": false_positives,
        "negatives_hard": negatives_hard,
        "false_positives_hard": false_positives_hard,
        "negatives_easy": negatives - negatives_hard,
        "false_positives_easy": false_positives - false_positives_hard,
        "per_question": per_question,
    }


def wilson_ci(hits: int, n: int, z: float = 1.96) -> tuple:
    """Wilson 95% interval on a proportion.

    Wilson rather than normal approximation because n is 64 and the score sits
    near 0.75, where the normal interval overshoots 1.0 and undercovers. This is
    the interval the report prints so a reader sees the number's width — on the
    current baseline it is about twenty points wide, which is the honest context
    for any single-run comparison of two rankers.
    """
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def min_detectable_flips(alpha: float = 0.05) -> int:
    """The smallest all-one-way flip count the exact paired test can call.

    Derived from the test itself rather than hardcoded: the two-sided exact
    binomial p for k flips all in one direction is 2 * (1/2)^k, so this walks k
    upward until that clears alpha. At alpha 0.05 the answer is six — which is
    why every report prints it: a pre-registered bar smaller than this is a bar
    the instrument cannot resolve, and two of this arc's probe bars were exactly
    that.
    """
    k = 1
    while mcnemar_exact(0, k) >= alpha:
        k += 1
    return k


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
        # Symmetric with `regressed`, because a gate that can only ever say "not
        # worse" leaves an intervention worth +5 questions exiting identically to
        # one worth nothing — which is how improvements went unreadable for the
        # whole goldv2 arc.
        "improved": len(for_current) > len(against) and p < 0.05,
    }


def render(result: dict, provenance: str) -> str:
    lines = [
        f"corpus: {provenance}",
        f"scored {result['scored']} question(s) at k={result['k']}",
        f"  R@{result['k']}              : {result['r_at_k']:.3f} ({result['hits']} hits)",
    ]
    if "r_at_1" in result:
        lines.append(f"  R@1 (informational): {result['r_at_1']:.3f} "
                     f"({result['hits_at_1']} first-slot hits)")
    n = result["scored"]
    if n:
        lo, hi = wilson_ci(result["hits"], n)
        flips = min_detectable_flips()
        # The report states its own resolution, every run, so a bar below it
        # cannot be pre-registered by someone who never saw the number.
        lines.append(f"  Wilson 95% CI      : [{lo:.3f}, {hi:.3f}] "
                     f"({(hi - lo) * 100:.1f}pp wide)")
        lines.append(f"  smallest clean gain: {flips} flips one way "
                     f"(+{flips / n:.1%}) — smaller true effects are invisible here")
    if result["avg_rank_to_first_hit"] is not None:
        lines.append(f"  rank to first hit  : {result['avg_rank_to_first_hit']:.2f}")
    if result.get("negatives_hard"):
        lines.append(
            f"  negatives (easy)   : {result['negatives_easy']}, "
            f"{result['false_positives_easy']} answered wrongly — empty banned "
            f"lists; structurally quiet")
        lines.append(
            f"  negatives (hard)   : {result['negatives_hard']}, "
            f"{result['false_positives_hard']} served their banned note — the "
            f"line a rejection floor would move")
    else:
        lines.append(f"  negatives          : {result['negatives']}, "
                     f"{result['false_positives']} answered wrongly")
    return "\n".join(lines)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="retrieval eval against the shipped ranker")
    ap.add_argument("--baseline", metavar="OUT", help="score and write the result here")
    ap.add_argument("--compare", metavar="BASELINE", help="score and compare against this")
    ap.add_argument("--verify-determinism", action="store_true",
                    help="run twice and assert the two runs agree")
    ap.add_argument("--drifted-ok", action="store_true",
                    help="compare even though the corpus moved since the "
                         "baseline was pinned; the drift is printed beside the "
                         "verdict (the standing tripwire's mode — experiments "
                         "should re-pin instead)")
    ap.add_argument("-k", type=int, default=DEFAULT_K)
    args = ap.parse_args(argv)

    binary = daemon_binary()
    try:
        provenance = require_warm_embedder(binary)
        entries = load_gold()
        check_canary(binary)
    except Setup as exc:
        print(f"eval-retrieval-shipped: {exc}", file=sys.stderr)
        return 2
    except Control as exc:
        print(f"\nINSTRUMENT CONTROL FIRED: {exc}", file=sys.stderr)
        return 4

    try:
        first = score(binary, entries, args.k)
    except Setup as exc:
        print(f"eval-retrieval-shipped: {exc}", file=sys.stderr)
        return 2
    except Control as exc:
        print(f"\nINSTRUMENT CONTROL FIRED: {exc}", file=sys.stderr)
        return 4

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
        pinned = dict(first)
        pinned["corpus"] = {**corpus_fingerprint(binary),
                            "pinned": date.today().isoformat()}
        Path(args.baseline).write_text(json.dumps(pinned, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
        print(f"\nbaseline written to {args.baseline} "
              f"(corpus: {pinned['corpus']['documents']} documents)")

    if args.compare:
        baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        try:
            drift = check_comparable(baseline, corpus_fingerprint(binary),
                                     args.drifted_ok)
        except Refused as exc:
            print(f"\nCOMPARISON REFUSED: {exc}", file=sys.stderr)
            return 3
        if drift:
            print(f"\n{drift}")
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
        if cmp["improved"]:
            print("\nIMPROVEMENT: more questions got better than worse, and the "
                  "flip count is unlikely under chance. Worth re-pinning.")
        else:
            print("\nno significant regression")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
