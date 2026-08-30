# RULE — what the sufficiency × utilization crossing has to show

**Frozen:** 2026-08-29, before the live crossing ran.
**Applies to:** `judge_use` / `quadrant` / `cross` in
`scripts/health/sufficient_context.py`.

## Why the crossing exists

Sufficiency alone cannot tell **bad retrieval** from **good context the model
ignored**. Both look like "the context did not help", and collapsing them would
put the blame for a model's inattention on the retriever — or hide a real
retrieval failure behind a model that answered well from its own knowledge.

Four corners, named so the difference cannot be smoothed over in a summary:

| | reply used it | reply did not |
|---|---|---|
| **context had it** | `served` | `ignored` |
| **context lacked it** | `salvaged` | `missed` |

`ignored` and `missed` are the pair that matters. A report that quotes only
"context did not help: N%" has merged them and is not usable for deciding
whether to change retrieval.

## The bars

**1 — All four corners populate on constructed cases.** Not a formality: if the
judge cannot place a reply that plainly uses good context differently from one
that plainly ignores it, the crossing is decoration.

**2 — The two utilization signals are reported apart, with their disagreement.**
Judged utilization and the deterministic name-match from task 4 are never
merged into one number.

**3 — A claim of use names what was used.** `{"verdict": "used"}` with no
`drew_on` is malformed, the same demand made of a rejection on the sufficiency
axis and by `grounding.go` before either.

**4 — The two questions are asked in separate calls.** Not a cost decision — it
doubles cost. One call answering both would let the first answer prime the
second, and two axes that move together measure one axis at twice the
confidence.

## What I expect, written first

**The disagreement between the two utilization signals will be large, and that
is not evidence against the judge.** The deterministic signal fires only when a
note's name appears verbatim in the reply — 7 of 3,004 injected notes, and
about 1% of turns. It is a floor with almost no reach. If judged utilization
comes in anywhere above a few percent, nearly every disagreement is the floor
failing to see use rather than the judge inventing it.

So the disagreement number is **not** a validity check on the judge. It is a
statement about how little the deterministic signal can see, and it is reported
because burying it would let a reader take the floor for a second opinion. The
real validity check is task 7's operator labels.

**I do not have a prior on the quadrant split** and will not invent one. The
one thing I would flag as surprising is a large `ignored` share, which would
say retrieval is working and the model is not reading what it gets — a
different problem from the one this arc set out to measure, and one worth
stopping on.

## Known blur, recorded before the numbers

`salvaged` has the softest boundary of the four, and constructing an
unambiguous example took three attempts. A reply that quotes the context only
to dismiss it is not salvage. Context that "partly" covers a question is often
just *sufficient*, and the judge said so on the second attempt. The corner is
real, but a small `salvaged` count should be read as a boundary that is
genuinely fuzzy rather than as a precise measurement.

## Privacy

Unchanged from the sufficiency judge. The judge's account of what a reply drew
on quotes the reply, so it stays in memory and out of the file — `_drew_on` is
underscore-prefixed and stripped by `persist_rows`, and there is a test that
fails if it ever reaches disk.
