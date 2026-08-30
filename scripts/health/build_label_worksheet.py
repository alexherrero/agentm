#!/usr/bin/env python3
"""Draw the labelling sample and write the operator's worksheet.

# Any prefix is a sample

The pool is drawn uniformly from the corpus, and the worksheet presents it in a
deterministic shuffle. That combination buys something worth more than a
cleverer design: **any prefix of the worksheet is itself a random sample**. The
operator can label forty turns or all of them and the labelled set is unbiased
either way, while whatever is left over is exactly the unlabelled pool
prediction-powered inference wants.

So nothing is held back, nothing has to be finished, and stopping early costs
precision rather than validity.

An earlier version split the draw in two — a uniform set for PPI and an
enriched set to give κ more signal in the rare strata. That was designed
against a pool expected to run to several hundred turns. Judging cost $0.203
per turn rather than the $0.03 projected, the pool is 139, and holding thirty
of those back would have taken them out of the only sample there is. The
question the enriched set existed to answer — does the judge do worse where it
is rare? — is answered instead by reporting agreement per stratum over whatever
gets labelled.

# What goes where

The worksheet holds the operator's own prompts and notes and is written to the
vault. The repo fixture holds hashes, strata and judge verdicts — no prompt
text, no note text — the same contract every other file in this arc keeps.

The judge's verdict is not in the worksheet, and neither is the assistant's
reply. A good reply makes thin context look sufficient, and the judge's answer
is the thing being measured against.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import recall_traffic  # noqa: E402
import sufficient_context  # noqa: E402

# The shuffle that makes every prefix a sample. Fixed, so the order can be
# reproduced and a partially-labelled worksheet still says which turns were
# seen first.
SEED = 20260830


def band(n_slugs: int) -> str:
    """Hit-count band, from the measured distribution rather than round numbers.

    684 injections split 31 / 139 / 514 across one note, two-to-four, and the
    five-note cap. Three bands is what the data has.
    """
    if n_slugs <= 1:
        return "thin"
    if n_slugs <= 4:
        return "mid"
    return "full"


def stratum(row: dict, n_notes: int = None) -> str:
    """Band and verdict.

    `n_notes` is passed explicitly because a judged pool row does not carry the
    slugs — reading `row["slugs"]` returned an empty list for every turn and
    banded the entire sample as `thin`, which the stratum printout is what
    caught.
    """
    if n_notes is None:
        n_notes = row.get("n_notes")
    if n_notes is None:
        n_notes = len(row.get("slugs") or [])
    return f"{band(n_notes)}/{row.get('verdict') or 'none'}"


def order(pool: list, *, seed: int = SEED) -> list:
    """The pool in a fixed shuffled order, so any prefix is a random sample.

    Sorted first, because the pool arrives in whatever order a JSON file
    happened to hold and a shuffle seeded over an unstable order is not
    reproducible.
    """
    out = sorted(pool, key=lambda r: r.get("turn") or "")
    random.Random(seed).shuffle(out)
    return out


BATCH = 20
# How much of each injected note to show. The question is whether the right
# material was retrieved, which its opening almost always settles; the full note
# is one click away when it does not.
NOTE_CHARS = 1100

# The whole parenthetical is consumed, not just up to the score. Stopping at
# the score left the rest of the header — "daemon-hybrid, space: desk)" — as the
# first line of every note body.
_NOTE_HEAD = re.compile(
    r"^### (\S+) \(kind: ([^,]+), score=([^ )]+)[^)\n]*\)[ \t]*\n?", re.M)


def split_notes(block: str) -> list:
    """The injected block as its individual notes.

    Falls back to one unnamed chunk when the block does not have the expected
    headers — a worksheet that silently showed nothing would be worse than one
    that shows an unsplit wall.
    """
    heads = list(_NOTE_HEAD.finditer(block or ""))
    if not heads:
        return [{"slug": None, "kind": None, "score": None,
                 "body": (block or "").strip()}]
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(block)
        out.append({"slug": m.group(1), "kind": m.group(2),
                    "score": m.group(3),
                    "body": block[m.end():end].strip()})
    return out


def render_turn(n: int, item: dict) -> list:
    lines = [f"## {n}. `{item['id']}`", "", "**LABEL: ?**", "",
             "### The request", "", "```",
             (item["prompt"] or "").strip()[:3000], "```", "",
             f"### What was retrieved ({item['n_notes']} notes)", ""]
    for note in split_notes(item.get("context") or ""):
        if note["slug"]:
            lines.append(f"**[[{note['slug']}]]**  ·  {note['kind']}  ·  "
                         f"score {note['score']}")
        body = note["body"]
        clipped = body[:NOTE_CHARS]
        lines += ["", "> " + "\n> ".join(clipped.splitlines()[:22])]
        if len(body) > len(clipped):
            lines.append(f">")
            lines.append(f"> *… {len(body) - len(clipped):,} more characters — "
                         f"open the note if the opening does not settle it.*")
        lines.append("")
    lines += ["---", ""]
    return lines


def worksheet(items: list, rubric_path: str, *, batch: int = None,
              of: int = None) -> str:
    """One batch of the operator's file. No judge verdict, no assistant reply."""
    where = f" — batch {batch} of {of}" if batch else ""
    out = [
        f"# Recall labelling{where}",
        "",
        f"**{len(items)} turns.** Read [the rubric]({rubric_path}) first; it is "
        "frozen and was committed before this sample was drawn.",
        "",
        "For each turn, replace `LABEL: ?` with `sufficient`, `insufficient`, "
        "or `n/a`. Add `FLAG: no_note_possible` on the line under it when the "
        "request *is* an information need but no note could ever have answered "
        "it.",
        "",
        "You are **not** shown the judge's verdict or the assistant's reply. "
        "That is deliberate: a good reply makes thin context look sufficient, "
        "and the judge's answer is the thing being measured against you.",
        "",
        "Each note is shown from the top, cut after about a thousand "
        "characters. The question is whether the right material was retrieved, "
        "which the opening usually settles — the full note is one click away "
        "when it does not.",
        "",
        "**Stop whenever you like, including between batches.** The order is a "
        "fixed shuffle of a sample already drawn evenly from the corpus, so any "
        "prefix of it is itself a random sample. Stopping early costs precision "
        "and nothing else.",
        "",
        "---",
        "",
    ]
    for i, it in enumerate(items, start=1):
        out += render_turn(i, it)
    return "\n".join(out)


def fixture_rows(items: list) -> list:
    """The items as they reach the repo.

    One function so the privacy contract has a single place to hold: the
    prompt and the injected notes stay in the operator's vault, and the repo
    gets hashes, strata and counts.
    """
    return [{k: v for k, v in it.items() if k not in ("prompt", "context")}
            for it in items]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", type=pathlib.Path, required=True,
                    help="the judged pool (sufficient_context --out)")
    ap.add_argument("--worksheet", type=pathlib.Path, required=True,
                    help="vault path for the operator's worksheet")
    ap.add_argument("--fixture", type=pathlib.Path, required=True,
                    help="repo path for the hash/stratum/verdict record")
    ap.add_argument("--rubric-link", default="RUBRIC.md")
    args = ap.parse_args(argv)

    pool = json.loads(args.pool.read_text(encoding="utf-8"))["rows"]
    pool = [r for r in pool if r.get("turn")]
    picked = order(pool)

    # Re-read the turns to recover their text, in memory only.
    turns = {}
    for t in recall_traffic.iter_injections(with_text=True):
        h = sufficient_context.grouped_hash(t.get("prompt_hash"))
        turns.setdefault(h, t)

    items, missing = [], 0
    for r in picked:
        t = turns.get(r["turn"])
        if t is None:
            missing += 1
            continue
        n_notes = r.get("n_notes")
        if n_notes is None:
            n_notes = len(t.get("slugs") or [])
        items.append({
            "id": r["turn"], "stratum": stratum(r, n_notes),
            "judge": r.get("verdict"), "n_notes": n_notes,
            "prompt": t.get("_prompt"), "context": t.get("_injected"),
        })

    args.worksheet.parent.mkdir(parents=True, exist_ok=True)
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    stem = args.worksheet.with_suffix("")
    written = []
    for n, chunk in enumerate(batches, start=1):
        f = pathlib.Path(f"{stem}-{n:02d}.md")
        f.write_text(worksheet(chunk, args.rubric_link, batch=n,
                               of=len(batches)), encoding="utf-8")
        written.append(f)
    args.fixture.parent.mkdir(parents=True, exist_ok=True)
    args.fixture.write_text(json.dumps({
        "seed": SEED,
        "note": ("one shuffled pool, drawn uniformly from the corpus. Any "
                 "prefix of this order is itself a random sample, so a "
                 "partially labelled worksheet is still unbiased and whatever "
                 "is left over is the unlabelled pool PPI uses."),
        "turns_not_recoverable": missing,
        "items": fixture_rows(items),
    }, indent=2) + "\n", encoding="utf-8")

    for f in written:
        print(f"  {f.name}  ({f.stat().st_size // 1024} KB)")
    print(f"worksheet : {len(written)} batches, {len(items)} turns total")
    print(f"fixture   : {args.fixture}")
    if missing:
        print(f"  {missing} pool rows had no recoverable turn and were dropped")
    strata: dict = {}
    for it in items:
        strata[it["stratum"]] = strata.get(it["stratum"], 0) + 1
    print("\n  stratum coverage")
    for s in sorted(strata):
        print(f"    {s:22s} {strata[s]:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
