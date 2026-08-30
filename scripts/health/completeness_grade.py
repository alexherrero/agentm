#!/usr/bin/env python3
"""completeness_grade — how much of a source survived its rewrite.

The deterministic half runs first: `agentmd completeness --json` reads the
enrichment journal, draws a sample and splits each source into numbered claims.
This half asks a model, per note, which of those claims the rewrite still
carries, and turns the answers into a coverage fraction and a by-class report for
the corpus scorecard.

Claim by claim rather than as one impression, because the two questions get
different kinds of answer. *Is this rewrite complete?* returns a feeling that
moves with the model's mood. *Which of these eleven claims survive?* returns
eleven decisions a person can check by reading eleven lines, and the number
underneath is an average of things rather than an opinion about a thing.

Three replicates per note, median reported, spread published alongside. One run
of a model-driven scorer is not a measurement, and a scorecard that prints a
single-run number invites a trust it has not earned.

A call that fails is not a note that scored zero. It is excluded and counted
separately — the alternative makes every usage-limit hour look like a corpus that
lost its content overnight, which is both false and the kind of false that gets
acted on.

Usage:

    agentmd completeness --json | python3 completeness_grade.py --json
    python3 completeness_grade.py --pairs pairs.json --replicates 3
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from typing import Callable, Optional

MODEL = "sonnet"
TIMEOUT_SEC = 120
REPLICATES = 3

# How the judge is asked. The claims are numbered and the answer is numbers,
# so the model never has to reproduce a claim to say it survived — which is what
# keeps a paraphrase from reading as a miss.
PROMPT = """You are checking whether a rewritten note still carries what its source said.

Below are the SOURCE's claims, numbered, and the REWRITE.

For each claim, decide whether the REWRITE still carries it. A claim is carried
if the rewrite says the same thing in any words — condensing, rephrasing and
reordering all count as carrying it. A claim is missing only if a reader of the
rewrite alone would not learn it.

Return a single JSON object and nothing else:

  {"kept": [1, 2, 5]}

listing the numbers of the claims the rewrite still carries. An empty list is a
valid answer. Do not explain."""

_JSON = re.compile(r"\{.*\}", re.S)


def build_prompt(pair: dict) -> str:
    lines = [PROMPT, "", "SOURCE CLAIMS:", ""]
    for i, c in enumerate(pair["claims"], start=1):
        lines.append(f"{i}. {c}")
    lines += ["", "REWRITE:", "", pair.get("rewrite", "")]
    return "\n".join(lines)


SYSTEM = "You compare two texts and answer only with JSON."


def _call_claude(prompt: str, **kw) -> str:
    """One `claude -p` call, returning its text or "" on any failure."""
    return _call_claude_json(prompt, **kw).get("result", "")


def _call_claude_json(prompt: str, *, model: str = MODEL,
                      timeout: int = TIMEOUT_SEC, system: str = SYSTEM) -> dict:
    """One `claude -p` call, returning the whole response envelope.

    The envelope carries `total_cost_usd` alongside the text, and a judging loop
    that guesses its own cost from token estimates will be wrong — a call with
    an empty prompt bills about $0.14 here, because the CLI ships a large system
    prompt even with `--tools none`. Measured beats estimated.

    An empty dict is any failure: timeout, non-zero exit, or a launch error.

    MCP is stripped and hooks are disabled for the same two reasons the
    answerhood labeller gives: the servers cost roughly a minute of startup per
    call, and a live reflect hook would write into the operator's real vault from
    a grading run — a scorer that mutates the corpus it is scoring.
    """
    cmd = [
        "claude", "-p",
        "--model", model,
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--settings", '{"disableAllHooks":true}',
        "--output-format", "json",
        "--system-prompt", system,
        "--tools", "none",
    ]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    try:
        obj = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"result": proc.stdout}
    return obj if isinstance(obj, dict) else {"result": proc.stdout}


def parse_kept(text: str, n_claims: int) -> Optional[set]:
    """The claim numbers a reply names, or None if it did not answer.

    None rather than an empty set on a malformed reply, because those mean
    opposite things: an empty set is "the rewrite kept nothing", and None is "the
    judge did not say". Collapsing them scores a broken call as total loss.
    """
    m = _JSON.search(text or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    kept = obj.get("kept")
    if not isinstance(kept, list):
        return None
    out = set()
    for v in kept:
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        if 1 <= v <= n_claims:
            out.add(v)
    return out


def grade_pair(pair: dict, *, replicates: int = REPLICATES,
               caller: Callable = None) -> dict:
    """Coverage for one pair, over `replicates` independent calls."""
    caller = caller or _call_claude
    n = len(pair["claims"])
    runs, failures = [], 0
    for _ in range(replicates):
        kept = parse_kept(caller(build_prompt(pair)), n)
        if kept is None:
            failures += 1
            continue
        runs.append(len(kept) / n if n else 0.0)

    out = {"rel": pair["rel"], "class": pair.get("class", "untyped"),
           "claims": n, "replicates": len(runs), "failures": failures}
    if runs:
        out["coverage"] = round(statistics.median(runs), 4)
        # The spread is published rather than hidden behind the median, because a
        # note whose three runs disagree is not the same finding as one whose
        # three agree, and only one of them supports a number on a scorecard.
        out["spread"] = round(max(runs) - min(runs), 4)
    return out


def aggregate(rows: list) -> dict:
    """Per-class and overall coverage, over the rows that got an answer."""
    scored = [r for r in rows if "coverage" in r]
    by_class: dict = {}
    for r in scored:
        by_class.setdefault(r["class"], []).append(r["coverage"])

    return {
        "notes": len(rows),
        "scored": len(scored),
        "ungraded": len(rows) - len(scored),
        "coverage": round(statistics.mean(c for r in scored
                                          for c in [r["coverage"]]), 4)
        if scored else None,
        "by_class": {k: {"n": len(v), "coverage": round(statistics.mean(v), 4)}
                     for k, v in sorted(by_class.items())},
        "max_spread": round(max((r.get("spread", 0.0) for r in scored),
                                default=0.0), 4),
    }


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pairs", help="read pairs from this file instead of stdin")
    ap.add_argument("--replicates", type=int, default=REPLICATES,
                    help=f"model calls per note (default {REPLICATES})")
    ap.add_argument("--limit", type=int, default=0,
                    help="grade at most this many pairs (0 = all of them)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    raw = (open(args.pairs, encoding="utf-8").read() if args.pairs
           else sys.stdin.read())
    if not raw.strip():
        print("no input — pipe `agentmd completeness --json` in, or pass --pairs",
              file=sys.stderr)
        return 2
    doc = json.loads(raw)
    pairs = doc["pairs"] if isinstance(doc, dict) else doc
    if args.limit:
        pairs = pairs[:args.limit]

    rows = [grade_pair(p, replicates=args.replicates) for p in pairs]
    summary = aggregate(rows)
    summary["replicates"] = args.replicates

    if args.json:
        print(json.dumps({"summary": summary, "notes": rows},
                         indent=2, sort_keys=True))
        return 0

    print(f"graded {summary['scored']} of {summary['notes']} note(s)")
    if summary["ungraded"]:
        print(f"  {summary['ungraded']} ungraded — the judge did not answer; "
              "excluded rather than scored zero")
    if summary["coverage"] is None:
        print("no coverage: nothing was graded")
        return 0
    print(f"coverage   {summary['coverage']:.1%}")
    print(f"max spread {summary['max_spread']:.4f} across replicates")
    print("\nby class:")
    for k, v in summary["by_class"].items():
        print(f"  {k:14s} {v['coverage']:.1%}  (n={v['n']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
