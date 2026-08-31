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


# Prompts the system injects rather than a person typing. The recall hook fires
# on these too — 233 of 688 injections, a third of all retrievals, pulling
# 14.8M characters of notes into context for text nobody wrote — and the judge
# rightly calls 86.3% of them "not an information need".
#
# Matched on a *leading* tag, not a substring anywhere. This repository's own
# sessions discuss `<task-notification>` constantly, and an anywhere-rule would
# quietly drop real prompts that quote one. Measured over the corpus the two
# rules differ on a single turn, and that turn is machine-generated too — an
# autonomous-loop timer invocation, matched separately below.
_MACHINE_TAGS = ("task-notification", "system-reminder", "local-command-stdout",
                 "command-name", "command-message", "command-args")
_MACHINE = re.compile(
    r"^\s*(?:<(?:" + "|".join(_MACHINE_TAGS) + r")>"
    r"|# Autonomous loop check)")


def is_machine_prompt(prompt: str) -> bool:
    """Was this prompt injected by the system rather than typed?"""
    return bool(_MACHINE.match(prompt or ""))


BATCH = 20
# How much of each injected note to show. The question is whether the right
# material was retrieved, which its opening almost always settles; the full note
# is one click away when it does not.
NOTE_CHARS = 1100

# The whole parenthetical is consumed, not just up to the score. Stopping at
# the score left the rest of the header — "daemon-hybrid, space: desk)" — as the
# first line of every note body.
_NOTE_HEAD = re.compile(
    r"^### (\S+) \(kind: ([^,]+), score=([^ )]+)([^)\n]*)\)[ \t]*\n?", re.M)
_SPACE = re.compile(r"space:\s*([A-Za-z0-9_-]+)")
_TAGS = re.compile(r"tags:\s*\[([^\]]*)\]")


def split_notes(block: str) -> list:
    """The injected block as its individual notes.

    Falls back to one unnamed chunk when the block does not have the expected
    headers — a worksheet that silently showed nothing would be worse than one
    that shows an unsplit wall.
    """
    heads = list(_NOTE_HEAD.finditer(block or ""))
    if not heads:
        return [{"slug": None, "kind": None, "score": None,
                 "space": None, "tags": [],
                 "body": (block or "").strip()}]
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(block)
        tail = m.group(4) or ""
        sp = _SPACE.search(tail)
        tg = _TAGS.search(tail)
        out.append({"slug": m.group(1), "kind": m.group(2),
                    "score": m.group(3),
                    "space": sp.group(1) if sp else None,
                    "tags": [t.strip() for t in tg.group(1).split(",")
                             if t.strip()] if tg else [],
                    "body": block[m.end():end].strip()})
    return out


_HEADING = re.compile(r"^#\s+(.+)$", re.M)
_STATUS = re.compile(r"\*\*Status:\*\*\s*\**\s*([^\n*(]{1,40})")
_USER_STATED = re.compile(r"^\s*User stated:", re.M)
_RESEARCH_ID = re.compile(r"^#\s+R\d+\b", re.M)


def note_status(body: str) -> str:
    """The note's own Status line, when it has one."""
    m = _STATUS.search(body or "")
    if not m:
        return ""
    return " ".join(m.group(1).split()).strip(" .—-")[:24]


def note_title(body: str) -> str:
    """The note's first heading, or its opening sentence.

    Its own words either way. This is the one field most likely to tell the
    operator whether the retrieval was on-topic at a glance.
    """
    m = _HEADING.search(body or "")
    if m:
        return " ".join(m.group(1).split())[:90]
    for line in (body or "").splitlines():
        t = line.strip()
        if t and not t.startswith(("#", ">", "-", "*", "|", "```")):
            return " ".join(t.split())[:90]
    return ""


def note_type(slug: str, kind: str, body: str, tags: list = None) -> str:
    """What kind of thing this note is, from its slug, kind and shape.

    Ordered most specific first. The declared `kind` is right when it is not
    "unknown", which it is for about three quarters of retrieved notes — the
    slug conventions carry the rest.
    """
    slug = slug or ""
    body = body or ""
    if slug.startswith("PLAN.archive"):
        return "archived plan"
    if slug == "PLAN" or slug.startswith(("PLAN-", "PLAN.")):
        return "active plan"
    if slug.startswith("progress"):
        return "progress log"
    if slug.startswith("RULE"):
        return "frozen rule"
    if slug.startswith(("DIAGNOSIS", "VERDICT", "PROMPT-")):
        return "working note"
    if kind and kind != "unknown":
        return {"idea-incubator-research": "idea (research)",
                "opinion-supplement": "opinion supplement",
                "handoff-artifact": "handoff artifact"}.get(kind, kind)
    if _USER_STATED.search(body):
        return "captured preference"
    # The recall header's own tags, when kind said nothing. `idea-incubator-*`
    # and `design-*` are the ones that carry real meaning here.
    for t in (tags or []):
        if "idea-incubator" in t:
            return "idea"
        if t.startswith("design"):
            return "design"
    if _RESEARCH_ID.search(body) or slug.startswith("research"):
        return "research note"
    if slug.startswith("agentm-"):
        return "design"
    return "note"


def _plural(word: str, n: int) -> str:
    return word if n == 1 else word + "s"


def inventory(notes: list) -> str:
    """One line naming the shape of the retrieval."""
    counts: dict = {}
    for note in notes:
        t = note_type(note["slug"], note["kind"], note["body"],
                      note.get("tags"))
        counts[t] = counts.get(t, 0) + 1
    parts = [f"{n} {_plural(t, n)}"
             for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return ", ".join(parts)


_FENCE_LINE = re.compile(r"^\s*(`{3,}|~{3,})", re.M)


def fence_for(text: str) -> str:
    """A fence long enough that `text` cannot break out of it.

    CommonMark closes a fenced block only on a run at least as long as the one
    that opened it, so one backtick more than the longest run inside is always
    safe.
    """
    longest = 0
    for m in re.finditer(r"`+", text or ""):
        longest = max(longest, len(m.group(0)))
    return "`" * max(3, longest + 1)


def balance_fences(excerpt: str) -> str:
    """Close any fence the clip left open.

    An excerpt cut at a fixed length lands mid-block often enough that this is
    the normal case, not an edge one. An unclosed fence does not degrade the
    page it is on — it consumes every page after it.
    """
    opens = _FENCE_LINE.findall(excerpt or "")
    if len(opens) % 2 == 0:
        return excerpt
    return (excerpt or "") + "\n```"


def render_turn(n: int, item: dict) -> list:
    notes = split_notes(item.get("context") or "")
    label = item.get("label") or "?"
    lines = [f"## {n}. `{item['id']}`", "", f"**LABEL: {label}**", ""]
    if item.get("flag"):
        lines += [f"FLAG: {item['flag']}", ""]
    lines += [
             "### The request", ""]
    prompt = (item["prompt"] or "").strip()[:3000]
    pf = fence_for(prompt)
    lines += [pf, prompt, pf, "",
             f"### What was retrieved — {item['n_notes']} notes",
             "", f"*{inventory(notes)}*", "",
             "| # | note | what it is | its own title |",
             "|---|---|---|---|"]
    for i, note in enumerate(notes, start=1):
        slug = note["slug"] or "(unnamed block)"
        link = f"[[{note['slug']}]]" if note["slug"] else slug
        kind = note_type(note["slug"], note["kind"], note["body"],
                         note.get("tags"))
        if kind == "note" and note.get("space"):
            kind = f"note in {note['space']}"
        st = note_status(note["body"])
        if st:
            kind = f"{kind} · {st}"
        title = note_title(note["body"]).replace("|", "\\|")
        lines.append(f"| {i} | {link} | {kind} | {title} |")
    lines += ["",
              "<details>",
              "<summary>the retrieved text — open when the table does not "
              "settle it</summary>",
              ""]
    for note in notes:
        if note["slug"]:
            lines.append(f"**{note['slug']}**")
        body = note["body"]
        clipped = balance_fences("\n".join(body[:NOTE_CHARS].splitlines()[:22]))
        lines += ["", "> " + "\n> ".join(clipped.splitlines())]
        if len(body) > len(clipped):
            lines += [">", f"> *… {len(body) - len(clipped):,} more characters "
                           f"— open the note itself if you need them.*"]
        lines.append("")
    lines += ["</details>", "", "---", ""]
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
        "Each turn leads with a table of **what** was retrieved — the kind "
        "of note, its status, and its own title. That is usually enough to say "
        "whether the right material came back. The retrieved text is folded "
        "underneath for when it is not, and each note links to itself for when "
        "*that* is not enough either.",
        "",
        "Every field in the table comes from the note's own bytes — its "
        "declared kind, its slug, its first heading, its Status line. Nothing "
        "there was written by a model; a summary that was wrong would make "
        "your label wrong with nothing on the page to show it.",
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
    return [{k: v for k, v in it.items()
             if k not in ("prompt", "context", "label", "flag")}
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
    ap.add_argument("--carry-labels", type=pathlib.Path,
                    help="a labels JSON rescued from a previous worksheet; "
                         "labels are restored by turn id, so a reordered or "
                         "renumbered sheet still keeps them on the right turn")
    ap.add_argument("--human-only", action="store_true",
                    help="drop prompts the system injected — a third of the "
                         "traffic, which no note could ever answer")
    args = ap.parse_args(argv)

    pool = json.loads(args.pool.read_text(encoding="utf-8"))["rows"]
    pool = [r for r in pool if r.get("turn")]
    picked = order(pool)

    # Re-read the turns to recover their text, in memory only.
    turns = {}
    for t in recall_traffic.iter_injections(with_text=True):
        h = sufficient_context.grouped_hash(t.get("prompt_hash"))
        turns.setdefault(h, t)

    carried = {}
    if args.carry_labels and args.carry_labels.exists():
        carried = json.loads(args.carry_labels.read_text(encoding="utf-8"))

    items, missing, machine = [], 0, 0
    for r in picked:
        t = turns.get(r["turn"])
        if t is None:
            missing += 1
            continue
        if args.human_only and is_machine_prompt(t.get("_prompt")):
            machine += 1
            continue
        n_notes = r.get("n_notes")
        if n_notes is None:
            n_notes = len(t.get("slugs") or [])
        prev = carried.get(r["turn"], {})
        items.append({
            "id": r["turn"], "stratum": stratum(r, n_notes),
            "judge": r.get("verdict"), "n_notes": n_notes,
            "label": prev.get("label"), "flag": prev.get("flag"),
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
        "population": ("human-typed prompts only" if args.human_only
                       else "every prompt the recall hook fired on"),
        "machine_prompts_dropped": machine,
        "note": ("one shuffled pool, drawn uniformly from the corpus. Any "
                 "prefix of this order is itself a random sample, so a "
                 "partially labelled worksheet is still unbiased and whatever "
                 "is left over is the unlabelled pool PPI uses."),
        "turns_not_recoverable": missing,
        "items": fixture_rows(items),
    }, indent=2) + "\n", encoding="utf-8")

    for f in written:
        print(f"  {f.name}  ({f.stat().st_size // 1024} KB)")
    kept = sum(1 for it in items if it.get("label"))
    print(f"worksheet : {len(written)} batches, {len(items)} turns total")
    if carried:
        print(f"  {kept} of {len(carried)} rescued labels restored by turn id")
    print(f"fixture   : {args.fixture}")
    if missing:
        print(f"  {missing} pool rows had no recoverable turn and were dropped")
    if machine:
        print(f"  {machine} machine-generated prompts dropped "
              f"(task notifications, system reminders)")
    strata: dict = {}
    for it in items:
        strata[it["stratum"]] = strata.get(it["stratum"], 0) + 1
    print("\n  stratum coverage")
    for s in sorted(strata):
        print(f"    {s:22s} {strata[s]:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
