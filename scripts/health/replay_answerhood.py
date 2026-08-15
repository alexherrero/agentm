#!/usr/bin/env python3
"""replay_answerhood.py — the probe harness, promoted as the replay instrument.

`wiki/designs/agentm-rejection-and-vocabulary.md` § Testability commits to this:
the harness that produced the design's evidence lives in the repo rather than in
a scratch directory, because a result nobody can re-run is a claim.

**What it replays.** Every candidate set the agent was actually served during the
agent-layer gate, recovered from the recorded replicates — `week3_daemon_shim.py`
logs `result_paths` per call and `week3_daemon_retest.py` folds that into
`per_question.tool_call_log`. Replaying the real failing run beats re-running it:
it tests the labeller against the candidates that actually produced the 45
failures, not against a fresh draw.

**Both halves, because one is not a result.** A labeller that keeps everything
scores 100% preservation and 0% rejection; one that drops everything scores the
reverse. The separation claim needs both, which is the rule the original probe
was written under and the reason its verdict was trustworthy.

    negative half   — every served call of every negative trial. A trial counts
                      as rejected only when NO candidate is labelled `answers`
                      on ANY call in it; anything labelled `answers` is
                      something the consumer will still be pointed at.
    answerable half — every served call where an expected note was returned. A
                      trial is preserved when an expected note is labelled
                      `answers` on at least one call.

**It shares the labeller's excerpt selector rather than carrying its own.** The
probe's first pass got that selector wrong and 43.2% of its apparent
over-rejections were the instrument; a replay tool with a second copy of it
would be free to drift back into exactly that error while the shipped path
stayed correct.

    python3 scripts/health/replay_answerhood.py \\
        --replicates "<vault>/Agent/_meta/health/goldv2/agent-layer-r*.json" \\
        --corpus ~/.agentm/corpus-snapshots/Vault \\
        --out "<vault>/Agent/_meta/health/goldv2/labeller-replay.json"
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import answerhood_labeller as al  # noqa: E402


def load_note(corpus: Path, rel: str) -> str:
    try:
        return (corpus / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def units_from(replicates: list[Path]) -> list[dict]:
    """One unit per served call, tagged with the half it belongs to."""
    out = []
    for path in replicates:
        doc = json.loads(path.read_text())
        rep = doc.get("run_label") or path.stem
        for q in doc["per_question"]:
            expected = q.get("expected") or []
            is_negative = not expected
            for call in (q.get("tool_call_log") or []):
                if not call.get("served"):
                    continue
                paths = call.get("result_paths") or []
                if not paths:
                    continue
                if not is_negative and not (set(paths) & set(expected)):
                    # The answerable half is about preservation, so a call that
                    # never returned an expected note has nothing to preserve
                    # and would dilute the rate toward whatever the labeller
                    # does on irrelevant input.
                    continue
                out.append({
                    "rep": rep,
                    "id": q["id"],
                    "stratum": q.get("stratum"),
                    "half": "negative" if is_negative else "answerable",
                    "question": q["question"],
                    "expected": expected,
                    "paths": paths,
                })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--replicates", required=True,
                    help="glob for the recorded agent-layer replicate JSONs")
    ap.add_argument("--corpus", required=True,
                    help="the frozen corpus copy the replicates were served from")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None,
                    help="first N units only — for a cheap smoke run")
    ap.add_argument("--exclude-stratum", default=None,
                    help="skip this stratum. Used to avoid re-buying a slice "
                         "already measured under the same frozen prompt — the "
                         "episodic run that froze the prompt is a valid task-6 "
                         "measurement and does not need paying for twice")
    ap.add_argument("--stratum", default=None,
                    help="score only this stratum; prompt refinement runs on the "
                         "episodic-temporal slice alone, for cents, before the "
                         "full replay is bought")
    ap.add_argument("--half", default=None, choices=["negative", "answerable"],
                    help="score only one half. Refinement targets preservation, "
                         "but a prompt loosened on the answerable half must then "
                         "be re-measured on BOTH — the same knob moves each, "
                         "which is why task 6 re-measures rejection rather than "
                         "inheriting it")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the units and print the shape without calling a model")
    ap.add_argument("--legacy-prompt", action="store_true",
                    help="use the original probe's strict-answerhood wording "
                         "instead of the shipped prompt. The two differ in one "
                         "clause — whether a derived answer counts — so running "
                         "both over the same slice on the same instrument is "
                         "what isolates the prompt from everything else")
    args = ap.parse_args(argv)

    replicates = sorted(Path(p) for p in glob.glob(args.replicates))
    if not replicates:
        raise SystemExit(f"no replicate JSONs matched {args.replicates!r}")
    corpus = Path(args.corpus)
    if not corpus.is_dir():
        raise SystemExit(f"corpus not found: {corpus}")

    if args.legacy_prompt:
        # The probe's exact wording, kept verbatim so the comparison is against
        # what actually produced the recorded evidence rather than a paraphrase
        # of it. `keep` is accepted by the shipped parser for this reason.
        al.PROMPT = """You are a retrieval gate. A search returned candidate notes for a query. \
For each candidate, decide whether that note actually ANSWERS the query.

Keep a candidate only if a reader of that note could state the answer to the query from it.
Drop it if it is merely about a related topic, mentions the subject in passing, or \
discusses the area without containing the specific answer.

It is normal and correct for the answer to be NONE of them. Many queries have no answer \
in this corpus at all. If no candidate answers the query, return an empty keep list.

QUERY: {question}

CANDIDATES:
{candidates}

Return only JSON, no prose, no code fence: {{"keep": [indices of candidates to keep]}}"""
        print("using the probe's original strict-answerhood prompt", flush=True)

    units = units_from(replicates)
    if args.stratum:
        units = [u for u in units if u["stratum"] == args.stratum]
    if args.exclude_stratum:
        units = [u for u in units if u["stratum"] != args.exclude_stratum]
    if args.half:
        units = [u for u in units if u["half"] == args.half]
    if args.limit:
        units = units[:args.limit]
    if not units:
        raise SystemExit("no units matched the given --stratum/--half filters")
    halves = defaultdict(int)
    for u in units:
        halves[u["half"]] += 1
    print(f"{len(replicates)} replicate(s) -> {len(units)} served calls "
          f"({dict(halves)})", flush=True)
    if args.dry_run:
        return 0

    # One document-frequency table over every note the whole run will show, the
    # way the probe built it. Built per call over <=20 candidates instead, IDF
    # flattens toward uniform and the selector slides back toward the raw counts
    # the probe had to correct — measured at 49.7% of long notes receiving a
    # different excerpt. The replay is the instrument, so it gets the good table
    # and reports which one it used.
    pool_paths = sorted({p for u in units for p in u["paths"][:al.MAX_CANDIDATES]})
    pool_df, pool_n = al.build_df([load_note(corpus, p) for p in pool_paths])
    print(f"document frequencies pooled over {len(pool_paths)} distinct notes",
          flush=True)

    def run(u):
        cands = [al.Candidate(path=p, text=load_note(corpus, p))
                 for p in u["paths"][:al.MAX_CANDIDATES]]
        res = al.label(u["question"], cands, df=pool_df, n_docs=pool_n)
        return {**u,
                "answering": [c.path for c in res.answering],
                "labelled": res.labelled,
                "note": res.note,
                "cost_usd": res.cost_usd,
                "error": res.error}

    done = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for n, r in enumerate(ex.map(run, units), 1):
            done.append(r)
            if n % 50 == 0:
                print(f"  {n}/{len(units)}  "
                      f"${sum(x['cost_usd'] for x in done):.2f}", flush=True)

    Path(args.out).write_text(json.dumps(done, indent=1), encoding="utf-8")

    # --- both halves, per trial ------------------------------------------
    trials = defaultdict(list)
    for r in done:
        trials[(r["rep"], r["id"])].append(r)

    neg_total = neg_rejected = 0
    ans_total = ans_preserved = 0
    per_stratum = defaultdict(lambda: [0, 0])
    for (_, qid), calls in trials.items():
        if calls[0]["half"] == "negative":
            neg_total += 1
            neg_rejected += all(not c["answering"] for c in calls)
        else:
            ans_total += 1
            kept = any(set(c["answering"]) & set(c["expected"]) for c in calls)
            ans_preserved += kept
            s = calls[0]["stratum"]
            per_stratum[s][1] += 1
            per_stratum[s][0] += kept

    degraded = sum(1 for r in done if not r["labelled"])
    print(f"\nwrote {args.out}")
    print(f"cost ${sum(r['cost_usd'] for r in done):.2f}  "
          f"degraded calls {degraded}/{len(done)}")
    if neg_total:
        print(f"negative rejection: {neg_rejected}/{neg_total} = "
              f"{neg_rejected/neg_total:.1%}")
    if ans_total:
        print(f"answers preserved:  {ans_preserved}/{ans_total} = "
              f"{ans_preserved/ans_total:.1%}")
    for s in sorted(per_stratum):
        got, tot = per_stratum[s]
        print(f"  {s:20s} {got}/{tot} = {got/max(tot,1):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
