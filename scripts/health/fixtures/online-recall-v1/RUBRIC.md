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

## Amendment, 2026-08-30 (second) — two questions, because one was doing the work of both

**What happened.** The operator labelled twenty turns and agreement with the
judges came out at **κ = 0.008** against Claude and **0.017** against Gemini —
zero, with the disagreement running one way on 17 of 20. Asked which reading
they had applied, they confirmed: *was the retrieval reasonable and on-topic*,
not *could these notes alone answer it*.

**That is not a mistake, and the labels were not discarded.** The two questions
catch different failures, and the pair says more than either:

| | fails when |
|---|---|
| **relevant** | recall returned the wrong area entirely — a coverage or vocabulary failure |
| **sufficient** *while relevant passes* | recall found the right area but not the note that answers it — a **ranking** failure |

Task 6 found the `ignored` corner empty: the model is not discarding good
context. So if most retrievals are relevant while few are sufficient, the
diagnosis is precision rather than coverage, and those have different fixes.
The twenty labels already written are kept as **relevance** judgements.

**The wording that failed, and why it kept failing.** Three disagreements in
this task have now come from the same omission: the rubric never said *what the
reader is assumed to know*. "Could the notes alone answer the request" reads
one way if the reader is you — carrying the conversation, the repo and the
month's history — and another if the reader knows nothing. Both readings are
reasonable; only one was intended. It is stated below rather than implied.

**Where the judge is also wrong.** On one turn — "are we in any arc right now
in the blog?" with the archived June arc plan retrieved — the judge demanded
"current status as of today" and marked it insufficient. This rubric says
inference from what is there counts, and a reader holding "the last arc closed
2026-06-27" can answer that question. The judge is stricter than its own
instructions on exactly this move, and that is a defect on its side, not the
operator's.

## Amendment, 2026-08-30 (third) — adjudication, and what it gives up

**The operator overruled the blind design, with a better argument than the one
I made for it.** On the "close the june plan" turn they had marked `sufficient`
without noticing that no June-dated plan was among the three retrieved — May
24, May 28, July 24. Their point: an unaided label made in ignorance of that
fact is not ground truth, it is a mistake, and κ against a mistake measures
nothing. My reasoning had assumed unaided labelling produces *correct* labels.
It produces *independent* ones, which is not the same thing.

**So the worksheet now shows the machine's verdict and its reasoning, and the
operator rules on it.** Their ruling is final and is what the judge is measured
against.

**What that forfeits, stated plainly.** Agreement measured after the operator
has seen a verdict is not chance-corrected independent agreement, and κ over
these labels is never reported as if it were. The one honest blind number this
task will ever have is the twenty labels already written: κ = 0.008 against
Claude, 0.017 against Gemini, with the disagreement running one way on 17 of 20.
That stands as the unaided figure and is reported as such — noisy at n=20, and
collected under the relevance reading rather than this one, both of which are
said wherever it appears.

**What it buys.** An adjudicated gold set: labels where a careful reader checked
the reasoning and ruled. That is how most evaluation sets are actually built,
and it is more accurate per item than anything unaided. The judge is then
measured as accuracy against those rulings, which is a real number with a
different name.

**Two guards against the obvious failure**, which is the operator deferring to a
machine that sounds confident:

1. **Both models are shown when they disagree.** Where Claude and Gemini differ
   the sheet says so and gives both. A reader cannot defer to consensus that
   does not exist, and the models differ on roughly a fifth of turns.
2. **The facts are shown separately from the verdict.** Every word of the
   request is checked against everything retrieved, and both the found and the
   missing lists are printed in full — uniformly, every turn, chosen by nobody.
   That is the fact the operator missed on the June turn (`june` appears
   nowhere in three retrieved plans) and it is stated without a verdict
   attached, so it supports a judgement rather than replacing one.

## The question you are answering

For each turn you will see **the request** made to the coding assistant and
**the notes that were automatically injected** alongside it.

**Two ticks per turn.** They are separate questions and a turn can pass one and
fail the other — that combination is the most informative outcome there is.

> **RELEVANT — did recall return material in the right area?**
>
> Yes if the notes are about the thing being asked about, even when they do not
> settle it. No if they are about something else. This is a judgement about
> whether retrieval aimed correctly.

> **SUFFICIENT — could these notes *alone* answer the request?**
>
> **Assume a reader who knows nothing but what is on the page.** Not you: not
> the conversation above, not the repo, not what happened last month. If that
> reader would have to go and find something else before they could act, the
> answer is no — even when the notes are plainly relevant, and even when *you*
> could act on them easily.
>
> Inference still counts. Notes saying "the last arc closed on 27 June" answer
> "are we in an arc now?" without saying so in those words. What does not count
> is knowledge the reader would have to bring.

You are judging the *notes*, not the assistant's reply, and not whether the
assistant did well. You will not be shown the reply — deliberately, so a good
answer cannot make thin context look sufficient.

### Worked, from the operator's own turns

| request | retrieved | relevant | sufficient | why |
|---|---|---|---|---|
| "are we in any arc right now in the blog?" | the archived June arc plan, closed | **yes** | **yes** | a reader knowing nothing can infer "no arc" from the close |
| "close the june plan with Task 4 obsoleted" | plans from May 24, May 28, July 24 | **yes** | **no** | right kind of material, but no June plan is present to act on |
| "draft a brief for both" | routing verdicts, prompt packs | **yes** | **no** | "both" refers to the conversation; no note can supply it — tick `no_note_possible` |

## The three labels

**`sufficient`** — someone holding only these notes could respond to the
request. Partial coverage counts as sufficient when the uncovered part is not
what was being asked. Rephrasing, condensing and inference from what is there
all count; the notes do not have to state the answer in the request's words.

**`insufficient`** — the notes leave a real gap. Someone holding only these
would have to go and find something else before they could respond.

**`n/a`** — the request needs no information at all to act on. A bare approval
("yes", "go ahead"), a bare retry, or a command whose meaning is complete on its
own ("run the tests", "continue").

**An instruction is still an information need when it names something you would
have to know to act.** "Close the june plan", "fix the vault drafts", "move step
2 after the pixel move" — each presupposes specific knowledge, so ask the same
question of them: could the notes alone let someone carry that out? Phrasing
something as a command does not make it `n/a`.

*This sentence was sharpened on 2026-08-30, and the reason is measured. Two
independent judges — Claude and Gemini, asked the identical question — disagreed
about exactly this seam on 23.3% of turns, which was most of their total
disagreement and drove the headline sufficiency rate to 14.0% under one and
30.0% under the other. The operator ruled that a knowledge-presupposing
instruction is an information need, on the grounds that most real work is
instructions and the narrow reading would measure a small slice of it.*

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
