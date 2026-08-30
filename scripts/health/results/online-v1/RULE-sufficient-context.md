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

## Privacy, restated because it is easy to lose in a judging loop

The judge runs locally via `claude -p`. Only the query hash, the verdict, and
the count of named gaps reach disk. Not the prompt, not the injected text, and
not the judge's wording of what was missing — that wording quotes the query by
construction. The terminal may show it; the file may not.
