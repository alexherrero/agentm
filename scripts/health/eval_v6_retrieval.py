#!/usr/bin/env python3
"""eval_v6_retrieval.py — V6-20 eval: RRF hybrid retrieval vs. the old merge formula.

PLAN-wave-e-v6-index task 5 verification: "V6-20's inaugural run replicates
the graph-ablation on the real vault against task 2's pre-registered query
set — reports the three named signals from ROADMAP-AgentMemoryV6.md:157
(accuracy · compression · discovery-rate)."

Signal definitions used here (grounded in the vault's own wording — see
research-dream-mode-design.md's E26 section and ROADMAP-AgentMemoryV6.md's
eval-harness-extension item; adapted from the corpus-consolidation sense to
a per-query ranking sense, since this task changes ranking, not corpus size):

  - accuracy: P@5 / R@5 against the 22 gold queries' expected-note sets.
  - compression: average rank position of the first expected-note hit
    (lower = fewer results need injecting for the same coverage — the
    per-query analogue of "coverage@K / size" when size is fixed at K=5
    and what varies is how deep into the ranking the answer sits).
  - discovery-rate: fraction of (query, expected-note) pairs the new
    formula finds in its top-5 that the old formula does NOT — genuinely
    new coverage contributed by BM25 + RRF + altitude, not just re-ranking
    what the old formula already found.

Environment note (honestly reported, not hidden): this session's sandbox
cannot load the sqlite-vec extension (`enable_load_extension` unsupported
on this Python build), so vector similarity is unavailable for BOTH the
old and new formula in this run — the comparison below is BM25-improved-
lexical-plus-altitude (new) vs. raw-keyword-count (old), not full
vector+lexical hybrid fusion. Still a genuine, fair, apples-to-apples
comparison (both formulas are equally vec-less here); re-run once
sqlite-vec is loadable to also measure the vector stream's contribution.

Graceful-skip contract: no reachable vault -> exit 0, skip (matches
eval_v6_graph.py's contract; CI has no vault access).

Usage:
    python3 scripts/health/eval_v6_retrieval.py [--vault-path PATH]
                                                 [--query-set PATH] [--jsonl-out PATH]
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_MEMORY_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_MEMORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SCRIPTS))

_DEFAULT_QUERY_SET = _REPO / "scripts" / "health" / "fixtures" / "v6-eval" / "query-set-v0.json"

HEALTH_SUITE = "eval-v6-retrieval"
HEALTH_AXIS = "memory persist+recall"

# The queued-plans copy of PLAN-wave-e-v6-index.md and the "<vault>/"
# placeholder prefix a few q14-q17 entries in query-set-v0.json carry (a v0
# authoring artifact, not a fixture bug worth a silent edit — see that
# file's own hash-pinning discipline in fixtures/v6-eval/README.md: a
# correction is a new hashed version, never a v0 mutation). Resolved here,
# at eval time, instead.
_PLACEHOLDER_PREFIX = "<vault>/"
_PLACEHOLDER_REAL_PREFIX = "projects/agentm/"


def _resolve_expected_path(raw: str) -> str:
    if raw.startswith(_PLACEHOLDER_PREFIX):
        return _PLACEHOLDER_REAL_PREFIX + raw[len(_PLACEHOLDER_PREFIX):]
    return raw


def _resolve_vault(arg_vault_path: str | None) -> Path | None:
    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return None


def _emit_jsonl(jsonl_out: str | None, check: str, passed: bool, weight: float = 1.0) -> None:
    if not jsonl_out:
        return
    record = {"suite": HEALTH_SUITE, "axis": HEALTH_AXIS, "check": check, "pass": passed, "weight": weight}
    with open(jsonl_out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _old_formula_top_k(vault: Path, query_text: str, k: int = 5) -> list[str]:
    """Reconstructs the pre-task-5 merge (sim x 0.85 + keyword x 0.05, raw
    _grep_search keyword count) — recall.py's live query() no longer runs
    this path, but every piece it was built from (_vec_search, _grep_search,
    the SIM_WEIGHT/KEYWORD_WEIGHT constants) is still present, unchanged, so
    this is a faithful reconstruction of the OLD baseline for comparison,
    not an approximation.
    """
    import recall

    query_tokens = recall._tokenize(query_text)
    try:
        vec_results = recall._vec_search(vault, query_text, k=max(k * 2, 10), mode="local")
    except Exception:
        vec_results = {}
    grep_results = recall._grep_search(vault, query_tokens)
    all_paths = set(vec_results.keys()) | set(grep_results.keys())
    scored = []
    for path in all_paths:
        sim = vec_results.get(path, 0.0)
        keyword = grep_results.get(path, 0)
        combined = sim * recall.SIM_WEIGHT + keyword * recall.KEYWORD_WEIGHT
        if combined > 0:
            scored.append((path, sim, combined))
    scored.sort(key=lambda r: (-r[2], -r[1], r[0]))
    return [p for p, _, _ in scored[:k]]


def _new_formula_top_k(vault: Path, query_text: str, k: int = 5) -> list[str]:
    import recall
    results = recall.query(vault=vault, query_text=query_text, k=k, mode="local")
    return [r["path"] for r in results]


# -----------------------------------------------------------------------------
# Decay-curve gate (auto-organization part 1, task 2) — a SECOND, independent
# comparison mode this same eval harness runs, distinct from the old-formula-
# vs-new-formula (V6-3) comparison above. This one isolates the stepped
# decay-curve retune (task 1) from the RRF fusion formula itself, which is
# unchanged: both sides of this comparison rank through the exact same live
# recall.query() pipeline; only which decay curve backs decay_score differs.
# -----------------------------------------------------------------------------

@contextlib.contextmanager
def _stepped_decay_curve():
    """Temporarily swap lifecycle.compute_decay_score for the shadow stepped
    variant (task 1), so recall.query()'s live RRF pipeline ranks using it
    for the duration of this context — restored on exit no matter what,
    including on exception. recall.py looks up `lifecycle.compute_decay_score`
    via the module attribute on every call (`import lifecycle` then
    `lifecycle.compute_decay_score(...)`, not a name bound at import time),
    so reassigning the attribute here is sufficient and nothing in recall.py
    or lifecycle.py's own source is modified — this stays an eval-harness-
    local substitution, never a change to either module's real behavior.
    """
    import lifecycle
    original = lifecycle.compute_decay_score
    lifecycle.compute_decay_score = lifecycle.compute_decay_score_stepped
    try:
        yield
    finally:
        lifecycle.compute_decay_score = original


def _new_formula_top_k_with_stepped_decay(vault: Path, query_text: str, k: int = 5) -> list[str]:
    """Same RRF pipeline as _new_formula_top_k, ranked with the shadow
    stepped decay curve substituted in — the task 2 comparison this backs
    isolates the decay-curve retune from the fusion formula, which this
    function does not touch."""
    with _stepped_decay_curve():
        return _new_formula_top_k(vault, query_text, k=k)


def run_eval(
    vault: Path,
    query_set_path: Path,
    *,
    old_top_k_fn=_old_formula_top_k,
    new_top_k_fn=_new_formula_top_k,
) -> dict:
    doc = json.loads(query_set_path.read_text(encoding="utf-8"))
    queries = doc["entries"]

    old_p_at_5 = old_r_at_5 = 0.0
    new_p_at_5 = new_r_at_5 = 0.0
    old_first_hit_ranks: list[int] = []
    new_first_hit_ranks: list[int] = []
    discovery_pairs = 0
    total_expected_pairs = 0
    n = len(queries)

    per_query: list[dict] = []

    for q in queries:
        expected = [_resolve_expected_path(p) for p in q["expected_notes"]]
        old_top = old_top_k_fn(vault, q["query"], k=5)
        new_top = new_top_k_fn(vault, q["query"], k=5)

        old_hits = [e for e in expected if e in old_top]
        new_hits = [e for e in expected if e in new_top]

        old_p_at_5 += len(old_hits) / 5.0
        new_p_at_5 += len(new_hits) / 5.0
        old_r_at_5 += len(old_hits) / len(expected) if expected else 0.0
        new_r_at_5 += len(new_hits) / len(expected) if expected else 0.0

        if old_hits:
            old_first_hit_ranks.append(min(old_top.index(e) + 1 for e in old_hits))
        if new_hits:
            new_first_hit_ranks.append(min(new_top.index(e) + 1 for e in new_hits))

        for e in expected:
            total_expected_pairs += 1
            if e in new_top and e not in old_top:
                discovery_pairs += 1

        per_query.append({
            "id": q["id"], "old_hits": len(old_hits), "new_hits": len(new_hits),
            "expected_count": len(expected),
        })

    result = {
        "queries_evaluated": n,
        "accuracy": {
            "old_p_at_5": old_p_at_5 / n if n else 0.0,
            "new_p_at_5": new_p_at_5 / n if n else 0.0,
            "old_r_at_5": old_r_at_5 / n if n else 0.0,
            "new_r_at_5": new_r_at_5 / n if n else 0.0,
        },
        "compression": {
            "old_avg_rank_to_first_hit": (
                sum(old_first_hit_ranks) / len(old_first_hit_ranks) if old_first_hit_ranks else None
            ),
            "new_avg_rank_to_first_hit": (
                sum(new_first_hit_ranks) / len(new_first_hit_ranks) if new_first_hit_ranks else None
            ),
            "old_queries_with_a_hit": len(old_first_hit_ranks),
            "new_queries_with_a_hit": len(new_first_hit_ranks),
        },
        "discovery_rate": {
            "new_found_old_missed_pairs": discovery_pairs,
            "total_expected_pairs": total_expected_pairs,
            "rate": discovery_pairs / total_expected_pairs if total_expected_pairs else 0.0,
        },
        "per_query": per_query,
    }
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--vault-path", default=None)
    p.add_argument("--query-set", default=str(_DEFAULT_QUERY_SET))
    p.add_argument("--jsonl-out", default=None)
    p.add_argument(
        "--decay-curve", choices=("exponential", "stepped"), default="exponential",
        help=(
            "exponential (default): the original V6-3 old-formula-vs-new-formula "
            "comparison, unchanged. stepped: auto-organization task 2's gate -- "
            "compares today's live RRF ranking (exponential decay) against the "
            "same RRF ranking with the shadow stepped decay curve (task 1) "
            "substituted in, isolating the decay-curve retune from the fusion "
            "formula, which is identical on both sides of this comparison."
        ),
    )
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    vault = _resolve_vault(args.vault_path)
    if vault is None or not vault.exists():
        print(
            "[eval-v6-retrieval] no reachable vault — skipping (graceful-skip; "
            "expected in CI, which has no access to the private vault).",
            file=sys.stderr,
        )
        return 0

    query_set_path = Path(args.query_set)
    if not query_set_path.exists():
        print(f"[eval-v6-retrieval] query set not found: {query_set_path} — skipping", file=sys.stderr)
        return 0

    if args.decay_curve == "stepped":
        result = run_eval(
            vault, query_set_path,
            old_top_k_fn=_new_formula_top_k,
            new_top_k_fn=_new_formula_top_k_with_stepped_decay,
        )
    else:
        result = run_eval(vault, query_set_path)
    print(json.dumps(result, indent=2))

    acc = result["accuracy"]
    comp = result["compression"]
    disc = result["discovery_rate"]

    accuracy_improved = acc["new_r_at_5"] > acc["old_r_at_5"] or acc["new_p_at_5"] > acc["old_p_at_5"]
    accuracy_regressed = acc["new_r_at_5"] < acc["old_r_at_5"] and acc["new_p_at_5"] < acc["old_p_at_5"]

    compression_improved = (
        comp["new_avg_rank_to_first_hit"] is not None
        and comp["old_avg_rank_to_first_hit"] is not None
        and comp["new_avg_rank_to_first_hit"] < comp["old_avg_rank_to_first_hit"]
    ) or comp["new_queries_with_a_hit"] > comp["old_queries_with_a_hit"]
    discovery_improved = disc["rate"] > 0.0

    # The plan's guard rule, applied: never merge on accuracy alone — must
    # also move compression or discovery-rate. Accuracy holding steady (not
    # improving, but not regressing either) is fine; regressing outright is
    # not, regardless of what the other two signals show.
    merge_gate_passed = not accuracy_regressed and (compression_improved or discovery_improved)

    label = (
        "auto-org task 2 (exponential decay vs. shadow stepped decay, same RRF fusion)"
        if args.decay_curve == "stepped" else "V6-3 RRF hybrid retrieval"
    )

    print(
        f"\n[eval-v6-retrieval:{args.decay_curve}] accuracy_improved={accuracy_improved} "
        f"accuracy_regressed={accuracy_regressed} "
        f"compression_improved={compression_improved} "
        f"discovery_improved={discovery_improved} "
        f"-> merge_gate_passed={merge_gate_passed}",
        file=sys.stderr,
    )

    _emit_jsonl(
        args.jsonl_out,
        f"{label}: R@5 {acc['old_r_at_5']:.3f}->{acc['new_r_at_5']:.3f}, "
        f"compression(avg-rank) {comp['old_avg_rank_to_first_hit']}->{comp['new_avg_rank_to_first_hit']}, "
        f"discovery-rate={disc['rate']:.3f}",
        merge_gate_passed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
