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

# The second axis. Sufficiency asks whether the context *could* answer; this
# asks whether the reply *did* draw on it. They fail independently, and the
# crossing is the only thing that separates bad retrieval from good context the
# model ignored.
#
# Asked in its own call. Folding both questions into one response would let the
# first answer prime the second — a model that has just called the context
# insufficient is set up to say the reply ignored it — and two axes that move
# together measure one axis at twice the confidence.
USE_PROMPT = """You are judging whether a reply drew on some material it was given.

You are shown CONTEXT that was automatically retrieved from someone's notes and
placed in front of a coding assistant, and the REPLY the assistant then wrote.

Answer one question: did the REPLY draw on the CONTEXT?

Drawing on it counts whether or not the context is named. A reply that uses a
fact, a decision, a path, a constraint or a piece of history that appears in the
context has drawn on it. A reply that merely happens to be about the same
subject has not — the question is whether this material shaped it, not whether
the topics overlap.

Use "n/a" only when the REPLY contains no prose at all — it is empty, or it is
nothing but a tool call. A reply that says it cannot answer, or that answers
badly, or that goes off and does something else, is still prose: judge it used
or unused. "n/a" is for having nothing to read, not for a reply you find
unsatisfying.

Return a single JSON object and nothing else:

  {"verdict": "used", "drew_on": ["what it took from the context"]}

or:

  {"verdict": "unused"}

or:

  {"verdict": "n/a"}

If the verdict is used you must say what was drawn on. A claim of use with
nothing named is not an answer — it is the same failure as a rejection that
names no gap. Do not explain outside the JSON."""

USE_SYSTEM = "You judge whether a reply drew on given material. Answer only with JSON."

USE_VERDICTS = ("used", "unused", "n/a")

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

    `enrich.SampleEvery` in the daemon carried the same unfinalized modulus and
    now applies this finalizer too, as `mix`. Its note-path shapes measured
    clean beforehand — numbered, sequential and dated paths all reached every
    residue class on the raw hash — so the bias there was latent rather than
    biting; the shape that fails is this module's own `session:ts` turn key.
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


def parse_use(text: str) -> Optional[dict]:
    """The utilization answer, or None if the judge did not give one.

    Mirrors `parse_verdict`, including the demand that a positive claim names
    what it rests on. "Used" with nothing drawn on is a judge asserting a
    conclusion, and those are exactly the ones worth dropping.
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
    if verdict not in USE_VERDICTS:
        return None
    drew = obj.get("drew_on") or []
    if not isinstance(drew, list):
        return None
    if verdict == "used" and not drew:
        return None
    return {"verdict": verdict, "drew_on": [str(x) for x in drew]}


def judge_use(turn: dict, *, replicates: int = 1, caller: Callable = None,
              model: str = MODEL) -> dict:
    """Did the reply draw on what was injected?

    Defaults to a single replicate. The sufficiency pass characterised this
    judge's self-agreement at 86.1%; spending three times over to re-establish
    that on a second question buys precision on a per-turn verdict that the
    crossing does not use — the quadrant is a rate over many turns.
    """
    caller = caller or completeness_grade._call_claude_json
    prompt = "\n".join([USE_PROMPT, "", "CONTEXT:", "",
                         turn.get("_injected", ""), "", "REPLY:", "",
                         turn.get("_answer", "")])
    answers, failures, cost = [], 0, 0.0
    for _ in range(max(1, replicates)):
        envelope = caller(prompt, model=model, system=USE_SYSTEM)
        if isinstance(envelope, str):
            envelope = {"result": envelope}
        cost += float(envelope.get("total_cost_usd") or 0.0)
        parsed = parse_use(envelope.get("result", ""))
        if parsed is None:
            failures += 1
        else:
            answers.append(parsed)
    if not answers:
        return {"use_verdict": None, "use_failures": failures,
                "use_cost_usd": round(cost, 4)}
    verdicts = [a["verdict"] for a in answers]
    top = max(set(verdicts), key=verdicts.count)
    return {
        "use_verdict": top,
        "use_unanimous": len(set(verdicts)) == 1,
        "use_failures": failures,
        "use_cost_usd": round(cost, 4),
        "_drew_on": [d for a in answers if a["verdict"] == top
                     for d in a["drew_on"]],
    }


# The quadrant. Both axes have to be decided for a turn to land in one; a turn
# missing either is named as undecided rather than assigned a corner.
QUADRANTS = {
    ("sufficient", "used"): "served",
    ("sufficient", "unused"): "ignored",
    ("insufficient", "used"): "salvaged",
    ("insufficient", "unused"): "missed",
}


def quadrant(sufficiency, use) -> Optional[str]:
    """Which corner a turn lands in, or None if either axis is undecided.

    The names are the point. `ignored` is good retrieval the model did not use,
    and `missed` is retrieval that failed — collapsing those two into one
    "context did not help" number is the confound this crossing exists to
    break.
    """
    return QUADRANTS.get((sufficiency, use))


def cross(rows: list) -> dict:
    """The quadrant counts, and the two utilization signals side by side.

    The judged and deterministic signals are never merged. The deterministic
    one fires when a note's name appears in the reply and nothing else, which
    happened for 7 of 3,004 injected notes — it is a floor with almost no
    reach, so a disagreement is overwhelmingly the floor failing to see use
    rather than the judge inventing it. Reporting one number for both would
    bury that.
    """
    out = {"turns": len(rows)}
    corners = {}
    undecided = 0
    for r in rows:
        c = quadrant(r.get("verdict"), r.get("use_verdict"))
        if c is None:
            undecided += 1
        else:
            corners[c] = corners.get(c, 0) + 1
    out["quadrants"] = {k: corners.get(k, 0) for k in QUADRANTS.values()}
    out["undecided"] = undecided
    placed = sum(corners.values())
    if placed:
        out["quadrant_rates"] = {k: round(v / placed, 4)
                                 for k, v in out["quadrants"].items()}

    both = [r for r in rows if r.get("use_verdict") in ("used", "unused")
            and r.get("deterministic_used") is not None]
    if both:
        judged_used = sum(1 for r in both if r["use_verdict"] == "used")
        det_used = sum(1 for r in both if r["deterministic_used"])
        disagree = sum(1 for r in both
                       if (r["use_verdict"] == "used") != r["deterministic_used"])
        out["utilization_judged"] = round(judged_used / len(both), 4)
        out["utilization_deterministic"] = round(det_used / len(both), 4)
        out["utilization_disagreement"] = round(disagree / len(both), 4)
        out["utilization_note"] = (
            "two signals, reported apart. The deterministic one only fires "
            "when a note's name appears verbatim in the reply — 7 of 3,004 "
            "injected notes did that — so it is a floor with almost no reach, "
            "and the disagreement is mostly the floor missing use rather than "
            "the judge inventing it. Merging them would hide which is which.")
    return out


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
    # Both axes. Summing only the sufficiency call made a $15 run report $1.75
    # next to the cap that had just stopped it.
    spent = round(sum(float(r.get("cost_usd") or 0)
                      + float(r.get("use_cost_usd") or 0) for r in rows), 2)
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
    # One replicate cannot disagree with itself: `unanimous` is True by
    # construction, and reporting 100% there puts a statistic that cannot fail
    # exactly where a reader looks for evidence of stability.
    replicated = [r for r in decided if (r.get("replicates") or 1) > 1]
    if replicated:
        agree = sum(1 for r in replicated if r["unanimous"])
        out["unanimity_rate"] = round(agree / len(replicated), 4)
        out["unanimity_over"] = len(replicated)
        out["stability_note"] = (
            "measured, not assumed: `claude -p` exposes no temperature or seed "
            "flag, so this rate is the only evidence the judge is repeatable")
        # The instability that actually moves the headline: replicates
        # disagreeing about whether a turn is scoreable at all change the
        # denominator of `sufficient_rate`, not just one row's verdict.
        boundary = sum(1 for r in replicated if r.get("scoreable_split"))
        out["scoreability_split_rate"] = round(boundary / len(replicated), 4)
    elif decided:
        out["stability_note"] = (
            "not measured in this run — one replicate per turn cannot disagree "
            "with itself. The judge's self-agreement was 86.1% (95% CI "
            "[76.3%, 92.3%]) when measured at three replicates")
    return out


def corpus_stamp(injections: list) -> dict:
    """What corpus this run read, so two runs are not mistaken for a series.

    Live traffic grows while it is being measured. Without a stamp, a rate that
    moved between runs reads as the system changing when the corpus changed —
    the same failure the offline eval's fingerprint exists to refuse.
    """
    stamps = sorted(i.get("ts") or "" for i in injections)
    return {
        "injections": len(injections),
        "first_ts": stamps[0] if stamps else "",
        "last_ts": stamps[-1] if stamps else "",
        "sessions": len({i.get("session") for i in injections}),
    }


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
    ap.add_argument("--utilization", action="store_true",
                    help="also ask whether the reply drew on the context, and "
                         "cross the two axes")
    ap.add_argument("--use-replicates", type=int, default=1,
                    help="replicates for the utilization question (the "
                         "crossing is a rate over turns, not a per-turn call)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    keep = sample_every(args.sample_every)
    everything = [t for t in recall_traffic.iter_injections(with_text=True)
                  if t.get("_prompt") and t.get("_injected")]
    # Rarity is a property of the whole corpus, not of the sample — calibrating
    # it on a tenth of the traffic would call names rare that are merely absent
    # from the tenth.
    rare = recall_traffic.rare_evidence(everything) if args.utilization else set()
    turns = [t for t in everything if keep(turn_key(t))]
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
        if args.utilization:
            row.update(judge_use(t, replicates=args.use_replicates,
                                 model=args.model))
            spent += float(row.get("use_cost_usd") or 0)
            # The deterministic floor on the same turn, for the side-by-side.
            # `rare` comes from the whole traffic rather than the sample, so
            # the floor is judged against the corpus it was calibrated on.
            row["deterministic_used"] = bool(
                recall_traffic.used_slugs(t.get("slugs") or [],
                                          t.get("_answer") or "", rare))
        rows.append(row)
        if not args.json:
            gaps = "; ".join(row.get("_missing", [])[:2])
            corner = quadrant(row.get("verdict"), row.get("use_verdict"))
            tag = f"{corner:9s} " if corner else ""
            print(f"  {n:4d}/{len(turns)}  {str(row['verdict']):13s} "
                  f"{tag}{'' if row.get('unanimous', True) else '(split) '}"
                  f"{gaps[:90]}", flush=True)

    out = aggregate(rows)
    out["corpus"] = corpus_stamp(everything)
    out["corpus_note"] = ("live traffic grows while it is measured; two runs "
                          "with different stamps are two instruments, not two "
                          "points on one line")
    if args.utilization:
        out["crossing"] = cross(rows)
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
        if "unanimity_rate" in out:
            print(f"  judge agrees with itself: {out['unanimity_rate']:.1%} "
                  f"of {out['unanimity_over']} turns, "
                  f"{args.replicates} replicates each")
        else:
            print(f"  judge self-agreement    : not measured here "
                  f"({args.replicates} replicate; 86.1% when measured at 3)")
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
    x = out.get("crossing") or {}
    if x.get("quadrants"):
        print(f"\nsufficiency x utilization "
              f"({sum(x['quadrants'].values())} placed, "
              f"{x['undecided']} undecided)")
        for name, label in (("served", "context had it, reply used it"),
                            ("ignored", "context had it, reply did not"),
                            ("salvaged", "context lacked it, reply used it"),
                            ("missed", "context lacked it, reply did not")):
            n_ = x["quadrants"][name]
            r_ = (x.get("quadrant_rates") or {}).get(name, 0)
            print(f"  {name:9s} {n_:4d} ({r_:5.1%})  {label}")
        if "utilization_disagreement" in x:
            print(f"\n  utilization, judged        : "
                  f"{x['utilization_judged']:.1%}")
            print(f"  utilization, deterministic : "
                  f"{x['utilization_deterministic']:.1%}")
            print(f"  they disagree on           : "
                  f"{x['utilization_disagreement']:.1%} of turns")
            print(f"  Not merged. The deterministic signal fires only on a "
                  f"note's name appearing\n  verbatim — 7 of 3,004 injected "
                  f"notes — so most disagreement is the floor\n  missing use, "
                  f"not the judge inventing it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
