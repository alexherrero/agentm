# Rubric — labelling whether injected context was sufficient

**Frozen 2026-08-30, before the sample was drawn.** The commit that adds this
file contains no turn data; the draw lands in a later commit. That ordering is
the point and is checkable in `git log`.

## Disclosure, because "before any turn is read" is a claim I have to qualify

I have not read the sample — it does not exist yet. I have seen roughly five
turns' worth of the *judge's own gap descriptions* scroll past in terminal
output during tasks 5 and 6 (for example, one turn where the judge said the
context lacked "what option 'A' refers to"). I have not read the prompts or the
injected blocks behind them, and none of those turns were selected for this
sample by anything other than the deterministic sampler.

That exposure is small but not zero, and it did shape one thing: the existence
of the "no note could have answered this" flag below, which I added after
noticing that class of turn. Recording it here rather than claiming a clean
room.

## Amendment, 2026-08-30 — the population narrowed after the draw

**What changed.** The sample is now **human-typed prompts only**, down from
every prompt the recall hook fired on. 49 of the 139 drawn turns were dropped;
90 remain.

**Why.** The operator, partway through batch one, asked why so many entries did
not read like questions. They were right. The hook fires on records the *system*
injects — `<task-notification>` when a background job reports, and
`<system-reminder>` blocks — and runs a full retrieval against text like
`pass: 15 | skipping: 2`. That is **233 of 688 injections, 33.9% of all
retrievals**, pulling 14,797,695 characters of notes into context for prompts
nobody wrote. It is a defect in recall, filed separately.

**Why this does not bias the labels.** No label for this sample existed when the
change was made — the operator had not written any that survived — and the
filter is a property of the prompt's own shape, decided without reference to the
judge's verdict or to any label. Checked both ways: dropped turns were 86.3%
`n/a` by the judge, kept turns 56.2%, and the sufficiency rate across the whole
judged set barely moved, 12.5% to 12.2%.

**What it does change** is what the result describes: *recall on prompts a
person actually typed*, which is the population worth knowing about. Any figure
from this sample carries that qualifier.

**The filter matches a tag at the start of the prompt, not anywhere in it.**
This repository's sessions discuss `<task-notification>` constantly — the
message that prompted this amendment did — and a substring rule would drop real
prompts as machine noise. Over the corpus the two rules differ on one turn, and
that turn is machine-generated too.

**A consequence to state plainly:** 90 labelled turns is below the measured
100-label floor for a PPI interval. κ, raw agreement, per-stratum agreement and
a labels-only population estimate with a Wilson interval are all unaffected and
will be reported. PPI will produce a point estimate and no interval, and will
say so.

## The question you are answering

For each turn you will see **the request** made to the coding assistant and
**the notes that were automatically injected** alongside it.

> **Could the injected notes alone have answered the request?**

You are judging the *notes*, not the assistant's reply, and not whether the
assistant did well. You will not be shown the reply — deliberately, so a good
answer cannot make thin context look sufficient.

## The three labels

**`sufficient`** — someone holding only these notes could respond to the
request. Partial coverage counts as sufficient when the uncovered part is not
what was being asked. Rephrasing, condensing and inference from what is there
all count; the notes do not have to state the answer in the request's words.

**`insufficient`** — the notes leave a real gap. Someone holding only these
would have to go and find something else before they could respond.

**`n/a`** — the request is not an information need at all. Commands ("run task
5"), approvals ("yes", "go ahead"), and instructions to act have no answer for
notes to contain, so neither label above can be true of them.

Pick exactly one. If you are torn between `sufficient` and `insufficient`,
choose `insufficient` — the whole arc is calibrated to under-claim rather than
over-claim, and a coin-flip scored as success is the failure mode that has cost
this project the most.

## The separate flag

**`no_note_possible`** — tick this when the request *is* an information need,
but no note could ever have satisfied it: the answer lives in the conversation
above ("what does option A refer to?"), in the code, or in the outside world.

This is **not** one of the three labels and does not enter the agreement
calculation. It exists because "the notes did not answer it" and "no notes
could have" are different facts, and only one of them is a retrieval failure.
Tick it alongside whichever label you chose.

## Deliberately not in scope

- **Whether the retrieval was *good*.** A turn where perfect notes exist in the
  vault but the wrong ones were pulled is still `insufficient`. Which of those
  is a ranking failure and which is a vocabulary failure is a different
  question, and answering it here would blur this one.
- **Whether the assistant's reply was correct.** Not shown, on purpose.
- **How many notes were injected.** Five thin notes and one dense note are
  judged the same way: could they answer it.

## Pre-registered expectation

Written before any label exists.

**Cohen's κ between operator and judge: 0.4–0.5.** The field norm for
LLM-versus-human relevance judgment is 0.2–0.5, and TREC 2024 measured GPT-4o
at 56% *raw* agreement on support assessment from scratch. Raw agreement
overstates κ by 33.8–41.2 points, so raw agreement is not reported as if it
were κ.

A moderate κ is the **expected** outcome, not a failure — it is precisely what
prediction-powered inference exists to correct for.

**κ below 0.2 means the judge is not usable**, and this task will say so
rather than ship it. **κ above 0.7 would be surprising** and I would look for
a leak — the most likely one being that the labels were collected in an order
that let the judge's verdict be visible.

## What the numbers must carry

- **κ with a confidence interval**, never a bare point estimate.
- **Raw agreement reported separately and labelled as not-κ**, so the two
  cannot be confused.
- **PPI checked against a synthetic case with known ground truth** before it is
  run on real labels — an estimator is not trusted because its formula looks
  right.
- **The pre-registered expectation compared to the outcome either way**,
  including if it was wrong.

## Labelling order

Turns are presented in a fixed, shuffled order stored with the worksheet. The
judge's verdict is **not** shown next to the turn. Neither is the assistant's
reply. If you want to stop partway, stop — a partial set of labels collected
without seeing the judge's answers is worth more than a complete set collected
after seeing them.
