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

| | baseline | max-score fusion, 2-term | lexical-fusion (in-daemon) | +vector RRF | +chunking | +rerank+floor | +question | +lex3 | hook e2e | +temporal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| distinctive-token | 3/12 | 7/12 | 7/12 | 8/12 | 8/12 | 7/12 | 9/12 | 11/12 | 8/12 | 8/12 |
| episodic-temporal | 3/12 | 6/12 | 6/12 | 7/12 | 7/12 | 7/12 | 9/12 | 9/12 | 8/12 | 8/12 |
| pure-paraphrase | 1/18 | 5/18 | 5/18 | 7/18 | 9/18 | 6/18 | 11/18 | 10/18 | 12/18 | 12/18 |
| research-corpus | 0/12 | 6/12 | 6/12 | 7/12 | 6/12 | 3/12 | 10/12 | 11/12 | 10/12 | 10/12 |
| research-density | 0/10 | 3/10 | 3/10 | 6/10 | 6/10 | 2/10 | 9/10 | 8/10 | 9/10 | 9/10 |
| **R@5** | **10.9%** | **42.2%** | **42.2%** | **54.7%** | **56.2%** | **39.1%** | **75.0%** | **76.6%** | **73.4%** | **73.4%** |
| **negative rejection** | **35%** | **0%** | **0%** | **0%** | **0%** | **40%** | **0%** | **0%** | **0%** | **0%** |

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

## The dense arm's scope is the dominant variable, and it can regress the baseline

Measured on 2026-08-12 while building the `+vector RRF` rung. The design's
original call was that the vector arm covers `memory/` only, because memory
notes are atomic by capture doctrine and `desk/` would need a chunking policy.
Cross-tabulating the gold set against that scope, before writing any code,
refutes it: the 64 answerable questions expect 90 note paths, and they sit
**`desk/` 60 · `memory/` 25 · `external/` 5**. Per stratum, the number of
questions with *any* answer inside `memory/`:

| stratum | n | lexical hits | reachable in `memory/` | ceiling |
|---|---:|---:|---:|---:|
| pure-paraphrase | 18 | 5 | 5 | **8** |
| research-corpus | 12 | 6 | 12 | 12 |
| distinctive-token | 12 | 7 | 1 | 8 |
| episodic-temporal | 12 | 6 | 1 | 6 |
| research-density | 10 | 3 | 0 | 3 |

The ceiling column is the union — every question the lexical arm already hits
plus every question a *perfect* `memory/`-only dense arm could reach. It caps
the whole ladder at **37/64 = 57.8%** against a 70–90% target band, and it puts
the step-2 rule's `paraphrase ≥50%` clause (9 of 18) above a ceiling of 8. That
clause was unreachable by construction, not merely hard.

Building it both ways then produced the sharper finding. A `memory/`-only dense
arm does not merely under-reach; it **regresses the lexical baseline**:

| arm | R@5 |
|---|---:|
| `lexical-fusion` (no dense arm) | 27/64 = 42.2% |
| `+ dense arm, `memory/` only | 26/64 = 40.6% |
| `+ dense arm, `memory/` + `desk/` + `external/` | 35/64 = 54.7% |

The mechanism is reciprocal-rank displacement, and it is a property of RRF
rather than of the model. The dense arm returns its 50 best candidates for
every query, always — cosine similarity has no natural empty result, so there
is always a top 50. For a question whose answer lives outside the embedded
scope, those 50 are all noise, and a noise document at dense rank 1 scores
1/61 = 0.0164 against a *correct* lexical hit at rank 3 scoring 1/63 = 0.0159.
The correct answer is displaced by a document the dense arm was never in a
position to judge.

Two things follow. **A dense arm narrower than the question distribution is
worse than no dense arm**, which is the opposite of the intuition that a
partial vector index is a partial improvement. And the cross-encoder floor at
step 3 is doing more work than the design assumed: it is not only the rejection
gate for negatives, it is the only thing that can stop the dense arm from
promoting confident noise on questions it cannot answer.

## Bake-off: EmbeddingGemma-300M against Qwen3-Embedding-0.6B

Both on the frozen corpus, same questions, same scope, same RRF. The decisive
run is the last one in this table: **identical 2,048-token window, identical
`memory/` + `desk/` + `external/` scope, complete backfills on both sides, on an
otherwise-idle machine**, which isolates model quality from every other variable.

| | EmbeddingGemma-300M-Q8_0 | Qwen3-Embedding-0.6B-Q8_0 |
|---|---:|---:|
| research-corpus (`memory/` scope, own windows) | 9/12 = 75.0% | 8/12 = 66.7% |
| full gold set (`memory/` scope, own windows) | 26/64 = 40.6% | 24/64 = 37.5% |
| **full gold set (wide scope, window parity)** | **35/64 = 54.7%** | **24/64 = 37.5%** |
| distinctive-token · episodic · paraphrase · research-corpus · density | **8/12 · 7/12 · 7/18 · 7/12 · 6/10** | 6/12 · 5/12 · 5/18 · 5/12 · 3/10 |
| dimensions | 768 | 1024 |
| disk | 333 MB | 639 MB |
| backfill throughput | ~101 notes/s | ~24 notes/s |

The first two rows are one and two questions apart — not real differences, and
they are recorded rather than leaned on. The parity row is: **EmbeddingGemma
wins every stratum**, by 11 questions overall. Qwen at parity scores 37.5%,
which is *below* the 42.2% lexical-fusion baseline, meaning its dense arm is a
net loss on this corpus — the same reciprocal-rank displacement described above,
except caused by a weaker model rather than a narrow scope.

The operational case points the same way and is worth recording because it very
nearly produced the opposite verdict. Qwen's only design advantage over
EmbeddingGemma is a longer context window, and **that window is not usable on
this hardware**: batch buffers are sized from the context, and at `-b/-ub 8192`
the model took a `kIOGPUCommandBufferCallbackErrorPageFault` six requests into
an idle machine with 37 GB free. 4096 faulted the same way one request later.
llama.cpp says of that state, "recreate the backend to recover" — the process
never serves again, so the symptom is hybrid retrieval *down* rather than slow.
A window whose compute buffer faults is not a window, so the axis Qwen was
nominated for does not exist here; run at the window that does work, it loses on
quality anyway.

**EmbeddingGemma is pinned**, at 768 dimensions and a 2,048-token window, which
truncates 562 of 9,473 notes (5.9%) to their head. The Metal fault is worth
re-testing on other hardware — it is a property of the model-and-Metal pairing,
not of the weights — but nothing about the parity result depends on it.

Two measurement bugs were found and fixed on the way, both of which had made a
working model look broken:

- **`-b` and `-ub` both bound a sequence, and only `-ub` was being set.** The
  logical batch defaults to 2048 whatever the context is, so an 8k-window model
  was rejecting 3,600-token notes. The symptom reads as a model fault; it is a
  launch-argument fault.
- **A wedged `llama-server` answers `GET /health` with 200 while failing every
  embedding.** A supervisor trusting `/health` reports `embedder ok (warm)`
  indefinitely while every search silently falls back to lexical. Liveness now
  comes from the work — three consecutive failed embeddings condemn the child —
  and the scorecard refuses to publish a hybrid column when any query fell back,
  rather than reporting a lexical run under a hybrid header.


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

---

# Task 3: cross-encoder rerank + floor — refuted

Run 2026-08-13 on the same frozen corpus, rule re-anchored before any code
(see the plan's task 3, *"Re-anchored 2026-08-13, before any task-3 code"*):
**negative rejection ≥70% (≥14/20 return nothing) while overall R@5 ≥ the
`+chunking` column (36/64 = 56.2%).**

## Recovery ceiling, measured before the floor was chosen

Scored the chunked hybrid arm at `k=20` and counted, for each of the 28
current `+chunking` misses, whether an expected note appears anywhere in the
fused top-20. **14 of 28 reachable** — that is the rerank's entire recovery
ceiling; the other 14 are not candidates fusion ever retrieved, and no
reranker can surface what fusion never returned. Even a perfect reranker
recovering all 14 caps the ladder at 50/64 = 78.1%, before any floor removes
a single false positive.

**Watchlist, both checked reachable before scoring.** `ep05` (episodic, the
`ep03`-masked displacement casualty) sits at fused rank 6; `rc08`
(research-corpus, the rank-5-to-outside-top-5 casualty) sits at fused rank 7.
Both inside the top-20, so both are fair tests of the mechanism.

## Bake-off: jina wins both axes and is far cheaper; neither clears the rule

`bge-reranker-v2-m3-Q8_0` and `jina-reranker-v2-base-multilingual-Q8_0`, both
already installed with verified hashes (see progress-hybrid-retrieval.md's
task-3 prerequisites). Full 84-question run, both models, same fused
candidates, same floor-derivation discipline:

| | bge-reranker-v2-m3 | jina-reranker-v2-base-multilingual |
|---|---:|---:|
| distinctive-token | 7/12 | 7/12 |
| episodic-temporal | 7/12 | 7/12 |
| pure-paraphrase | 6/18 | 6/18 |
| research-corpus | 3/12 | 3/12 |
| research-density | 1/10 | 2/10 |
| **R@5** | **32.8%** (21/64) | **39.1%** (25/64) |
| **negative rejection** | **10%** (2/20) | **40%** (8/20) |
| CE ms/pair, p50 / p90 | 125.5 / 176.0 | 17.7 / 30.0 |
| mean pairs/query | 58.8 | 98.8 |
| whole-search latency, p50 / p90 | 7.5s / 15.0s | 2.1s / 4.0s |

jina wins on R@5, wins on rejection, and is roughly 7x cheaper per pair
despite averaging *more* pairs per query — its own context window is 1024
tokens against bge's 2048 (measured, see `daemon/internal/rerank/model.go`'s
catalog comment; `llama-server`'s `/props` reports `n_ctx: 1024` for jina
regardless of the `-c 2048` launch flag, the identical
smaller-than-launched-window phenomenon task 2 found on EmbeddingGemma), so
its chunks are smaller and there are more of them. **jina is the model that
would ship if either did**; neither clears the rule, so neither ships. The
`+rerank+floor` column above is jina's run.

## Three defect hypotheses, ruled out before accepting the numbers

A cross-encoder measured at +1.93 relevant / −11.04 irrelevant on its own
probe does not plausibly halve recall on every stratum by wiring alone —
that is the shape of a bug, not a model verdict, and task 2's own precedent
(three defects, "two of which made a working model look broken") is reason
enough to check before writing anything down.

1. **Pair count.** `rerank_pairs` ran 20-136 (mean 58.8-98.8), which reads
   large against "the fused top-20." Verified via the daemon's own `matched`
   field, which is separate from `rerank_pairs`: `matched` was 20 on every
   query that reached the reranker — exactly 20 fused *candidates*, always.
   `rerank_pairs` is those 20 candidates chunk-expanded (the pre-registered
   policy below), and running that far above 20 is `research-corpus` and
   `research-density`'s own long documents, not a wrong candidate set.
2. **Floor scale.** Verified two points directly against the daemon's raw
   JSON output: `rc08`'s candidate scored bge raw −4.005 → sigmoid 0.0179,
   correctly below the 0.10 floor; a returned negative (`ng15`, "rollback
   procedure fts5 index schema changes") scored raw −0.708 → sigmoid 0.330,
   correctly above it. The arithmetic is right on both sides of the floor;
   the floor is simply too low to catch this negative, which is the finding
   below, not a bug.
3. **Chunk vs. head.** `TestRerankFusedScoresCandidateByBestChunk`
   (`daemon/cmd/agentmd/rerank_test.go`) pins a long candidate whose answer
   text sits past the first chunk and asserts it still survives — passing,
   plus the pair counts above are direct evidence multi-chunk scoring is
   really happening, not a single head chunk repeated.

All three ruled out. The numbers are a measurement.

## The chunk-scoring policy, pre-registered

`Result` carries no chunk index — verified (`ChunkIdx` exists on `VectorRow`,
not on `Result`). Policy chosen before scoring: **every fused candidate is
re-chunked from the index's own stored text (`Index.DocText`, new) using the
same `ChunkText` the embedder uses, CE-scored chunk by chunk, and kept at its
best-scoring chunk** — the identical "score a note by its best chunk" rule
`VectorSearch` already applies to the dense arm, now applied to the
cross-encoder. This is uniform regardless of which arm surfaced a candidate:
a lexical-only hit is chunked and scored exactly like a dense hit, because
both are just text fetched fresh from the index at rerank time. Capped at 20
chunks per candidate (`maxChunksPerCandidate`) so one out-of-scope file the
lexical arm alone surfaces (never bounded by the embedder's own chunking)
cannot dominate a single query's pair budget; never observed to bind on an
in-scope note.

**A finding the implementation forced, not anticipated.** The cross-encoder
scores query and document as one joined sequence — unlike the embedder,
which scores them in two separate passes each fitting the window whole — so
a chunk sized to the embedder's own budget left no room for the query once
the two shared a sequence: the first real query tried overflowed the
physical batch on 5 of 43 real candidate chunks. Fixed with a 128-token
reserve subtracted before chunking for rerank specifically
(`rerankQueryReserve`) plus a per-document shorten-and-retry fallback
(`scoreDocuments`, mirroring `embedBatch`'s identical fallback for the
embedder's own budget-estimate error) as the real backstop — the reserve
only decides how often that backstop has to run, not whether the system is
correct.

## Why the floor derivation looked clean off-gold and wasn't enough

Twelve answerable and twelve negative (query, passage) pairs, built from the
frozen corpus, excluding every gold-set expected-answer path and every gold
question's own phrasing (never swept against gold). bge separated sharply —
negatives at or below sigmoid 0.0237, answerables at or above 0.9374 except
one genuine hard case at 0.2520 — so the floor was derived from bge's own
measured gap (0.10) rather than the literature's ~0.35 prior, which would
have discarded that hard case. jina's own probe distribution was more
compressed (negatives mostly under 0.05, one hard case at 0.2251; answerables
0.4677-0.8686) and the 0.35 prior survived its own sanity check there
unchanged.

**The probe's negatives were the easy kind.** Every off-gold negative was a
genuine topic mismatch (a homelab question against a blog-ads plan; "what is
the capital of France" against a workflow note). The gold set's 20 negatives
are the hard kind: plausible questions about topics the corpus *does*
discuss at length but does not specifically answer — `ng15` above, or `ng09`
("decide rate limiting daemon http surface" against a real MCP-memory-server
research note). A general-purpose cross-encoder trained on natural-language
query-passage relevance reads "topically adjacent, densely related internal
engineering corpus" as relevant enough to clear a floor calibrated against
obviously-unrelated pairs. This is measurably the same failure the design
document already named for BM25 — *"a plausible question about a
well-discussed topic outscores a question answered by one small note"* —
recurring one layer up, on a signal the design's own premise (*"a
cross-encoder score is a calibrated relevance judgment... the signal a BM25
floor pretends to be and measurably is not"*) expected to be immune to it.

## Recovered vs. lost, against `+chunking`'s 36 hits

| | bge | jina |
|---|---:|---:|
| of 14 reachable misses, recovered | 3 (`ep05`, `pp06`, `pp10`) | 2 (`ep05`, `pp05`) |
| of 36 `+chunking` hits, lost | 18 | 13 |
| net R@5 | 36 − 18 + 3 = 21/64 | 36 − 13 + 2 = 25/64 |

**Watchlist verdict.** Both models recover `ep05` — real evidence the
mechanism does what a reranker is for when it works: a topically-related
wrong chunk outranking the right note, reversed. Neither recovers `rc08`;
both floor it to empty (bge sigmoid 0.0179, jina's candidate scored
similarly below its 0.35 floor). `rc08` was reachable, so this is a genuine
recovery failure, not an unreachable target reported as one — the mechanism
tried and the cross-encoder was simply not confident in the correct pairing
for a `_daemon_query_terms`-reduced query against a short, abstractly-worded
note.

**The dominant failure mode is ranking, not the floor.** Of jina's 39
misses, 21 were the cross-encoder outranking a wrong candidate above the
right one (survived the floor, lost the ranking) against 18 floored to
empty; bge split 31 outranked against 12 floored. Even a floor of zero would
not have rescued most of this loss — the cross-encoder's own relevance
ordering disagrees with the gold labels more often than fusion's did, which
a floor cannot fix by being better placed.

## Verdict

**Refuted, on both clauses, for both candidates.** jina — the model that
would ship — reaches 39.1% R@5 against a ≥56.2% requirement and 40%
rejection against a ≥70% requirement. Per the plan's own ground rule, this
is recorded and reverted rather than shipped on partial credit; no rerank
mode reaches production. Per the design's own amendment log entry (dated
2026-08-12, *"the cross-encoder floor is load-bearing earlier than
stated"*), this **promotes the deliberate path's LLM rejection gate from
optional to load-bearing** — the fast path's own rejection story (both the
BM25 floor and now the CE floor) does not hold on this corpus, so a
consumer that needs correct rejection has to pay for judgment rather than a
score threshold.

**What this does not refute.** The dense arm (`+vector RRF`, `+chunking`)
stands; nothing here touches fusion or the vector arm's own 56.2%. `ep05`'s
recovery is real evidence the rerank mechanism is not broken in principle —
it is under-powered against this corpus's topical density and this query
representation, not inert.

**For task 4.** The hook-latency question is now separately urgent: jina's
own CE cost alone runs p50 17.7ms/pair x ~99 pairs/query mean ≈ 1.7s, and
bge's p50 125ms/pair x ~59 pairs/query mean ≈ 7.4s — both far past the
300ms warm budget task 5 has to clear, before counting fusion, embedding, or
CLI overhead. Since rerank does not ship, this is not task 5's problem to
solve today, but it is the reason a floor-only, no-rerank hook path (fusion
+ vector + a BM25-style floor, or the LLM gate directly) is worth
considering there rather than assuming rerank was simply pending a latency
optimization.

---

# Task 3 post-mortem: the refutation stands, its cause was half-recorded — and the fix is a recall rung

Run 2026-08-14, operator-directed, after the refutation landed. The operator's
challenge: CE rerank works elsewhere, so suspect the test before the technique.
The suspicion was right in part, and following it found the next rung.

## The test fed both models keyword soup

`cmdSearch` has one query string for all three arms, and the scorecard passes
`recall._daemon_query_terms(question)` — the hook's extracted AND-terms. Every
CE pair was therefore `("circumstances automatically invoke worktrees", chunk)`.
Both rerankers are trained on natural-language query/passage pairs; the task-3
verification checked the candidate set, the floor's scale, and chunk coverage,
but never the query string itself. Counterfactual, same note, same model (bge):

| question | terms query | natural question |
|---|---:|---:|
| rd08 correct note | sig 0.000 (floored) | sig **0.959** |
| rc09 correct note | sig 0.003 (floored) | sig 0.197 |

The floored-to-empty class (6 bge / 7 jina true positives returning nothing) is
this artifact. The 2026-08-13 amendment named "a query representation richer
than the reduced keywords" as a live re-audit hypothesis; it fired.

## What survives the artifact — three structural findings

**No floor separates positives from negatives in either format.** bge under
natural questions: correct notes score 0.003–0.959; hard negatives' best
candidates score 0.267–0.906 — fully interleaved. A negative about worktrees
finds worktree docs at 0.906 while ep03's true answer scores 0.008. On a
single-owner corpus whose negatives are by design about topics the corpus is
saturated with, topical relevance and answerhood come apart, and a
similarity-trained CE measures the former. The rejection clause is dead at any
threshold; only an answerhood judgment (the deliberate-path LLM gate) can
deliver it.

**The demotions are a length subsidy.** Every bge demotion winner was a
22–39KB roadmap or plan archive displacing a 1.4–11KB note. Max-over-chunks
gives a 20-chunk document twenty noisy draws at its maximum; an atomic note
gets one. Persists under natural questions on the probes. Max-chunk CE scoring
is structurally anti-correlated with atomic capture.

**The jina conversion is compromised.** Canonical textbook pair: bge sig
0.9996, jina sig 0.843, with plausibly-relevant chunks pinned at ~0.34 —
against its own 0.35 floor. Its arm understates the model; do not re-run jina
from the gpustack GGUF. (Immaterial to the refutation: the healthy bge
interleaves too.)

Loss decomposition against +chunking's 36 hits: bge kept 18 / demoted 12 /
floored 6; jina kept 23 / 6 / 7. With 50 of 64 answers in the fused top-20, a
zero-signal reranker expects ~12.5/64 (19.5%); bge landed 21/64 — nearer noise
than the 78.1% ceiling.

## The reach diagnosis: the artifact was also throttling the dense arm

The same terms string is what the dense arm embedded
(`task: search result | query: <terms>`). The reduction exists for FTS5's AND;
applying it to the embedder was incidental. Probing the 28 +chunking misses
with the natural question embedded instead (raw cosine over the task-2.5 chunk
vectors, best-chunk-per-note, penalties not applied — diagnosis, not the
production path):

| measure | terms | natural question |
|---|---:|---:|
| expected note in dense top-50 (RRF depth) | 15/28 | **22/28** |
| expected note in dense **top-5 directly** | — | **16/28** |

Individual moves: rd10 dense rank 3,019 → **1**; rd03 360 → 1; rd04 376 → 1;
dt12 122 → 1; rc03 109 → 2. Separately, some 3-term subset of the extracted
terms reaches lexical top-5 for 14/28 (2-term fusion is implemented; triples
are not). Union of the two mechanisms: 22 of 28 misses become reachable. The
78.1% "ceiling" was a property of the terms-shaped candidate pool, not of the
corpus.

Residue neither mechanism reaches: pp07, pp15, pp16, rc01, rd01 (+ep09 at
dense 7) — the alias/filing arc's territory, correctly out of scope.

## What this licenses

Step 3.5 (question passthrough): the daemon accepts the natural question
alongside the terms; the dense arm embeds the question; the lexical arms keep
the terms. No new model, no constant to derive, and the production hook already
holds the raw prompt. Pre-registered rule in the design ladder. CE rerank
stays parked: even fixed, it cannot clear the 300ms hook budget at ~18–125
ms/pair, and its rejection story is dead above.

---

# Task 3.5: question passthrough — column `+question`, rule met well past the floor

Run 2026-08-14, rule pre-registered in the plan before any code: **overall R@5
≥ 62.5% (40/64), pure-paraphrase holds ≥50% (≥9/18), no stratum regresses by
more than one question, and the per-question gain/loss diff is published with
the column.**

## The mechanism

`agentmd search -question "<the natural question>"` (and the matching
argument on the MCP `mode` seam, still unpublished) hands the daemon the
question alongside the extracted terms. When present, the dense arm embeds
`WrapQuery(question)` instead of `WrapQuery(terms)`, truncated defensively to
the embedder's window first — `index.TruncateQuery`, which reuses `ChunkText`'s
own budget arithmetic (`windowBudget`, `daemon/internal/index/vector.go`)
rather than a second notion of the window. The lexical arms never see the
question: `index.Query.Text` is set from the terms in both the CLI (`main.go`)
and the MCP handler (`mcpsrv/server.go`), and the question is consumed only on
the branch that decides what to embed — `queryEmbedText`
(`daemon/cmd/agentmd/embedder.go`).

## Absence proven byte-identical, not merely claimed

Before scoring the new column, the branch build was run against the same
index with no `-question` flag and diffed against the landed `+chunking`
column's own per-question JSON — same 84 rows, same hits, same top-k path
lists, zero differences. This is the same discipline task 1 used to prove the
hook path unchanged after the fusion refactor (re-scoring `and` at exactly
10.9%), applied here at the row level rather than only the aggregate: 36/64
both times, and the 28 miss ids are the identical 28 miss ids.

## The result

| stratum | `+chunking` | `+question` | Δ |
|---|---:|---:|---:|
| distinctive-token | 8/12 | 9/12 | +1 |
| episodic-temporal | 7/12 | 9/12 | +2 |
| pure-paraphrase | 9/18 | 11/18 | +2 |
| research-corpus | 6/12 | 10/12 | +4 |
| research-density | 6/10 | 9/10 | +3 |
| **R@5** | **56.2%** (36/64) | **75.0%** (48/64) | **+12** |
| negative rejection | 0% | 0% | 0 |

Every clause clears with room to spare: overall R@5 is 12.5 points above the
62.5% floor (net +12 against the +4 floor); pure-paraphrase is 11.1 points
above its 50% floor; and no stratum regressed at all, so the "no more than one
question" tolerance was never tested. Negative rejection was already 0% at
`+chunking` and stays 0% — unchanged, not a new failure, and not part of this
rung's rule (that clause belongs to the deliberate-path LLM gate now, per the
2026-08-13 amendment).

## Per-question diff — published by id, the `ep05` lesson applied

**12 gained, 0 lost.** A flat or improving stratum total cannot be trusted to
mean "no swap happened" (task 2.5's `ep05`/`ep03` lesson) — here every stratum
improved, so the diff was checked question by question rather than inferred
from the totals holding.

| id | stratum | gained via |
|---|---|---|
| dt12 | distinctive-token | diagnosed (dense top-5 under question, rank 1) |
| ep04 | episodic-temporal | diagnosed (dense top-5, rank 1) |
| ep05 | episodic-temporal | **not diagnosed** — dense rank 19 alone (inside RRF depth, outside top-5); reached the fused top-5 through the lexical arm's own contribution |
| pp06 | pure-paraphrase | diagnosed (dense top-5, rank 1) |
| pp13 | pure-paraphrase | diagnosed (dense top-5, rank 1) |
| rc04 | research-corpus | diagnosed (dense top-5, rank 1) |
| rc06 | research-corpus | diagnosed (dense top-5, rank 2) |
| rc08 | research-corpus | diagnosed (dense top-5, rank 1) |
| rc12 | research-corpus | diagnosed (dense top-5, rank 1) |
| rd03 | research-density | diagnosed (dense top-5, rank 1) |
| rd04 | research-density | diagnosed (dense top-5, rank 1) |
| rd10 | research-density | diagnosed (dense top-5, rank 1) |

Two are worth naming individually. **`rc08`** is the research-corpus question
("What is the actual complaint about retrieval-augmented generation for
memory?") that task 3's cross-encoder investigation floored to empty under
both bake-off candidates — a genuine CE recovery failure, honestly reported at
the time. It is recovered here, by a mechanism with no cross-encoder in it at
all. **`ep05`** ("Why did I choose to split agentm into two repos and when?")
is the question task 3's rerank recovered and this rung was not expected to,
by the diagnosis's own accounting — its dense rank under the question (19) is
inside RRF's depth-50 window but not the top-5 the diagnosis counted directly,
so its recovery here is fusion synergy (the lexical arm's contribution lifting
a moderately-ranked dense candidate the rest of the way), not the diagnosed
mechanism. Recorded because the diagnosis is a probe, not the production path,
and this is exactly the kind of gain a probe restricted to one arm cannot see.

## Convergence against the diagnosis: 11 of 16

The post-mortem's reach diagnosis (raw cosine, best-chunk-per-note, no
fusion, no penalty — a probe) found the expected note in the dense top-5
directly for 16 of the 28 `+chunking` misses when the question was embedded
instead of the terms. Re-run against the same index and embedder to recover
the exact id set (not saved from the original session): `dt07, dt12, ep04,
pp05, pp06, pp09, pp13, pp17, rc03, rc04, rc06, rc08, rc12, rd03, rd04, rd10`
— and the re-probe reproduced the diagnosis's own headline numbers exactly
(16/28 top-5, 22/28 top-50), confirming it before using it as a checklist.

**11 of those 16 converted** to actual hits in the landed column (all except
`ep05`, which is not on this list — see above). **5 did not**: `dt07`,
`pp05`, `pp09`, `pp17`, `rc03`. Each was dense-top-5 by raw cosine but did not
survive reciprocal-rank fusion into the daemon's actual top-5 — the lexical
arm's own ranking, or another candidate's combined rank, won the fusion for
that query. This is fusion friction, named and expected: the rule's own +4-net
floor was set deliberately below the diagnosis's 16 direct candidates for
exactly this reason, and 11/16 converting plus one synergy gain (`ep05`) is
well inside that tolerance rather than a shortfall requiring investigation.
The design's own re-audit trigger ("falls well short of the 16 direct
candidates") did not fire, so RRF depth and per-arm contribution were not
touched, per the plan's explicit instruction not to sweep either against the
gold set.

The remaining 16 misses split into the 5 fusion-friction cases above and 11
the diagnosis never claimed: `dt02`, `dt10`, `pp07`, `pp10`, `pp15`, `pp16`,
`ep07`, `ep08`, `ep09`, `rc01`, `rd01` — outside dense top-50 entirely for
most of them, and previously named as the alias/filing arc's territory for
`pp07`, `pp15`, `pp16`, `rc01`, `rd01` specifically. Untouched by this rung by
design.

## Ops

Reused the task-2.5 chunked index verbatim (9,971 docs, 9,473 embedded notes,
11,761 chunk vectors, schema 4) — verified against the corpus's docmeta count
and the gold set's pinned `$corpus` block before scoring, no rebuild needed,
since this rung changes only the query side. Embedder: EmbeddingGemma-300M-Q8_0
at `-np 1 -c 2048 -b 2048 -ub 2048`, confirmed no stray `llama-server`
processes before starting it and none accumulated afterward. `degraded: []` on
every run. Scored via `retrieval_scorecard.py --mode hybrid --question
--embedder-url ... --embed-model embeddinggemma-300M-Q8_0 --vault <corpus>
--index <scratch index>`, re-run twice and confirmed bit-identical (same
hits, same ranks) before landing the column. Per-question JSON at
`<vault>/Agent/_meta/health/goldv2/question-20260814.json`, never in the repo.

---

# Task 4: three-term subset fusion — refuted

Run 2026-08-14, rule pre-registered in the plan before any code: **overall R@5
≥ 51/64 (79.7%) — net +3 over `+question`'s 48; no stratum regresses by more
than one question; and the per-question gain/loss diff is published by id.**

## The mechanism

`agentmd search -lex3` (and the matching unpublished argument on the MCP
`mode` seam) widens `fusion`'s — and, through it, `hybrid`'s lexical arm's —
subset set from every 2-term combination of the query's extracted terms to
every 2- **and** 3-term combination, still max-score across all of them. The
implementation is one new nested loop inside `searchFusion`'s existing
double loop (`daemon/internal/index/search.go`): for terms `i < j`, a third
index `l := j + 1; l < len(terms)` issues the triple `(i, j, l)` only when
`lex3` is true, so a query under three terms has no triple and the bound is
simply never satisfied — no special-cased fallback was needed. `searchHybrid`
threads `Query.Lex3` to its own internal `searchFusion` call, so the flag
reaches the hybrid arm the same way `-question` does.

## Absence proven byte-identical, both existing columns

Before scoring the new column, the branch build was run against the same
index with `-lex3` **absent** on both of the columns this rung could have
disturbed, and diffed row-by-row against each one's own saved per-question
JSON:

| column | mode | aggregate | rows compared | differences |
|---|---|---:|---:|---:|
| `lexical-fusion` (task 1) | `-mode fusion` | 27/64 = 42.2% | 84 | **0** |
| `+question` (task 3.5) | `-mode hybrid -question` | 48/64 = 75.0% | 84 | **0** |

Same 84 rows, same hits, same top-k path lists in both cases — the same
discipline task 1 and task 3.5 used, applied here before the new column was
allowed to touch either. This is also structurally guaranteed by the diff
itself: the inner triple loop is dead code when `lex3` is false, not a
differently-parameterized version of the old code path.

## The result

| stratum | `+question` | `+lex3` | Δ |
|---|---:|---:|---:|
| distinctive-token | 9/12 | 11/12 | +2 |
| episodic-temporal | 9/12 | 9/12 | 0 |
| pure-paraphrase | 11/18 | 10/18 | **−1** |
| research-corpus | 10/12 | 11/12 | +1 |
| research-density | 9/10 | 8/10 | **−1** |
| **R@5** | **75.0%** (48/64) | **76.6%** (49/64) | **+1** |
| negative rejection | 0% | 0% | 0 |

**Net +1 against a required net +3 (51/64).** The regression clause itself
held — pure-paraphrase and research-density each lost exactly one question,
inside the ≤1-question tolerance, and neither is the clause that failed.
What failed is the overall floor: three gains bought by two losses is not
enough margin. Re-run twice more after the number first landed (three runs
total, including the one that produced this column) and confirmed
bit-identical row-for-row every time — this is a measurement, not a fluke of
one process's timing.

## Per-question diff (mandatory clause): 3 gained, 2 lost

| id | stratum | direction |
|---|---|---|
| dt07 | distinctive-token | gained |
| dt10 | distinctive-token | gained |
| rc03 | research-corpus | gained |
| pp02 | pure-paraphrase | **lost** |
| rd04 | research-density | **lost** |

`rc08` and `ep05` — the recurring watch-list items from task 2.5's and task
3's displacement casualties, both recovered by task 3.5 — are untouched
here: neither appears in either column above, so both stay converted.

## Re-deriving the diagnosis's 7: exact reproduction, and why only 3 of 7 converted

The task text named 7 candidates — `dt02`, `dt10`, `pp10`, `rc03` at rank 1,
`dt07` at rank 3, `ep07` at rank 3, `ep08` at rank 5 — each reached by *some*
3-term subset in its own isolated `-mode and` search. Re-derived against the
branch build before trusting those ids, using the diagnosis's own method
(every 3-term combination of the query's extracted terms, each issued as its
own isolated AND search, best rank kept): **the same 7, at the same ranks,
exactly.** `dt02` → rank 1 via `rag tutorials folder`; `dt10` → rank 1 via
`coord through wave`; `pp10` → rank 1 via `use vault place`; `rc03` → rank 1
via `stops system handing`; `dt07` → rank 3; `ep07` → rank 3; `ep08` → rank
5. The diagnosis was accurate about isolated reachability.

**Only 3 of the 7 converted: `dt07`, `dt10`, `rc03`.** The isolated-triple
probe is not the production mechanism, and the gap between the two explains
the shortfall. `searchFusion` issues *every* 2- and 3-term subset of a query
simultaneously and keeps, per document, the best score *any* subset gave it
— for a 6-term query that is 15 pairs plus 20 triples, 35 sub-queries
competing in one max-score ranking, not one triple scored in isolation.
Re-running the 7 through the real competitive search (`-mode fusion -lex3`,
full 35-subset competition, `k=50` to see past the top-5 cut) instead of each
one's own private triple:

| id | isolated rank (own triple alone) | competitive rank (all subsets, `k=50`) | converted in `+lex3`? |
|---|---:|---:|---|
| dt02 | 1 | 31 | no |
| dt07 | 3 | 25 | **yes** |
| dt10 | 1 | 2 | **yes** |
| ep07 | 3 | not in top 50 | no |
| ep08 | 5 | 6 | no |
| pp10 | 1 | 6 | no |
| rc03 | 1 | 35 | **yes** |

`dt02` is the clearest illustration. Its winning triple, `rag tutorials
folder`, ranks the expected note 1st in an isolated search at raw score
11.42. Under the full 6-term query's 35-subset competition, *other* triples
(ones not involving any of `rag`/`tutorials`/`folder`) surface unrelated
`Agent/_meta/skill-discovery-cache/shubhamsaboo-awesome-llm-apps/*.md`
snapshots scoring 21.0–21.1 — nearly double — because those subsets match a
common phrase repeated across several dated cache snapshots of the same
upstream page. Max-score fusion is exactly "best single piece of evidence
wins," so those higher-scoring, unrelated candidates win the ranking outright
and the correct answer never reaches the fused top-50, let alone top-5.

Conversion also does not track lexical rank monotonically once RRF enters:
`rc03` converts from a **lexical** rank of 35 (a small RRF contribution,
1/(60+35)), while `ep08` and `pp10` — lexically much stronger at rank 6 —
do not convert. RRF rewards a candidate both arms agree on over one arm's
strong opinion (`fuseRRF`'s own doc comment), so a mediocre-but-present
lexical rank combined with a strong dense-arm rank can outscore a
lexically-strong candidate the dense arm ranks poorly. This is the same
"agreement over a single first place" property `TestRRFPrefersAgreementOverASingleFirstPlace`
pins in `hybrid_test.go` — task 4 is the first time it has been observed
working *against* a diagnosed candidate rather than for one.

## The two regressions: reciprocal-rank displacement, named in advance

Both losses were borderline hits under `+question`, and both were displaced
by a new candidate the widened lexical arm handed to RRF — exactly the
mechanism the plan's own "regression clause is the real test" section warned
about before any code existed.

**`pp02`** ("Where did we store the worktree rules...?", expected
`worktrees-never-auto.md`) sat at rank 3 under `+question`. Under `+lex3`, two
new documents enter the fused top-5 —
`crickets/_harness/archive/worktree-native-flow/PLAN.archive.20260706-worktree-native-flow.md`
and `agentm/_harness/designs/architecture-governance/worktree-native-verdict-draft.md`
— and the expected note is pushed out of the top 5 entirely.

**`rd04`** ("...which model only wants the instruction sentence glued onto the
query...?", expected `bge-small-optional-query-instruction.md`) sat at rank 5
under `+question` — already the last slot. Under `+lex3`, a single new
candidate, `_harness/designs/roadmap-research-2026-06/R01-retrieval-and-knowledge-graph.md`,
enters at rank 2 and pushes everything down one, dropping the expected note
past the cut.

Both were already at the edge of the window (rank 3, rank 5) before the
widening, which is precisely why one new competitive candidate was enough:
the same displacement risk task 2's `memory/`-only regression and task 2.5's
`rc08`/`ep05` swaps demonstrated, now observed a third time in the plan this
rung's own text predicted it for.

## Verdict

**Refuted on the overall floor; the regression clause held.** 49/64 = 76.6%
against a required ≥51/64 (79.7%); net +1 against a required net +3. No
stratum lost more than one question, so the tripwire clause the task's own
risk section was most worried about did not fire — the shortfall is a floor
miss, not a regression-clause miss, and the two are different failures with
different implications. Per the plan's ground rule, this closes as a finding,
recorded rather than shipped: `-lex3` is not requested by the hook and never
will be by default, matching `-mode fusion`, `-mode rerank`, and `-question`
before it. The code is kept rather than reverted — the same reasoning task
3's refuted `-mode rerank` used: it is quarantined behind an explicit flag
(CLI) and an unpublished argument (MCP) neither the hook nor the published
tool schema exposes, so its presence changes no production behavior, and it
is real, tested infrastructure that produced the measurement rather than a
throwaway driver. No parameter was swept against the gold set to try to
rescue the floor — the diagnosis's own "do not sweep to rescue" instruction
was honored by stopping here rather than tuning `rrfDepth`, the candidate cap,
or anything else against the answer sheet.

**What this licenses, and what it does not.** The mechanistic finding —
isolated single-subset reachability is a weak proxy for a max-score fusion's
actual competitive outcome, because every other subset's candidates are
competing in the same ranking — generalizes beyond this task. Any future
diagnosis that probes "does *some* configuration reach the answer" before a
change that issues *many* configurations simultaneously should expect the
same gap, roughly in the direction task 4 measured it (isolated-reachable ⊅
converts). It does not touch fusion or the dense arm's own standing 75.0%;
`+question` remains the last shipped rung.

## Ops

**Lexical-only latency, the widened arm's own cost.** `-mode fusion` (lex3
off, re-measured on this machine alongside the rest of this task rather than
carried over from task 1's number): p50 18.1ms, p90 23.7ms — consistent with
task 1's original p90 24.4ms, confirming comparable machine load rather than
a faster or slower run skewing the comparison. `-mode fusion -lex3` (the
widened arm): **p50 26.4ms, p90 39.8ms, max 69.5ms** — roughly 1.7x the
baseline's p90 against 2.3x the sub-query count (35 vs 15 for a 6-term
query), both far inside the hook's 300ms budget. Task 5 can treat the
widening as free on latency grounds; it simply does not clear its own recall
bar.

Corpus and index unchanged from task 3.5 (9,971 docs, 9,473 embedded notes,
11,761 chunk vectors, schema 4) — verified against the corpus's own docmeta
count before scoring, no rebuild needed, since this rung changes only the
query side. Embedder: EmbeddingGemma-300M-Q8_0 at `-np 1 -c 2048 -b 2048 -ub
2048`; no stray `llama-server` processes confirmed before starting it and
none accumulated afterward; `degraded: []` on every run. Scored via
`retrieval_scorecard.py --mode hybrid --question --lex3 --embedder-url ...
--embed-model embeddinggemma-300M-Q8_0 --vault <corpus> --index <scratch
index>`, run three times total and confirmed bit-identical (same hits, same
ranks) before landing the column. Per-question JSON at
`<vault>/Agent/_meta/health/goldv2/lex3-20260814.json`, never in the repo.

## Task 5: hook cutover — column `hook e2e`

**Rule met on both clauses.** p50/p90 213.8ms/222.4ms end-to-end through the
*installed* hook (n=84, real `bash memory-recall-prompt-submit.sh` invocations,
bash+python startup included, against the live vault) — well inside the 300ms
budget. Each of the five strata within one question of `+question`:
distinctive-token 8/12 (Δ1), episodic-temporal 8/12 (Δ1), pure-paraphrase
12/18 (Δ+1), research-corpus 10/12 (Δ0), research-density 9/10 (Δ0). Overall
R@5 47/64 = 73.4% against `+question`'s 48/64 = 75.0%.

**Scored two different ways for two different questions**, per the rule's own
"both halves matter": the strata clause ran through `retrieval_scorecard.py
--via-hook`, a new mode that calls `recall._daemon_search` directly — the
hook's own code (terms extraction, `-mode hybrid -question` wiring, the 250ms
subprocess budget) rather than a bare `agentmd search` invocation — against
the frozen corpus, with a generous per-query timeout so a query slow for
reasons unrelated to correctness is never miscounted as a miss (see Ops
below). The latency clause ran separately, through the real installed shell
wrapper, under its real unmodified 250ms daemon budget — the only way to
capture the process-spawn cost `--via-hook`'s own already-warm interpreter
does not pay.

**Per-question diff: 2 gained (`ep08`, `pp05`), 3 "lost" — and the 3 are not
real losses.** `dt01`'s expected answer is `desk/scratch/_index.md`; `ep10`
and `ep12`'s are under `_archive/`. `_daemon_search`'s own hygiene filter
(`_daemon_admissible`) excludes both unconditionally by default — the same
policy that keeps dream-staging proposals and retired notes out of ordinary
recall — while the raw CLI `agentmd search` the `+question` column was scored
through applies no such filter at all. The hook is not worse at the
mechanism; it is correctly declining to surface content ordinary recall was
never supposed to show. Reading the diff as "mechanism regressed 3, gained 2"
would be wrong; the honest reading is "mechanism gained 2, and 3 of the CLI's
own hits were never reachable through the real hook to begin with."

**Honest-empty rate on the 20 negatives: 0/20 (0.0%) genuine — the daemon
actually searched and found something every time, exactly as `-mode
fusion`/`hybrid` have scored since task 1.** This is not a regression task 5
introduced; `and`-mode's 35% rejection was already gone once the hook stopped
requiring every term in one note. Measured a second way, through the real
installed hook under its real 250ms budget: 1/20 (`ng14`) came back empty, but
that one is a timeout-and-honest-fallback artifact (see below), reported by
the hook as "NOTHING WAS SEARCHED" rather than folded into a false rejection —
the GH #92 discipline holding under a new failure shape it was not written
for. No floor, no manufactured rejection, exactly as the injection policy
specifies: an empty passes through unchanged, and a candidate list passes
through labelled, never filtered by a threshold this design explicitly
declined to invent.

**A finding, not a defect: hybrid's internal fusion depth (`rrfDepth = 50`,
task 2) has a real latency cliff for common-enough term combinations,
independent of the dense arm.** Two gold questions (`dt11`, `rd03`) and one
negative (`ng14`) took 6+ seconds under a generous budget — later confirmed,
isolated, and root-caused: `-mode fusion -k 50` alone (no dense arm at all)
on `dt11`'s/`rd03`'s terms costs the same 6+ seconds `-mode fusion -k 10`
answers in 35ms, for the identical terms. `searchHybrid` always asks its
internal `searchFusion` for `rrfDepth` (50) results regardless of the
caller's own `-k`, so *every* `-mode hybrid` query pays whatever `k=50`
costs, not what the caller asked for — a property of task 2's own mechanism
that predates this task and that no prior column's measurement happened to
trigger. The empirically measured rate is 3/84 (3.6%) of this gold set's
realistic questions. Production is unaffected in the sense that matters: the
existing 250ms subprocess budget (recall.py, `DAEMON_BUDGET_MS`, unchanged by
this task) already bounds the damage to one timeout-and-fallback per
affected prompt, reported honestly rather than silently. It is not free —
those three prompts pay the fallback's own cost (~400ms observed, since the
in-process engine's own stream-admission discipline discards a
budget-starved walk rather than reporting an arbitrary partial one) — but
p50/p90 across the full 84-question sample are unaffected, since three
outliers this size do not reach into the 90th percentile of 84. Flagged for
a follow-up investigation into `searchFusion`'s own `k`-scaling; out of this
task's scope to fix.

**Ops.** Corpus and index unchanged from task 4 (9,971 docs, 11,761 chunk
vectors) — verified against the corpus's own docmeta count and the gold
set's pinned `$corpus` block before scoring, no rebuild needed. No stray
`llama-server` processes before measuring; the resident `agentmd serve`
daemon's own embedder (fixed to loopback port 8901 as of this task —
`embed.DefaultAttachPort`) served every query in every run, live-vault
verification included — a one-shot `--via-hook`/hook-issued search never
needs to spawn its own child, which is the whole point of the fixed-port
attach default this task added (`embedderAttachDefault`/`embedderSpawnPort`,
`cmd/agentmd`). `degraded: []` on the landed `--via-hook` run (0 of 84 rows);
the two pathological queries were caught and initially refused by the
refuse-to-publish gate (`DEGRADED_MARKS` grew a third marker, `"(hook
skipped:"`, for exactly this) before the generous per-query budget resolved
them honestly rather than by loosening the gate. Per-question JSON at
`<vault>/Agent/_meta/health/goldv2/hook-e2e-20260814.json`, never in the
repo; the true end-to-end latency sample's raw per-question timings live in
the session scratchpad (`hook_e2e_latency.py`, `hook_e2e_latency_result.json`),
not archived — reproducible from the gold set and the installed hook alone.

## Task 5.5: temporal wiring — non-regression, and the extractor never fires on this gold set

**Rule met, and provably rather than vacuously.** No stratum regresses against
`hook e2e`: distinctive-token 8/12, episodic-temporal 8/12, pure-paraphrase
12/18, research-corpus 10/12, research-density 9/10, overall R@5 47/64 =
73.4%, negative rejection 0/20 — every cell identical to `hook e2e`. Landed
as the `+temporal` column above. The mechanism is `_extract_temporal_bound`
(`harness/skills/memory/scripts/recall.py`), a deterministic, model-free
regex-and-calendar-arithmetic extractor wired unconditionally into
`_daemon_search`: when it resolves a confident bound from the raw prompt, the
daemon call gains `-after`/`-before`; when it does not, the call is
byte-identical to before this task. **On this gold set it never resolves a
bound at all** — verified two independent ways, not merely asserted: (1)
calling `_extract_temporal_bound` directly against all 84 gold questions
returns `None` for every one of them; (2) diffing the `+temporal` run against
`hook-e2e-20260814.json` row for row — `hit`, `first_hit_rank`, the full
top-5 path list, and `correct_rejection` — finds **0 of 84 rows differ**. The
row-level proof is the one that matters: an aggregate match can hide a
one-for-one swap (the `ep05`/`ep03` lesson task 2.5 already paid for), and
this task checked the stronger claim rather than the weaker one.

**The pre-registered "14 at-risk" and "5 misses" figures do not survive
contact with a real extractor, and the task's own worked example says why.**
The pre-task-4 diagnosis that produced those numbers ran from an unpreserved
scratchpad probe (`task4_risk.py`), not from a resolvable-phrase parser, and
this task's own design considerations name the exact distinction that probe
collapsed: *"'When did I decide X' bounds nothing; 'what did I decide last
week' bounds something."* Every one of the 12 episodic-temporal gold
questions, and every other question the old diagnosis flagged (`dt09`,
`rd05`, `rd06`, `rd08`, `rd09`, `rd10`), is the first shape — an open
question asking FOR a date (`ep02` "When did I start working on shrimpi",
`ep06` "When was the last time we worked on dev-setup", `dt09` "before the
wiki pass ran in the worker", `rd05` "after the first save") — not a phrase
SUPPLYING one to filter with. None contains "last week", a month name
adjacent to "in"/"since", a bare year, or any other phrase this task's
pattern set recognizes; a broad keyword scan across all 84 questions for
`yesterday|today|last\s+\w+|this\s+(week|month|year)|past\s+\w+|since\s+\w+|
ago|<month names>|<4-digit year>` confirms only three hits in the entire gold
set (`ep03` "since my last blog post", `ep06` "the last time", `ng08` "the
last 5 security vulnerabilities") and each is independently unresolvable: an
event reference standing in for the very date being asked about, an open
temporal question, and "last" modifying a count rather than a day/week/month
unit, respectively — all three correctly abstain under
`_extract_temporal_bound` too (pinned as explicit test cases, not just
observed). The empirical at-risk set is **0, not 14**; the empirical upside
is **0, not 5** — including `ep09` ("When did we re-write agentm the first
time?"), the diagnosis's one supposedly-unique recovery candidate, which is
the identical "asks for a date" shape and does not convert here either. No
stratum table of before/after ranks follows because there is nothing in it:
zero questions moved, in either direction.

**This is a real extractor, not a no-op built to pass trivially.** It
recognizes the five phrase shapes the task named — "last week", "in June",
"yesterday", "since March", "in 2026" — plus their closest unambiguous
siblings (today, this/last week/month/year, since an explicit ISO date, a
bounded past-N days/weeks/months), each pinned by a hand-derived-literal test
in `scripts/test_recall_temporal.py` (37 tests). It resolves month names with
no year by rolling back to last year's occurrence when the named month has
not started yet this year (`_resolve_month_year`), refuses any bound whose
`after` falls on or after `now`'s own date (a future-dated bound could only
ever guarantee zero hits), and requires a trigger word ("in", "since",
"last", "this", "past") to sit immediately before the date token with
nothing between them — "in June" bounds, "the June release" does not match
at all, which is what keeps a topic label from being misread as a
capture-date constraint. The one residual ambiguity adjacency cannot resolve
— a trigger word used conversationally rather than temporally, e.g.
"interested in June's numbers" — is named in the function's own docstring as
a known, accepted risk; this gold set happens not to contain an instance of
it, and the non-regression measurement, not the heuristic alone, is what
would catch it if a future corpus did. The reference instant is injectable
(`now=`) specifically so none of this depended on the day the suite happened
to run.

**Why it stays wired rather than reverted.** The plan's own framing is that
an extractor which cannot clear the non-regression bar should stay unwired;
this one clears it byte-identically, so there is no failure to protect
against by disconnecting it. It is real, tested, production-reachable code
that will matter on traffic this gold set cannot represent — real prompts
carry "last week" and "yesterday" as casual asides far more often than a
gold set written as standalone questions does, which is the reason this rung
was sequenced after the hook cutover in the first place.

**Ops.** Corpus and index rebuilt fresh for this task (the prior session's
scratch index does not persist across sessions) — `agentmd reindex` against
`~/.agentm/corpus-snapshots/Vault` (9,971 docs, verified against the docmeta
count and the gold set's pinned `$corpus` block) followed by `agentmd embed`
scoped to `Agent/memory,Agent/desk,Agent/external`: 9,473 notes embedded,
564 chunked into 11,761 chunk vectors, 269 emergency shortenings — identical
to task 2.5's original backfill counts, confirming a faithful rebuild. No
stray `llama-server` processes before or after (one resident process
throughout, the operator's own `agentmd serve`, attached to via its fixed
loopback port rather than spawned fresh). `degraded: []` on the landed run.
The same pre-existing `rrfDepth=50` latency-cliff population task 5 named
(`dt11`, `rd03`) reproduced on this machine under the scorecard's default
250ms `--via-hook` budget — expected, not a new defect, and out of this
task's scope per the plan's own instruction; scored instead under a generous
10000ms `--via-hook-budget-ms` so a query slow for reasons unrelated to
correctness is never miscounted as a miss, exactly as task 5's own rule
required for the identical reason. Per-question JSON at
`<vault>/Agent/_meta/health/goldv2/temporal-20260814.json`, never in the
repo.
## Follow-up: the `k`-scaling cliff task 5 flagged — diagnosed and fixed

**The suspected mechanism is refuted; the cost is `snippet()`, and it tracks
term occurrences rather than note size.** Task 5 named hybrid's fixed
`rrfDepth = 50` and asked what in `searchFusion` scaled with `k`. Nothing in
the ranking does. The over-fetch window is `max(note.Overfetch, k)` and
`note.Overfetch` is 200, so every `k` at or below 200 issues an identical
ranking query. Phase-split on `rd03`, same corpus, same terms:

| phase | k=10 | k=50 |
|---|---|---|
| subset sweep (`runMatch` × 15) | 24.4ms | 22.9ms |
| `penalizeAndRank` | 0.4ms | 0.3ms |
| `fillFusedSnippets` | **1.3ms** | **6,476.9ms** |
| `matched` | 936 | 936 |

`matched` is identical, the sweep is flat, and the entire difference is the
snippet pass. Swept across `k` ∈ {10, 20, 30, 40, 50} the ranking stays inside
22.7–24.4ms while the total goes 26ms → 6,500ms.

**Occurrences, not bytes — established on a within-document control rather
than a correlation.** Timed through the system `sqlite3` CLI, outside the Go
code entirely, so the instrument is not the implementation agreeing with
itself. Ranking the full 200-row window costs 3ms. Then one document
(`punkpeye-awesome-mcp-servers/2026-07-08.md`, 1,032,973 bytes) held fixed
while only the query changes:

| match expression | occurrences in that doc | `snippet()` |
|---|---|---|
| `"server" "model"` | 7,647 × 296 | **223ms** |
| `"collapse" "model"` | 3 × 296 | 10ms |
| `"collapse" "output"` | 3 × 35 | 10ms |

Same bytes, 22× spread. Size only correlates because large notes tend to
contain common terms many times over.

**The falsifying case that settles it.** `dt11`'s returned window is *larger*
in bytes than `rd03`'s and far cheaper: 29 rows / 13.2 MB / 1,165 term
occurrences at 242ms, against `rd03`'s 50 rows / 11.9 MB / 100,110
occurrences at 6,446ms. A bytes-driven account predicts `dt11` is the
expensive one. It is not.

**The fix, and what it deliberately does not change.** Ranking and snippeting
split: `searchAnd` and `searchFusion` each grow a ranking half (`andRanked`,
`fusionRanked`) returning the winning match expression per row, and the
wrapper snippets exactly what it returns. `searchHybrid` reads its lexical arm
to `rrfDepth` for the ranks RRF needs, fuses, truncates to `k`, and snippets
those rows alone — where before it received fifty already-snippeted rows and
discarded all but `k`. `rrfDepth` and the over-fetch policy are both
unchanged: the depth was never the cost, so no recall was traded for the
latency, and the pre-registered rule governing a change to either is
untriggered. Snippet eligibility is deliberately kept to the lexical arm's own
returned rows. `fusionRanked`'s `wonBy` covers every candidate the sweep
considered — 936 on `rd03` — so snippeting straight from it would newly
highlight a row that matched lexically below the fusion window and was
promoted into the result by the dense arm. That may be an improvement, but it
changes what gets injected into a prompt, and this is a latency fix; widening
it is its own change to propose and score.

**Row-level re-scoring of all four landed columns, before and after.**
Aggregates agreeing is not rows agreeing — task 2.5 had stratum counts look
flat while `rc08` and `ep05` silently swapped — so this compares returned
paths, snippet text, scores and per-question hit vectors, not just verdicts.
84 questions × 4 arms = 336 rows per binary, frozen corpus, dense arm live
against the resident embedder on 8901:

| column | R@5 before | R@5 after | rows differing |
|---|---|---|---|
| `and` | 7/64 = 0.109 | 7/64 = 0.109 | 0 |
| `fusion` | 27/64 = 0.422 | 27/64 = 0.422 | 0 |
| `+lex3` | 32/64 = 0.500 | 32/64 = 0.500 | 0 |
| `hybrid --question` | 48/64 = 0.750 | 48/64 = 0.750 | 0 |

Zero differences in any compared field across all 336 rows. The reproduced
figures also match the landed record independently — `and` at the recorded
10.9% baseline and `hybrid --question` at `+question`'s recorded 48/64 —
which is the check that the reconstructed index is the same one those columns
were scored against.

**Latency on the production call shape.** Measured as `recall._daemon_search`
actually issues it — `-k 10 -mode hybrid -question <raw prompt>`, terms
positional, embedder live — because that subprocess is what the 250ms budget
bounds. An earlier pass of this investigation reported 1 → 0 from a
`-no-embedder` run at the same `k`; that shape skips the dense arm and pays no
embedding round-trip, and it understated `dt11`, which sits at 242ms without
the round-trip and 295ms with it. The production shape:

| | p50 | p90 | max | over 250ms |
|---|---|---|---|---|
| before | 87.6ms | 96.1ms | 6,322.8ms | **2** (`dt11` 295ms, `rd03` 6,323ms) |
| after | 77.9ms | 86.3ms | **110.3ms** | **0** |

**A larger, older hazard, not fixed here.** The same mechanism ships on `main`,
predates this plan, and needs no deep `k` — only one large note carrying a
common query term near the top. On `main`'s own binary against this corpus,
`agentmd search -k 5 "mcp servers"` costs 10.0s and `-k 50` costs 43.3s,
because three of that query's top five are ~1 MB lists in which "servers"
occurs thousands of times. Reachable through MCP `memory_search`, whose `k` is
caller-supplied and clamped to 50 with no budget to bound it. Left as an
operator call because every remedy — size- or occurrence-capped snippets,
chunking large notes in the lexical index as task 3 already does for the
vector arm, or lowering the MCP clamp — changes which text an agent reads.

**Ops.** Corpus rebuilt from the pinned snapshot rather than reused: 9,971
docs and 11,761 chunk vectors, both matching task 5's recorded counts exactly,
embedded with `embeddinggemma-300M-Q8_0` against the resident daemon's warm
child on 8901. Before/after binaries built from `6d0b0c9` and the fix commit
and confirmed distinct by hash. `Index.snippetedDocs` already documented this
invariant — "called for the k rows a caller reads and not for the 200-row
over-fetch window" — and nothing asserted on it for the fusion or hybrid
paths, which is how the regression reached a shipped column;
`snippetcost_test.go` now pins it per mode and fails on the pre-fix code with
`snippet() saw 50 documents for a k=5 search`.

## Task 6: agent-layer non-regression — refuted

**Rule:** agent-layer R@5 ≥ 0.725 (the week-1 Opus baseline), n≥6 replicates,
frozen corpus, budget-enforced, against the hybrid daemon.

**Refuted.** Mean R@5 across 6 replicates: **0.6799**, against the required
≥0.725 — five of six replicates fall short individually (0.6607, 0.6825,
0.6786, 0.6171, and 0.7004 land under the bar; only 0.7401 clears it). This is
recorded as a finding, not shipped as a pass, per the plan's own ground rule.
The shortfall does not touch the hook: the prompt-submit path is deterministic
(it always calls `-mode hybrid -question`, with no agent discretion involved)
and was separately measured and passed in task 5. What this task measures is a
different, adjacent surface — an interactive agent's own use of the
`memory_search` MCP tool, now that `mode`/`question` are published on it — and
that surface does not clear its bar.

### Proving the dense arm was live before trusting any number

Two checks, both against a dedicated scratch `agentmd serve` instance
(`--vault ~/.agentm/corpus-snapshots/Vault --index <task6 copy of task 5.5's
verified index> --embedder-url http://127.0.0.1:8901`, port 18821 — a
research instance separate from the operator's resident daemon, attached to
its same warm embedder child rather than spawning a second one):

1. `/status` reported `embedder: {state: warm, detail: "attached to
   http://127.0.0.1:8901", vectors: 9473, in_scope: 9473, stale: 0}` before any
   scoring began.
2. A live differential call, not an assertion: `pp02` ("Where did we store the
   worktree rules and can we change them depending on how agentm is
   installed/configured?", expected `Agent/memory/2026/05/
   worktrees-never-auto.md`) under the exact recorded query terms
   (`store worktree rules change depending agentm`) returns 5 confident wrong
   neighbors under the default `and` mode (`matched: 21`, raw BM25 scores
   13–17) and the correct note at **rank 3** under `mode: hybrid` with the
   question passed (`matched: 89`, RRF scores in the 0.024–0.031 band — the
   score regime alone confirms a different arm answered, not just a different
   result set) — reproducing `question-20260814.json`'s own recorded row
   byte-for-byte. Both the candidate set and the score shape change; the dense
   arm is live and reachable, not silently falling back to lexical.

### The named differing input, and why it is the honest one

Pre-registered before scoring: `pp02` is the input that has to differ for a
no-change result to fail. It is a **pure-paraphrase** question (by the gold
set's own labeling rule, it shares no content word with its target note) that
misses under every lexical variant this plan ever measured — `baseline`
(and-of-6), `lexical-fusion` (2-term), and `+lex3` (2- and 3-term, the widest
lexical net built in this plan) all miss it — and hits only once the dense arm
is queried with the natural question (`+question`, rank 3). `rd04` is the same
shape (research-density, lexically unreachable in `baseline`/`lexical-fusion`/
`+lex3`, hit at rank 5 under `+question`). A harness blind to the dense arm —
stubbed, degraded, or silently falling back — cannot reach either case; this
one demonstrably does, live, immediately before the scored run.

### The result, two ways

The rule's own denominator is the historic one: 60 questions in 2026-08-06,
blended — 52 answerable plus 8 negative, every negative scored as `r_at_5 =
1.0` (correctly rejected) or `0.0` (did not), folded into the same average as
the answerable questions. `week3_daemon_retest.py` reuses that exact
aggregation code unmodified (`git log` over this plan's full commit range
touches neither `week1_retrieval_experiment.py` nor
`week3_daemon_retest.py`), so the comparison is apples-to-apples on the
scoring code, even though the corpus and gold set are not identical
populations (goldv2 is bigger and its negative stratum was deliberately grown
and hardened after the original run — see below).

| | R@5 |
|---|---:|
| r1 | 0.6607 |
| r2 | 0.6825 |
| r3 | 0.7004 |
| r4 | 0.6786 |
| r5 | 0.6171 |
| r6 | 0.7401 |
| **mean (n=6)** | **0.6799** |

The same 6 runs, read the way every other column in this file's arm table
reads R@5 — over the 64 answerable questions only, rejection reported
separately — tell a different-shaped story:

| | answerable-only R@5 (64Q) | negative rejection (20Q) |
|---|---:|---:|
| r1 | 76.6% (49/64) | 60% |
| r2 | 78.1% (50/64) | 60% |
| r3 | 81.2% (52/64) | 65% |
| r4 | 78.1% (50/64) | 60% |
| r5 | 71.9% (46/64) | 55% |
| r6 | 82.8% (53/64) | 75% |
| **mean** | **78.1%** | **62.5%** |

**Answerable-question recall is not the problem — it is comparable to or
better than this ladder's best retrieval-layer columns** (78.1% against
`+question`'s 75.0% and `hook e2e`'s 73.4%). **The shortfall is concentrated
almost entirely in negative rejection.**

### Where it concentrates, against the historical baseline directly

`scripts/health/results/week1/opus-arm-a.json` — the actual 2026-08-06 Opus
Arm-A run that produced 0.725 — is still on disk and was read directly rather
than quoted from the design doc, so this comparison is against the real
per-stratum numbers, not a summary of them:

| stratum | 2026-08-06 (lexical-only tool, n=60) | 2026-08-14 (hybrid-capable tool, n=84, mean of 6) | Δ |
|---|---:|---:|---:|
| distinctive-token | 91.7% (11/12) | 87.5% | −4.2 |
| pure-paraphrase | 47.2% (8.5/18) | 52.8% | **+5.6** |
| episodic-temporal | 58.3% (7/12) | 55.8% | −2.5 |
| research-density | 100% (10/10) | 93.3% | −6.7 |
| research-corpus | *(stratum did not exist)* | 71.5% | — |
| **negative rejection** | **87.5% (7/8)** | **62.5% (12.5/20)** | **−25.0** |

Every answerable stratum moved by single-digit points either way — noise, or
in pure-paraphrase's case a real gain, consistent with what a dense arm is
for. Negative rejection is the one double-digit move, and it moved the wrong
way, hard. Two things are true about that stratum at once: it is genuinely a
harder population now (goldv2 grew it from 8 to 20 specifically at AgentKV's
own request — "both harnesses grow the negative stratum to n≥20 before anyone
tunes a threshold" — and the added 12 are deliberately the hard kind, "topics
the corpus discusses at length but does not specifically answer," per this
file's own task-3 section), and the rule is measured against today's frozen
goldv2 corpus regardless, which is the same standard every other column in
this ladder was held to. The harder population explains the direction; it
does not retroactively pass the gate.

### What does not explain it

The obvious hypothesis — that giving the agent access to `fusion`/`hybrid`
(which this entire ladder has measured at ~0% negative rejection every time
it was scored at the retrieval layer, since task 1) poisons the agent's own
rejection the same way — does not survive checking. Instrumented via a
one-line addition to `week3_daemon_shim.py`'s call log (`mode`,
`question_passed`, alongside the fields it already recorded), so this is
measured from the actual served calls, not inferred:

| | correctly rejected |
|---|---:|
| negatives where the agent used `fusion`/`hybrid` at least once | 77.2% (71/92) |
| negatives where the agent used only the default `and` mode | **14.3%** (4/28) |

The opposite of the naive hypothesis: negatives the agent explored more
thoroughly (touching the wider modes at some point in its up-to-6 calls)
rejected *better*, not worse. Two of the four questions missed in all six
replicates — `ng14` ("Which encryption scheme did we choose for the vault at
rest?") and `ng17` ("What did we agree on for multi-user access control in
the vault?") — never once triggered `fusion`/`hybrid` in any of the 6 runs;
the agent's first `and`-mode search returned something plausible-sounding
every time and it never pressed further. Read cautiously — this correlates
thoroughness with correct rejection, and an agent thorough enough to reach for
a second search mode is plausibly also the kind of agent that reasons more
carefully about the answer, which is a confound this data cannot separate
from a causal claim about mode choice itself. What it does rule out is the
specific mechanism this ladder would have predicted first. **The root cause of
the negative-rejection drop is not fully resolved by this task**, and is
named as the open question rather than a guess dressed as a finding.

Across all 1,795 served calls in the 6 runs: 1,434 (79.9%) never set `mode` at
all (the published default, `and`); 142 (7.9%) used `fusion`; 219 (12.2%) used
`hybrid` with the question passed. The agent, even with `mode`/`question`
published on the tool and the tool description's own guidance to escalate on
a thin result, mostly does not reach for hybrid search — which is itself
worth carrying forward: publishing a capability in a tool schema is not the
same as an agent reliably using it.

### Six always-missed answerable questions, cross-checked against this
### ladder's own written-off set

`pp05`, `pp07`, `pp09` (all pure-paraphrase) and `rc02` (research-corpus)
missed in all 6 replicates. `pp05` and `pp07` are on this plan's own
previously-recorded "alias/filing arc" write-off list (task 3's post-mortem
and the pre-task-4 diagnosis both name them as unreachable by any mechanism
this design scoped in) — consistent with, not contradicted by, the agent
layer's own result. `pp09` and `rc02` are new to this list; both are
vocabulary-bridging cases of the same shape.

### Verdict

**The rule fails, recorded rather than relaxed.** Nothing is reverted by this
task: the hook (task 5) is a separate, deterministic path that does not
depend on an agent's mode choice and was already separately measured and
passed; the mode/question MCP schema (also task 5) causes no regression on
its own terms (fusion/hybrid usage correlates with *better* rejection, not
worse); and answerable-question recall through the tool is flat-to-improved
against the original baseline. What is refuted is the specific claim this
task set out to test — that today's agent-layer performance, measured the
same way the original 0.725 was measured, clears that bar — and it does not.
This is the operator's call to make with the finding in hand, not a call this
task makes for them by unwinding five already-shipped, CI-green tasks on one
measurement that carries a real population confound.

### Ops

Corpus and index reused verbatim from task 5.5 (9,971 docs, 9,473 embedded
notes, 11,761 chunk vectors) — verified directly via `sqlite3` against the
index file before serving from it (`SELECT COUNT(*) FROM docmeta` → 9971,
`SELECT COUNT(DISTINCT doc_id) FROM embeddings` → 9473, `SELECT COUNT(*) FROM
embeddings` → 11761 — all three match every prior rung's recorded figures
exactly), then confirmed a second way by the scratch daemon's own startup log
(`index ready documents=9971 added=0 updated=0 removed=0`). No stray
`llama-server` processes before starting (one resident child on 8901
throughout, shared rather than duplicated); 0 embedder restarts across the
full run. All 6 replicates: `INTEGRITY: clean` (0 hook violations, 0 tool
escapes, 0 tool-call-count mismatches, 0 budget leaks, 0 driver errors).
Driver: `claude -p --model opus`, 6-call budget, `--settings
'{"disableAllHooks":true}'` plus the harness's own `permissions.deny` list, so
neither the operator's own recall hook nor the deferred-tool surface can leak
context into the driver. Total cost across all 6 replicates: $50.68; total
wall time: 10,784s (~3h across 6 concurrent replicate processes against one
shared daemon). Raw per-replicate scorecards (full per-question detail,
`tool_call_log`, and the `mode`/`question_passed` instrumentation) at
`<vault>/Agent/_meta/health/goldv2/agent-layer-r{1..6}-20260814.json`, never
in the repo.

## Final scorecard

*The spreadsheet-style summary: every landed and refuted rung, in one table,
as the plan closes.* Refuted columns (`+rerank+floor`, `+lex3`) are included
alongside the shipped ones, not omitted.

| | baseline | lexical-fusion | +vector RRF | +chunking | +rerank+floor (refuted) | +question | +lex3 (refuted) | hook e2e | +temporal | agent layer (n=6 mean) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| distinctive-token | 3/12 | 7/12 | 8/12 | 8/12 | 7/12 | 9/12 | 11/12 | 8/12 | 8/12 | 87.5% |
| episodic-temporal | 3/12 | 6/12 | 7/12 | 7/12 | 7/12 | 9/12 | 9/12 | 8/12 | 8/12 | 55.8% |
| pure-paraphrase | 1/18 | 5/18 | 7/18 | 9/18 | 6/18 | 11/18 | 10/18 | 12/18 | 12/18 | 52.8% |
| research-corpus | 0/12 | 6/12 | 7/12 | 6/12 | 3/12 | 10/12 | 11/12 | 10/12 | 10/12 | 71.5% |
| research-density | 0/10 | 3/10 | 6/10 | 6/10 | 2/10 | 9/10 | 8/10 | 9/10 | 9/10 | 93.3% |
| **R@5 (retrieval layer: 64Q; agent layer: blended 84Q mean)** | **10.9%** | **42.2%** | **54.7%** | **56.2%** | **39.1%** | **75.0%** | **76.6%** | **73.4%** | **73.4%** | **68.0%** |
| negative rejection | 35% | 0% | 0% | 0% | 40% | 0% | 0% | 0% | 0% | 62.5% |
| status | shipped | shipped | shipped | shipped | **refuted** | shipped | **refuted** | shipped | shipped | **refuted — see Task 6** |

The agent-layer column is not directly comparable cell-for-cell to the
retrieval-layer columns to its left — it is a 6-replicate mean of a
stochastic agent driving a tool over up to 6 calls, scored blended (84
questions, negatives included in the same average) rather than the
retrieval-layer convention of R@5-over-64-plus-rejection-over-20 reported
separately. Its own answerable-only reading (78.1%, negative rejection 62.5%
— see the Task 6 section above) is the one to set beside the other columns'
own R@5/rejection pairs. The blended 68.0% is what the rule (≥72.5%) is
actually checked against, and it is the number that refutes.

**The ladder, read honestly, end to end: 10.9% → 75.0% shipped at the
retrieval layer, with the largest single gain (+19 points, `+question`) coming
directly from the query-format artifact task 3's own failure exposed, and
task 4's failure catching a probe-methodology error that would otherwise have
propagated into `+lex3` and every rung after it. Two of eight rungs closed
refuted (`+rerank+floor`, `+lex3`) and both kept their code behind flags the
hook does not set — no production behavior changed on either refutation. The
ninth and final gate, agent-layer non-regression, also closes refuted — the
first refutation in this plan that is not quarantined behind an unpublished
flag, because the surface it tests (the published `mode`/`question` MCP
schema) is already live. Recorded, not shipped as a pass, per the plan's own
rule that a rung failing its rule is not a rung.**

---

# Alias oracle — rung 0 of rejection-and-vocabulary, rule met at 7 of 8

Run 2026-08-14, the first rung of the arc after hybrid retrieval
(`wiki/designs/agentm-rejection-and-vocabulary.md` §1). Rule pre-registered in
the design and in the plan before any alias text was written: **at least 6 of
the 8 targets convert to top-5 hits; no currently-passing question is lost,
published by id; no stratum regresses by more than one question.**

## What this measures, and what it deliberately is not

Eight gold questions (`pp05`, `pp07`, `pp09`, `pp15`, `pp16`, `pp17`, `rc01`,
`rd01`) miss on the ladder's best retrieval-layer column, `+question`, which is
where the set came from. Seven of them miss on every shipped column as well;
`pp05` is the exception, landing at rank 5 on `hook e2e`.

This rung hand-writes ideal `aliases:` frontmatter for those eight — one note
per question, in the vocabulary each question actually uses — on a copy of the
frozen corpus, and scores once. A hand-written ideal alias is the most
favourable input any alias engine could ever produce, so the result bounds the
whole vocabulary thread from above before an engine exists. It is the same move
the term-subset oracle made when it bounded the lexical thread at 82.8%.

**Nothing here ships, and the authored text is quarantined.** The aliases were
written with the gold questions in view. That is the licence this rung claims —
the same one the candidacy analysis and the k=20 reachability count already
carry — and it is exactly what disqualifies the text from reaching a shipped
mechanism. The alias text lives in the scratch corpus copy and in the vault-side
detail JSON, never in this repo, and must not become few-shot examples for the
filing pilot, which the design requires to be constructed blind to the gold set.

**It is also not a new ladder column, and is not appended to the final
scorecard above.** That table records what shipped and what was refuted. This
is a diagnostic ceiling.

## The measured result

| | `+question` baseline | `+question` + ideal aliases | hook e2e baseline | hook e2e + ideal aliases |
|---|---:|---:|---:|---:|
| distinctive-token | 9/12 | 9/12 | 8/12 | 8/12 |
| episodic-temporal | 9/12 | 9/12 | 8/12 | 8/12 |
| pure-paraphrase | 11/18 | **16/18** | 12/18 | **17/18** |
| research-corpus | 10/12 | **11/12** | 10/12 | **11/12** |
| research-density | 9/10 | **10/10** | 9/10 | **10/10** |
| **R@5** | **75.0%** (48/64) | **85.9%** (55/64) | **73.4%** (47/64) | **84.4%** (54/64) |
| negative rejection | 0/20 | 0/20 | 0/20 | 0/20 |

**All three clauses met, on both arms.**

## Per question, by id — every movement, not a stratum total

A flat aggregate hid a one-for-one swap in this project once already
(`rc08`/`ep05`), so gains and losses are published by id.

| target | `+question` rank | hook rank |
|---|---|---|
| `pp05` | miss → **1** | 5 → **1** (already a hit on this arm) |
| `pp07` | miss → miss | miss → **5** |
| `pp09` | miss → **1** | miss → **1** |
| `pp15` | miss → **1** | miss → **1** |
| `pp16` | miss → **1** | miss → **1** |
| `pp17` | miss → **2** | miss → **2** |
| `rc01` | miss → **1** | miss → **1** |
| `rd01` | miss → **1** | miss → **1** |

Seven of eight convert on `+question`. On the hook arm `pp05` was already a hit
at rank 5 — it is in the target set because the set was drawn from the
`+question` arm's misses — so that arm goes from 1 of 8 to **8 of 8**.

**Losses: none, on either arm.** No question that passed at baseline fails
after the aliases, and no non-target question changed state in either
direction. One rank moved without changing state: `pp13` 3 → 4 on both arms.
That is the reciprocal-rank displacement clause (b) exists to catch, showing up
at a magnitude that costs nothing — worth recording precisely because the same
mechanism has cost this project hits three times.

## `pp07` is fusion friction, not a vocabulary gap

The one non-conversion looked systematic — its aliased `+question` top-5 is
byte-identical to its baseline top-5, as though the alias had done nothing at
all — so the instrument was checked before the result was written down.

The alias worked perfectly. For the gold query's own terms
(`agentm never fully realize vault vision`), `F1-REAUDIT.md` is **outside the
lexical top-50 on the baseline index and rank 1 on the aliased index**
(`agentmd search -mode fusion -k 50 -no-embedder`, both indexes). Reciprocal-rank
fusion with the dense arm — where the note ranks poorly — placed it **7th**
overall. The hook arm excludes `_archive/` and `_inbox/` subtrees by default
(`recall.py`, over-fetching 2× to compensate), which removes two intervening
notes, and the same note lands **5th** there.

That is not a one-note observation. Measured for all eight, the aliased note's
rank on the lexical arm alone:

| | `pp05` | `pp07` | `pp09` | `pp15` | `pp16` | `pp17` | `rc01` | `rd01` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | >50 | >50 | >50 | >50 | >50 | >50 | >50 | >50 |
| aliased | 1 | 1 | 1 | 1 | 1 | 2 | 1 | 1 |

Every one of the eight goes from outside the lexical top-50 of a 9,971-note
corpus to the top two, seven of them to rank 1. `pp17` is second to
`Agent/memory/preferences/don-t-do-automaticaly-and-why.md` — a captured memory
of the operator's own question, which is a reasonable thing to rank first and
which the fused arm also puts above it.

So the vocabulary bound is effectively 8 of 8, and the residual miss is a
ranking property of RRF — the fusion-friction territory this ladder already
mapped in the `+question` post-mortem — rather than anything an alias engine
could fix. **This is the finding that shapes the filing pilot more than the
count does**: the pilot's ceiling is set by fusion, not by how good its alias
text is.

## Ops

Corpus restored from `<vault>/Agent/_meta/corpus-snapshots/goldv2-20260812.tar.gz`
to a scratch location distinct from the copies previous rungs used. Index built
from scratch and asserted by direct `sqlite3` count before any scoring — 9,971
docs, 9,473 embedded notes, 11,761 chunk vectors, the three figures every prior
rung recorded; **identical after the aliases**, so no note crossed a chunk
boundary. Embedder: EmbeddingGemma-300M-Q8_0 at `-np 1 -c 2048 -b 2048 -ub 2048`,
the resident child on the fixed loopback port, no stray `llama-server` processes
before or after. `degraded: []` on all four runs.

**Baseline reproduced before a single alias was written**: the fresh copy scores
`question-20260814.json` and `hook-e2e-20260814.json` **row for row, 0 of 84
differing on either arm** — `hit`, `first_hit_rank`, `correct_rejection`,
`returned`, and the full ordered top-5. That is what makes any later movement
attributable to the aliases rather than to the environment. Both aliased arms
were re-run once and are bit-identical, 0 of 84 rows differing.

**The edit is exactly eight notes and exactly one field.** A file-level md5
manifest of all 9,971 notes taken before authoring, diffed after, names eight
changed files, and each one's content diff touches only `aliases:`. One target
(`progress-model-routing-and-levers.md`) had no frontmatter at all and gained a
block containing only that field — deliberately nothing else, since a `status:`
or `mining_confidence:` line would move the note's rank-penalty class and make
the conversion attributable to a promotion rather than to vocabulary. Checked
rather than reasoned about: `docmeta.flags` is empty for all eight notes in both
indexes.

Per-question detail, including the authored alias text, at
`<vault>/Agent/_meta/health/goldv2/alias-oracle-20260814.json`, never in the
repo.

## What this licenses (alias oracle)

`alias-pilot` is licensed to build — the thread is real, and a mechanism that
gets anywhere near this text converts questions. It inherits two constraints
from this rung: it must be constructed blind to the gold set (the design's own
gold-blindness boundary), and its ceiling is bounded by fusion rather than by
alias quality, which `pp07` demonstrates concretely. What this rung does **not**
license is any claim about the −3.85-point bulk-backfill precedent: writing
eight ideal aliases and writing 1,930 generated ones are different experiments,
and the pilot still has to demonstrate it is not the second one wearing a new
name.

---

# Elicitation mini-gate — refuted at 69.2% against an 80% bar

Run 2026-08-14, Thread A of the rejection-and-vocabulary arc
(`wiki/designs/agentm-rejection-and-vocabulary.md` §2). Rule pre-registered in
the design, and the wording frozen in `1d8b9de` before the arm ran: **mean
negative rejection ≥80% AND the canary answerable mean within one question of
its own baseline slice, both clauses, arm difference by exact permutation
test.**

**Both clauses fail. The rung closes refuted.**

| | baseline (n=6) | elicitation (n=6) | bar | |
|---|---:|---:|---:|---|
| negative rejection | 62.5% | **69.2%** | ≥80% | FAILED |
| canary hits /15 | 13.17 | **12.00** | ±1 | FAILED (−1.17) |

Exact permutation test over all 924 label assignments, two-sided: rejection
**p = 0.0823**, canary **p = 0.1450**. Neither arm difference is distinguishable
from noise at n=6, so the honest reading is not "it helped a little and cost a
little" — it is that six replicates cannot separate either effect from zero.

## The gain is two questions, not a general lift

This is the finding the headline hides, and the reason per-question diffs are
published rather than stratum means.

+6.7 points is **+8 net rejections across 120 trials**, and two questions supply
seven of them: `ng03` 1/6 → 5/6 and `ng16` 3/6 → 6/6. Three more gain a single
trial each (`ng02`, `ng09`, `ng11`), `ng06` loses two (5/6 → 3/6), and **14 of
20 negatives are unchanged**. A mean would have reported a broad improvement the
per-question data does not support.

**And the questions that were always wrong stayed always wrong.** `ng07`,
`ng14` and `ng17` were rejected in 0 of 6 baseline replicates and 0 of 6
elicitation replicates. The design named `ng14` and `ng17` specifically as
negatives that never escalated; the tool text did not reach them at all. Whatever
they are failing on, it is not a shortage of permission to say no.

## The canary loss is mostly not over-rejection

Clause (b) exists to catch a fix that buys rejection by destroying true answers.
That did happen — but only three times in 90 trials: `ep02` twice and `rc01`
once, all on questions the baseline hit 6/6. The rest of the −1.17 is the agent
naming *different wrong notes* (`dt01` 6→5, `pp02` 5→3, `pp03` 6→5), which is
ordinary churn rather than the failure mode the clause was written for.

`rc02`, this rung's named watchlist case, moved 0/6 → 1/6. Not enough to read as
an effect.

## What did change: the variance collapsed

Baseline rejection across its six replicates spread **55–75%**. The elicitation
arm sits at **65–70%** — five replicates at 0.70 and one at 0.65. Behaviour
became markedly more consistent even though the mean never approached the bar.
Recorded because it is a real property of the change, not because it rescues the
result: a tighter distribution around a failing mean is still a failing mean.

## The prior that was recorded before the run, and held

Written into the pre-registration before the arm existed: the driver's own
system prompt **already** ends with *"Answering 'no answer found' when nothing
fits is correct and expected for some questions. A confident wrong path is worse
than admitting the vault does not cover it."* The 62.5% baseline was measured
with that sentence in place. So the tool description was always additive to an
existing permission rather than filling a silence, and the headroom available to
it was smaller than the design's framing implied.

What the new text adds is not permission but the answerhood *test* — how to tell
a related note from an answering one. The result says that distinction, stated
in a tool description, does not by itself convert an agent that has already
decided to answer.

## Instrument

**Index equivalence was verified rather than assumed.** The baseline replicate
JSONs record `index_documents: null`, so provenance could not be read off them —
but both arms log every served call with its query string and its exact result
paths. **31 queries were issued verbatim in both arms** at the same `k`/`mode`,
and every one returned an **identical result list**. The arm difference is
therefore not an index difference. This cost nothing and touched neither the
embedder nor the running replicates.

Two setup faults were caught before they could contaminate the run. The gate
daemon initially tried to **spawn** its own embedder on the port the live
daemon's embedder already holds; the child exited instantly and retried in a
loop, and every `hybrid` call silently degraded to the lexical arm. Since the
baseline driver issued 39 `hybrid` calls in r1 alone, running that way would
have measured a retrieval regression wearing an elicitation label. Fixed by
attaching to the resident embedder. Separately, the arm runs against
`~/.agentm/corpus-snapshots/Vault` — the copy the baseline used — and *not* the
alias-oracle copy, which carries eight hand-written aliases and would have mixed
a vocabulary change into a rejection measurement.

Corpus copy differs from the archived snapshot by one file: a daemon self-probe
note, which rotates in place so the count holds at 9,971. Probe notes are
excluded from census counts but **not** from search, so both arms carry one and
it is recorded rather than called pristine. Integrity clean across all six
replicates — no hook events, no tool escapes, no budget leaks, no driver errors;
two replicates logged one question that wanted more calls. Cost **$24.41**
against the plan's ~$21 estimate. Per-question detail at
`<vault>/Agent/_meta/health/goldv2/minigate-result-20260814.json`.

## Consequence

The wording is **reverted**, not kept. The arc's own methodology is that a rung
failing its rule closes refuted and is not shipped, and this one has no flag to
hide behind — a tool description is either in the schema or it is not. Neither
measured effect clears significance, and the change is not free: three trials of
genuine over-rejection on questions the baseline always answered. Reverting is a
revert commit plus a rebuild; the pre-change binary was kept at
`~/.local/bin/agentmd.pre-elicitation` throughout for exactly this outcome.

What this does **not** refute is the design's underlying claim that the
interactive surface can be helped by what the tool says to it. It refutes this
wording, at this strength, measured this way — and it says where the remaining
rejection failure is not: `ng07`, `ng14` and `ng17` never moved, so the residue
is not an elicitation problem. Thread A's other half, the deliberate-path
labeller, is unaffected: it is a different surface with a different lever, and
the probe evidence behind it (85.0% projected rejection against a cross-encoder's
40%) is untouched by this result.

---

# Answerhood labeller — refuted on two clauses, and strong on four strata of five

Run 2026-08-15, Thread A of the rejection-and-vocabulary arc
(`wiki/designs/agentm-rejection-and-vocabulary.md` §3), scored by deterministic
offline replay against the candidate sets the agent was actually served. Rule
pre-registered, all three clauses required.

| clause | measured | bar | |
|---|---:|---:|---|
| (a) negative trials with no candidate labelled `answers` | **82.5%** (99/120) | ≥80% | MET |
| (b) answerable trials keeping the expected note | **84.1%** (264/314) | ≥90% | FAILED |
| (c) episodic-temporal preserved | **54.0%** (34/63) | ≥80% | FAILED |

**Refuted.** The labeller ships behind a runner-job flag that defaults off,
which the design already specifies as "unlabelled-with-marker, exactly today's
behavior" — the quarantine this arc applies to a rung that fails its rule.

## The probe's own numbers, split by whether they were measured uniformly

This is the finding that outlives the verdict, and it was diagnosed *before*
these runs existed rather than read off them afterwards.

| | probe reported | uniform replay |
|---|---:|---:|
| negative rejection | 82.5% | **82.5%** |
| answers preserved | 86.6% | **84.1%** |
| episodic-temporal | 58.7% | **54.0%** |

The negative figure reproduces to the decimal. Both answerable figures come in
low. That is exactly the shape predicted by how the probe was built: its
negative half was measured uniformly, while its "corrected" answerable figures
are 240 trials scored with the *thin* excerpt plus 32 rescued by the corrected
one (272/314) — a mixed instrument its own honest-accounting section names as
an upper bound. Task 6's clauses (b) and (c) were set against that upper bound.
**A bar calibrated against a number that was never a baseline is a bar nobody
could have cleared**, and that is a plan defect rather than a labeller defect.

## Per stratum — the labeller fails in one place, not generally

| stratum | preserved |
|---|---:|
| research-density | 56/56 = 100.0% |
| research-corpus | 52/53 = 98.1% |
| pure-paraphrase | 68/77 = 88.3% |
| distinctive-token | 54/65 = 83.1% |
| **episodic-temporal** | **34/63 = 54.0%** |

**Excluding episodic-temporal, answerable preservation is 230/251 = 91.6% —
above clause (b)'s own bar.** One stratum carries the entire failure of both
failing clauses.

The episodic damage is structural, exactly as the probe warned: these are
questions whose answer is *derived* from a note rather than stated in it. Three
prompt-level attacks on it were measured and all three failed to move the cases
they targeted (see the task-5 record below).

## Half the answerable loss is disagreement, not rejection

Of the 50 unpreserved answerable trials, **29 were the labeller saying nothing
answers** and **21 were it naming a different note as answering**. Only the
first group is over-rejection in the sense clause (b) exists to police; the
second is a disagreement with the gold set about which of several notes answers,
and at least one is defensible on its face — `ep08` asks what caused a Drive
sync failure, and the labeller named
`drive-upload-staging-churns-transient-files-inside-the-vault.md` where gold
names `research-concurrent-vault-writes.md`.

That distinction is not used to argue the number up. It is recorded because a
labeller that never deletes makes a wrong verdict recoverable — the note is
still listed and readable — so the 21 disagreements cost a consumer far less
than the 29 silences, and the two should not be priced the same.

## The negatives, per question — including the three nothing else reached

| | agent baseline | labeller |
|---|---:|---:|
| `ng14` — which encryption scheme for the vault at rest | 0/6 | **6/6** |
| `ng07` — homelab threat intelligence | 0/6 | 2/6 |
| `ng17` — multi-user access control | 0/6 | 1/6 |
| `ng11` — *regression* | 4/6 | **0/6** |

`ng14` is the design's two-surface thesis as a measurement. It was rejected in
zero of six agent-layer replicates and zero of six elicitation replicates — no
interactive lever ever touched it — and a fresh-context judgment rejects it in
all six. `ng07` and `ng17` move only slightly, so the trio does not fall, but
the mechanism is demonstrated rather than argued.

`ng11` is the honest counterweight: the labeller never rejects a negative the
agent usually did. A real per-question loss inside a +20-point aggregate, and
precisely what a stratum mean would bury.

## Ops

Excerpting is the corrected IDF-weighted head + best-middle + tail, shared with
the shipped labeller rather than reimplemented — the promoted replay instrument
and the module use one selector, because the probe's first pass got that
selector wrong and 43.2% of its apparent over-rejections were the instrument.
Frequencies pooled over the run's whole candidate set (211–1,200 notes depending
on slice). `degraded` 0/506 on the negative half and 2/348 on the answerable
half, both surfacing through the visible marker rather than silently.

The episodic slice reuses the run that froze the prompt (`c206b36`) rather than
re-buying it: same prompt, same instrument, same corpus copy. Cost **$35.68**
across the three measuring runs, against calls that price at $0.047 rather than
the probe's $0.0048 — the corrected excerpt is ~2.8KB across up to 20 candidates
where the probe showed one 1.2KB chunk.

Per-question detail at
`<vault>/Agent/_meta/health/goldv2/labeller-replay-{negatives,answerable}-20260815.json`.

## What this licenses

The judgment is real and it is worth having on the deliberate path: 82.5%
rejection where the agent managed 62.5%, 100% and 98.1% preservation on the two
research strata, and a complete conversion of a negative no interactive lever
could reach. What it does not license is running unflagged over episodic
questions, where it is wrong nearly half the time in a way three prompt
interventions failed to shift.

The re-scope this suggests, and does **not** itself authorize: clause (b) and
(c) were priced against a mixed-instrument upper bound, so a future rung should
re-set them against these uniform numbers before spending anything, and should
consider whether a labeller that never deletes needs an 80% bar on the one
stratum where derivation, not answerhood, is the real question.

---

# Alias pilot — refuted on clause (a): a clean null, not the backfill repeated

Run 2026-08-16, rule pre-registered in the design (§4) and the plan before any
code: **converts ≥3 of the eight oracle-validated targets; loses zero
currently-passing questions net, published by id; leaves the 20 negatives'
behaviour unchanged.** A pilot that cannot beat "do nothing" closes refuted.

## The mechanism, and what makes it a fair test of the family

`scripts/alias_pilot.py` — a batched Sonnet 5 pass, gold-blind by construction,
over a fixed ≤300-note scope described entirely by structural patterns (every
`_index.md`, everything under `external/`, every `PLAN.archive.*.md` — never a
note list), propose→confirm behind the corpus-write gate. It reuses
`alias_backfill.py`'s generation, cleaning, and write primitives verbatim —
same prompt rules, same alias cleaning, same frontmatter mechanics as the
reverted 2026-08 bulk backfill — so the only variables between this pilot and
its explicit null hypothesis are scope (targeted, not bulk) and gating
(propose→confirm, not write-on-generate). Gold-blindness was made mechanical,
not asserted: `test_alias_pilot.py`'s `GoldBlindnessTests` instruments every
file a real propose run opens and checks the built prompt against the eight
oracle questions' own literal text.

## Ops

Fresh copy of `goldv2-20260812` restored to a scratch location distinct from
every prior rung's copy. Index built from scratch and asserted before any
alias was written: 9,971 docs / 9,473 embedded notes / 11,761 chunk vectors —
the three figures every prior rung recorded, exact. Embedder:
EmbeddingGemma-300M-Q8_0 at `-np 1 -c 2048 -b 2048 -ub 2048`, resident, not
spawned. `degraded: []` throughout.

**Baseline reproduced before a single alias was written**: 0 of 84 rows differ
against `question-20260814.json` and `hook-e2e-20260814.json` on either arm.

`propose` selected **120 notes** in scope — not the 290 the plan's task 1
counted against the live vault; the frozen corpus is four days younger, and
120 is the correct number to score against, not a discrepancy. 120 of 120
aliased, 0 skipped, 0 errored. All three of the oracle's in-scope targets
(`pp05`, both `pp09` paths, `pp15`) are among them. `apply --allow-ungated`
wrote them to the scratch copy — `--allow-ungated` exists because a
bare-extracted tarball has no git history, so `agentmd gate corpus-write`
cannot answer "does an undo exist" at all; the design's gate governs *live*
writes, and this measurement arm is verified by the manifest diff instead,
per the design's own Data Integrity section.

**The edit is exactly 120 notes and exactly one field each.** A file-level
md5 manifest taken before authoring, diffed after, names 120 changed files;
a per-file check (pristine text plus exactly one inserted `aliases:` line,
nothing else) passes for all 120, zero violations. Index rebuilt from
scratch: 9,971 docs / 9,473 embedded notes unchanged, 11,762 chunk vectors
(**+1**, not a defect — 120 notes gained alias text against the oracle's 8,
and one note's addition was enough to cross a chunk boundary; explained, not
swept past). The aliased index was scored twice; 0 of 84 rows differ between
the two runs, on either arm.

## The measured result

| stratum | `+question` baseline | `+question` + pilot | hook baseline | hook + pilot |
|---|---:|---:|---:|---:|
| distinctive-token | 9/12 | 9/12 | 8/12 | 8/12 |
| episodic-temporal | 9/12 | 9/12 | 8/12 | 8/12 |
| pure-paraphrase | 11/18 | 11/18 | 12/18 | 12/18 |
| research-corpus | 10/12 | 10/12 | 10/12 | 10/12 |
| research-density | 9/10 | 9/10 | 9/10 | 9/10 |
| **R@5** | **75.0%** (48/64) | **75.0%** (48/64) | **73.4%** (47/64) | **73.4%** (47/64) |
| negative rejection | 0/20 | 0/20 | 0/20 | 0/20 |

**Byte-identical stratum totals, on both arms.** Not one question changed
hit/miss state anywhere in the 84-question set.

## Clause by clause

**Clause (a) — FAILED, 0 of 8 (0 of 3 in-scope) converted, both arms.** `pp05`,
both `pp09` paths, and `pp15` — the three targets the fixed scope structurally
reaches — stay misses after aliasing, on `+question` and on the hook arm alike.

**Clause (b) — MET, net +0.** Zero currently-passing questions lost, zero
non-target questions gained, on both arms.

**Clause (c) — MET.** All 20 negatives' `correct_rejection` identical before
and after, on both arms.

**Three non-target hits moved rank by exactly one, on both arms, and stayed
hits:** `ep04` 3→4, `ep05` 2→3, `pp08` 1→2. Reciprocal-rank displacement — the
mechanism this project has now watched cost real hits three times — showing
up here at a magnitude that cost nothing. Recorded because of that history,
not because it did damage this time.

## Why clause (a) failed: a vocabulary miss, not fusion friction

The oracle's one non-conversion (`pp07`) was diagnosed as fusion friction: its
hand-written alias won the lexical arm outright (rank 1, from outside the
top 50) and still lost to reciprocal-rank fusion. The same diagnostic run
against this pilot's three in-scope targets (`agentmd search -mode fusion -k
50 -no-embedder`, baseline index vs aliased index) shows a different failure:

| target | note | baseline lexical rank | aliased lexical rank |
|---|---|---:|---:|
| `pp05` | `home-tech-next/_index.md` | >50 | >50 |
| `pp09` | `external/primos/_index.md` | >50 | >50 |
| `pp09` | `external/primos/analysis/_summary.md` | >50 | >50 |
| `pp15` | `PLAN.archive.20260724-loose-ends-os-install-matrix.md` | >50 | >50 |

**Zero rank movement.** These aliases never became lexically competitive at
all — the pilot didn't lose to fusion the way `pp07` did; it never reached
fusion with a fighting chance. Reading what was generated against what the
gold questions ask explains why. `pp05`'s question is *"Give me a list of my
pending project ideas for the house?"* — a structural, meta-level request
naming the note's *role* ("pending", "ideas", "list"). The generated aliases
describe the note's *content* instead: "nas and identity consolidation plan",
"home network umbrella project", "cloudflare tunnel for home services" —
accurate, and useless for this query, because none share the vocabulary the
question actually uses. `pp09` and `pp15` show the identical pattern:
content-true aliases, meta-vocabulary miss.

The prompt asks for "the phrasings someone would plausibly type when looking
for that note" (`alias_backfill.py`'s `TASK_RULES`, reused verbatim, per
task 2's own discipline of not re-deriving a tested primitive) — and a
gold-blind model answers with plausible *content* phrasings. The oracle's
hand-written aliases, gold-informed by construction, could target the exact
idiosyncratic *framing* a real question happens to use; a mechanism blind to
that question has no way to know the framing in advance. This is recorded as
the finding, not chased — tuning the prompt toward these three specific
misses now would mean reading what they need and writing back toward it, the
same gold-informed-by-the-back-door trap task 1 declined when it fixed the
scope before checking which widening would recover more targets.

## What this does and does not license

**Not the −3.85-point backfill wearing a new name.** The explicit comparison
of record is "do nothing," and on this scorecard the pilot does exactly
that — R@5 unchanged on both arms, no negative's behaviour changed, no
non-target question lost. The pilot's scope-and-gate discipline (targeted,
never bulk; propose→confirm; the manifest-verified single-field edit) kept a
mechanism that does not clear its own bar from costing anything, which is
itself evidence the discipline works, independent of whether this particular
generation mechanism does.

**Not a verdict on aliasing in general.** The oracle already showed the
ceiling is real: hand-written aliases converted 7 of 8. What failed here is
one specific, narrow generation strategy — the backfill's own content-focused
prompt, run gold-blind — against three targets whose gold questions happen to
ask in a structural register the prompt was never told to reach for. A
prompt that explicitly asked for both content and structural/meta phrasings
("what kind of thing is this — a list, a summary, a decision, a status —
and what would someone call it") is untested, not refuted, by this run.

**Per the design's own rule, this closes refuted.** Nothing is applied to the
live vault. The alias story returns to capture-time practice only, per §4's
own text, unless a future rung is built and pre-registers a rule against a
different generation strategy — which is a new rung, not a re-run of this one.

Per-question detail, including the 120 generated alias sets and the four
lexical-rank diagnostics, at
`<vault>/Agent/_meta/health/goldv2/alias-pilot-20260816.json`, never in the
repo.

---

# Alias pilot, structural variant — refuted the same way, a sharper null

Run 2026-08-16, same day, operator-approved successor to alias-pilot's own
re-audit trigger: *"any successor alias-generation rung should pre-register
against a **different** prompt strategy."* Same three clauses as alias-pilot,
unchanged, on a **fresh** `goldv2-20260812` copy (the previous scratch copy
carries alias-pilot's own aliases and cannot be reused).

## The one variable that changed

`alias_pilot.py propose --variant structural` — everything held constant
(scope, gate, gold-blindness boundary, write mechanics) except the prompt,
which gains one instruction block asking the model to also name the note's
*role* (list/index, summary, decision/plan record, status/capability
snapshot, audit/review) and write one or two aliases targeting that role
directly, in template form ("my list of `<category>`", "does `<thing>` still
support `<capability>`") — alongside the unchanged content-phrasing rules.
Pure category language and placeholders; `GoldBlindnessTests` extended to
cover this variant the same way it covers the original.

## Ops

Fresh copy restored to a scratch location distinct from alias-pilot's own.
Index built and asserted before any alias was written: 9,971 / 9,473 / 11,761,
exact. Baseline reproduced row-for-row against `question-20260814.json` and
`hook-e2e-20260814.json` (0/84 differing, both arms) before writing anything.
`propose --variant structural` selected the identical **120-note scope**
alias-pilot's own run selected (same corpus, same fixed scope definition —
confirms the scope itself is deterministic, only the prompt changed), all 120
aliased. `apply --allow-ungated` wrote them; manifest-verified as exactly 120
files changed, each touching only `aliases:`, 0 violations. Index rebuilt:
9,971 / 9,473 unchanged, 11,762 chunk vectors (+1, same as alias-pilot's own
run — the alias-text-crosses-a-chunk-boundary explanation applies again, same
magnitude). Aliased index scored twice; 0/84 rows differ between runs, either
arm.

## The measured result

| stratum | `+question` baseline | `+question` + structural | hook baseline | hook + structural |
|---|---:|---:|---:|---:|
| distinctive-token | 9/12 | 9/12 | 8/12 | 8/12 |
| episodic-temporal | 9/12 | 9/12 | 8/12 | 8/12 |
| pure-paraphrase | 11/18 | 11/18 | 12/18 | 12/18 |
| research-corpus | 10/12 | 10/12 | 10/12 | 10/12 |
| research-density | 9/10 | 9/10 | 9/10 | 9/10 |
| **R@5** | **75.0%** (48/64) | **75.0%** (48/64) | **73.4%** (47/64) | **73.4%** (47/64) |
| negative rejection | 0/20 | 0/20 | 0/20 | 0/20 |

**Byte-identical stratum totals to alias-pilot's own content-only result.**

## Clause by clause

**Clause (a) — FAILED, 0 of 8 (0 of 3 in-scope), both arms.** Identical
outcome to the content-only prompt: `pp05`, both `pp09` paths, and `pp15`
stay misses.

**Clause (b) — MET, net +0.** **Clause (c) — MET**, all 20 negatives
unchanged. Three non-target hits moved rank (`dt06` 1→3 worse, `ep04` 3→4
worse, `ep05` 2→1 better) and stayed hits — a different trio than
alias-pilot's own (`ep04`, `ep05`, `pp08`), overlapping on two ids,
reciprocal-rank displacement recurring under a different prompt, still
costing nothing.

## Why it failed again: the lexical rank still never moves, even where the wording got closer

| target | note | baseline lexical rank | structural-aliased lexical rank |
|---|---|---:|---:|
| `pp05` | `home-tech-next/_index.md` | >50 | >50 |
| `pp09` | `external/primos/_index.md` | >50 | >50 |
| `pp09` | `external/primos/analysis/_summary.md` | >50 | >50 |
| `pp15` | `PLAN.archive.20260724-loose-ends-os-install-matrix.md` | >50 | >50 |

**Zero movement, identical to the content-only run — but this time the
generated text got demonstrably closer and it still didn't move the rank.**
`pp05`'s structural aliases include *"list of home tech sub-projects"* —
containing "list," a term the gold query (`list pending project ideas house`)
actually uses, unlike anything the content-only prompt produced. It made no
lexical difference: the note is still outside the top 50 of 9,971 documents.
The likely reason, read off the corpus rather than assumed: "list" and
"project(s)" are both common terms across a project-heavy vault, so a thin
one-or-two-term overlap on high-frequency terms doesn't carry enough BM25
weight to outrank the many other documents that also contain them. This
sharpens the earlier diagnosis rather than replacing it: it is not only that
the content-only prompt reached for the wrong vocabulary — even a prompt that
explicitly asks for the right *kind* of vocabulary and partially succeeds
still loses to term-frequency competition in a corpus this size, unless the
overlap is precise enough to include a genuinely distinguishing term.

## What this licenses

**A second clean null, not a second version of the backfill's damage.**
Unchanged R@5 on both arms, no negative changed, no non-target question lost
— exactly like alias-pilot. The scope-and-gate discipline held again.

**The stronger claim, now earned by two independent prompts.** alias-pilot's
own amendment-log entry left open whether the content-only prompt specifically
was the problem. This rung tested that directly, changing only the prompt,
and got the identical clause-(a) outcome. The honest reading is no longer "the
first prompt reached for the wrong words" alone — it is that **prompting
alone, gold-blind, has not found a way to make these three particular notes
lexically competitive for these three particular questions**, across two
independently pre-registered attempts. A third prompt variant is not
foreclosed, but the burden is now on showing what would be different about it,
not merely that it is different.

**Nothing applied to the live vault**, per the design's own rule for a
refuted rung, same as alias-pilot.

Per-question detail, including all 120 generated alias sets under the
structural variant and the four lexical-rank diagnostics, at
`<vault>/Agent/_meta/health/goldv2/alias-pilot-structural-20260816.json`,
never in the repo.

# Path signal — refuted on clause (b): the distinguishing token was already in the body

Run 2026-08-16, prompted by the reciprocal handoff's §1 finding and by the two
alias rungs whose scoping error it explains. The FTS5 table has declared
`docs(path UNINDEXED, title, meta, body)` with `weightPath = 0.0` since it was
written, so the directory a note lives in has never been searchable — and 23
notes in the corpus are `_index.md` or `_summary.md` files whose indexed title
is literally `index` or `summary`, which leaves the strongest column at 4×
carrying nothing for exactly the notes whose subject lives in the folder name.

Four clauses, all registered in the plan before any code: **(a)** non-regression
first, `+question` R@5 ≥ 48/64 and hook ≥ 47/64 with no stratum down more than
one question; **(b)** at least one of `pp09` / `pp17` converts on `+question`;
**(c)** observed movement matches the registered per-question prediction in ≥ 6
of 8 targets, on both arms; **(d)** the 20 negatives' `correct_rejection`
unchanged per id.

## The control is a different thing here, and the difference matters

Every prior rung proved absence by running the new build with its flag off and
diffing row-for-row against the landed column. **That technique does not work
for a schema change.** FTS5's bm25 length normalisation counts tokens across all
indexed columns, so indexing `path` changes every document's length whatever
weight it carries — `weightPath = 0.0` with `path` indexed is not a no-op, and a
flag-off arm of the new build would have read as one.

The control here is a pristine index built by the **unmodified `main` binary at
`26a1499`**, not by the changed build. It reproduces both landed columns
exactly: **0 of 84 rows differ** against `question-20260814.json` and
`hook-e2e-20260814.json`. Its schema reads back as
`fts5(path UNINDEXED, title, meta, body)` and its version as 4, which is the
assertion that it is the shape it claims to be.

## Ops

Fresh `goldv2-20260812` restored to a scratch location distinct from every prior
rung's copy. The corpus is not edited by this rung at all — only the schema is.
Both indexes were built from scratch and asserted: **9,971 docs / 9,473 embedded
notes / 11,761 chunk vectors, identical on both**, so the lexical schema changed
and the dense arm did not, which is the integrity claim the plan asked for.
Embedder EmbeddingGemma-300M-Q8_0 at `-np 1 -c 2048 -b 2048 -ub 2048`, resident
and attached. The changed index reads back as `fts5(path, title, meta, body)` at
schema version 5. Scored twice: **0 of 84 rows differ between the two runs**, on
either arm.

`weightPath = 2.0`, chosen before the run and from the weights already measured
rather than from a sweep. A path is a hand-authored subject signal in the same
family as a title, but it is diluted — six to eight tokens of which one or two
say anything, against a title that is nearly all signal and a `meta` column of
hand-written aliases with no boilerplate at all. So it sits strictly below
`meta` and strictly above `body`, and 2.0 is that interval's midpoint. The arc
has twice refused a constant fitted to these 84 questions, and this is the same
refusal made in advance rather than at review.

## The measured result

| stratum | `+question` control | `+question` + path | hook control | hook + path |
|---|---:|---:|---:|---:|
| distinctive-token | 9/12 | 9/12 | 8/12 | 8/12 |
| episodic-temporal | 9/12 | 9/12 | 8/12 | 8/12 |
| pure-paraphrase | 11/18 | 11/18 | 12/18 | 12/18 |
| research-corpus | 10/12 | 10/12 | 10/12 | 10/12 |
| research-density | 9/10 | 9/10 | 9/10 | 9/10 |
| **R@5** | **75.0%** (48/64) | **75.0%** (48/64) | **73.4%** (47/64) | **73.4%** (47/64) |
| negative rejection | 0/20 | 0/20 | 0/20 | 0/20 |

Not one of the 84 questions changed hit/miss state, on either arm. Five hits
moved rank and stayed hits, the same five on both arms: `ep04` 3→2, `rc08` 4→3,
`rd04` 5→4 and `rd05` 2→1 all better, `rd10` 2→3 worse.

## Clause by clause

**Clause (a) — MET.** R@5 flat on both arms at its floor, every stratum ±0, no
question lost. For a change that touches every document's length normalisation
this is the outcome worth having, and it was the least certain of the four.

**Clause (b) — FAILED, 0 of 2.** Neither `pp09` nor `pp17` converted, on either
arm. This is what refutes the rung.

**Clause (c) — MET at 6 of 8, and the pass is weak in a way worth naming.** Both
CONVERTS predictions failed and all six NOCHANGE predictions held. Clause (c)
was written to catch *unpredicted* movement; nothing moved, so it passes on the
strength of six correct predictions that nothing would happen. **It is not
evidence for the mechanism here, and reading it as such would be the error the
clause exists to prevent.** A successor rung should pair it with a clause that
fails when a predicted movement is absent — currently that is clause (b)'s job
alone, and only for two of the eight targets.

**Clause (d) — MET.** All 20 negatives unchanged, per id.

## Why it failed: the token was already matchable, from the body

The registered prediction required a path token that (i) the indexed title does
not carry, (ii) overlaps the query's extracted terms, and (iii) is rare enough
to earn IDF weight. Re-running `probe_paths.py` against the control index
reproduces that derivation exactly, including `primos` at df 47, `developer` at
576 and `workflows` at 2,850. All three conditions hold.

There was a fourth condition nobody wrote down, and it is false in all eight
cases: **the token also has to not already be matchable from the note's own
body.** The two indexes differ only in whether `path` is indexed, so for any
token, the documents matching it in one index and not the other are exactly the
documents for which the path made it newly matchable. Asked that way, per target
(df measured on the changed index):

| target | overlapping token | df | newly matchable from the path? |
|---|---|---:|---|
| `pp09` | `primos` | 47 | **no — already in the body** |
| `pp17` | `developer` | 580 | **no — already in the body** |
| `pp17` | `workflows` | 2,855 | **no — already in the body** |
| `pp07`, `pp15` | `agentm` | 1,980 | no — already in the body |

The only tokens the path made newly matchable for these notes are structural:
`agent` (97.2% of the corpus), `desk` (20.8%), `projects` (17.1%), `harness`,
`archive`, `crickets`. None of them appears in these queries, and IDF floors the
common ones to nothing in any case. A folder named for its subject holds notes
that talk about that subject, so the distinguishing word is already in the
prose. The path was never the only place it lived.

The lexical-arm ranks say the same thing from the other side: **all eight
targets sit outside the top 50 on both indexes**, unmoved. The change is not
inert — a bare `primos` query moves `external/primos/_index.md` from −10.539 to
−10.694 and lifts `analysis/_summary.md` from 7th to 4th *within the primos
cluster* — but it moves every note in that folder together, and the contest
those notes lose is against documents matching `kept notes`, which the folder
name does not touch.

## What indexing the path actually added, corpus-wide

Measured across all 9,971 documents rather than inferred from the eight:

- **262 distinct directory tokens** in the whole corpus. The directory
  vocabulary is far smaller than the prose vocabulary, which is the first
  quantitative reason to expect little from it.
- **23,238 new (document, token) matchable pairs**, and **9,819 of 9,971
  documents — 98.5% — gained at least one.**
- The top of that list is entirely boilerplate and machine-generated:
  `memory` (7,019 documents), `agent` (6,461), `desk` (2,035), `inbox` (1,054),
  `scratch` (1,052), then timestamp and hash directory names — `20260712`,
  `17f58659`, `015948` — at 635 documents each.
- Of the 23 `_index.md` / `_summary.md` notes this rung was built for, **22
  gained a new matchable token**, and in every target case that token was
  boilerplate rather than the folder's own name.

So the design call was right about IDF and wrong about what would be left over.
The boilerplate did discount itself and cost nothing, exactly as predicted —
clause (a) is the proof of that. What the prediction missed is that once IDF has
correctly discounted the boilerplate, there is nothing else in the path that the
body did not already say.

## Form A tested a compound, disclosed before the run

The `path` column holds the whole relative path, so indexing it also re-carries
every filename-stem token, which makes the effective weight on a title-shaped
match 6.0 rather than 4.0 across the corpus. That was written into the plan's
risks before the run as the first place to look if clause (a) failed. Clause (a)
did not fail, so the compound cost nothing measurable — and the narrower variant
the plan named as its successor (directory segments in a column of their own) is
not worth building, since it would remove a side effect that measured at zero
while leaving the actual cause untouched.

## Verdict, and what does not ship

**Refuted on clause (b).** Reverted rather than quarantined behind a flag: a
column's UNINDEXED-ness is fixed when the virtual table is created, so there is
nothing to hide it behind — the same reason and the same call as the elicitation
rung's revert. Nothing reaches the live binary or the vault.

## What this licenses

**A third clean null in one week, and the three now share a cause.**
`alias-pilot` and `alias-pilot-structural` wrote body vocabulary into notes whose
problem was structural; this rung added structural vocabulary to notes whose
distinguishing word was already in the body. All three assumed a signal was
*missing*. In every case it was **present and outranked**. The handoff's §1 said
so for five of the eight already — `primos` at lexical rank 1, `bm25 k1` at 1,
`embeddings vector db` at 1 — and this rung is what makes that general rather
than anecdotal: adding a token a document already contains does not change which
document wins.

**The next mechanism has to change the competition, not the vocabulary.** Two
candidates remain from the handoff's §5, and this result reorders them.
Chunk-level lexical indexing (item 2) attacks length subsidy, which is the
mechanism by which 22–39KB roadmaps beat 1.4–11KB notes on term-frequency mass:
a competition problem rather than a vocabulary one, and the one a 9,971-note
corpus can measure where a 120-note vault cannot. Deterministic acronym
extraction (item 3) is a vocabulary mechanism, and vocabulary mechanisms are now
0 for 3 here.

Per-question detail for all 84 questions on both arms, the per-target directory
token analysis with document frequencies, and the corpus-wide new-signal
measurement at `<vault>/Agent/_meta/health/goldv2/path-signal-20260816.json`,
never in the repo.

# Chunk-lexical indexing — refuted: the mechanism that helps its targets also

promotes coincidental noise, and clause (a) caught it

Retrieval-competition arc, section 1. The dense arm already chunks (11,761
chunk vectors over 9,473 embedded notes; 564 notes split); the lexical arm
scored whole documents, so a long document's accumulated term-frequency mass
was not fully discounted by FTS5's fixed `b = 0.75` length normalisation.
This rung added a chunk-level lexical arm — a separate FTS5 table, `docs`
untouched — fused into `+question`/hook hybrid search behind a `-chunk-lex`
flag, default off.

## Task 1: the pre-flight probe changed the plan before any Go code existed

The plan's own size-ratio heuristic (a miss whose expected note is short and
whose top-5 contains a document several times its size) produced 9
candidates. Checking each against the actually-scored mechanism (2-term
subset fusion, `-lex3` off) found 5 of the 9 are pure dense-arm displacement
— the winning long document never appears in the lexical-fusion candidate
pool under any subset, at any depth. Chunking the lexical arm cannot touch a
document the lexical arm never finds. **Lexical reachability has to gate the
derivation, not size ratio alone**; a future rung with a similar "long
document displaces short one" shape should check this before counting a
target as its own.

Of the 4 real candidates, the literature-standard aggregation (MaxP — rank by
best chunk score) was directly measured and rejected: on all 4, the long
document's own densest chunk out-scored the short document's, not from many
noisy draws (the cross-encoder's own failure mode, Task 3 above) but because
that one chunk simply contained the winning term pair more times, in less
text. **Best-chunk RANK fused by RRF** was registered instead — the
mechanism that actually helped: two targets' expected notes had a
whole-document rank outside the production `rrfDepth=50` window (76, 86 —
zero fusion contribution today) that compressed to inside it (17, 45) once
ranked chunk-vs-chunk instead of document-vs-document, independent of
whether the specific competing long document still won its own local
comparison (it did, at rank 5 and rank 2).

## A real bug, found and fixed, that turned out not to be the story

Building the arm, `chunks.body` was populated from `ChunkText`'s return
value, which prefixes the note's title onto every chunk — correct for the
dense arm's single embedded string, but stored verbatim in a *lexical* body
column (alongside title in its own column) it double-counted every title
term. Fixed with a new `ChunkBody` helper sharing `ChunkText`'s boundary
math but returning raw body slices; re-verified against the flag-off no-op
control (still 0/84 diff). Fixing it moved the regression from 39/64 to
40/64 on `+question` — real, but not remotely the dominant effect, which
cost a second ~5-minute re-embed cycle to learn.

## The measured result

| stratum | `+question` control | `+question` signal | hook control | hook signal |
|---|---:|---:|---:|---:|
| distinctive-token | 9/12 | 9/12 | 8/12 | 8/12 |
| episodic-temporal | 9/12 | 8/12 | 8/12 | 6/12 |
| pure-paraphrase | 11/18 | 8/18 | 12/18 | 8/18 |
| research-corpus | 10/12 | 8/12 | 10/12 | 8/12 |
| research-density | 9/10 | 7/10 | 9/10 | 7/10 |
| **R@5** | **75.0%** (48/64) | **62.5%** (40/64) | **73.4%** (47/64) | **57.8%** (37/64) |
| negative rejection | 0/20 | 0/20 | 0/20 | 0/20 |

## Why: rank compression cannot tell relevance from coincidence

Traced on a lost hit, `dt12` ("agentm agent right" → `agentm agent right`).
Control correctly ranks the expected note (`agent-m-crickets-branding.md`)
3rd. Signal-on displaces it with a Windows/PowerShell administration note —
"Find Specific Rights Delegations" — that has nothing to do with AgentM. In
Windows-admin vocabulary, "agent" (a delegation agent) and "right" (an
access right) co-occur densely in that note's own short, single, undiluted
chunk, earning it the best bm25 score in the corpus for that pair and,
because it is short, a corpus-wide rank good enough to cross into the fusion
window — the identical mechanism task 1 measured helping its two intended
targets. **Rank compression rewards any locally-dense chunk, whether the
density reflects genuine topical relevance or two moderately common words
landing near each other in an unrelated short note.** Task 1's probe could
not have caught this: it measured only the 4 hand-picked candidates the
mechanism was built to help, never the other 60 already-correct questions —
which is exactly what clause (a)'s non-regression check exists for.

## Clause by clause

**(a) Non-regression — FAILED.** R@5 −12.5pp (`+question`), −15.6pp (hook).
Worst stratum move −3 (pure-paraphrase, question) / −4 (pure-paraphrase,
hook), both past the −1 floor by a wide margin.

**(b) Conversion — FAILED.** Neither `dt07` nor `pp16` (the registered
positive-prediction set) converted, on either arm.

**(c) Prediction, two halves — FAILED.** Positive half 0 of 2 — closes the
rung refuted on this clause alone, per the arc-wide rule that a positive
half of 0 closes refuted regardless of the negative half. Negative half
held 7 of 7 (`dt02`, `pp07`, `pp09`, `pp15`, `ep09`, `rd01`, `rc03`).

**(d) Negatives — MET.** All 20 unchanged, per id, both arms.

**(e) Latency — not measured, moot.** The rule already fails on (a)/(b)/(c);
a latency result cannot change the ship/no-ship decision.

## Verdict

**Refuted, on clauses (a), (b) and (c).** Reverted rather than shipped on
partial credit: both worktree commits (the chunk table, the fusion wiring)
were reset out before any PR opened, so nothing reaches the live binary or
the vault. This is the first rung in the retrieval-competition arc to refute
on a *mechanism* rather than a missing-signal premise — the registered
aggregation does exactly what it was measured to do for its intended
targets, and that effect is inseparable from what it does to everything
else it also touches. A bounded, rank-based aggregation was chosen
specifically to avoid the cross-encoder's known many-noisy-draws failure
mode; it does avoid that failure mode, and trades it for a different one.

**What this licenses.** The design's own "residue is vocabulary-shaped by
construction" framing (step 4's amendment-log entry) is corrected separately
in `wiki/designs/agentm-hybrid-retrieval.md`'s own amendment log — three
vocabulary rungs and now one competition-mechanism rung have each found a
real, specific reason a signal reaches its target but a *different* document
still wins, and none of the four reasons generalise into "add a bounded
rank-based signal and the right document wins." Section 2 of the arc
(off-gold probe set, then Vector-PRF on the dense arm) is unaffected — Vector-
PRF works purely in vector space and shares no mechanism with this rung's
lexical chunk-and-rank approach.

Per-question detail for all 84 questions, both arms, control and signal:
`<vault>/Agent/_meta/health/goldv2/chunk-lexical-20260816.json`, never in the
repo. Full derivation of the aggregation choice and the dt12 trace:
`progress.md`'s 2026-08-16 "task 5" entry.

# Off-gold probe set + Vector-PRF — refuted: pseudo-relevance feedback

amplifies noise on exactly the queries it would need to help

Retrieval-competition arc, section 2. Two deliverables: (1) a durable,
gold-blind off-gold probe set (`~/.agentm/corpus-snapshots/offgold-prf/
scratch-scripts/offgold-probe-set.json`, 14 answerable + 6 negative pairs
drawn from the frozen corpus, mechanically checked against all 84 gold
questions' literal text and expected-answer paths — reusable by sections 3
and 4, not rebuilt); (2) Vector-PRF on the dense arm — Rocchio pseudo-
relevance feedback (`q' = α·q + β·mean(top-k)`, one re-search) behind a new
`Query.PRF` flag, default off.

## Pre-flight probe: clean, but the wrong kind of clean

The off-gold probe set's dense-arm-alone top-3/top-5 clean fraction was
14/14 (100%) — every single probe question ranked its expected note 1st.
This licensed PRF's published Rocchio defaults (`α=1.0`, `β=0.75`, `k=3`)
with no deviation, per the registered decision rule. Recorded honestly at
the time, not just in hindsight: all 14 probe questions were drawn from
research-density-style reference clusters (highly distinctive technical
facts — `ErrEventOverflow`, `SetMaxOpenConns(1)`), the easiest shape for a
dense embedder, mirroring gold's own research-density stratum (historically
9–10/10) rather than its weakest stratum (pure-paraphrase). The probe set
never measured what a dirty top-k looks like, because it never contained a
query whose initial dense rank was mediocre — and the mechanism's actual
failure mode, below, is exactly that case.

## The measured result

| stratum | `+question` control | `+question` signal | hook control | hook signal |
|---|---:|---:|---:|---:|
| distinctive-token | 9/12 | 9/12 | 8/12 | 8/12 |
| episodic-temporal | 9/12 | 9/12 | 8/12 | 8/12 |
| pure-paraphrase | 11/18 | 10/18 | 12/18 | 10/18 |
| research-corpus | 10/12 | 9/12 | 10/12 | 9/12 |
| research-density | 9/10 | 8/10 | 9/10 | 8/10 |
| **R@5** | **75.0%** (48/64) | **70.3%** (45/64) | **73.4%** (47/64) | **67.2%** (43/64) |
| negative rejection | 0/20 | 0/20 | 0/20 | 0/20 |

`+question` lost `pp06`, `pp11`, `rc10`, `rd04` and gained `pp05`; hook lost
the same four and gained nothing. Neither `ep09` nor `rc01` — the two
questions registered before any code as PRF's plausible conversions, chosen
because their dense-arm-alone rank was 4 (real headroom, unlike `dt07`/
`rc03`'s already-optimal rank 1) — converted on either arm.

## Why: feedback amplifies noise when the seed retrieval is already mediocre

Traced `rc10` by hand ("Why does search sometimes hand back something that
has nothing to do with the question?" → the note about search returning
unrelated results). Its dense-arm-alone rank was 12 pre-PRF — outside its
own top-5; the control's fused rank-2 hit came entirely from lexical-arm
agreement, not the dense arm. PRF's own top-3 feed for this query was a
cluster of near-duplicate `_inbox` boilerplate notes
(`never-explains-why.md`, `never-returns-an-empty-result-set.md`/`-3.md`) —
topically unrelated to the actual answer. Mixing the query toward their
mean collapsed the expected note's dense rank from 12 to 2486, and the
lexical arm's own agreement was no longer enough to rescue it in fusion.

This is PRF's well-documented Achilles' heel from the IR literature —
pseudo-relevance feedback amplifies noise exactly when the seed retrieval
is not already good — landing on this corpus in the one shape the pre-
flight probe could not have caught: a probe set built entirely from
easy, rank-1 queries cannot measure what happens when the seed rank is
mediocre, because it never contains a mediocre seed. **A mechanism whose
own safety check is blind to its own failure mode is not a safety check
that passed — it is a safety check that was never exercised.**

## Clause by clause

**(a) Non-regression — FAILED, both arms.** `+question` R@5 45/64 (70.3%),
below the 48/64 floor. Hook 43/64 (67.2%), below the 47/64 floor. Hook's
pure-paraphrase stratum moved −2 (12→10), past the −1 ceiling on its own —
the primary clause fails on both the headline number and the per-stratum
guard.

**(b) Conversion — FAILED.** Neither `ep09` nor `rc01` (the registered
positive-prediction set) converted, on either arm.

**(c) Prediction, two halves — FAILED.** Positive half 0 of 2 — closes the
rung refuted on this clause alone, per the arc-wide rule that a positive
half of 0 closes refuted regardless of the negative half. **This is the
third rung in a row on this exact failure mode** (`path-signal`'s positive
half was 0 of 2; `chunk-lexical`'s was 0 of 2; this section's is 0 of 2) —
a pattern worth naming plainly rather than treating each as an independent
surprise. Negative half also failed independently: 57 of 62 held on
`+question`, 58 of 62 on hook — `pp05`/`pp06`/`pp11`/`rc10`/`rd04` all moved
unpredicted, the collateral-damage shape clause (a) exists to catch.

**(d) Negatives — MET.** All 20 unchanged, per id, both arms.

**(e) Latency — MET.** Signal hook p50 ~122ms / p90 ~130ms / max ~159ms,
comfortably inside the 300ms budget (control hook p50 78ms) — a real but
modest cost from the second vector pass. The only clause this rung passes
cleanly.

## Verdict

**Refuted, on clauses (a), (b) and (c).** Reverted rather than shipped on
partial credit, matching `chunk-lexical`'s own precedent: both worktree
commits (the Go plumbing, the scoring-path wiring) were reset out before
any PR opened, so nothing reaches the live binary. Unlike `chunk-lexical`
— which helped its two intended targets while quietly breaking others —
this rung helped **none** of its intended targets while also breaking
others: a strictly worse failure shape. Three rungs in a row now share the
same positive-half-zero signature, across three structurally different
mechanisms (lexical chunk-and-rank, dense-vector pseudo-relevance
feedback, and — before both — path-token indexing). That repetition is
itself evidence worth carrying into the next section: a registered
prediction that fails to fire three times running says more about how
this arc is deriving its "plausible conversion" targets than about any
one mechanism.

**What this licenses.** The off-gold probe set survives as a durable,
reusable artifact for sections 3 and 4 — its own limitation (only
easy/rank-1 queries) is now a documented, known gap rather than an
unstated assumption, and a future section that wants a genuine clean/dirty
signal should draw its probe questions from a harder stratum shape (closer
to pure-paraphrase's zero-overlap difficulty) before trusting a "clean"
reading. Vector-PRF itself is closed: its published defaults amplify
whatever the seed retrieval already contains, good or bad, and this
corpus's own mediocre-seed queries are exactly where that bites.

Per-question detail for all 84 questions, both arms, control and signal:
`<vault>/Agent/_meta/health/goldv2/offgold-prf-20260816.json`, never in the
repo. Full derivation, the `rc10` trace, and the probe set's own
construction: `progress.md`'s 2026-08-16 task 1–6 entries.

# Embedder-swap probe (section 3) — closed without a run: already answered

`BRIEF-retrieval-competition.md`'s section 3 proposed re-embedding the frozen
corpus with Qwen3-Embedding-0.6B in place of EmbeddingGemma-300M, targeting
`pure-paraphrase` — the stratum reachable only by the dense arm. Read before
any code or re-embed, per this arc's own rule-before-work convention. Two
things surfaced during that reading, both before any Go code or re-embed:

**This exact comparison already ran, at full parity, on 2026-08-12** — before
this arc existed. `wiki/designs/agentm-hybrid-retrieval.md`'s own 2026-08-12
amendment-log entry and this file's "Bake-off: EmbeddingGemma-300M against
Qwen3-Embedding-0.6B" section record it: identical 2,048-token window
(the same constraint section 3's own brief independently arrives at, for the
same Metal-page-fault reason), identical scope, complete backfills, idle
machine. EmbeddingGemma won **every stratum**, 35/64 against 24/64 overall —
and on `pure-paraphrase` specifically, the exact stratum section 3 targets,
EmbeddingGemma scored 7/18 against Qwen's 5/18. Qwen was worse, not better, on
the one stratum this probe existed to fix. The design doc recorded an explicit
re-audit trigger for this decision: *"different GPU hardware or a llama.cpp
release that fixes the fault would make Qwen's window testable again, but its
quality would have to beat 35/64 to matter."* Neither condition has changed —
same Mac, no llama.cpp fix recorded anywhere in this repo's history. The
brief's own framing ("EmbeddingGemma-300M is #2 among sub-1B models") does not
cite or reference this prior result, and the bake-off's own numbers directly
contradict the premise that a stronger sub-1B embedder is untested territory
here.

**A web research pass found no better local-servable candidate either.**
Google's own EmbeddingGemma paper (arXiv:2509.20354) states it is the **#1**
text-only model under 500M parameters on MTEB English v2 at time of
publication — not #2, correcting the brief's framing independently of the
in-repo bake-off. More specifically relevant to this stratum: EmbeddingGemma's
own MTEB task breakdown scores STS at 74.7 against Retrieval at 51.2 — its
best category is exactly the semantic-similarity-under-surface-variation shape
`pure-paraphrase` needs — and a PTEB (paraphrase-robustness) study found it has
the smallest score drop of its size class when evaluated under paraphrasing
versus original phrasing. That is the opposite of "this model is likely weak
on paraphrase." Larger sub-1B candidates checked (Snowflake Arctic-Embed-L-v2.0
at 568M) are reported losing to EmbeddingGemma on benchmark comparison despite
being larger, and any candidate needing a meaningfully larger context window
reopens the Metal page-fault failure mode that already ruled out Qwen3's own
selling point.

## Verdict

**Closed without a run — the mechanism's answer is already on record.**
Neither the in-repo empirical result nor the external literature that would
license spending a re-embed and a scoring pass on this exists; both point the
same direction the 2026-08-12 bake-off already settled. No Go code, no
re-embed, no worktree commit. This is a probe closing on its pre-flight
reading rather than its measured result, which the arc's own brief explicitly
priced as an acceptable outcome for section 3 ("cheap enough that a null is
affordable") — here the null arrived before the cost of a re-embed was paid at
all.

**What this licenses.** `pure-paraphrase`'s residue is not an embedding-model
quality problem on this corpus — the strongest available small local-servable
model is already deployed, and it is comparatively strong on exactly the axis
this stratum needs. The stratum's zero-lexical-overlap-by-construction shape
means what it needs is a query-side bridge, not better embedding geometry:
section 4's mechanism (HyDE — embed an LLM-generated hypothetical answer
instead of the bare question) is the one still-untried mechanism actually
shaped for this gap, and the arc proceeds to it directly rather than running
a fourth rung that would independently re-derive this same negative.
**Re-audit trigger, carried forward from the 2026-08-12 entry, unchanged:**
different GPU hardware, a llama.cpp fix for the Metal page fault, or a new
sub-1B model that demonstrably beats EmbeddingGemma's 35/64 parity result
would make a future embedder-swap probe worth running again.

# HyDE probe (section 4) — refuted on non-regression, but the first rung in

this arc whose positive half is not zero

Retrieval-competition arc, section 4. Generate a Haiku hypothetical
document per question (`q' = ` a declarative, note-shaped passage that
would answer the question, never the question itself) and embed that in
place of the bare question — a query-side bridge for `pure-paraphrase`'s
zero-lexical-overlap gap, the mechanism section 3's own close-out
concluded was needed after ruling out an embedder swap.

## Two instrument bugs, found and fixed before any scoring run

Both surfaced during task 3's hand-read of the generated hypothetical
documents, per this arc's own "check the instrument" discipline.

**Context leakage.** The `claude -p` generation subprocess inherited the
worktree's cwd, so Claude Code auto-loaded this repo's own CLAUDE.md and
AGENTS.md into what was supposed to be a *blind* hypothetical generation —
confirmed directly: an early pass's `pp15`/`pp16`/`pp09` output quoted
`install.sh --hooks`, `/model opusplan`, and specific (wrong) repo
structure verbatim. Fixed by running the subprocess from a neutral cwd
with no CLAUDE.md/AGENTS.md in its parent chain. `--bare` would close the
one remaining gap — the user's *global* `~/.claude/CLAUDE.md`, which is
not cwd-gated — but breaks authentication in this environment (skips
keychain reads, demands an `ANTHROPIC_API_KEY` that was not available) and
was not used. **Residual, documented risk**: {`pp02`, `pp12`, `pp16`,
`pp17`} show direct, specific leakage from the global CLAUDE.md's
worktree/model-routing content; {`pp07`, `pp15`} show softer,
structural overlap. Four of the seven positive-half target questions
carry some degree of this risk.

**Task self-awareness.** The system prompt originally named "HyDE" by
term, and on imperative-phrased entries (`pp03`, `pp04` — "Let's update my
voice...") the model recognized the meta-task and broke character into
clarifying questions instead of generating a passage. Fixed by rewriting
the prompt to never name the task and to explicitly forbid meta-commentary
regardless of the query's grammatical form.

Final generation: 84/84, 0 failures, $0.5995 total (~$0.0048–0.0094/call,
matching the brief's estimate closely once leaked context stopped
inflating input tokens — the first, contaminated pass cost $1.61).

## The measured result

No Go code needed: a substitute gold-set JSON with each entry's
`"question"` field replaced by its cached hypothetical text (id/expected
paths/stratum unchanged) let the existing `retrieval_scorecard.py --mode
hybrid --question` score the signal arm unmodified.

| stratum | control | signal |
|---|---:|---:|
| distinctive-token | 9/12 | 8/12 |
| episodic-temporal | 9/12 | 9/12 |
| pure-paraphrase | 11/18 | 11/18 (same rate, different composition) |
| research-corpus | 10/12 | 8/12 |
| research-density | 9/10 | 9/10 |
| **R@5** | **75.0%** (48/64) | **70.3%** (45/64) |
| negative rejection | 0/20 | 0/20 |

Two signal scoring runs bit-identical (generation is cached, so everything
downstream is deterministic and replayable).

## Per question, by id

**Target set** (all 7 `pure-paraphrase` misses, fusion-friction correction
excludes none — no target sits at dense rank 1): `pp05`, `pp07`, `pp09`,
`pp10`, `pp15`, `pp16`, `pp17`.

**Converted (4 of 7):** `pp05`, `pp09`, `pp10`, `pp15`. **Still miss (3 of
7):** `pp07`, `pp16`, `pp17` — exactly the three heaviest leak-risk
questions from the hand-read above. The mechanism's unleaked signal
converted every clean target it could plausibly help and nothing it
couldn't; the leak, where it existed, did not even help.

**Negative-half violations (11, outside the target set):** lost —
`dt01`, `dt12`, `pp02`, `pp06`, `pp12`, `pp14`, `rc02`, `rc06`, `rd05`
(hit→miss); gained — `dt10`, `rd01` (miss→hit, unpredicted). No shared
cause across the four affected strata (dt, pp, rc, rd) the way `rc10`'s
PRF trace found one for section 2 — collateral damage here looks diffuse
rather than mechanism-specific.

**Against the alias-oracle's own eight** (`pp05`, `pp07`, `pp09`, `pp15`,
`pp16`, `pp17`, `rc01`, `rd01` — NOTES.md § "Alias oracle"): HyDE converts
4 of 8 in measurement (`pp05`, `pp09`, `pp15`, `rd01`), though nothing
ships, so all eight remain unconverted in the live system.

## Clause by clause

**(a) Non-regression — FAILED.** R@5 45/64 (70.3%) < 48/64 floor.
`research-corpus` 10/12→8/12, a −2 drop, exceeds the −1-question ceiling —
fails on both the headline number and the per-stratum guard.

**(b) Conversion — MET.** 4 of 7 target-set questions converted (at least
one required).

**(c) Prediction, positive half — MET, the first in this arc.**
Registered before scoring: at least 2 of 7 convert. Measured: 4 of 7.
`path-signal`, `chunk-lexical`, and `offgold-and-prf` all scored 0 of N on
this clause; this is the first positive half greater than zero.

**(c) Prediction, negative half — FAILED.** Registered: the remaining 57
answerable questions hold. Measured: 11 of 57 changed, 46/57 (80.7%)
held — worse than section 2's own 57/62 (91.9%) held, which that
section's close-out already judged a failure at a *better* hold rate than
this.

**(d) Negatives — MET.** All 20 `correct_rejection` values unchanged, per
id.

**(e) Latency — MOOT.** No hook floor applies; HyDE cannot reach the hook
by the layering rule (deliberate-path only).

## Verdict

**Refuted, on clauses (a) and (c)'s negative half** — despite meeting (b)
and (c)'s positive half for the first time in this arc. This is the
`chunk-lexical` shape, not the `path-signal`/`offgold-and-prf` shape: the
mechanism demonstrably helped its intended targets while breaking others
it was never aimed at, across four strata with no obvious shared cause.
No Go code was touched (confirmed: `git status`/`git diff --stat` on the
worktree both empty) — nothing to revert.

**What this licenses.** `pure-paraphrase`'s residue is reachable by a
query-side bridge in principle — the clean conversions (`pp05`, `pp09`,
`pp10`) are real, unleaked signal, not contamination — but this specific
mechanism's collateral cost (9 regressions across 4 strata) is not paid
for by 3–4 clean gains. A future HyDE-shaped rung that bounds the
mechanism's blast radius (e.g., only substituting the query text when a
cheap pre-check suggests the bare-question dense arm is already weak,
rather than substituting unconditionally for every query) is the
re-audit trigger worth naming, not a re-run of this exact form.
Four sections into this arc (section 3 closed without a run; sections 1,
2, and 4 all refuted on non-regression), the arc-close gate's release
condition — "at least one rung that moves the deterministic number" — has
gone unmet across every section run so far, worth carrying into the
close-out's own gate assessment rather than treated as this section's
surprise alone.

Per-question detail for all 84 questions, both arms:
`<vault>/Agent/_meta/health/goldv2/hyde-20260816.json`, never in the repo.
Full derivation: `_harness/PLAN.md` tasks 1–6 (archived at close-out) and
`progress.md`'s 2026-08-16 HyDE entries.

---

# Outcome-filtered alias generation (section 5) — refuted at the pre-flight
probe, and the sharpest null of the three alias rungs

Retrieval-competition arc, section 5 — the conditional rung, run under explicit
operator approval after sections 1–4 left the alias oracle's eight unconverted
in the live system. Rule pre-registered and **committed before any alias text
existed** (`RULE-alias-outcome-filter.md`, `c70b136`).

## The mechanism

Generate aliases gold-blind, then keep one only if it **demonstrably works**:
with every candidate applied and indexed on a scratch copy, query the lexical
arm with the alias's own text and keep the alias only if its own note comes
back in the top-5. Everything else is dropped.

Structurally different from both prior alias pilots, which applied every
generated alias unfiltered, and deliberately *not* Doc2Query--'s relevance
filter — the SIGIR 2024 reproducibility study found relevance filtering harms
recall-based metrics, and R@5 is recall-shaped. This asks whether the alias
moved retrieval, not whether a model judges it relevant.

Two properties are load-bearing. The filter reads an index carrying **every**
candidate, so an alias must win under the competition it will actually face
rather than in isolation. And it is **lexical-only**, which is what makes the
whole pass cheap: it needs `reindex`, never `embed`.

## The target set is three, and the residue was never reachable

Re-derived against the corpus rather than assumed. The eligible set intersected
with the fixed structural scope reproduces alias-pilot's recorded **120-note
scope exactly** — the cross-check that the derivation is sound — and reaches
only **`pp05`, `pp09`, `pp15`**.

**`pp07`, `pp16`, `pp17`, `rc01`, `rd01` are outside the scope entirely.** The
hard residue this section was briefed to attack is not reachable by this
mechanism at all. The scope was **not** widened to reach it: choosing a
widening after seeing which targets it would recover is the gold-informed back
door alias-pilot's own task 1 declined. Registered up front in the rule, not
discovered afterwards.

## The result

Generation: 120 of 120 aliased, 1m17s. Filter: **451 aliases kept, 101 dropped**
(18%), 119 of 120 notes surviving.

Lexical-arm rank of the expected note for the gold query's own terms, k=50:

| target | note | baseline | filtered |
|---|---|---:|---:|
| `pp05` | `home-tech-next/_index.md` | >50 | >50 |
| `pp09` | `external/primos/_index.md` | >50 | >50 |
| `pp09` | `external/primos/analysis/_summary.md` | >50 | >50 |
| `pp15` | `PLAN.archive.20260724-loose-ends-os-install-matrix.md` | >50 | >50 |

**Zero movement — the same null both prior alias pilots produced.** Per the
pre-registered rule the rung closes **refuted at the probe**, and the full
embed + scoring run was never bought. That is the probe doing its job, not a
truncated experiment.

## The null was proven live before it was believed

A flat reading is exactly what a broken instrument produces, and this project
has been burned by that before, so the aliases were shown to be live in the
index that was measured:

- The alias text is written into the scored corpus copy (frontmatter line 10 of
  `home-tech-next/_index.md`) and **absent from the pristine baseline copy**.
- Querying the scored index with an alias's **own text** returns its own note at
  **rank 1**.

The filter did exactly what it promised. The promise does not transfer.

## `pp09` is the finding, and it is not the earlier diagnosis repeated

The structural variant's recorded diagnosis was that generated aliases lacked a
distinguishing term. That does not apply here: `pp09`'s surviving alias carries
`primos`, a corpus-rare term the gold query itself uses. Traced by hand rather
than assumed:

| query | baseline rank | filtered rank | documents matching |
|---|---:|---:|---:|
| `primos` | **1** | **1** | 47 |
| `who are the primos` (the alias's own text) | 7 | **1** | 427 |
| `kept notes primos` (the gold query) | >50 | >50 | 196 |

The corpus **already ranks the right note first** for the distinguishing term,
with no alias at all. The alias moves its own phrasing 7 → 1, so the mechanism
works. The gold query still fails because fusion's two-term subset carries the
common words (`kept`, `notes`) and dilutes the rare one.

**So the residue here is not vocabulary-shaped at all — it is
query-formulation-shaped.** No alias can help a note the corpus already ranks
first for the term that distinguishes it. This converges with `pp07`'s
independently-diagnosed fusion friction rather than restating the alias
thread's vocabulary story, and it is the strongest reason yet to stop treating
this residue as a vocabulary problem.

## An instrument bug found on the way, worth inheriting

The first reach-derivation reported all eight targets "missing from corpus" —
impossible for a corpus that scores 48/64. `resolve_memory_root()` returns an
explicit argument unchanged, and `agentmd classify --vault <vault-root>` emits
`Agent/`-prefixed paths, which silently breaks `in_pilot_scope`'s `external/`
prefix test and shrinks the scope from 120 to 84. **Both consumers want the
memory root, not the vault root.** The same class of mismatch is pinned by a
test in the filter itself: search results are vault-root-relative while journals
are memory-root-relative, so without an explicit path prefix every alias reads
as a failure and the run produces a silent, total null — indistinguishable from
this rung's real result.

## Ops

Fresh `goldv2-20260812` copy restored to a scratch location distinct from every
prior rung's (the restore script hard-refuses a pre-existing copy, since several
prior copies carry generated aliases). Index built from scratch and asserted
before anything was written: **9,971 docs / 9,473 embedded notes / 11,761 chunk
vectors**, exact. Embedder attached, never spawned — the resident
EmbeddingGemma-300M-Q8_0 at `-np 1 -c 2048 -b 2048 -ub 2048`. Baseline
reproduced row-for-row before generation: **0 of 84 rows differ** on either arm,
`+question` 48/64, hook 47/64, `degraded: []`.

**No Go change, confirmed rather than assumed** — the mechanism is a
corpus-write plus index-rebuild, so there is no `bin-sig`/`bin-main` split and
none of the "binary must match the index it reads" trap the path-signal rung
recorded.

**Code kept, inert.** `alias_pilot.py filter` has no live caller and runs only
when invoked, so nothing ships to the live vault, per the design's rule for a
refuted rung. `call_model`'s neutral-cwd fix is kept on its own merits: it is a
genuine defect, not part of this mechanism — the subprocess inherited the
caller's working directory, so Claude Code auto-loaded this repo's CLAUDE.md and
AGENTS.md into a generation meant to be blind to them. The HyDE probe hit the
same leak and caught it by hand. **Both prior alias pilots predate the fix and
were very likely contaminated the same way** — which does not overturn their
verdicts (leaked repo context would, if anything, have helped, and they scored
0), but is recorded so it is not rediscovered a fourth time.

Per-note detail, including all 120 generated alias sets and every filter
decision, at
`<vault>/Agent/_meta/health/goldv2/alias-outcome-filter-20260816.json`, never in
the repo.

## What this licenses — and the arc-close gate

**Three independently pre-registered alias-generation strategies have now
produced the same null**: content-only prompting, structural/role prompting, and
outcome-filtered generation. The third eliminated the first two's diagnosed
failure mode by construction — every surviving alias provably retrieves its own
note — and the gold-query rank still did not move. The honest reading is no
longer "the prompt reached for the wrong words." It is that **alias generation
cannot reach this residue, because the residue is not a vocabulary gap.**

**All five sections of the retrieval-competition arc are now accounted for** —
section 1 (chunk-lexical) refuted, section 2 (off-gold + Vector-PRF) refuted,
section 3 (embedder swap) closed without a run, section 4 (HyDE) refuted on
collateral damage after the arc's only positive prediction half, and section 5
refuted at its probe. **The arc-close gate's release condition — "at least one
rung that moves the deterministic retrieval-layer number" — has gone unmet
across every section run.** The live R@5 is unchanged at 48/64 (75.0%) on
`+question` and 47/64 (73.4%) on the hook arm, the same figures the arc opened
with.

That is the arc's answer to the question it was built to ask, and it is the
strongest single data point for the gate re-pricing decision the operator
flagged before this section ran: five sections, zero shipped mechanisms, and a
gate still holding for a rung that has not arrived. Re-pricing it against
retirement — accepting the current ceiling rather than paying ~$50.68/3 hours to
confirm a number that has not moved — is now a better-evidenced option than
holding for the next rung. **That remains the operator's call; this rung's job
was to make it an informed one.**

---

# goldv3 changeover (2026-08-17) — decontaminate, relabel, re-baseline

**Fixture versions change here.** `gold-set-v3.json` replaces `gold-set-v2.json`
as the scored fixture; `goldv3-20260817.tar.gz` replaces `goldv2-20260812` as
the corpus snapshot. Neither the daemon, the hook, nor the query paths changed
— this is an instrument fix, not a retrieval change (`_harness/PLAN.md`,
`touches_architecture: false`). **Comparability with every v2 number above is
deliberately broken as of this entry**; nothing below is compared silently
against anything above it.

## Why

`_harness/goldv3-diagnosis.md` (written 2026-08-17, immediately after the
retrieval-competition arc closed above) traced the arc's 16 chronic misses to
four causes: the gold set's own drafting message had been mined into the
corpus as decoy preference notes (contaminating `pp17`, `pp07`, `dt04`); six
questions had defensible-or-better answers the label didn't credit; the hook
arm's denominator counted three questions whose only answers live in
subtrees the hook excludes by policy; and a genuinely hard core of
paraphrase/role-register questions remained, correctly bounded, unchanged.
Operator-approved 2026-08-17 in full. This entry is the arc-close gate's
"instrument is not the bug" evidence — the ladder's live numbers were real,
but part of what they measured was measurement error, not retrieval
failure.

## Ops

Task 1 purged the four decoy notes from the **live** vault (git-recoverable,
commit `1936019`) — truncated fragments of
the operator's 2026-08-07 gold-question-drafting message, mined as
`kind: preferences` on the "explicit always/never directive" heuristic
firing against an enumerated question list. A phrase-level contamination
census (recreated from the diagnosis; the section-5 scratchpad's original
script was gone) confirmed the four are gone and surfaced one further,
unpurged artifact — see "The one estimate that missed, and why" below.

Corpus snapshot cut fresh from the post-purge live vault (`goldv3-20260817.tar.gz`,
same recipe as every prior snapshot: `.md` files only, no `.git`, top-level
`Vault/` prefix). **The corpus grew organically in the five days since
`goldv2-20260812`** — 9,971 → 15,029 files — mostly `memory/_inbox/` (9,415 of
14,529 embedded notes), the miner's own low-confidence workflow/preference
capture that FOLLOWUPS (b) and (c) below already name as a standing defect.
Not curated or filtered for this snapshot; whatever the live vault held
post-purge is what got cut, per the plan's brief.

New integrity triple (the old 9,971/9,473/11,761 does not apply):
**15,029 docmeta rows / 14,529 distinct embedded notes / 17,407 chunk
vectors**, asserted by direct `sqlite3` count before scoring. Embedder
attached, never spawned — the resident EmbeddingGemma-300M-Q8_0 at
`-np 1 -c 2048 -b 2048 -ub 2048`, embed scope `Agent/memory,Agent/desk,Agent/external`
unchanged. `degraded: []` on every run, both arms, both replicates.

## The measured result

| | v2 final (`goldv2-20260812`, 2026-08-16 close) | v3 opening (`goldv3-20260817`) |
|---|---:|---:|
| `+question` R@5 | 48/64 (75.0%) | **50/64 (78.1%)** |
| hook e2e R@5 | 47/64 (73.4%) | **48/61 (78.7%)** — denominator now excludes the 3 `hook_reachable: false` questions (task 3), reported separately below |
| negative rejection | scored 0/20 inline | **reported separately** as `layer: gate-only` (task 3) — still 0/20 at this layer, unchanged in substance, no longer counted inside R@5's sweep |

`+question` scored twice, bit-identical (0 of 84 rows differ). Hook arm scored
twice, bit-identical (0 of 84 rows differ). `hook-excluded (policy)`:
`dt01`/`ep10`/`ep12`, 0/3 hit via the hook (expected — their answers live in
hook-excluded subtrees; all three hit on `+question`, rank 1/2/1, exactly as
Group A predicted).

## Per-question delta, the 8 relabeled/expanded/rewritten entries

The diagnosis predicted near-certain conversion for 5 and probabilistic
conversion for 3. Measured against reality, honestly:

| id | diagnosis prediction | v3 change | measured |
|---|---|---|---|
| `dt07` | near-certain | relabel to the wiki-style lesson | **converted** — rank 1 |
| `pp09` | near-certain | prefix-accept `external/primos/` | **converted** — rank 1 (top hit is `progress.md` under the accepted prefix, not either originally-labeled file — the folder-accept doing exactly the job it was built for) |
| `pp10` | near-certain | expand accept-set (+2 preference notes, +conversation) | **converted** — rank 1, on the added `i-want-this-context-vault-to.md` |
| `ep08` | near-certain | expand accept-set (+2 notes) | **converted** — rank 1, on the added `vault-git-directory-sits-outside-the-drive-sync-set.md` |
| `pp16` | near-certain | expand accept-set (+2 notes), fix 2 typos | **converted** — rank 1, on the added `R06-token-efficiency.md` |
| `ep07` | ~70% | rewrite to "first AgentM **development** arc" | **still miss** — top-1 is `desk/projects/blog/_harness/PLAN.archive.20260627-agentm-arc.md`. The rewrite disambiguated the question's own wording but the blog's archived "Agent M arc" PLAN apparently still shares enough terms (including, plausibly, "development") to keep winning; the collision the diagnosis named is real and the one-word rewrite did not clear it. |
| `pp07` | ~60% | restore truncated tail ("...FRIDAY") | **still miss, and not for the reason estimated** — see below, its own section |
| `pp17` | ~40% | fix `automaticaly` → `automatically` typo | **still miss** — top-1 is `desk/projects/shrimpi/conventions/auto-sync-worktree.md`, an unrelated project's "do this automatically, don't ask first" convention note. Genuine paraphrase/vocabulary overlap, not contamination: the purge worked (the decoy is gone and does not appear anywhere in the top-5 on either arm), the typo fix removed the shared-typo lexical magnet, and the question still loses to a different note on ordinary term overlap. |

**5 of 5 near-certain conversions landed exactly as predicted.** **0 of 3
probabilistic conversions landed** — expected value from the diagnosis's own
70%/60%/40% was ≈1.7; measured was 0. Two of the three (`ep07`, `pp17`) miss
for reasons the diagnosis already named and correctly flagged as uncertain
(a real competing note, not an instrument defect). The third (`pp07`) misses
for a reason the diagnosis did not anticipate at all:

### The one estimate that missed, and why: a zombie decoy

`pp07`'s v3 top-1 is `desk/scratch/inbox-20260813-074616-16856bac/235-inbox_collapse-collapse.proposal.md`
— not one of the four purged decoys, and not in the `_harness/PLAN.md`
family of documents task 1's own contamination census flagged as worth
checking (those documents do not appear in either arm's top-5 anywhere in
this run; the meta-document risk the task-1 progress note raised did not
materialize). This is a **dream/consolidation dedup proposal note** — the
daemon's own machinery, proposing to collapse the two purged
`never-fully-realize-the-vault-vision*.md` decoys as content-identical
duplicates — and its body **quotes the decoy's full text verbatim**,
FRIDAY tail and `actuall` typo included, as a "supporting excerpt." Purging
the two source decoys in task 1 did nothing to this note, because it was
never one of the four named paths: it is a *different* file that happens to
embed the same contaminating text as evidence for a proposal about the
files that got purged. The census in task 1 did in fact surface this exact
path (`pp07 <- ...235-inbox_collapse-collapse.proposal.md`, shared phrase
`'why did agentm never fully realize'`) — correctly left unpurged per the
plan's locked scope ("the four purge paths, exactly... nothing else"), and
now confirmed as the actual, sole reason `pp07` still misses. Filed as a
sixth FOLLOWUP below — this is a defect class the original five did not
name: dream/consolidation proposal notes can outlive and re-contaminate
after their subject decoys are purged.

## What this changes about the estimates

The diagnosis's confidence calibration held for the "near-certain" tier (5/5)
and was optimistic for the "probabilistic" tier (0/3 against an expected
1.7) — not because the mechanisms were wrong (prefix-accept, expand-set, and
typo-fix all worked exactly as designed everywhere they were tried), but
because two of the three probabilistic cases face a real competing note the
diagnosis had already flagged as a live risk, and the third faces a defect
class nobody had found yet.

### Correction (2026-08-17, same day): the net is +5 −3, not +2 clean

This section first read "`+question` R@5 moved 48/64 → 50/64 — the 5
near-certain conversions minus the 3 still-open probabilistic misses, exactly
as the arithmetic requires." **That sentence was wrong, and it hid three
regressions.** The 3 probabilistic misses were already misses in v2, so they
subtract nothing: 48 + 5 = 53 expected against 50 measured. A per-question
diff of the two recorded runs gives the real movement:

- **Gained (5):** `dt07`, `pp09`, `pp10`, `pp16`, `ep08` — the label fixes,
  exactly as predicted.
- **Lost (3):** `pp02` (was rank 3), `pp06` (was rank 5), `ep04` (was rank 3).

None of the three is a mechanism regression — nothing about retrieval changed.
All three were **displaced out of the top-5 by documents that did not exist in
the v2 snapshot**: `pp02` by `research-worktree-pr-loop.md` /
`worktree-native-verdict-draft.md` and two others, `pp06` by
`github-claude-chip-resolved.md`, `ep04` by ordinary shuffling once its
neighbours grew. The corpus grew 9,971 → 15,029 files in the five days between
snapshots, and that growth cost three questions outright.

**The interpretation this forces:** the instrument fix was worth +5; concurrent
corpus growth was worth −3; the +2 headline is the sum of two unrelated
effects, not a clean measurement of the relabel. It also means **the ladder's
absolute number should be expected to drift downward over time as the vault
grows, independent of retrieval quality** — a fixed-corpus rung stays valid
internally (every arm scores the same frozen snapshot), but cross-snapshot
comparisons now carry a growth term that has to be named rather than assumed
away. Any future changeover should publish the gained/lost split, not a net.

The genuinely-hard core the arc bounded
(`rc01`, `rd01`, `rc03`, `pp05`, `pp15`, `dt10`, plus now `ep07`/`pp07`/`pp17`
until their specific defects are addressed) is what a future ranking-side
rung should be judged against — not the 48/64 or 50/64 headline alone.

Per-question detail for all 84 questions, both arms:
`<vault>/Agent/_meta/health/goldv3/question-20260817.json` and
`hook-e2e-20260817.json`, never in the repo. `_harness/goldv3-diagnosis.md` is
executed as of this entry; `_harness/PLAN.md` tasks 1-4 (archived at
close-out).

## Post-baseline reach probe: the residue is ordering, not recall

Run immediately after the baseline, to decide what the next rung should be
rather than assume it. Two measurements, both against the v3 index.

**Rank depth.** Scoring the same `+question` arm at wider `k`:

| k | R@k | |
|---|---:|---|
| 1 | 28/64 | 43.8% |
| 2 | 40/64 | 62.5% |
| 3 | 46/64 | 71.9% |
| 5 | 50/64 | 78.1% |
| 10 | 55/64 | 85.9% |
| 20 | **59/64** | **92.2%** |

**Per-miss reachability.** For each of the 14 `+question` misses, the wanted
note's rank in a k=50 pool: `pp05` 6, `pp06` 6, `ep04` 6, `rc03` 7, `pp02` 8,
`dt10` 11, `pp17` 13, `pp07` 14, `rc01` 27, `ep07` 40, `ep09` 44. Only `pp15`
and `rd01` are absent from the pool entirely. `dt02` is its own case — lexical
rank **1**, yet absent from the hybrid top-50, so fusion is discarding a
rank-1 lexical hit rather than merely diluting it.

**What this settles.** The candidate pool already contains the labeled answer
for 92.2% of answerable questions; 11 of 14 misses are in it. The remaining
loss is overwhelmingly **ordering inside the pool**, not failure to retrieve
into it. Three consequences, each of which redirects work that looked
reasonable an hour earlier:

1. **Another query-side bridge is the wrong rung.** HyDE-shaped mechanisms buy
   pool *entry*, and only `pp15`/`rd01` need that. This retroactively explains
   the HyDE probe's shape — it converted some targets and broke nine others
   because it was paying recall cost to solve a ranking problem.
2. **R@1, not R@5, is where the headroom is.** 22 questions sit at ranks 2–5;
   9 more at 6–20. A perfect reorder over the k=20 pool takes R@1 from 28/64
   to 59/64 — 31 questions, against 9 for R@5.
3. **The remaining gold-label headroom is ~1–2 questions, and further relabel
   is a net negative.** A mislabeled question has the system's defensible
   answer at rank 1 and the gold pointing elsewhere; these have the *labeled*
   answer at rank 6–44, which is the ranking signature, not the labeling one.
   `ep04` is the one genuinely murky case (its top-3 are prose-register polish
   plans, its label a voice convention, and neither is squarely "when the
   cross-model pass was set up"). Left alone deliberately: the goldv3 relabel
   was an operator-approved, evidence-backed list, and extending it by
   accepting whatever the system returns converts the benchmark into a mirror.

**Falsified on the way:** the ranking-penalty FOLLOWUP filed hours earlier
against `status: inbox` / `mining_confidence: LOW` notes. Those are 9,415 of
14,529 embedded notes — 65% of the corpus — but occupy **1.4%** of miss top-5
slots, and machine-generated notes appear at statistically the same rate in
hits (10.0%) as in misses (11.4%), so their presence does not predict
miss-ness. The ranker is already ignoring the miner exhaust. That FOLLOWUP was
filed on the strength of one dramatic case (`pp17`'s decoy) and does not
generalize; it is struck rather than left to look like queued work.
Duplicate-slug results were checked in the same pass and are also a non-issue
(4 wasted slots across 3 questions).

