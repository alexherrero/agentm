# RULE — what the sufficient-context judge has to clear to be believed

**Frozen:** 2026-08-29, before any judge call was made.
**Applies to:** `scripts/health/sufficient_context.py`.

## Why a bar is needed at all

The deterministic comparator came in at 0.2% (7 named notes of 3,004). It
cannot carry this measurement, so the judge carries essentially all of it. That
removes the cross-check a weak-but-honest baseline would have given, and the
right response is to make the judge prove itself *before* its numbers are
quoted anywhere — not after a number looks interesting.

## The bars

**1 — Constructed cases, both directions.** A query whose context plainly
contains the answer scores `sufficient`. A query whose context is plainly about
something else scores `insufficient`. Both, or the judge is not reading.

**2 — It refuses the ill-posed case.** A request that is an instruction rather
than a question ("run task 5", "yes", "continue") scores `n/a`. The ICLR
autorater was built for QA; most of this traffic is commands. A judge that
invents a sufficiency verdict for "run task 5" is generating noise that would
then be averaged into a headline rate.

**3 — Stability, measured: unanimity ≥ 0.80 across 3 replicates.**
`claude -p` exposes no temperature or seed flag, so determinism cannot be
asserted, only observed. Three replicates unanimous on ≥80% of turns implies
roughly ≥93% per-call consistency (`p³ + (1−p)³ ≥ 0.80` gives `p ≈ 0.93`).

Below 0.80 the per-turn verdict is not trustworthy and I will say so rather
than quietly leaning on majority vote to hide it. The aggregate rate may still
be estimable in that case, but it stops being a per-turn instrument and the
write-up has to say which one it is.

**Amended 2026-08-29, before the run this bar now governs — the metric was
measuring the wrong turns.** The first implementation computed unanimity over
*scored* turns only, excluding `n/a`. A calibration run then came back with 2
of 3 `n/a` turns split, none of which the metric could see. Excluding the
turns where a judge is least stable is not a stability measurement, so
unanimity now spans **every turn that produced a verdict**. The 0.80 bar
carries over to the stricter metric unchanged; loosening a bar to keep a pass
is the exact move this file exists to prevent.

A second number ships alongside it: **`scoreability_split_rate`**, the share of
turns whose replicates disagree about whether the turn is an information need
at all. That failure is sharper than an unstable verdict, because it moves the
turn in or out of the denominator of `sufficient_rate` — the headline's base
changes rather than one row's answer. No bar is set on it here; it is being
measured for the first time and I will not invent a threshold before seeing
what the instrument does.

**And the first result is reported with its interval, not just its point.**
6 of 7 unanimous is 85.7% with a 95% interval of [48.7%, 97.4%] — the bar sits
*inside* that interval, so the first run is not evidence the bar was cleared.
The bar as frozen never named a sample size, which was an omission in it. Any
claim that this judge is stable needs an interval that clears 0.80, not a point
estimate that does.

**4 — The failure path is exercised, and never scores zero.** A call that
times out, returns nothing, or returns unparseable text is *excluded and
counted*. This is the completeness-v1 lesson: that run scored failed calls as
zero and produced a number that was mostly timeouts wearing a result's clothes.

**5 — A rejection names the gap.** `{"verdict": "insufficient"}` with no
`missing` list is rejected as malformed, not accepted as a verdict. Borrowed
from `grounding.go`, whose faithfulness judge makes the same demand: a
rejection with nothing named is a judge that disliked the input rather than one
that found a problem.

## What I expect, written down first

I expect bars 1, 2, 4 and 5 to pass — they are properties of a prompt and a
parser, and I control both.

I am **less sure about bar 3**, and that is the one that matters. If the judge
disagrees with itself on more than a fifth of turns, this instrument does not
support per-turn claims no matter how good its aggregate looks, and the honest
report is that the online measurement is noisier than the offline one it was
meant to validate.

## What would make me abandon the judge rather than fix it

If unanimity is near chance (~0.5) the judge is not measuring anything stable
and no prompt engineering rescues that within this task — it would mean the
question is ill-posed for this traffic, and the finding is that online
sufficiency is not measurable this way. I would rather report that than ship an
instrument whose numbers move on re-run.

## A confound found while the first real run was going, recorded before its number is quoted

The judge is answering exactly the question it was asked — *could this context
alone answer this request* — and that is **not** the same question as *did
recall do its job*. Two of the first four real turns were scored `insufficient`
for gaps like "what option 'A' refers to (the prior menu of choices being
decided between)". That answer lives in the **conversation**, not in any note.
No retrieval system could have supplied it, and no change to recall would move
that verdict.

So `sufficient_rate` has a floor built into it that has nothing to do with
retrieval quality, and a reader meeting "14% sufficient" will hear "recall
fails 86% of the time", which the number does not say. Whatever this run
reports, that sentence goes next to it.

Not fixed inside this task, deliberately. Adding a fourth category
(`conversational`) mid-run would invalidate the replicates already bought, and
attributing an insufficiency to *recall* rather than to the request's own shape
is what task 6's utilisation signal and task 7's operator labels exist to do.
Recorded here so the limitation is on the record before the number is, rather
than discovered afterwards by someone reading the rate.

## The cross-model check, and what sharpening the n/a seam has to achieve

**Frozen 2026-08-30, before the re-run.**

Asking a second model the identical question turned out to be the cheapest
useful thing in this task, because it runs before the operator's hours rather
than after. Gemini, via `agy`, on the same 90 turns, blind to Claude's answers:

| | turns | share |
|---|---|---|
| agree outright | 61 | 67.8% |
| differ on whether it is a question at all | 21 | **23.3%** |
| both say question, differ on sufficiency | 8 | 8.9% |

κ = 0.4134, 95% CI [0.2551, 0.5716]. Restricted to the 26 turns both call a
question, κ = 0.1938 with CI [−0.1124, 0.50] — imprecise, but not reassuring.

**The finding that matters: the headline was model-dependent.** Sufficiency came
out 14.0% under Claude and 30.0% under Gemini. A number that moves by more than
two-fold with the judge is a fact about the judge, and it was on its way into a
report as a fact about recall.

Reading all 21 boundary disagreements showed a single cause: instructions that
presuppose knowledge — "close the june plan", "fix the vault drafts". Claude
read them as information needs, Gemini as commands to act. The rubric's own
wording ("instructions to act") backed Gemini. Both judges were following it;
it was ambiguous. The operator ruled that such instructions *are* information
needs, since most real work is instructions.

**What the sharpened wording has to achieve, written before the re-run:**

1. **Boundary disagreement falls well below 23.3%.** If it does not move
   materially, the seam was never definitional and no wording fixes it — the
   honest conclusion then is that this question cannot be asked of a model at
   this precision, and the task says so.
2. **The two models' sufficiency rates converge.** 14.0% against 30.0% is the
   defect; if they still differ by more than roughly ten points, no single
   number can be published, whatever κ says.
3. **κ over all turns rises.** Predicted 0.5–0.6 — the boundary cases were most
   of the disagreement, so removing them should move it. Below 0.45 would mean
   the sharpening did not take.

I am *less* confident about the inner κ of 0.19. Sharpening the n/a wording
does not touch how sufficiency itself is judged, so that number may not move at
all; what should change is how many turns reach it. If the inner κ stays near
0.2 on a larger inner set, that is the real limit of this instrument, and it
belongs in the write-up rather than being averaged away.

### What the re-run gave, against those bars

| bar | predicted | outcome | |
|---|---|---|---|
| boundary disagreement | well below 23.3% | **11.4%** | met |
| sufficiency rates | within ~10 points | **9.9 points** (6.8% vs 16.7%) | met by a hair |
| κ overall | 0.5–0.6 | **0.5229** [0.3668, 0.679] | met |
| inner κ | *"may not move at all"* | **0.1938 → 0.4516**, 26 → 68 turns | **prediction wrong** |

**The prediction I got wrong is worth more than the ones I got right.** I wrote
that sharpening the n/a wording could not touch how sufficiency itself is
judged. It did, because the inner set changed composition: the
knowledge-presupposing instructions the operator's ruling admitted are *easier*
to agree about than the turns that were there before. The ceiling I warned might
be this instrument's real limit was not where I thought it was.

**Bar 2 is not a clean pass and is not recorded as one.** 9.9 points sits inside
a ten-point line I drew myself, which is too close to lean on, and the *ratio*
got worse — 2.14× before, 2.45× after. What is actually informative is that the
intervals overlap: Claude [2.9%, 14.9%] against Gemini [9.8%, 26.9%]. The two
estimates are not statistically distinguishable at this n, and a single
published number still waits on the operator's labels to arbitrate.

**The substantive movement.** Both models now score about 83% of turns as real
information needs, up from 48% and 33%, and sufficiency fell for both — Claude
14.0% to 6.8%, Gemini 30.0% to 16.7% — because the newly admitted instruction
turns are mostly insufficient. Memory rarely knows which june plan was meant.

Two turns of 90 lost a verdict from one model or the other and are excluded and
counted rather than scored, per the failure rule above. Claude's half cost
$21.92; `agy` reports no cost and none is invented for it.

## The adversarial review, and what it cost the headline

An adversarial reviewer was pointed at the instrument rather than at the labels,
because every significant failure in this arc has been an instrument failure.
It returned sixteen findings. Five of the top six verified against the
artefacts; the numbers below are mine, not its.

### The precision floor, which bounds everything else here

The same judge, same prompt, same 90 turns, run three times:

| run | sufficient | |
|---|---|---|
| 1 | 6/75 | **8.0%** |
| 2 | 10/77 | **13.0%** |
| 3 | 9/74 | **12.2%** |

**A 5.0-point spread from re-running alone**, test-retest κ 0.73–0.78 across all
three pairings. Two consequences, both load-bearing.

First, the "model dependence" this file diagnosed and then certified as fixed —
Claude 6.8% against Gemini 16.7% — is *substantially one model's own variance*.
The sharpened wording did close a real ambiguity, but the residual gap it was
measured against was never as solid as the table above it implied.

Second, **every confidence interval in this arc covers sampling error only.**
None of them include run-to-run drift, and the drift is comparable to the
widths being quoted. Nothing here can resolve a difference smaller than about
five points, and no claim in this arc should be finer than that.

The test-retest κ is also a ceiling: an agreement measure between two raters
cannot meaningfully exceed the reliability of either, so the cross-model κ of
0.52–0.59 sits close to the most the instrument could ever show.

### What the headline should have been

Applied one at a time, so each correction's size is visible:

| | sufficient | 95% CI |
|---|---|---|
| as published | 13/79 = 16.5% | [9.9%, 26.2%] |
| minus gaps no note could hold (−33) | 11/47 = 23.4% | [13.6%, 37.2%] |
| and hybrid arm only (−9 lexical) | 11/43 = **25.6%** | [14.9%, 40.2%] |

Half of the judge's own insufficiency verdicts — 33 of 67 — named an
unresolved referent from the conversation, "what 'both' refers to" and its
kind. This file recorded that confound and never measured it; counting those as
retrieval failures was the single largest error in the published figure.

### What the sample actually describes

**72 of the 90 turns, 80%, come from the memory system's own repository.**
Seven projects are represented, but four turns in five are agentm talking about
itself — a repository that discusses recall constantly and therefore asks it
unusually well-aimed questions. This measures how recall performs while the
operator works *on recall*. Nothing here is evidence about the rest of their
work, and `corpus_stamp` does not record the project mix.

Session clustering is real but small: ICC 0.064 on the panel labels, design
effect 1.15, so intervals widen about 7%. The review put it at 0.179; that did
not reproduce.

### A statistic that could not fail, and one that now can

`production_judge_matches_panel = 0.90` had a floor of 0.822 by construction —
the panel's label is a majority containing the judge's own vote, so whenever the
other two agreed a match was guaranteed before the third grader was asked. A
leave-one-out panel, with the judge excluded from the label it is scored
against, gives **89.2% [80.7%, 94.2%]**: nearly the same point, on a statistic
that could have come out otherwise.

### Two review claims that did not survive checking

The "perfectly nested" 2×2 — zero cells where Claude says sufficient and Gemini
says insufficient — has three such cells. And the ICC above. Both came from run
1 where mine came from run 2, which is the drift finding reaching the reviewer
too.

### The κ interval was wrong, and the correction crosses a pre-registered line

`cohen_kappa` used σ₀², the variance under H₀: κ = 0 — correct for a
significance test, wrong for an interval around an observed κ, and citing the
right authors for the wrong formula made it invisible to reading. Against a
5,000-resample bootstrap the null form was off by 0.047 and 0.127 in width and
the non-null form by 0.005 and 0.018. Corrected, and the cross-model
both-scored interval now reaches **below 0.2** — the line this file
pre-registers as "the judge is not usable". It no longer excludes that.

## Privacy, restated because it is easy to lose in a judging loop

The judge runs locally via `claude -p`. Only the query hash, the verdict, and
the count of named gaps reach disk. Not the prompt, not the injected text, and
not the judge's wording of what was missing — that wording quotes the query by
construction. The terminal may show it; the file may not.
