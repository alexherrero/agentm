# RULE — MMR and one-hop spreading activation as a ranking-side rung (task 184)

**Registered 2026-10-04, before any stage code and before any arm runs.
Template: `../RULE-TEMPLATE.md`; contract:
`wiki/reference/Retrieval-Eval-Contract.md`; ladder row 7 of
`wiki/designs/agentm-hybrid-retrieval.md`.**

## Mechanism

Two read-side stages that run after reciprocal-rank fusion and never replace
it. Both act on ranking: neither changes a note, a query term, or the fused
scores the two arms produce.

- **MMR (maximal marginal relevance), flag `-mmr`.** It reorders the fused
  candidates so that a near-duplicate of a note already picked gives way to a
  note that covers something else. Every refuted rung changed a score (the
  cross-encoder floor, floorless rerank), the vocabulary (three alias
  strategies, enrichment, HyDE, vector-PRF) or the term selection (`+lex3`,
  rare-term fusion). MMR changes which candidates fill the five slots.
- **Spreading activation, flag `-spread`.** It follows one hop of the link
  graph out from the top hits and admits linked notes that neither arm
  fetched. It is the first rung to read the link graph, which has been in the
  index since V6-2 and has never fed recall.

The diagnosis it rests on is task 143's online measurement: recall supplies
sufficient context on about a quarter of the turns that needed it, and the
failure is retrieval precision, not attention (design § Online measurement).
REPIN-QUEUE.md's `ep06` is the shape MMR targets: release entity pages for v2,
v3 and v4 crowd one question's top five.

### Fixed parameters (no tuning on the test questions)

Both stages run only in `hybrid` mode with a query vector in hand. A hybrid
search that degraded to the lexical arm runs neither.

**MMR**
- **Pool:** every row of the fused list, after the walls and the in-arm
  demotions (at most 2 × `rrfDepth` = 100 rows).
- **Relevance:** the fused score, min-max normalised over the pool to [0, 1].
  A pool whose scores are all equal reads 1 throughout.
- **Note vector:** the L2-normalised mean of the note's stored chunk vectors
  under the query's embedding model. A note with no vector has no similarity
  term, so it falls back to its relevance alone.
- **Selection:** greedy, picking the row with the highest
  `0.7 · relevance − 0.3 · max cosine to the rows already picked`. The max
  over an empty set is 0. Ties go to the higher fused score, then to the path.
  **λ = 0.7.**
- **Demotion guard:** a row whose penalty names `consolidated`, `superseded`
  or `archived` becomes eligible only once every row above it in the fused
  order has been picked. Diversity can push a demoted note down, but never
  lifts it past a note that outranked it.
- **Output:** the pool in pick order, truncated to `k` as before.

**Spreading activation**
- **Seeds:** the top 5 rows of the fused list.
- **Edges, one hop:** the seed's outbound resolved wikilinks, from the body
  and from the frontmatter (which carries `related:`, `consolidated_into:` and
  `consolidated_from:`), plus inbound links whose source is an entity page
  (`agent/memory/entities/`). Markdown links, other backlinks and second hops
  are not followed. An activated note never seeds, so a link cycle ends after
  one step.
- **Eligibility:** a neighbour that the walls remove (archived or superseded
  unless the query asks for them, staged, `recall_exempt_areas`,
  `always_load_areas`) is dropped before selection. So is a neighbour outside
  the query's date bounds, and so are the seeds themselves.
- **Cap:** at most **3** neighbours per seed: the ones whose note vector is
  nearest the query vector. Notes without a vector rank after those with one,
  by path.
- **Score:** the seed's fused score × **0.5** × the neighbour's own
  multiplier, computed by the function the arms use (class penalty, project
  activity, age, project mismatch). A consolidated neighbour is always
  demoted, never released.
- **Merge:** a neighbour already in the list keeps the better of its two
  scores. The list is then re-sorted by score, with ties broken by path.

**`+both`** runs activation first, then MMR over the expanded pool.

## Arms and instruments

- **Arms:** `baseline` (the shipped search, no stage flag), `+mmr`, `+spread`
  and `+both`. All four arms use one binary and one frozen snapshot (a copy
  of the vault, the index and the access sidecar), so the stage flag is the
  only difference between them.
- **Primary:** `gold-set-v3.json` through `eval_retrieval_shipped.py`, the
  nightly gate's own instrument, in the hook's exact query shape: hybrid mode,
  RRF k = 60, extracted terms for the lexical arm, the question for the dense
  arm, ×2 over-fetch, admissibility, temporal bounds, k = 5. There is no
  re-pin: the arms are compared with each other on the snapshot, never
  against a new gold baseline.
- **Secondary, descriptive only, with no weight in the verdict:** task 143's
  90 judged turns, re-queried in hook shape per arm. For each arm it reports
  how many turns' top five changed against the baseline and how many notes
  entered. The plan's "a note a labeller called needed appears in the top 5"
  cannot be scored, because 143's labels are turn-level (`sufficient` or
  `insufficient`) and none names a note. No LLM judge runs in this rung. If a
  stage ships, the design's at-the-change interleaved judged run is its
  follow-up, at n ≥ 6 per arm with a permutation test.

## Population

Counted in step 2, on the frozen snapshot, before any stage code exists, and
recorded in the table below. These are upper bounds on reach:

- **MMR:** baseline misses whose expected note (or a successor that stands in
  for it) sits in the fused pool, but not in the hook's top five.
- **Spreading activation:** baseline misses whose expected note is one typed
  hop from one of the baseline's top-5 seeds.

**Reach floor: 6**, the instrument's minimum detectable flips. An arm that
reaches fewer than 6 misses cannot clear the bar below. It closes *refuted for
want of reach*, and its stage is not built.

| stage | reachable misses (upper bound) | floor met? |
|---|---|---|
| MMR | *step 2* | |
| spreading activation | *step 2* | |

## The bar

An arm passes only if every clause holds against the baseline on the same
snapshot:

1. **Net gain ≥ +3 questions** at R@5 over the scored questions (the plan's
   floor, from the ladder's `+lex3` precedent).
2. **Power-checked bar:** `flips_for > flips_against` and
   `mcnemar_exact(flips_for, flips_against) < 0.05`. This is the eval's own
   `improved` flag. With N flipped questions the arm must win at least B of
   them. B is 6 of 6, 7 of 7, 8 of 8, 8 of 9, 9 of 10, 10 of 11, 10 of 12,
   11 of 13, 12 of 14 and 12 of 15. **Power check:**
   `coin_pass_probability(6, 6) = 0.0156`, and for every B above it stays at
   or below 0.0195 (for example, `coin_pass_probability(8, 9) = 0.0195` and
   `coin_pass_probability(12, 15) = 0.0176`). All are at or under 0.05. The
   smallest passing net gain is therefore **+6**, so clause 1 never binds. It
   is kept so that the plan's number stays visible.
3. **Strata:** no stratum's hits fall by more than one question.
4. **Hard negatives:** false positives on the 30 negatives, and on the 10
   near-miss negatives counted on their own, do not rise.
5. **Latency:** the arm's p95 wall time for the hook-shaped `agentmd search`
   call stays at or under **250 ms**. That is `DAEMON_BUDGET_MS`; past it the
   hook drops the call and the prompt gets nothing. It is measured warm,
   against the snapshot, over three interleaved passes of the gold questions,
   and p50 and p95 are reported per arm.
6. **Walls:** no arm serves a `recall_exempt_areas` note, and no demoted note
   sits above a row that outranked it in the fused order. Both are unit-tested
   and re-checked in the run.
7. **The per-question diff is published**, with gains and losses by id.

The verdict goes to the operator. A passing stage ships on by default only
when the operator ratifies it. If no arm passes, the refutation joins the
ladder and the flags and stage code come out.

## Positive controls

- **The eval's standing controls:** the schema assertion, the planted canary
  and the score spread. Any one of them aborts the run.
- **Flags-off identity:** the step-4 binary, run with no stage flag,
  reproduces the step-2 baseline question for question, with identical rank
  lists. This catches a stage leaking into the default path.
- **Stage liveness:** each stage arm must change the daemon's returned top 10
  on at least one question. A stage that changes nothing is dead or
  misattached, and its null is not a verdict.
- **Determinism:** every arm runs twice, and the two runs are identical.

## Prediction

**No arm passes**, at about 85% confidence. Twelve read-side rungs have been
refuted, and the instrument's minimum detectable gain is six one-way flips
(+9.4 points).

- **MMR:** between −1 and +2 net. It helps where near-duplicates crowd a
  distinct answer out of the top five (`ep06` is that shape). It costs where
  two acceptable answers are near-duplicates of each other, such as a lesson
  and the card it consolidates.
- **Spreading activation:** between −2 and +1 net. An activated neighbour
  scores at most half its seed's fused score, which for the rank-1 seed is
  about a single-arm rank-1 hit, so most neighbours land below the five
  slots. Hub pages are capped at 3 neighbours, but repo pages link to up to
  171 notes, so a rise in hard-negative false positives is the likeliest way
  it fails.
- **`+both`:** no better than the better single stage.

## Per-question record

*Filled in step 5.*

## Outcome

*Filled in step 6, whatever it says.*
