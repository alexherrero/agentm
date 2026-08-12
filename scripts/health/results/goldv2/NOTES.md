# goldv2 baseline — lexical retrieval, hook-shaped, 2026-08-12

First run of `retrieval_scorecard.py` against `gold-set-v2.json` on the frozen
`goldv2-20260812` corpus (9,971 notes, vault head `4391c9e`). Deterministic:
query in, ranked list out, no model in the loop, so one run is exact.

## What this number is, and what it is not

**Overall R@5 = 10.9% (7 of 64 answerable questions).**

This is **not** comparable to week 1's 0.725 and nobody should quote them side
by side. Week 1 scored an *agent* driving `memory_search` — composing its own
queries, retrying with different vocabulary, up to six tool calls per question.
This scores the *tool alone*, single-shot, with the question reduced by
`recall._daemon_query_terms` exactly as the prompt-submit hook reduces it.

Two different pipelines. The agent layer is where users live; **this layer is
where the prompt-submit recall hook lives**, and that hook has no driver to
retry for it. So 10.9% is the honest hook-path number, and it is the first time
that path has been measured at all.

| stratum | n | hit@5 | rate |
|---|---:|---:|---:|
| distinctive-token | 12 | 3 | 25.0% |
| episodic-temporal | 12 | 3 | 25.0% |
| pure-paraphrase | 18 | 1 | 5.6% |
| research-corpus | 12 | 0 | 0.0% |
| research-density | 10 | 0 | 0.0% |
| **overall** | **64** | **7** | **10.9%** |
| negative (returned nothing) | 20 | 7 | 35.0% |

Latency p50 15.8ms, p90 74.3ms, max 114.1ms — including CLI process startup,
so the in-daemon figure is lower. Comfortably inside the hook's 300ms budget.

## The mechanism, isolated

FTS5 ANDs its terms and the extractor emits up to six. **A single term absent
from a note excludes that note entirely**, regardless of how well the other
five match. Long documents are the only ones that satisfy a six-term AND.

Worked example, `rc09` — "Which small Gemini model was picked for always-on
background work?" reduces to `small gemini model picked always background`.
The note that answers it contains *gemini*, *model* and *always*, but not
*small*, *picked* or *background*. It is not ranked low; it is not a candidate.
A 38KB design document containing all six wins instead.

This puts **atomic capture in direct tension with AND semantics.** The capture
doctrine says one concept per note, which makes notes short, which makes them
term-poor, which makes them systematically ineligible for multi-term queries.
The corpus is being punished for following its own design.

## The failure is dishonest, not merely wrong

Of 57 misses, **44 returned a confident wrong neighbour and only 13 returned
nothing.** Of those 44, **27 were `desk/` working documents** against 6 from
`memory/`. This reproduces the 2026-08-11 finding at gold-set scale and with
the mechanism now explained: `desk/` documents are long, so they are the ones
eligible to satisfy the AND.

An empty answer is honest. A wrong neighbour is not. Three quarters of this
harness's failures are the dishonest kind.

## What this licenses, and what it does not

Licensed, because the mechanism is isolated and the corpus is frozen:

- **AgentKV's score-thresholded OR fallback** now has a named defect to fix.
  Their floor constant does not transfer (see the reciprocal handoff), but the
  shape does — with a floor derived from our own score distribution.
- **A space-aware rank signal** has a measured target: 27 of 44 wrong
  neighbours are `desk/`.
- **Hybrid retrieval** is aimed at the paraphrase and research strata, which
  read 5.6% and 0.0% — the two the AND requirement hurts most.

Not licensed:

- No claim that recall "collapsed." The agent layer was not measured here.
- No verdict on the vector sidecar. That decision needs both layers, and this
  is one.
- No claim about aliases from this run. Research-corpus scored 0/12 across both
  halves of the `alias_overlap` split, so the split could not discriminate —
  with the AND requirement dominating, alias vocabulary never got a chance to
  matter. Re-read that split only after the OR fallback lands.

## Rejection is a floor, not a verdict

35% of negatives "correctly rejected" means the tool returned nothing, which
happens when a term was missing from the index — not because anything judged
the corpus unable to answer. There is no score threshold in this stack. Read
the number as a floor and expect it to move when the OR fallback lands, which
is exactly the trade AgentKV measured at 20% on their corpus.

## Reproducing

```bash
python3 scripts/health/retrieval_scorecard.py \
    --gold-set scripts/health/fixtures/week1-gold/gold-set-v2.json \
    --json "$MEMORY_VAULT_PATH/_meta/health/goldv2/baseline-fts5-20260812.json"
```

**Per-question detail is not committed.** A scorecard names the note behind
every hit and miss, which mirrors vault paths — the operator's own `Personal/`
notes among them — into a public repo. The aggregates in this file are the
public artifact; the detail lives at
`<vault>/Agent/_meta/health/goldv2/`. `scripts/health/results/**/*.json` is
gitignored for that reason, not by oversight.

Requires a running daemon serving the goldv2 corpus. The corpus is archived at
`<vault>/_meta/corpus-snapshots/goldv2-20260812.tar.gz`; restore it and point a
daemon at it to reproduce this exactly rather than against a drifted live vault.
