# Slop rubric v3 — frozen 2026-08-26

**Status:** frozen. Written and committed before the v3 sample was drawn and
before any note in it was read.

**Supersedes v2**, which failed its own calibration at κ = 0.349, and v1 before it
at κ = 0.189. Both were withdrawn under rules they wrote for themselves.

## What changed, and it is not the wording

v1 asked "does this make a claim?" and kept clipped fragments. v2 asked "is this a
fragment?" and flagged notes the operator read as damaged-but-real. Reading v2's
disagreements found the actual cause: `reflect._excerpt_around` had been slicing
at a character offset, so 2,318 note bodies opened part-way through a word. Both
rubrics had been asking whether those notes were slop. Neither asked whether they
were broken.

They were broken, and they are gone — retired by deterministic rules with no model
calls and no labelling, alongside 2,633 duplicates and notes mined from prose
nobody typed.

**So v3 is not a better-worded v2 over the same corpus.** It is the same question
asked of a corpus that has already had its junk removed, and the honest
expectation is that it finds very little. That expectation is written down here so
that finding little counts as a result rather than as a rubric that failed.

## What you are labelling

Fifteen specific notes, not a sample of categories. Ten are what
`agentmd slop` ranked least novel; the rest are controls it did not flag, mixed in
and shuffled. You cannot tell which is which from the order, and that is what
makes the recall figure mean anything.

| label | meaning |
|---|---|
| `expire` | Deleting it loses nothing. |
| `review` | Worth a human look, not obviously disposable. |
| `keep` | A real memory. |
| `unsure` | You genuinely cannot tell in thirty seconds. |

`unsure` is a real answer. Unsure notes leave the scored set and are reported as
their own number.

## The decision procedure

Work down. The first rule that fires decides.

**1. Is it an unfilled skeleton?** Headings with nothing under them, `TODO`,
placeholder text, a template's own prompts left in place.

→ `expire` if it is also a near-copy of something else. Otherwise `review`.

**2. Is it a near-copy of another note, in substantially the same words?**

Substantially the same *words*. Not the same subject.

→ `review`. Not `expire`: which copy to keep is a judgement, and this rubric does
not know what links to what.

**3. Was it written by a test?** Verification markers, probe slugs, fixture text.

→ `expire`.

**4. Does it use many words to say nothing?** Restates its own title. Summarises
without a fact. Advice with no specifics.

→ `review`.

**5. Does it state something you could act on or be wrong about, in its own
words?**

→ `keep`.

**6. Otherwise** → `review`.

## What is explicitly not slop

**Two notes on one subject are two memories.** This is the rule most likely to
decide this particular fifteen. `deepseek-ocr` and `deepseek-ocr-2` are two
models; `gpt-5-3-instant` and `gpt-5-4-thinking` are two system cards. They read
alike because they are the same *kind* of note about the same *kind* of thing, and
a detector that cannot tell that from duplication is a detector that deletes half
a reference library.

**Short and dense is not slop.** "The Metal compute buffers page-fault above
roughly two thousand tokens and poison the server; chunk instead." Twelve words
carrying a fact, a threshold and a remedy.

**A bare reference is not slop** *if the note is the reference*. A title, a URL
and one line saying why it matters is a complete memory of the kind it is.

**Being badly written is not slop.** This is about emptiness, not craft.

**Being old, superseded or expired is not slop.** Aging is the lifecycle's job.

## The bar, and what it decides

Both parties label the same fifteen independently. Cohen's kappa is computed on
those fifteen. **κ ≥ 0.60 proceeds**; below it, v3 is withdrawn like its
predecessors.

0.60 is unchanged from v2 deliberately. A bar lowered after two failures is not a
bar, and this instrument would underpin a precision figure for a detector with a
band that deletes things.

**What the labels decide, and I do not:** where the band goes. The novelty
distribution on the current corpus runs from 0.278 to 1.000, and the ten lowest
are the pairs named above. If they label `keep`, the measured precision of any
band drawn above 0.278 is zero, and the honest report is that this corpus contains
no slop these signals can find. That is a result. It is written here, before the
labels, so that it cannot be presented afterwards as though it had been the plan.
