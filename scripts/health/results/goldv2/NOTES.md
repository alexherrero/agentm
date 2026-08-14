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
