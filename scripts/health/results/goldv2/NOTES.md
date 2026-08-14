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

| | baseline | max-score fusion, 2-term | lexical-fusion (in-daemon) | +vector RRF | +chunking | +rerank+floor |
|---|---:|---:|---:|---:|---:|---:|
| distinctive-token | 3/12 | 7/12 | 7/12 | 8/12 | 8/12 | 7/12 |
| episodic-temporal | 3/12 | 6/12 | 6/12 | 7/12 | 7/12 | 7/12 |
| pure-paraphrase | 1/18 | 5/18 | 5/18 | 7/18 | 9/18 | 6/18 |
| research-corpus | 0/12 | 6/12 | 6/12 | 7/12 | 6/12 | 3/12 |
| research-density | 0/10 | 3/10 | 3/10 | 6/10 | 6/10 | 2/10 |
| **R@5** | **10.9%** | **42.2%** | **42.2%** | **54.7%** | **56.2%** | **39.1%** |
| **negative rejection** | **35%** | **0%** | **0%** | **0%** | **0%** | **40%** |

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
