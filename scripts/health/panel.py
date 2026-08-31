#!/usr/bin/env python3
"""Coalesce several graders into one label set, and say where they disagreed.

Three graders answer the same sufficiency question: Sonnet (the production
judge, the thing being evaluated), Gemini through `agy` (a different family),
and Fable (a stronger, slower Claude). An adversarial reviewer then attacks the
result where it is contested.

# Where the money goes

Fable costs $0.797 a turn against Sonnet's $0.203, so it does not run
everywhere. It runs on every turn the first two graders disagree about — the
contested ones, where a third opinion decides something — and on a random
sample of the turns they agree about, which is the part that actually matters:
two models from one lineage agreeing is exactly what a shared blind spot looks
like, and only a check on the *agreed* cases can find it.

# What this is and is not

It is a machine-adjudicated label set, not human ground truth. Two of the three
graders are Claude-family and the judge under evaluation is also Claude, so
agreement between them is partly agreement about being the same kind of thing.
The number this supports is "the cheap production judge tracks a stronger
panel at rate X", which is worth knowing and is not "the judge is correct".

The operator's review is what makes it more than circular. Where they overrule
the panel is itself the measurement: a handful of overrules means the panel is
sound, and many means it is not.
"""
from __future__ import annotations

import collections
import random
from typing import Optional

# Ordered from least to most claimed, so "who was more generous" is answerable.
RANK = {"n/a": 0, "insufficient": 1, "sufficient": 2}

CONTROL_SAMPLE = 15
CONTROL_SEED = 20260830


def contested(rows: list) -> list:
    """Turn ids where the first two graders differ."""
    return [r["id"] for r in rows
            if r.get("claude") and r.get("gemini")
            and r["claude"] != r["gemini"]]


def control(rows: list, *, n: int = CONTROL_SAMPLE,
            seed: int = CONTROL_SEED) -> list:
    """A random sample of turns the first two graders agreed on.

    Not a formality. If the two Claude-and-Gemini agreements are wrong together,
    nothing in the contested set would reveal it — a tiebreaker never sees the
    cases nobody tied on.
    """
    agreed = [r["id"] for r in rows
              if r.get("claude") and r.get("gemini")
              and r["claude"] == r["gemini"]]
    rng = random.Random(seed)
    agreed = sorted(agreed)
    rng.shuffle(agreed)
    return agreed[:n]


def coalesce(row: dict) -> dict:
    """One turn's verdicts reduced to a label, with the reason it is that.

    No silent majority. When the graders split without a majority the turn is
    returned `contested` with every verdict attached, because a coin-flip
    dressed as a decision is the failure this whole arc keeps finding.
    """
    votes = {k: row.get(k) for k in ("claude", "gemini", "fable")
             if row.get(k)}
    if not votes:
        return {"label": None, "basis": "no grader answered", "votes": {}}

    counts = collections.Counter(votes.values())
    top, n_top = counts.most_common(1)[0]
    tied = [v for v, c in counts.items() if c == n_top]

    if len(tied) > 1:
        return {"label": None, "basis": "graders split with no majority",
                "votes": votes, "contested": True}

    if len(votes) == 1:
        basis = f"only {list(votes)[0]} answered"
    elif n_top == len(votes):
        basis = f"all {len(votes)} graders agreed"
    else:
        dissent = [f"{k}={v}" for k, v in votes.items() if v != top]
        basis = f"{n_top} of {len(votes)}, against {', '.join(dissent)}"

    return {"label": top, "basis": basis, "votes": votes,
            "unanimous": n_top == len(votes) and len(votes) > 1}


def summarise(rows: list) -> dict:
    """The panel's own shape: how often it agreed, and with whom."""
    out: dict = {"turns": len(rows)}
    labels, contested_n, per_grader = [], 0, collections.defaultdict(
        collections.Counter)
    unanimous = 0
    for r in rows:
        c = coalesce(r)
        if c.get("contested"):
            contested_n += 1
        if c["label"]:
            labels.append(c["label"])
        if c.get("unanimous"):
            unanimous += 1
        for g in ("claude", "gemini", "fable"):
            if r.get(g):
                per_grader[g][r[g]] += 1
    out["labelled"] = len(labels)
    out["contested_no_majority"] = contested_n
    out["unanimous"] = unanimous
    out["distribution"] = dict(collections.Counter(labels))
    out["per_grader"] = {g: dict(c) for g, c in per_grader.items()}

    # How often the production judge matches the panel it is being scored by.
    graded = [r for r in rows if r.get("claude")]
    matched = 0
    for r in graded:
        c = coalesce(r)
        if c["label"] and c["label"] == r["claude"]:
            matched += 1
    if graded:
        out["production_judge_matches_panel"] = round(matched / len(graded), 4)
        out["production_judge_note"] = (
            "not accuracy — the panel contains the judge and two of its three "
            "graders are the same model family, so this is how far a cheap "
            "judge tracks an expensive one, not how often it is right")
    return out


def disagreement_report(rows: list) -> list:
    """Every turn worth a person's attention, hardest first.

    Ordered by how split the graders were, so a reader who stops early has
    spent their time where the panel was least sure of itself.
    """
    out = []
    for r in rows:
        c = coalesce(r)
        votes = c["votes"]
        if len(set(votes.values())) <= 1:
            continue
        spread = max(RANK[v] for v in votes.values()) \
            - min(RANK[v] for v in votes.values())
        out.append({"id": r["id"], "label": c["label"], "basis": c["basis"],
                    "votes": votes, "spread": spread,
                    "contested": bool(c.get("contested"))})
    out.sort(key=lambda x: (not x["contested"], -x["spread"]))
    return out
