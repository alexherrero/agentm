# Slop rubric v2 — frozen 2026-08-23

**Status:** frozen. Written and committed before the v2 sample was drawn and
before any note in it was read. Nothing here changes once labelling starts; if it
is wrong again, the labels are void and v3 gets a new sample.

**Supersedes v1**, which failed its own calibration: κ = 0.189 exact, 60% raw
agreement over fifteen held-out notes. Under v1's pre-registered rule those
labels were withdrawn rather than patched. `../slop-sample/agreement.json` has
the numbers.

## What went wrong in v1, and the one change that follows

All six v1 disagreements ran the same direction — the agent kept a note the
operator flagged. Six of six one-way is p≈0.03 against a null of random
direction, so it was a property of the rubric rather than small-sample noise.

The cause was v1's first rule:

> Does it make a claim you could act on or be wrong about? If yes → `keep`. Stop
> here.

That fires on the *claim inside* a note rather than on the note as written. Five
of six disagreements were clipped fragments — `User stated: ...never fan out
parallel implementers; the autonomy boundary is...` — which quote a real directive
inside a mangled note. One reader saw the directive and kept it; the other saw the
note and flagged it. **Both readings were faithful to v1's words**, which is
exactly what a measurement instrument may not permit.

So rule 1 now asks about the note, and a new rule handles the quoting case
explicitly. That is the whole change. Everything else in v1 held up.

## The four labels

| label | meaning |
|---|---|
| `expire` | Deleting it loses nothing. |
| `review` | Slop worth a human look, but not obviously disposable. |
| `keep` | A real memory. |
| `unsure` | You genuinely cannot tell in thirty seconds. |

`unsure` is a real answer. Forcing a binary on an ambiguous note manufactures
confidence that was never there; unsure notes leave the scored set and are
reported as their own number.

## The decision procedure

Work down the list. The first rule that fires decides.

**1. Is the note a clipped fragment?**

Signs: it starts mid-word or mid-sentence (`...om that agent's own config`); it
opens with `User stated:` or `User corrected the agent:` followed by an excerpt
trailing off in both directions; its title is a fragment (`never by`, `always
goes through`); its body is a quotation and nothing else.

**A note that quotes a directive is not a note that states one.** The claim may
be perfectly real and still leave the note a clipping — that is the case v1 got
wrong, and it is decided here rather than by rule 2.

- Clipped, and one of a visibly repeated set → `expire`
- Clipped, seemingly unique → `review`

**2. Is it an unfilled skeleton?**

Headings with nothing under them, `TODO`, `TBD`, placeholder text, a template's
prompts left in place.

- Skeleton **and** a near-copy of something else → `expire`
- Skeleton alone → `review`

**3. Is it a near-copy of another note, in substantially the same words?**

Including when both copies are good. **A duplicate of a real memory is still a
duplicate** — v1's rule 1 short-circuited before this test and kept both halves
of a byte-identical pair. If you cannot check the corpus, answer from whether it
reads as one of a set of identical notes.

→ `review`. Not `expire`: which copy to keep is a judgement, and this rubric does
not know which one anything else links to.

**4. Was it written by a test?**

Verification markers (`peregrine-lattice-5502`), probe slugs, fixture text. These
state checkable facts, so v1's rule 1 kept them. They are not memories.

→ `expire`.

**5. Does it use many words to say nothing?**

Restates its own title. Summarises without a fact. Advice with no specifics.
Reads like it was generated to fill a slot.

→ `review`.

**6. Does the note state something you could act on or be wrong about, in its own
words?**

→ `keep`.

**7. Otherwise** → `review`.

Note that the default moved. In v1 the last rule was `keep`; here anything that
reaches the end without stating something is `review`. A corpus this rubric is
uncertain about should surface for a human, not pass silently.

## What is explicitly not slop

**Short and dense is not slop.** "The Metal compute buffers page-fault above
roughly two thousand tokens and poison the server; chunk instead." Twelve words
carrying a fact, a threshold and a remedy. Length is an AND-gate in the detector
for this reason, never a signal of its own.

**A bare reference is not slop** *if the note is the reference*. A title, a URL
and one line saying why it matters is a complete memory of the kind it is. A
title and a URL with no line at all is rule 6 — it states nothing — and lands at
`review`.

**Repeating a fact from a different angle is not a near-copy.** Two notes on one
subject from different sources are two memories. Near-copy means substantially
the same *words*.

**Being badly written is not slop.** A clumsy sentence carrying a real claim is
`keep`. This is about emptiness, not craft.

**Being old, superseded or expired is not slop.** Aging is the lifecycle's job.

## Calibration comes first this time

v1 spent 175 labels — 34 from the operator, 141 from the agent — before anyone
measured whether the two readers agreed. They did not, and all of it was
withdrawn.

So v2 calibrates before it scales:

1. A fresh sample is drawn. **Fifteen calibration notes** are set aside.
2. The operator and the agent label **the same fifteen**, independently, neither
   seeing the other's answers.
3. Cohen's kappa is computed on those fifteen.
4. **κ ≥ 0.60 proceeds** to the remaining notes. Below that, v2 is withdrawn the
   way v1 was, and the cost was fifteen notes rather than a hundred and
   seventy-five.

0.60 is the floor of "substantial" on the conventional reading, and this
instrument is going to underpin a precision figure for a detector with a band
that deletes things. Moderate agreement is not enough for that.

The threshold is written here, before the run, because a bar chosen after seeing
the number is not a bar.
