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
