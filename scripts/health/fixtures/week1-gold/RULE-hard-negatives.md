# RULE — hard negatives (task 4, recall-verdict)

**Registered before any negative was authored or measured.**

## What the current sub-metric is

Zero false positives across 20 negatives, forever — and structurally, not
impressively: every negative entry carries an **empty** `expected_note_paths`,
and the eval's FP check is `if expected: hit = any(...)`. The branch never
runs. The dial is not saturated; it is painted on. This file is the documented
finding the task's first step asked for, and the bar for replacing it.

## The bar, per authored negative

A near-miss negative is admitted only if all three hold, **measured at
authoring time and recorded in the entry itself**:

1. **The distinguishing entity is absent corpus-wide.** A case-insensitive scan
   of every admissible note finds zero occurrences. Unanswerability is
   grounded, not asserted.
2. **The banned path is the ranker's own choice.** The question is run through
   the shipped search shape, and the top admissible result becomes the entry's
   `expected_note_paths` — the note the system would confidently serve. A
   hand-picked "plausible" note would test my guess about the ranker; this
   tests the ranker.
3. **≥ 2 content terms from the question appear in that banned note's body.**
   The adjacency is measured, not vibed.

Entries carry `hardness: near-miss` and an `authoring` block with the entity,
its hit count (must be 0), and the shared terms.

## The pre-registered prediction

Written before the first measured run: **10 of 10 hard negatives will return
their banned note in the top 5.** Not as pessimism — as arithmetic. No
rejection floor ships (the floor rung was refuted: BM25 measures term-match
strength, not answer-existence, and negatives scored *above* answerables in
the floor sweep), so a query built from real vocabulary plus an absent entity
retrieves exactly what its real vocabulary retrieves. Today's FP rate on hard
negatives is a **characterization of the shipped system**, not a defect count.

What the line buys is falsifiability: the day anything ships that claims to
know when it doesn't know — a floor, an abstention head, a calibrated score —
this is the dial that must move, and it finally can.

## Scoring changes

`false_positives` stays (total, back-compat). New: `false_positives_hard` /
`negatives_hard` and the easy complements, rendered as separate lines. The
paired comparison still excludes all negatives.

## The prediction, checked (2026-08-28, same day)

Measured on the first pinned run after authoring: **10 of 10** hard negatives
returned their banned note in the top 5. The pre-registered prediction held to
the digit, for the reason it gave — no shipped component knows when it doesn't
know. The dial now reads what the floor sweep said in numbers: term-match
strength is not answer-existence. It finally has somewhere to go.
