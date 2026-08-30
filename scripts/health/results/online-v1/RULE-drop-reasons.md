# RULE — record why a recall came back empty (online-recall task 3)

**Registered before the code changes. Contract:
`wiki/reference/Retrieval-Eval-Contract.md`.**

## The gap this closes

The ledger writes `hits: []` when a recall surfaces nothing — 4,254 rows, 36%
of current traffic. That value is ambiguous between two opposite conditions:

* the daemon returned no rows at all (a retrieval problem — vocabulary, index,
  or embedder), and
* the daemon returned a full slate and the hook's post-processing dropped every
  one (a policy problem — directory rules, always-load dedup, scope).

Task 2 showed the distinction is live rather than theoretical: the five
recoverable zero-hit queries return five results each when replayed today.
Without the split, any future work on the 36% is guessing which half it is
addressing.

## What gets recorded

Per recall, integers only:

| field | meaning |
|---|---|
| `returned` | rows the daemon handed back, before any filtering |
| `dropped.inadmissible` | failed recall's directory rules (`_inbox`, `scratch`, `_archive`, dotdirs) |
| `dropped.unrooted` | vault root could not be resolved for the path |
| `dropped.out_of_scope` | outside the memory root while scope is not `vault` |
| `dropped.deduped` | already injected as always-load, or a repeat within the slate |
| `dropped.malformed` | row was not a usable object |

**No terms. No prompt text. Nothing prompt-derived.** The extracted terms were
the obvious thing to store and are deliberately excluded: they are prompt
vocabulary, the ledger's documented contract is *query as a hash, never raw
text* (`recall_counter.py:7`), and the operator's privacy call for this plan is
hashes and verdicts only. The counts answer the question more precisely than
the terms would have — "five returned, five deduped" names a cause; six words
would still need interpreting.

## The constraint this must not violate

`agentm-recall-trace.md:248-255`: instrumentation must not touch what recall
returns. This task adds counting to a loop that already exists and changes no
ranking, no filtering, no ordering, and no injected output. **The proof is that
the recall path's existing tests stay green without being edited** — if a test
had to change, the behaviour changed, and the task has failed its own rule.

## Prediction

Written before the first row lands: **most zero-hit recalls will show
`returned == 0`**, i.e. genuine retrieval misses rather than over-filtering.
Reasoning: the hook's filters are narrow (directory rules plus dedup against a
currently-empty always-load set), and an empty always-load set cannot dedup
anything. If instead a large share shows `returned > 0` with everything
dropped, the 36% is substantially self-inflicted and the fix is policy, not
retrieval — a more actionable outcome than the prediction, and the reason the
prediction is written down rather than assumed.

## Verification

Synthetic daemon responses, no live corpus needed: all-inadmissible rows record
five returned and five inadmissible with `hit_count` zero; an empty response
records zero returned; a normal response records the drops it made. Each
counter is mutated in turn and its test confirmed red.

## Outcome

Filled after the first real rows accumulate.
