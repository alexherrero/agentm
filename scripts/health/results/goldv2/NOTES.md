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

## Candidacy analysis (2026-08-12, second pass) — this re-ranks the levers

For every miss, does the target appear *anywhere* in the top 50, or is it not
a candidate at all? The answer decides whether ranking levers or vocabulary
levers can help, and it was measured before choosing:

| class | n | what could fix it |
|---|---:|---|
| target a candidate, outranked (ranks 8, 9, 34, 39) | 4 | re-ranking (space-aware, length norm) |
| target absent from top-50, query returned nothing | 13 | OR-on-empty fallback |
| target absent from top-50, query returned wrong docs | **40** | **vocabulary bridging only** |

The two cheap levers are therefore bounded: perfect re-ranking recovers at
most 4 misses, a perfect OR-on-empty fallback at most 13, and **the two
together cap at 37.5% R@5** — while 40 of 57 misses (70%) are untouchable by
both, because the AND already returned confident wrong documents, so the
fallback never fires and no re-ordering can surface a note that is not a
candidate.

A first version of this file called the OR fallback "the highest-value lever."
That was wrong, and the candidacy split is what showed it. The 27-of-44
desk/ figure stands as a fact about what the wrong answers look like, not
about what a desk demotion can recover.

Two probes on the 40+13 non-candidates:

- Keeping only the N longest question terms (a crude distinctiveness proxy)
  creates candidacy in 3 of 53 cases — the length heuristic is not the lever.
- Term *selection* is: rc09's six-term query misses entirely, but the right
  three of those six (`gemini model always`) hit the target at rank 1. The
  index knows document frequencies, so IDF-aware selection inside
  `_daemon_query_terms` is cheap, testable on this harness, and plausibly
  recovers the partial-overlap subset. It cannot touch the zero-overlap
  strata: `pure-paraphrase` is zero-overlap **by labeling rule**, so
  single-shot lexical failure there is close to definitional — the only
  bridges are aliases written in question vocabulary, or vectors.

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


---

# 3a experiment: term-selection fusion — refuted, and it re-aims the plan

Run 2026-08-12 on the same frozen corpus, rule written before the code:
*converts ≥10 of the wrong-doc non-candidates into top-5 hits without dropping
the negative floor below 7/20.*

## What was tested, in order, and what each step killed

**IDF-aware selection: refuted before implementation.** The plan was to keep
the rarest terms. Measured document frequencies for `rc09` say that picks
exactly the wrong ones — `picked` (df 77) and `background` (df 175) are the
two rarest *and* the two absent from the target, while the winning triple
`gemini model always` includes the corpus's 823- and 4,206-document terms.
Rarity does not predict presence in the answer. No code was written.

**The oracle ceiling is enormous.** For each of the 53 non-candidate misses,
is there *any* subset of the same extracted terms that reaches top-5?
**45 of 53.** The information is already in the query; ANDing all six destroys
it. Winning subsets are mostly 2–3 terms. This means term selection, not
vocabulary, is the dominant lexical defect — and it contradicts this file's
earlier claim that 40 of 57 misses need "vocabulary bridging only."

**Implementable fusion captures half the ceiling.** Issue every n-term subset
and fuse. RRF dilutes badly (2-term 28%, 3-term 11%) because a document
appearing in many mediocre sub-rankings outranks one placing first in a single
precise sub-query. Max-score fusion — best single piece of evidence wins —
reaches **23/53 (43%)** at both n=2 and n=3.

**On the full gold set it fails its own rule.**

| | baseline | max-score fusion, 2-term | lexical-fusion (in-daemon) |
|---|---:|---:|---:|
| distinctive-token | 3/12 | 7/12 | 7/12 |
| episodic-temporal | 3/12 | 6/12 | 6/12 |
| pure-paraphrase | 1/18 | 5/18 | 5/18 |
| research-corpus | 0/12 | 6/12 | 6/12 |
| research-density | 0/10 | 3/10 | 3/10 |
| **R@5** | **10.9%** | **42.2%** | **42.2%** |
| **negative rejection** | **35%** | **0%** | **0%** |

Recall nearly quadrupled and rejection went to zero — every one of the 20
negatives returned a confident wrong answer. The pre-registered floor was
7/20. **Rejected.** This is the OR rewrite's trade at larger magnitude, and
the rule existing in advance is the only reason it was not shipped on the
strength of "+31 points."

The third column is the same arm run through `agentmd -mode fusion` instead of
the throwaway driver, and it reproduces the simulation cell for cell rather than
within noise — the ladder's first rung, quarantined behind a flag the
prompt-submit hook does not set. The `and` arm re-scored at 10.9% on the same
restored corpus after the refactor, which is what makes "the hook path is
unchanged" a measurement rather than a claim. Fusion is also *cheaper* here
(p90 24.4ms against the baseline's 79.2ms): fifteen two-term queries each seek a
short doclist, where one six-term query scans a long one.

## Why no score floor rescues it, and what that implies

Sweeping a floor over the fused top-1 score:

| floor | R@5 | rejection |
|---:|---:|---:|
| none | 42.2% | 0% |
| 14 | 20.3% | 45% |
| 16 | 7.8% | 60% |
| 20 | 1.6% | 100% |

The reason is visible in the distributions: **negatives score higher than
answerable questions.** Median top-1 is 15.1 for negatives against 13.2 for
answerables; the highest-scoring negative (19.5) beats the lowest-scoring
answerable (7.9) by a wide margin. The two classes overlap almost completely
and are ordered the wrong way.

That is not a tuning problem. **BM25 measures term-match strength, not
answer-existence.** A plausible question about a well-discussed topic with no
specific answer — "what is our retention policy for daemon request logs" —
matches many documents strongly. A question answered by one small atomic note
matches one document weakly. Ranking by match strength therefore ranks
negatives *above* positives, and no monotone threshold on that score can
separate them.

Floor 14 is a genuine Pareto improvement over baseline on both axes
(20.3%/45% against 10.9%/35%), and it is **not recommended for shipping**: the
constant was chosen by looking at this sweep, on 84 questions, which is
fitting to the answer sheet.

## What this licenses

- **Do not ship 3a.** Its own rule refuted it, and the floor variant is
  overfitted.
- **The sidecar case is now made by our own data, not by AgentKV's.** A cosine
  similarity is a similarity; BM25 is a match strength. Their `< 0.55`
  threshold assumes a signal ours does not have, which is also the most likely
  explanation for their rejection sitting at 20% in every arm — their floor was
  not doing work either. **Send this section back to them.**
- **The lexical channel still has 45/53 of unrealized headroom**, unreachable
  by any policy that cannot tell a good subset from a bad one at query time.
  An agent can, by iterating — which is exactly why the week-1 agent-layer
  number is 0.725 and this hook-layer number is 0.109. The hook has no driver.


---

# Arm comparison, per stratum (2026-08-12)

All arms on the frozen `goldv2-20260812` corpus, same questions, same
`recall._daemon_query_terms` extraction. Deterministic; one run is exact.
`oracle` is not an arm — it is the best any subset of the same extracted terms
could do, and exists to bound what term selection can ever achieve.

| arm | distinctive | episodic | paraphrase | research-corpus | research-density | **overall** | rejection |
|---|---:|---:|---:|---:|---:|---:|---:|
| and-of-6 (production) | 25% | 25% | 6% | 0% | 0% | **10.9%** | 35% |
| rrf fusion, 2-term | 58% | 42% | 17% | 33% | 20% | **32.8%** | 0% |
| max fusion, 2-term | 58% | 50% | 28% | 50% | 30% | **42.2%** | 0% |
| max fusion + floor 14 | 33% | 17% | 11% | 25% | 20% | **20.3%** | 45% |
| max fusion + floor 16 | 17% | 8% | 0% | 8% | 10% | **7.8%** | 60% |
| *oracle (best subset)* | *92%* | *83%* | *67%* | *100%* | *80%* | ***82.8%*** | — |

Two patterns, and both are the point.

**Recall and rejection move against each other, with no arm in the corner.**
Every configuration that raises recall drops rejection and vice versa. Only
`floor 14` beats production on both axes, and its constant was chosen by
reading this sweep, so it is fitted to the answer sheet and not shippable.

**The oracle sits far above every implementable arm** — 82.8% against the best
real arm's 42.2%. The information needed is present in the extracted terms for
53 of 64 questions; no query-time policy recovers more than half of it, because
choosing the right subset requires knowing which terms appear in the answer.
An agent discovers that by iterating; a single-shot hook cannot.

**A column is missing on purpose.** There is no hybrid or vector arm here
because none exists yet. That column is what the sidecar plan is for, and the
gap between 42.2% and 82.8% is the space it has to compete in.

## Why AgentKV's numbers do not transfer

Their FTS5-only arm scored 68.57% overall; ours scores 10.9% on the same
architecture. The difference is corpus: 120 notes against 9,971. On a small
corpus an AND over six terms still leaves candidates; on ours it leaves only
documents long enough to contain every term. Their hybrid headline (82.86%)
happens to land near our *oracle ceiling* (82.8%), which is a coincidence of
two unrelated quantities and should not be read as a target we have reached
in principle. Compare shapes and mechanisms with them; do not compare levels.
