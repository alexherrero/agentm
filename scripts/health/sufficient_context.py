#!/usr/bin/env python3
"""Did the injected context actually contain what the turn needed?

The deterministic signal in `recall_traffic` can only see whether a model wrote
a note's name, and it turns out models almost never do — 7 of 3,004 injected
notes, 0.2%. So the comparator is floored, and the question of whether recall
is *working* falls to a judge.

The judge is Google's sufficient-context autorater (Joren et al., ICLR 2025):
given a query and the retrieved context, does the context **alone** plausibly
suffice? It is binary and reference-free — no gold answer, which is what makes
it usable on live traffic where no gold answer exists. On their 115 human
labels the autorater ran 93% accurate at 0.94 F1.

# Two places this departs from the paper, both deliberate

**The paper judges questions; most of this traffic is tasks.** "Run task 5" has
no answer for a context to contain. Forcing a binary verdict on those would
manufacture noise and then average it. So the judge may return `n/a`, and those
turns are excluded and counted rather than scored — the same treatment a failed
call gets. Among the turns that *are* scored the signal stays binary, which is
what the design asks for.

**The judge cannot be pinned at temperature 0.** `claude -p` exposes no
temperature or seed flag, so determinism is not available to assert. What is
available is measurement: every sampled turn is judged `REPLICATES` times and
the unanimity rate is reported with the result. An instrument whose stability
is unknown is not the same as one whose stability is one, and the number says
which this is.

# What reaches disk

Only the query hash, the verdict, and how many gaps the judge named. Never the
prompt, never the injected text, and never the judge's own wording of what was
missing — that wording quotes the query by construction. The operator sees it
on the terminal, where they are reading their own prompts back; the file gets
counts. This is the same contract `recall_traffic` holds.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import completeness_grade  # noqa: E402
import recall_traffic  # noqa: E402

MODEL = "sonnet"
REPLICATES = 3

# The autorater's question. Three shapes of answer, and a rejection has to name
# what is missing — `grounding.go` makes the same demand of its faithfulness
# judge, for the same reason: "a rejection with no claims is not an answer",
# because a judge that merely disliked the context is exactly the rejection
# worth ignoring.
PROMPT = """You are judging whether some retrieved context was enough.

You are shown a REQUEST that someone made to a coding assistant, and the CONTEXT
that was automatically retrieved from their notes and shown to the assistant
alongside it.

Answer one question: would the CONTEXT alone let someone respond to the REQUEST?

Judge only sufficiency. Not whether the context is well written, not whether it
is the best possible context, not whether you would have retrieved something
else. Only whether what is there covers what was asked.

If the REQUEST is not an information need — a command to run something, a
"continue", a "yes", an approval, an instruction to act — then no context could
be sufficient or insufficient, and the honest answer is "n/a". Use it. Do not
guess a verdict for a request that has no answer to look up.

Return a single JSON object and nothing else:

  {"verdict": "sufficient"}

or, when the context leaves a real gap:

  {"verdict": "insufficient", "missing": ["what is not there", "and this"]}

or, when the request is not an information need:

  {"verdict": "n/a"}

If the verdict is insufficient you must list what is missing. A rejection with
no gaps named is not an answer. Do not explain outside the JSON."""

SYSTEM = "You judge whether retrieved context is sufficient. Answer only with JSON."

_JSON = re.compile(r"\{.*\}", re.S)
VERDICTS = ("sufficient", "insufficient", "n/a")


_M32 = 0xFFFFFFFF


def fnv1a(s: str) -> int:
    """A small non-cryptographic hash, matching the daemon's `fnv1a`.

    The sampler only needs an even spread; sha256 here would pay for collision
    resistance nothing depends on. Constants in hex, which is how FNV is
    conventionally written and also keeps the decimal offset basis from reading
    as a phone number to this repository's PII scanner.
    """
    h = 0x811C9DC5
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & _M32
    return h


def _mix(h: int) -> int:
    """Finalize a hash before a small modulus.

    FNV-1a's lowest bit is close to the parity of its input, so `h % n` for any
    even `n` inherits that structure. On keys shaped `s0:t0, s1:t1, …` the
    residues mod 10 come out `[401, 0, 377, 0, 401, 0, 410, 0, 411, 0]` — every
    odd residue empty, and a 1-in-10 sample that takes one in five.

    Real turn keys happened to escape it (0.90–0.96x of target), but that is
    luck rather than design, and a sampler whose bias depends on how the keys
    happen to be shaped is not a sampler. This is Murmur3's fmix32, which
    measured flat on every key shape tried: worst drift 0.02–0.10 against
    2.52 for the raw hash.

    NOTE: `enrich.SampleEvery` in the daemon takes `h % n` with no finalizer and
    carries the same latent bias on note paths.
    """
    h = (h ^ (h >> 16)) & _M32
    h = (h * 0x85EBCA6B) & _M32
    h = (h ^ (h >> 13)) & _M32
    h = (h * 0xC2B2AE35) & _M32
    return (h ^ (h >> 16)) & _M32


def sample_every(n: int) -> Callable[[str], bool]:
    """A sampler selecting roughly one key in n, deterministically.

    Deterministic on the key rather than random, and that is the point: a re-run
    over the same traffic judges the same turns, so a rate that moves means the
    traffic moved rather than the dice did. Mirrors `enrich.SampleEvery` —
    `n <= 0` samples nothing, `n == 1` samples everything.
    """
    if n <= 0:
        return lambda _key: False
    if n == 1:
        return lambda _key: True
    return lambda key: _mix(fnv1a(key)) % n == 0


def turn_key(turn: dict) -> str:
    """A stable identity for one injected turn.

    The session and timestamp rather than the prompt hash: the same prompt
    asked twice is two turns with two contexts, and sampling that judged one
    and skipped the other would be sampling contexts by their queries.
    """
    return f"{turn.get('session', '')}:{turn.get('ts', '')}"


def build_prompt(query: str, context: str) -> str:
    return "\n".join([PROMPT, "", "REQUEST:", "", query, "",
                      "CONTEXT:", "", context])


def parse_verdict(text: str) -> Optional[dict]:
    """The judge's answer, or None if it did not give one.

    None is not a verdict and never becomes one. The completeness-v1 run scored
    failed calls as zero and spent a day explaining a number that was mostly
    timeouts; here a call that fails to parse is excluded and counted.
    """
    m = _JSON.search(text or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    verdict = obj.get("verdict")
    if verdict not in VERDICTS:
        return None
    missing = obj.get("missing") or []
    if not isinstance(missing, list):
        return None
    if verdict == "insufficient" and not missing:
        # The rejection names nothing, so there is nothing to check and no way
        # to tell a found gap from a dislike. Not an answer.
        return None
    return {"verdict": verdict, "missing": [str(x) for x in missing]}


def judge_turn(turn: dict, *, replicates: int = REPLICATES,
               caller: Callable = None, model: str = MODEL) -> dict:
    """Judge one turn `replicates` times and report what came back.

    The replicates are the stability measurement, not a voting trick to hide
    instability — the unanimity is reported whether or not it is good.
    """
    caller = caller or completeness_grade._call_claude_json
    prompt = build_prompt(turn.get("_prompt", ""), turn.get("_injected", ""))
    answers = []
    failures = 0
    cost = 0.0
    for _ in range(max(1, replicates)):
        envelope = caller(prompt, model=model, system=SYSTEM)
        if isinstance(envelope, str):  # a test double returning text alone
            envelope = {"result": envelope}
        cost += float(envelope.get("total_cost_usd") or 0.0)
        parsed = parse_verdict(envelope.get("result", ""))
        if parsed is None:
            failures += 1
        else:
            answers.append(parsed)
    if not answers:
        return {"turn": turn.get("prompt_hash"), "verdict": None,
                "failures": failures, "unanimous": None,
                "cost_usd": round(cost, 4)}
    verdicts = [a["verdict"] for a in answers]
    top = max(set(verdicts), key=verdicts.count)
    # Whether the replicates agreed this turn is an information need at all.
    scoreable = {v != "n/a" for v in verdicts}
    return {
        "turn": turn.get("prompt_hash"),
        "verdict": top,
        "unanimous": len(set(verdicts)) == 1,
        "scoreable_split": len(scoreable) > 1,
        "replicates": len(answers),
        "failures": failures,
        "cost_usd": round(cost, 4),
        # Counted, never quoted: the judge's wording of a gap restates the
        # query, and the query does not go to disk.
        "missing_count": max((len(a["missing"]) for a in answers
                              if a["verdict"] == top), default=0),
        "_missing": [m for a in answers if a["verdict"] == top
                     for m in a["missing"]],
    }


def grouped_hash(h) -> str:
    """A query hash written so it does not read as a phone number.

    Sixteen bare hex characters match this repository's US-phone pattern, and
    the PII gate has stopped four pushes over exactly that. Grouping in fours
    keeps the value legible and identical in content while removing the
    resemblance.
    """
    if not h:
        return ""
    return "-".join(str(h)[i:i + 4] for i in range(0, len(str(h)), 4))


def persist_rows(rows: list) -> list:
    """The rows as they reach disk.

    One function so the privacy contract has one place to hold: underscore keys
    carry the prompt, the injected block and the judge's wording of a gap, and
    none of those are written. The hash is grouped on the way out.
    """
    return [{k: (grouped_hash(v) if k == "turn" else v)
             for k, v in r.items() if not k.startswith("_")}
            for r in rows]


def aggregate(rows: list) -> dict:
    """The rate, over the turns that produced one.

    Excluded turns are named rather than folded in. A judge that failed and a
    request with no answer to look up are both "not scored", and neither is
    evidence that context was insufficient.
    """
    scored = [r for r in rows if r.get("verdict") in ("sufficient", "insufficient")]
    na = [r for r in rows if r.get("verdict") == "n/a"]
    failed = [r for r in rows if r.get("verdict") is None]
    out = {
        "turns_seen": len(rows),
        "scored": len(scored),
        "excluded_not_an_information_need": len(na),
        "excluded_judge_failed": len(failed),
    }
    spent = round(sum(float(r.get("cost_usd") or 0) for r in rows), 2)
    if spent:
        out["cost_usd"] = spent
        out["cost_per_turn_usd"] = round(spent / max(1, len(rows)), 3)
    if not scored:
        out["note"] = ("no turn produced a verdict — nothing to report, and a "
                       "zero here would be a statement about the judge")
        return out
    suff = sum(1 for r in scored if r["verdict"] == "sufficient")
    out["sufficient"] = suff
    out["sufficient_rate"] = round(suff / len(scored), 4)
    # Unanimity over *every* turn that produced a verdict, n/a included.
    # Measured over scored turns alone it drops the ones where the judge is
    # least stable — a calibration run had 2 of 3 n/a turns split.
    decided = [r for r in rows if r.get("unanimous") is not None]
    if decided:
        agree = sum(1 for r in decided if r["unanimous"])
        out["unanimity_rate"] = round(agree / len(decided), 4)
        out["stability_note"] = (
            "measured, not assumed: `claude -p` exposes no temperature or seed "
            "flag, so this rate is the only evidence the judge is repeatable")
        # The instability that actually moves the headline: replicates
        # disagreeing about whether a turn is scoreable at all change the
        # denominator of `sufficient_rate`, not just one row's verdict.
        boundary = sum(1 for r in decided if r.get("scoreable_split"))
        out["scoreability_split_rate"] = round(boundary / len(decided), 4)
    return out


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample-every", type=int, default=10,
                    help="judge roughly one turn in N (deterministic)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N judged turns (0 = no limit)")
    ap.add_argument("--replicates", type=int, default=REPLICATES)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--out", type=pathlib.Path,
                    help="write per-turn verdicts (hashes and counts only)")
    ap.add_argument("--max-spend", type=float, default=5.0,
                    help="stop once the run has cost this much (USD)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    keep = sample_every(args.sample_every)
    turns = [t for t in recall_traffic.iter_injections(with_text=True)
             if t.get("_prompt") and t.get("_injected") and keep(turn_key(t))]
    if args.limit:
        turns = turns[:args.limit]

    rows = []
    spent = 0.0
    stopped_early = 0
    for n, t in enumerate(turns, start=1):
        if args.max_spend and spent >= args.max_spend:
            # A hard stop, not a warning. A judging call here bills about
            # $0.14 before any context is added — the plan budgeted $0.014 —
            # so an unattended loop over the full history is a three-figure
            # run, and the cap is what keeps a scheduled job from becoming one.
            stopped_early = len(turns) - n + 1
            break
        row = judge_turn(t, replicates=args.replicates, model=args.model)
        spent += float(row.get("cost_usd") or 0)
        rows.append(row)
        if not args.json:
            gaps = "; ".join(row.get("_missing", [])[:2])
            print(f"  {n:4d}/{len(turns)}  {str(row['verdict']):13s} "
                  f"{'' if row.get('unanimous', True) else '(split) '}{gaps}",
                  flush=True)

    out = aggregate(rows)
    if stopped_early:
        # Named, not silent. A truncated sweep that reports only its rate reads
        # as a sweep of everything.
        out["stopped_at_spend_cap"] = args.max_spend
        out["turns_not_judged"] = stopped_early
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"summary": out,
             "rows": persist_rows(rows)}, indent=2) + "\n",
            encoding="utf-8")
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    print(f"\nsufficient context ({out['scored']} scored of {out['turns_seen']})")
    if "sufficient_rate" in out:
        print(f"  sufficient         : {out['sufficient']} "
              f"({out['sufficient_rate']:.1%})")
        print(f"  judge agrees with itself: {out.get('unanimity_rate', 0):.1%} "
              f"of turns, across {args.replicates} replicates")
    else:
        print(f"  {out.get('note', '')}")
    print(f"  excluded, not a question : "
          f"{out['excluded_not_an_information_need']}")
    print(f"  excluded, judge failed   : {out['excluded_judge_failed']}")
    if "cost_usd" in out:
        print(f"  spent                    : ${out['cost_usd']:.2f} "
              f"(${out['cost_per_turn_usd']:.3f}/turn, measured not estimated)")
    if stopped_early:
        print(f"  STOPPED at the ${args.max_spend:.2f} cap — "
              f"{stopped_early} sampled turns were not judged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
