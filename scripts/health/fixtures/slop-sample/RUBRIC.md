# Slop rubric — frozen 2026-08-22

**Status:** frozen. Written and committed *before* the sample was drawn and
before any note in it was read, and before the slop detector exists. Nothing in
this file may change once labelling starts; if it turns out to be wrong, the
labels are void and a new rubric gets a new sample.

That sequence is the whole point. A rubric written after seeing what a detector
flags is a description of the detector, and precision measured against it is a
number that cannot fail.

## What is being labelled

Each note gets exactly one of four labels. You are judging **the note as it
stands**, not whether the topic is interesting and not whether you remember
writing it.

| label | meaning |
|---|---|
| `expire` | An unfilled skeleton **and** a near-copy of something else. Deleting it loses nothing. |
| `review` | Slop worth a human look, but not obviously disposable. |
| `keep` | A real memory. Not slop. |
| `unsure` | You genuinely cannot tell in thirty seconds. |

`unsure` is a real answer and it is not a failure. Forcing a binary on an
ambiguous note manufactures confidence that was never there. Unsure notes are
excluded from the scored set and reported as their own number.

## The decision procedure

Work down the list. The first rule that fires decides.

**1. Does it make a claim you could act on or be wrong about?**
If yes → `keep`. Stop here.

This is the single most important question, and it is deliberately first. A note
that says something true and specific is a memory, however short and however
plainly written.

**2. Is it an unfilled skeleton?**
Headings with nothing under them. `TODO`, `TBD`, placeholder text, a template's
prompts left in place. The shape of a note without its content.

If yes, go to 3. If no, go to 4.

**3. Is it also a near-copy?**
Does another note in the corpus say the same thing, in substantially the same
words? If you cannot check, answer from whether it reads as one of a set of
identical stubs.

- Skeleton **and** near-copy → `expire`
- Skeleton but seemingly unique → `review`

**4. Does it use many words to say nothing?**
Restates its own title. "It is worth noting that X is important." Summarises
without a fact. Advice with no specifics. Reads like it was generated to fill a
slot.

If yes → `review`.

**5. Otherwise** → `keep`.

## What is explicitly not slop

These are named because a detector will be tempted by all of them, and the
verification criterion calls out the first one by name.

**Short and dense is not slop.** "The Metal compute buffers page-fault above
roughly two thousand tokens and poison the server; chunk instead." — twelve words
carrying a fact, a threshold and a remedy. A length floor that fired on this
would be wrong, which is why the design makes length an AND-gate rather than a
signal of its own.

**A bare reference is not slop.** A URL with one line saying why it matters is a
complete memory of the kind it is.

**Repeating a fact from a different angle is not a near-copy.** Two notes about
the same subject, written from different sources or for different purposes, are
two memories. Near-copy means substantially the same *words*.

**Being badly written is not slop.** A clumsy sentence carrying a real claim is
`keep`. This rubric is about emptiness, not craft.

**Being old is not slop.** Aging is the lifecycle's job, not this one's.

## How the labels will be used

The operator labels a stratified subset cold. The agent labels the remainder
**without seeing the operator's labels or the detector's output**, and its
agreement with the operator on a held-out slice of the operator's own labels is
reported alongside the detector's precision and recall.

If that agreement is poor, the rubric failed and both sets of numbers are
withdrawn — not patched. A rubric two readers apply differently is not a
measurement instrument.

## Strata

Drawn by size and whether the note carries a `type`, rather than by type alone:
1,374 of 1,898 candidate notes are untyped, because filing has not run, so type
strata would put most of the sample in one bucket.

| stratum | why it is its own bucket |
|---|---|
| short · typed | the named trap — short dense notes must not be flagged |
| short · untyped | the same trap, on the unfiled mass |
| normal · preference | the largest typed class |
| normal · reference | the second largest, and the one most likely to be terse by nature |
| normal · other typed | convention, idea, fix, workflow pooled — each too small alone |
| normal · untyped | the bulk of the corpus |
| long | over 2k, where empty elaboration has room to hide |
