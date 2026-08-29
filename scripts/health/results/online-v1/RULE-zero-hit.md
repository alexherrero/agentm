# RULE — characterizing the zero-hit half (online-recall task 2)

**Registered before any sweep runs. Contract:
`wiki/reference/Retrieval-Eval-Contract.md`; template:
`scripts/health/results/RULE-TEMPLATE.md`.**

## Mechanism under test

The recall hook's term extractor caps its output at **six terms** (measured
over 675 real injections: median 6, min 1, max 6), and the lexical arm conjoins
them — every term must appear in the same note. As the term count rises,
P(all terms co-occur in one note) falls, and against a corpus of ~7,900
documents that product gets small fast. If that is the driver, the 44.4%
zero-hit rate is a query-construction property rather than a corpus or ranking
property, and it has obvious levers.

## Population

The **real extracted term-sets** from task 1's 675 transcript injections — real
prompt vocabulary, not invented queries. Each is re-queried at k = 1…6 terms
across the daemon's three modes (`and`, `fusion`, `hybrid`). Nothing is
inferred from prompts we cannot see; the terms are what the hook itself
recorded in its transparency line.

**Known bias, stated up front:** these 675 are the injections that *reached a
transcript*, which skews toward recalls that already returned something. That
makes this a conservative test — if even the successful population improves
when terms are dropped, the effect on the zero-hit population is unlikely to be
smaller. It also means the absolute rates here are **not** the corpus-wide
zero-hit rate, and must not be reported as such.

## The prediction

Written before the sweep:

1. **Zero-hit rate falls monotonically as terms are dropped** (k=6 worst,
   k=1 best).
2. **`and` mode falls fastest** — it is the pure conjunction; `fusion` and
   `hybrid` should degrade more gently because they combine sub-queries and a
   dense arm.
3. **The originally-injected notes mostly survive term reduction** — dropping
   terms should widen the result set, not replace it.

If (1) fails, the AND-semantics account is **eliminated** and the 44.4% needs a
different explanation. That outcome is worth as much as a confirmation and gets
recorded with equal prominence.

## Positive control

A query whose terms are drawn verbatim from one known note must return that
note. If the control fails, the probe is not measuring retrieval and no number
from the run counts — the same liveness discipline the offline eval's canary
enforces.

## Not in scope

Changing the query path. This task measures and predicts. Any back-off rule,
cap change, or mode switch is its own plan with its own pre-registered bar —
shipping a fix inside a measurement task is how a probe stops being evidence.

## Per-question record

Filled by the run: hit rate per (mode, k), never a single blended number.

## Outcome

Filled after the run.

## Per-question record

Sweep over real term-sets, terms only, no question (isolating the conjunction):

| mode | k=1 | k=2 | k=3 | k=4 | k=5 | k=6 |
|---|---|---|---|---|---|---|
| `and` | 0% | 0% | 0% | 0% | 0% | 0% |
| `fusion` | 0% | 0% | 0% | 0% | 0% | 0% |
| `hybrid` | 0% | 0% | 0% | 0% | 0% | 0% |

Mean results returned: 4.8–5.0 at every cell, i.e. a full slate every time.

The five term-sets that **actually returned zero in production**, re-issued
today, at every term count and in every mode: **5 results each, none empty.**

## Outcome — the hypothesis is refuted, and the answer was elsewhere

**Prediction 1 (zero-hit falls monotonically as terms drop): not confirmed —
and, importantly, not testable on this data.** Every cell reads 0%, so the
series is trivially monotone. The probe's first version printed HOLDS for that
and meant nothing; `check_monotone` now returns `no-variation` as a third
answer so a prediction that cannot fail is never scored as confirmed.

**Prediction 2 (`and` degrades fastest): refuted.** No mode degrades at all.

**Prediction 3 (injected notes survive reduction): vacuously true.**

**The AND-semantics account is eliminated.** The daemon returns a full slate
for six-term queries, for the successful population *and* for the five queries
that returned nothing in production. Whatever emptied those results is not the
conjunction, and the RULE's stated bias — that the 675-injection population
skews to recalls that already worked — turned out to be the limiting factor
exactly as written.

**Where the answer actually was: time.** The lifetime 44.4% is an average over
two different systems.

| period | recalls | zero-hit |
|---|---|---|
| before the ladder (to 2026-08-13) | 3,462 | **60.3%** |
| after the ladder (2026-08-15 on) | 5,512 | **35.9%** |
| last 7 days | 2,565 | 35.9% |

The hybrid-retrieval ladder merged **2026-08-14** (`8feb58e`, #438). The
production zero-hit rate breaks there and has been flat for a fortnight since.
Hit rate after the ladder: **64.1%, Wilson 95% [62.8%, 65.4%], n = 5,512** — an
interval roughly eight times tighter than the offline gold set's ±10.6pp at
n = 64.

**This is observational, not causal.** It is a before/after on a system that
changed in more than one way: the corpus grew, thousands of notes were retired
and rewritten in the same window, and the daemon changed with the ladder. The
effect is large (24 points) and the timing is tight, which makes it the best
available evidence that offline work reached production — and it is not a
controlled experiment, and is not written as one.

**What this closes:** the plan's framing that "44% surface nothing" describes
today. It does not. The current rate is ~36%, stable, and the headline number
for any future comparison is the post-ladder band with its interval.

**What it opens, unfunded and unscheduled:** the five production-zero queries
succeed today, so their failure was a property of the corpus or index at the
time rather than of the query. Explaining that needs per-recall state the
ledger does not keep — it records *that* a recall failed and nothing about
*why*. Adding the extracted terms to zero-hit ledger rows is the cheap
unblocking change, and it is a query-path edit, which this RULE puts out of
scope on purpose.
