#!/usr/bin/env python3
"""Repair the notes a character-offset excerpt cut in half.

`reflect._excerpt_around` sliced at a raw character offset until 2026-08-24, so
mined memories were written with bodies opening part-way through a word —
`...all back to direct push`, where the word was `fall`. The function is fixed and
makes no new ones. This is what to do about the ones already written.

## Two outcomes, and the second one is not a lesser version of the first

**Repaired** — the session transcript the note was mined from still exists, the
excerpt is found in it verbatim, and the body is re-cut with the fixed function.
Byte-exact, nothing inferred.

**Marked** — everything else. `excerpt_edges_unverified: true` goes into the
frontmatter and the body is left alone. The note stays exactly as it was; what
changes is that search, triage and a human reading it can now tell it apart from a
note whose edges were cut properly.

The key is named for what it can support. It does not say the edges *are* ragged,
because the note cannot show that — `...back to direct push` and `...alls back to
direct push` are the same shape from outside. It says they were cut by a function
that ignored word boundaries and cannot be checked from here, which is true of
every mined note written before 2026-08-24 and false of every one after.

Marking rather than trimming is the whole design call. Trimming the partial word
looks safe — the fragment carries no information, so dropping it costs nothing —
and it is not, because nothing in the note says which leading word is a fragment.
The old code emitted `...` whenever anything was elided, whether or not the cut
landed on a space, so `...preface with "I'll continue"` and `...all back to direct
push` are indistinguishable from the outside. Measured over the corpus, a trim
improves about three thousand bodies and silently eats an unknown number of
complete words. An unbounded loss is not a repair.

## What it will not touch

Only notes whose *body is* the excerpt, or which carry mining frontmatter. A first
count of the damage matched any file containing the phrase and swept in 1,467
documents that merely quote mined bodies — dreaming's own staged proposals, and
the labelling worksheets written to review this very problem. A pass driven by
that count would have rewritten the evidence.

## How it writes

Through `RevertLog.record_and_apply`, one entry per batch, so a bad run is one
`revert` away. Dry-run by default: a corpus-wide rewrite that happens because
somebody typed the command without arguments is the failure the whole
propose-then-confirm apparatus exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import reflect  # noqa: E402

# What a mined body looks like, and the four prefixes reflect.py writes.
BODY_PREFIX = re.compile(
    r"\A(?:##[^\n]*\n+)?(?P<prefix>User stated: |User corrected the agent: |"
    r"Fix observed: )?(?P<excerpt>\.\.\..*?)$",
    re.M)
FRONTMATTER = re.compile(r"\A---[ \t\r]*\n(.*?)\n---[ \t\r]*\n", re.S)
MINING_FM = re.compile(r"^mining_confidence:", re.M)
SESSIONS_FM = re.compile(r"^sessions:\s*\[(.+?)\]", re.M | re.S)
MARKER_FM = re.compile(r"^excerpt_edges_unverified:", re.M)

# An elided edge — an ellipsis against lowercase letters, which is where a
# mid-word cut *may* be.
#
# May, not is. `...back to direct push` and `...alls back to direct push` are the
# same shape from outside, because the pre-fix function emitted `...` for any
# elision whether or not it landed on a space. So this matches the population
# whose edges cannot be trusted, not the subset that is actually broken — and the
# marker is named for what that supports.
ELIDED_HEAD = re.compile(r"\A\.\.\.[a-z]{2,}")
ELIDED_TAIL = re.compile(r"[a-z]{2,}\.\.\.\Z")

# The frontmatter key a marked note carries.
#
# "Unverified", not "ragged". Raggedness is only ever proved in the case where it
# is also repaired: the transcript survives, the re-cut differs, and the note is
# fixed rather than flagged. Everywhere else the truthful claim is that the edges
# were cut by a function that did not respect word boundaries and cannot be
# checked from the note.
MARKER = "excerpt_edges_unverified"

# How many notes one run may touch without being told otherwise.
#
# 25, matching `dream_confirm.DEFAULT_AUTO_APPLY_BATCH_CAP` — the same reasoning
# applies and a second number would be a second thing to keep in step. A pass over
# two thousand notes is several runs, deliberately, so a mistake is caught after
# twenty-five of them rather than all of them.
DEFAULT_BATCH = 25


class RepairError(RuntimeError):
    """The repair pass could not do what it was asked."""


@dataclass
class Finding:
    """One note, and what can be done about it."""

    rel: str
    excerpt: str
    elided_head: bool
    elided_tail: bool
    # repairs maps a damaged excerpt to its re-cut form. A note can have several,
    # and can have some repairable and some not — which is why the outcome below
    # is not the whole story and `unrepaired` is reported alongside it.
    repairs: dict = field(default_factory=dict)
    unrepaired: int = 0
    # outcome is decided before anything is written, so a dry run and a real run
    # report the same thing.
    outcome: str = "marked"  # repaired | repaired-and-marked | marked | already-marked
    reason: str = ""

    def as_dict(self) -> dict:
        out = {"rel": self.rel, "outcome": self.outcome, "reason": self.reason,
               "elided_head": self.elided_head, "elided_tail": self.elided_tail}
        if self.repairs:
            out["repaired_excerpts"] = len(self.repairs)
        if self.unrepaired:
            out["unrepaired_excerpts"] = self.unrepaired
        return out


@dataclass
class Report:
    findings: list = field(default_factory=list)
    scanned: int = 0
    skipped_not_mined: int = 0

    def counts(self) -> dict:
        out = {}
        for f in self.findings:
            out[f.outcome] = out.get(f.outcome, 0) + 1
        return out


def is_mined_note(raw: str) -> bool:
    """Whether this file *is* a mined excerpt rather than one quoting others.

    Two independent signals, because neither covers the corpus alone: the body
    beginning with a mining prefix, and `mining_confidence` in the frontmatter.
    A document that quotes mined bodies has neither — its own body is prose about
    them, and its frontmatter is a report's.
    """
    m = FRONTMATTER.match(raw)
    head, rest = (m.group(1), raw[m.end():]) if m else ("", raw)
    if MINING_FM.search(head):
        return True
    body = rest.lstrip("\n")
    return bool(re.match(r"\A(?:##[^\n]*\n+)?"
                         r"(?:User stated|User corrected the agent|Fix observed): ",
                         body))


def find_excerpts(raw: str) -> list:
    """Every excerpt in a mined note, in the order they appear.

    A note carries more than one. The body holds the passage the candidate was
    named for; a `## Supporting excerpts` block lists the rest, one per line
    behind a `>`. Over the first batch repaired, all twenty-five notes had a
    Supporting block and in all twenty-five it held a *different* passage —
    repairing only the body left half of each note as it was.

    Deduplicated, because the two are sometimes the same string and replacing it
    twice would be a no-op the second time and a confusing count the first.
    """
    m = FRONTMATTER.match(raw)
    rest = raw[m.end():] if m else raw
    out, seen = [], set()

    def add(text: str) -> None:
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    for line in rest.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # The body line, behind one of the four prefixes reflect.py writes.
        for prefix in ("User stated: ", "User corrected the agent: ",
                       "Fix observed: "):
            if stripped.startswith(prefix):
                add(stripped[len(prefix):])
                break
        else:
            # A Supporting-excerpts line, or a bare body excerpt with no prefix.
            if stripped.startswith("> ..."):
                add(stripped[2:])
            elif stripped.startswith("..."):
                add(stripped)
    return out


def find_excerpt(raw: str) -> str:
    """The first excerpt, for callers that only need one."""
    found = find_excerpts(raw)
    return found[0] if found else ""


def transcript_for(raw: str, transcripts: Path) -> Path | None:
    """The session transcript this note was mined from, if it still exists."""
    m = FRONTMATTER.match(raw)
    if not m:
        return None
    s = SESSIONS_FM.search(m.group(1))
    if not s:
        return None
    for entry in s.group(1).split(","):
        sid = entry.strip().strip("'\"[]")
        if "/" not in sid:
            continue
        slug, uuid = sid.rsplit("/", 1)
        p = transcripts / slug / f"{uuid}.jsonl"
        if p.exists():
            return p
    return None


def recut_from(transcript: Path, excerpt: str) -> str:
    """Re-cut the excerpt from its transcript, on word boundaries.

    The excerpt's interior is verbatim source, so it is the search key. Only its
    *edges* were damaged, and only the edges move.

    Returns empty when the text cannot be found, which is a real outcome rather
    than a failure: a transcript can exist and not contain the passage, because a
    session id is recorded per candidate and a note can carry the wrong one.
    """
    inner = excerpt.strip(".").strip()
    words = inner.split()
    if len(words) < 5:
        return ""
    # The whole interior, damaged edges included.
    #
    # Dropping the first and last tokens looks necessary and is not: a damaged
    # edge is always a *substring* of the word it was cut from — `alls` of
    # `falls`, `hard-stopp` of `hard-stopped` — so it still matches, and
    # `find` returns a position inside that word which the boundary snap then
    # widens back out to the whole of it.
    #
    # Keeping them is strictly more precise, which is the direction to err in for
    # a pass that rewrites memories: a longer needle matches fewer passages, and a
    # wrong match here would replace a note's body with a different conversation.
    # The narrower version was tried first and no test could tell the two apart.
    needle = inner
    if len(needle) < 20:
        return ""

    try:
        with transcript.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                # A cheap reject before parsing: the needle has to survive JSON
                # escaping to appear in the raw line, and most lines will not have
                # it at all. Lines it wrongly rejects — a passage broken across an
                # escape — fall through to "does not contain the passage", which
                # marks rather than guesses.
                if needle not in line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # The message *text*, not the serialized record. Cutting a window
                # out of `json.dumps(...)` writes `"content":` and friends into a
                # memory body, which is the class of thing this pass removes.
                text = reflect._extract_text(msg)
                i = text.find(needle)
                if i < 0:
                    continue
                return reflect._excerpt_around(
                    text, i, i + len(needle),
                    radius=len(words[0]) + len(words[-1]) + 8)
    except OSError:
        return ""
    return ""


def mark(raw: str) -> str:
    """Add the marker to the frontmatter. Body untouched."""
    m = FRONTMATTER.match(raw)
    if not m:
        return f"---\n{MARKER}: true\n---\n\n" + raw.lstrip("\n")
    head, rest = m.group(1), raw[m.end():]
    lines = [ln for ln in head.split("\n") if not ln.startswith(f"{MARKER}:")]
    return "---\n" + "\n".join(lines + [f"{MARKER}: true"]) + "\n---\n" + rest


def replace_body_excerpt(raw: str, old: str, new: str) -> str:
    """Swap one excerpt for its repaired form, changing nothing else."""
    if old not in raw:
        raise RepairError("the excerpt is not in the note it came from")
    return raw.replace(old, new, 1)


def scan(vault: Path, *, transcripts: Path, limit: int = 0) -> Report:
    """Decide what happens to every damaged note. Writes nothing."""
    rep = Report()
    for path in sorted(vault.rglob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rep.scanned += 1
        if not is_mined_note(raw):
            rep.skipped_not_mined += 1
            continue
        excerpts = [e for e in find_excerpts(raw)
                    if ELIDED_HEAD.match(e) or ELIDED_TAIL.search(e)]
        if not excerpts:
            continue

        rel = str(path.relative_to(vault))
        first = excerpts[0]
        f = Finding(rel=rel, excerpt=first,
                    elided_head=bool(ELIDED_HEAD.match(first)),
                    elided_tail=bool(ELIDED_TAIL.search(first)))

        t = transcript_for(raw, transcripts)
        if t is not None:
            for e in excerpts:
                recut = recut_from(t, e)
                if recut and recut != e:
                    f.repairs[e] = recut
        f.unrepaired = len(excerpts) - len(f.repairs)

        m = FRONTMATTER.match(raw)
        marked_already = bool(m and MARKER_FM.search(m.group(1)))

        if f.repairs and not f.unrepaired:
            f.outcome = "repaired"
            f.reason = f"{len(f.repairs)} of {len(excerpts)} re-cut from {t.name}"
        elif f.repairs:
            # Both, and said as both. A note where one passage came back and
            # another did not is not honestly described by either label alone.
            f.outcome = "repaired-and-marked"
            f.reason = (f"{len(f.repairs)} of {len(excerpts)} re-cut from "
                        f"{t.name}; the rest are unverified")
        elif marked_already:
            f.outcome, f.reason = "already-marked", "seen by an earlier run"
        elif t is None:
            f.outcome = "marked"
            f.reason = "no surviving transcript to re-cut from"
        else:
            f.outcome = "marked"
            f.reason = f"{t.name} exists but does not contain the passage"
        rep.findings.append(f)
        if limit and len(rep.findings) >= limit:
            break
    return rep


def apply(vault: Path, rep: Report, revert_log, run_id: str, *,
          batch: int = DEFAULT_BATCH, only: str = "") -> str:
    """Write the decided outcomes, through the revert log.

    One `record_and_apply` for the batch. A half-applied repair leaves the corpus
    in a state no single revert undoes, which is worse than not starting.

    `only` restricts the run to one outcome. The two are not equally consequential
    — a repair rewrites a body from a transcript, a mark adds a frontmatter key —
    and an operator has good reason to land the fifty exact ones, read them, and
    decide about the two thousand separately. Without this they arrive together in
    path order, and the first thing anyone sees is the larger, duller half.
    """
    mutations = []
    for f in rep.findings:
        if f.outcome == "already-marked":
            continue
        # No filter check here. `only` is honoured at the write below, and a
        # note that ends up unchanged is dropped by the `body == raw` test — so a
        # skip clause at this point is a second implementation of the same rule.
        # Removing it changed no test, which is how it was found.
        raw = (vault / f.rel).read_text(encoding="utf-8")
        body = raw
        # The filter governs what gets written, not just which notes are visited.
        # A note with one repairable passage and one that is not passes an
        # `--only repaired` filter, and marking it there would land half the
        # marking pass early — which is exactly what landing them separately was
        # for.
        if only != "marked":
            for old, new in f.repairs.items():
                body = replace_body_excerpt(body, old, new)
        if f.unrepaired and only != "repaired":
            body = mark(body)
        if body == raw:
            continue
        mutations.append((vault / f.rel, body))
        if len(mutations) >= batch:
            break
    if not mutations:
        return ""
    return revert_log.record_and_apply(run_id, "repair_excerpts", mutations)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", required=True, help="vault root to scan")
    ap.add_argument("--transcripts", default=str(Path.home() / ".claude/projects"))
    ap.add_argument("--apply", action="store_true",
                    help="write the changes; without it nothing is touched")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--only", choices=("repaired", "marked"), default="",
                    help="write only this outcome; the two differ enough in "
                         "consequence to be landed separately")
    ap.add_argument("--limit", type=int, default=0, help="stop scanning after N findings")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    vault = Path(args.vault)
    rep = scan(vault, transcripts=Path(args.transcripts), limit=args.limit)

    if args.json:
        print(json.dumps({"scanned": rep.scanned, "counts": rep.counts(),
                          "findings": [f.as_dict() for f in rep.findings]}, indent=2))
    else:
        counts = rep.counts()
        print(f"scanned {rep.scanned} notes, {rep.skipped_not_mined} not mined")
        for k in ("repaired", "repaired-and-marked", "marked", "already-marked"):
            if counts.get(k):
                print(f"  {k:<15} {counts[k]}")
        for f in rep.findings[:5]:
            print(f"\n  {f.rel}\n    {f.outcome}: {f.reason}")

    if not args.apply:
        # stderr, so `--json` stays machine-readable. A notice on stdout after the
        # document makes the whole output unparseable, and the first thing anyone
        # does with a dry run is pipe it somewhere.
        print("dry run — nothing written. Pass --apply to write.", file=sys.stderr)
        return 0

    from revert_log import RevertLog  # noqa: E402
    import time
    run_id = f"repair-{int(time.time())}"
    entry = apply(vault, rep, RevertLog(vault), run_id, batch=args.batch,
                  only=args.only)
    print(f"\napplied as {run_id} / {entry}")
    print(f"undo with: RevertLog(vault).revert({run_id!r}, {entry!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
