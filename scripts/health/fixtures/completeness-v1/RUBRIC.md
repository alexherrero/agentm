# Completeness rubric v1 — frozen

**Frozen 2026-08-27, before any note in the sample was drawn or read.**

Three rubrics have failed in this arc, all of them asking *is this note worth
keeping?* — a question that turns on what the operator wants their vault to hold,
which is private to them and not recoverable from the text. This one asks a
different kind of question. **Did the rewrite drop something the source said?**
Both graders can point at the same two pieces of text and argue about them, and
that is the whole reason to expect this rubric to survive where those did not.

## What is being graded

One pair: the **source** (a note as it stood before enrichment) and the
**rewrite** (the same note after). Enrichment is meant to condense, so the
rewrite being shorter is the intended outcome and never by itself a fault.

The question is only ever: *what did the source say that the rewrite no longer
says?*

## The three grades

**`complete`** — nothing substantive was lost. Wording changed, order changed,
filler went, the note got shorter. A reader of the rewrite alone knows everything
the source would have told them.

**`minor`** — something substantive is gone, but a reader of the rewrite would
not be misled and would not need to open the source to act. A supporting example
dropped where the point survives. A second instance of a pattern already stated.

**`major`** — a fact, number, name, date, caveat, condition, reason or
consequence is gone, and its absence would change what a reader does: they would
act differently, reach a wrong conclusion, or have to go back to the source to
find out. Also `major` when the rewrite keeps the words and loses the point.

## Rules that decide the hard cases

These exist because each names a way an earlier rubric went wrong, or a shape
this corpus is known to contain.

1. **Condensing is not loss.** The rewrite is supposed to be shorter than the
   source. Length, tone, register and phrasing are never graded.

2. **Grade against what the source actually says, not what it meant to say.**
   Much of this corpus was captured through a bug that cut notes mid-word, so a
   source may stop in the middle of a sentence. A rewrite that says so — *"the
   source cuts off here"* — has lost nothing and is `complete`. Only a rewrite
   that drops something the source *did* manage to say is graded down. This is
   the rule the second slop rubric lacked, and its absence cost fifteen labels.

3. **Additions are out of scope.** If the rewrite asserts something the source
   does not, that is a faithfulness fault and a different gate already refuses
   it. Grade only what went missing.

4. **Frontmatter is not the note.** Tags, confidence, status and timestamps
   change by design. Grade the title, summary and body.

5. **A dropped identifier is `major`.** A name, path, flag, error string, command
   or number is what someone searches for later; the sentence around it is not.
   Losing one is losing the note's usefulness even when the prose still reads
   well.

6. **When the source says nothing substantive, the rewrite cannot lose
   anything.** An empty or near-empty source grades `complete`. It may well be a
   bad note; that is the slop detector's question, not this one.

7. **Unsure is a real answer.** Use it rather than guessing. Unsure grades are
   excluded from the scoring and reported as a count, so a coin flip does not
   become evidence in either direction.

## The pre-registered bar

Written here, before the sample is drawn, and not adjusted afterwards.

The automated pass returns a **coverage fraction in [0, 1]** per note — the share
of the source's claims the rewrite still carries. The operator's grade is the
ground truth. Mapping for the correlation: `complete` = 2, `minor` = 1,
`major` = 0.

* **Primary — rank agreement.** Spearman ρ between the automated coverage and the
  operator's ordinal grade must be **≥ 0.50**, with **p < 0.05 by exact
  permutation test** over the observed grades. A correlation without the
  permutation test is a number that a small n can produce by luck.

* **Secondary — separation.** `median(coverage | complete) −
  median(coverage | major) ≥ 0.20`. A monotone relationship that moves the score
  by a hair would satisfy ρ and still be useless on a scorecard.

* **Stability.** The scorer runs **3 replicates per note**; the reported score is
  the per-note median, and the per-note spread is published alongside it. One run
  of a model-driven scorer is not a measurement.

* **Sample.** 32 pairs, drawn by seed from the enriched corpus, stratified by
  note type so the by-class report has something in each cell. ≥30 is the task's
  floor; 32 leaves room for a pair that turns out unusable.

**If the primary bar fails, the pass is not validated and this document says so.**
The bar does not move afterwards, and neither does the mapping above.

## What this rubric does not decide

Whether the note should exist, whether it is well written, whether it is filed in
the right place, and whether enrichment should have run on it at all. Those are
other questions with other instruments.
