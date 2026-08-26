#!/usr/bin/env python3
"""Apply the miner's now-fixed rules to the notes it already wrote.

Three defects were fixed in three commits, and none of them cleaned up:

  #487  one operator sentence became as many as 42 files, because the writer
        appended `-1`, `-2`, `-3` forever without comparing bodies.
  #488  1,452 of 1,490 preference matches came from messages nobody typed —
        hook injections, compaction summaries, and agentm's own retrieval prompt
        quoting vault notes back into itself. Notes became prompts became notes.
  (this arc)  excerpts were cut at a raw character offset, so 2,318 bodies opened
        part-way through a word.

Each fix stops new damage. This is the pass over the 2,326 notes already written,
and it decides each one by the same rules the live path now uses — imported from
`reflect`, never re-implemented. A second copy of "was this typed by a person"
would drift from the first, and the first is the one with 400 transcripts of
evidence behind it.

## What it decides, in order

**Injected** — the note's source message can be found in a surviving transcript
and `reflect._operator_text` returns nothing for it. The operator did not write
this. That is the strongest verdict available and it is only available where the
transcript survives; everywhere else the honest answer is that we cannot tell.

**Duplicate** — another note in the same directory carries a byte-identical body.
One survives; the rest are redundant. Which one survives is decided by path order,
which is arbitrary and stated: the bodies are identical, so there is nothing to
choose between them.

**Ragged** — what `repair_excerpts` already does, and it runs last because
repairing the edges of a note that is about to be expired is wasted work.

## What it does not do

It does not delete. An injected or duplicate note gets `status: expired` and a
`retired_because` line, which is the lifecycle's own word for a note that has left
the live corpus without leaving the vault. The design is explicit that a
superseded memory is rank-penalized rather than removed and its text stays in git.

It does not guess. A note whose transcript is gone cannot be tested against #488's
rule, and it is reported as untestable rather than swept up on the grounds that
most of its neighbours failed.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import reflect  # noqa: E402
import repair_excerpts as rx  # noqa: E402

FRONTMATTER = rx.FRONTMATTER
SESSIONS_FM = rx.SESSIONS_FM
STATUS_FM = re.compile(r"^status:\s*(\S+)", re.M)

# The frontmatter line a retired note carries, so the reason survives the run.
REASON_KEY = "retired_because"

# How many notes one run may touch. 25, matching
# `dream_confirm.DEFAULT_AUTO_APPLY_BATCH_CAP` and `repair_excerpts`, for the
# reason those give: a mistake caught after twenty-five rather than after two
# thousand.
DEFAULT_BATCH = rx.DEFAULT_BATCH


@dataclass
class Verdict:
    rel: str
    outcome: str  # injected | duplicate | ragged | keep | untestable
    reason: str
    duplicate_of: str = ""


@dataclass
class Report:
    verdicts: list = field(default_factory=list)
    scanned: int = 0

    def counts(self) -> dict:
        c = collections.Counter(v.outcome for v in self.verdicts)
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def _primary_first(path: Path):
    """Order so `<slug>.md` is visited before `<slug>-1.md`, `<slug>-2.md`, …

    Plain path order does the opposite: `a-1.md` sorts before `a.md`, because a
    hyphen precedes a dot. That made the numbered copy the survivor and retired
    the original — backwards, since the numbered ones are what the collision
    handler appended and the un-numbered one is what anything pointing at this
    note is pointing at. `reflect._existing_capture` checks `<slug>.md` first for
    the same reason.
    """
    m = reflect._NUMBERED_SIBLING.search(path.name)
    stem = path.name[:m.start()] if m else path.stem
    return (str(path.parent), stem, 1 if m else 0, path.name)


def _body(raw: str) -> str:
    """The mined body, using reflect's own reader so the key matches #487's."""
    return reflect._written_body(raw) or ""


def _transcript(raw: str, transcripts: Path):
    return rx.transcript_for(raw, transcripts)


def was_injected(raw: str, transcripts: Path) -> tuple[bool, str]:
    """Whether the operator wrote this, per #488's rule. (verdict, why)

    Three answers, not two. `False` with a reason means the transcript confirms a
    person typed it; `False` with an "untestable" reason means we could not look.
    The caller has to keep those apart, because sweeping up what it could not test
    is exactly the move this pass exists to avoid.
    """
    # The excerpt, not `_written_body`. That returns the whole candidate body —
    # the `## slug` heading, the `User stated: ` prefix, the mining metadata —
    # which is the right key for #487's dedup, because it is what is stable across
    # re-mines, and the wrong string to search a transcript with, because the
    # prefix is the miner's word and never appears in what anyone typed. Searching
    # on it reported every note untestable, which read as "0 injected".
    found = rx.find_excerpts(raw)
    if not found:
        # Not untestable — testable by a different signal, and it says typed.
        #
        # A note with no excerpt is not an excerpt the search failed on; it is the
        # idea/workflow lane, which captures the whole message rather than a
        # window around a pattern. Measured over the 900 of them in this vault:
        # none carries a `User stated:` prefix, and the median body is 304
        # characters against #488's 270 for messages a person typed and 8,266 for
        # everything else. They read like their author — "add a follow-up to
        # rename the agent space in the vault", typos and all.
        #
        # Named separately so a sweep over the unverifiable population cannot
        # collect them by sharing a bucket with the ones it could not check.
        return False, "whole-message capture, not an excerpt"
    t = _transcript(raw, transcripts)
    if t is None:
        return False, "untestable: no surviving transcript"

    needle = found[0].strip(".").strip()
    if len(needle) < 40:
        return False, "untestable: body too short to locate"
    probe = needle[:50]
    try:
        with t.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if probe not in line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if needle not in reflect._extract_text(msg):
                    continue
                # Found it. Now the live rule decides, not us.
                if msg.get("type") == "assistant":
                    return True, "the agent wrote this, not the operator"
                if not reflect._operator_text(msg):
                    return True, ("the host did not attribute this to a person "
                                  "(hook injection, compaction summary, or a "
                                  "prompt quoting the vault back at itself)")
                return False, "the operator typed this"
    except OSError:
        return False, "untestable: transcript could not be read"
    return False, "untestable: passage not found in the transcript"


def scan(vault: Path, *, transcripts: Path,
         sweep_untestable: bool = False) -> Report:
    """Decide every mined note. Writes nothing.

    `sweep_untestable` retires the excerpt-lane notes whose transcript is gone, on
    the strength of #488's population measurement rather than a per-note check —
    1,452 of 1,490 preference matches came from messages nobody typed. It is off
    by default and its verdict is `presumed-injected`, never `injected`, because
    the difference between what was proved and what was inferred is the whole
    reason a person can undo this.
    """
    rep = Report()
    # Bodies already claimed, per directory — #487's key is the body alone,
    # scoped to the family a collision handler would have built.
    survivors: dict = {}

    for path in sorted(vault.rglob("*.md"), key=_primary_first):
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not rx.is_mined_note(raw):
            continue
        rep.scanned += 1
        rel = str(path.relative_to(vault))

        m = FRONTMATTER.match(raw)
        if m:
            status = STATUS_FM.search(m.group(1))
            if status and status.group(1).strip("'\"") == "expired":
                rep.verdicts.append(Verdict(rel, "keep", "already expired"))
                continue

        injected, why = was_injected(raw, transcripts)
        if injected:
            rep.verdicts.append(Verdict(rel, "injected", why))
            continue
        if sweep_untestable and why.startswith("untestable: no surviving"):
            rep.verdicts.append(Verdict(rel, "presumed-injected", why))
            continue

        body = _body(raw)
        if body:
            key = (str(path.parent), body)
            first = survivors.get(key)
            if first is not None:
                rep.verdicts.append(
                    Verdict(rel, "duplicate",
                            "byte-identical body already filed in this directory",
                            duplicate_of=first))
                continue
            survivors[key] = rel

        excerpts = [e for e in rx.find_excerpts(raw)
                    if rx.ELIDED_HEAD.match(e) or rx.ELIDED_TAIL.search(e)]
        if excerpts:
            rep.verdicts.append(Verdict(rel, "ragged", why))
        else:
            rep.verdicts.append(Verdict(rel, "keep", why))
    return rep


def retire(raw: str, reason: str) -> str:
    """Mark a note expired, with the reason, leaving the body alone.

    Not a delete. The design says a memory that leaves the live corpus is
    rank-penalized rather than removed and its text stays in git, and a cleanup
    that deleted would make the evidence for its own decisions unavailable.
    """
    m = FRONTMATTER.match(raw)
    line = f"{REASON_KEY}: {reason}"
    if not m:
        return f"---\nstatus: expired\n{line}\n---\n\n" + raw.lstrip("\n")
    head, rest = m.group(1), raw[m.end():]
    lines = [ln for ln in head.split("\n")
             if not ln.startswith(("status:", f"{REASON_KEY}:"))]
    return "---\n" + "\n".join(lines + ["status: expired", line]) + "\n---\n" + rest


def apply(vault: Path, rep: Report, revert_log, run_id: str, *,
          batch: int = DEFAULT_BATCH, only: str = "") -> str:
    mutations = []
    for v in rep.verdicts:
        if v.outcome not in ("injected", "presumed-injected", "duplicate"):
            continue
        if only and v.outcome != only:
            continue
        path = vault / v.rel
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        reason = v.reason
        if v.outcome == "duplicate":
            reason = f"duplicate of {v.duplicate_of}"
        elif v.outcome == "presumed-injected":
            # Says inferred, not proved, and names the evidence. If the operator
            # ever wants the genuine ones back, this line is how they find them.
            reason = ("presumed injected: transcript gone, so unverifiable. "
                      "Retired on the population measurement in #488 — 1,452 of "
                      "1,490 preference matches came from messages nobody typed")
        body = retire(raw, reason)
        if body == raw:
            continue
        mutations.append((path, body))
        if len(mutations) >= batch:
            break
    if not mutations:
        return ""
    return revert_log.record_and_apply(run_id, "retro_mining_cleanup", mutations)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--transcripts", default=str(Path.home() / ".claude/projects"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--only",
                    choices=("injected", "presumed-injected", "duplicate"),
                    default="")
    ap.add_argument("--sweep-untestable", action="store_true",
                    help="also retire excerpt-lane notes whose transcript is gone, "
                         "on #488's population evidence rather than a per-note check")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    vault = Path(args.vault)
    rep = scan(vault, transcripts=Path(args.transcripts),
               sweep_untestable=args.sweep_untestable)

    if args.json:
        print(json.dumps({"scanned": rep.scanned, "counts": rep.counts(),
                          "verdicts": [vars(v) for v in rep.verdicts]}, indent=2))
    else:
        print(f"scanned {rep.scanned} mined notes")
        for k, n in rep.counts().items():
            print(f"  {k:<12} {n}")
        why = collections.Counter(v.reason for v in rep.verdicts
                                  if v.outcome in ("injected", "keep", "ragged"))
        print("\nreasons:")
        for r, n in why.most_common(6):
            print(f"  {n:>5}  {r[:74]}")

    if not args.apply:
        print("dry run — nothing written. Pass --apply to write.", file=sys.stderr)
        return 0

    from revert_log import RevertLog  # noqa: E402
    import time
    run_id = f"retro-{int(time.time())}"
    entry = apply(vault, rep, RevertLog(vault), run_id, batch=args.batch,
                  only=args.only)
    print(f"\napplied as {run_id} / {entry}")
    print(f"undo with: RevertLog(vault).revert({run_id!r}, {entry!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
