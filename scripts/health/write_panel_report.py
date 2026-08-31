#!/usr/bin/env python3
"""Write the panel's results where the operator can review and overrule them.

Ordered so that stopping early still spends the reader's attention well: the
turns the graders split on come first, then the ones a majority carried, then
the unanimous ones. A reader who reads only the first section has seen every
place the panel was unsure of itself.

Each row carries the label, which graders voted for what, and why — so a
disagreement can be adjudicated from the page rather than by trusting a
verdict. The operator's ruling is what everything downstream is measured
against, so the sheet is built for overruling, not for approval.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build_label_worksheet as bw  # noqa: E402
import panel  # noqa: E402
import recall_traffic  # noqa: E402
import sufficient_context  # noqa: E402


def section(title: str, note: str, entries: list) -> list:
    if not entries:
        return []
    out = [f"## {title}", "", note, ""]
    out += entries
    return out


def render(item: dict, n: int) -> list:
    """One turn: the request, what came back, the votes, and the reasons."""
    c = panel.coalesce(item)
    votes = " · ".join(f"{g}=**{v}**" for g, v in c["votes"].items())
    lines = [
        f"### {n}. `{item['id']}`", "",
        f"**PANEL: {c['label'] or 'CONTESTED'}**  —  *{c['basis']}*", "",
        f"**AGREE? ** (leave blank to accept, or write your own label + why)",
        "", f"votes: {votes}", "",
    ]
    prompt = (item.get("prompt") or "").strip()
    pf = bw.fence_for(prompt)
    lines += ["**The request**", "", pf, prompt[:2000], pf, ""]

    notes = bw.split_notes(item.get("context") or "")
    lines += [f"**What came back** — {bw.inventory(notes)}", ""]
    for note in notes:
        if note["slug"]:
            kind = bw.note_type(note["slug"], note["kind"], note["body"],
                                note.get("tags"))
            st = bw.note_status(note["body"])
            lines.append(f"- [[{note['slug']}]] · {kind}"
                         f"{' · ' + st if st else ''} — "
                         f"{bw.note_title(note['body'])[:80]}")
    lines.append("")

    cov = bw.term_coverage(item.get("prompt"), item.get("context"))
    if cov["missing"]:
        lines += ["**Words from the request absent from every note:** "
                  + ", ".join(f"`{w}`" for w in cov["missing"][:20]), ""]

    for grader, key in (("Claude", "claude_why"), ("Fable", "fable_why")):
        why = item.get(key) or []
        if why:
            lines.append(f"**{grader} says missing:**")
            lines += [f"- {w[:220]}" for w in why[:4]]
            lines.append("")
    lines.append("---")
    lines.append("")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args(argv)

    data = json.loads(args.panel.read_text(encoding="utf-8"))
    rows = data["rows"]

    turns = {}
    for t in recall_traffic.iter_injections(with_text=True):
        turns.setdefault(
            sufficient_context.grouped_hash(t.get("prompt_hash")), t)
    for r in rows:
        t = turns.get(r["id"])
        if t:
            r["prompt"] = t.get("_prompt")
            r["context"] = t.get("_injected")

    split, carried, agreed = [], [], []
    for r in rows:
        c = panel.coalesce(r)
        vals = set(c["votes"].values())
        (split if c.get("contested") else
         carried if len(vals) > 1 else agreed).append(r)

    body = ["# Recall sufficiency — the panel's results", "",
            "Three graders answered the same question on each turn: **Sonnet** "
            "(the production judge, and the thing being evaluated), **Gemini** "
            "via `agy` (a different model family), and **Fable** (stronger and "
            "four times the cost, so it ran only where it decided something — "
            "every turn the first two split on, plus fifteen they agreed on as "
            "a control).", "",
            "**Read this expecting to overrule it.** Your ruling is what "
            "everything downstream is measured against; the panel is a "
            "proposal. Where the graders disagreed comes first, so stopping "
            "early still spends your attention where the panel was least sure "
            "of itself.", "",
            "**What this is not.** Two of the three graders are Claude-family "
            "and the judge being scored is also Claude, so their agreeing is "
            "partly agreement about being the same kind of thing. This is a "
            "machine-adjudicated label set, not ground truth.", "",
            "---", ""]

    n = 0
    for title, note, group in (
        ("Where the graders could not agree", "No majority. These are the "
         "turns the panel genuinely could not settle, and a coin-flip dressed "
         "as a decision is the failure this whole arc keeps finding — so they "
         "are handed over unresolved.", split),
        ("Where a majority carried it", "One grader dissented. Worth a look: "
         "the dissent is named in the basis line.", carried),
        ("Where every grader agreed", "Unanimous. Skim unless something looks "
         "wrong — though the control found no case of the pair being wrong "
         "together, fifteen turns cannot rule out much below one in five.",
         agreed),
    ):
        entries = []
        for r in group:
            n += 1
            entries += render(r, n)
        body += section(title, note, entries)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(body), encoding="utf-8")
    print(f"{args.out}  ({len(rows)} turns)")
    print(f"  no majority : {len(split)}")
    print(f"  majority    : {len(carried)}")
    print(f"  unanimous   : {len(agreed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
