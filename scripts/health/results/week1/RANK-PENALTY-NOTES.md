# Week 1 follow-up — does rank-penalizing miner fragments improve retrieval?

Run 2026-08-07. Arm A only, Opus driver only, 60 questions, 6-call budget, the
same gold set and the same integrity checks as the 2026-08-06 run. All 17 runs
report zero hook violations, zero tool escapes, zero call-count mismatches, and
zero budget leaks.

The question: `agentm-rescope-memory.md` calls for demoting miner fragments, and
nobody had measured whether that helps before the Go daemon bakes a penalty into
its index.

**Answer: yes, by +3.75 points of R@5 (p = 0.0195). Implement it.** The exact
shape to implement is in "What the daemon should do" below, and it is simpler
than the design assumes — the penalty's strength turns out not to matter at all.

Two findings landed alongside it that matter at least as much: the tokenizer and
column-weighting knobs the brief asked to try were already shipped, and the
largest defect in this retrieval surface is not ranking at all.

---

## What was built

`week1_corpus.py` grew three orthogonal knobs, each switchable alone so a score
change is attributable to one of them:

- **A rank penalty** — three classes (`fragment`, `status`, `staging`),
  multiplicative on the BM25 score over a 200-row over-fetch window. It demotes
  and never excludes: a penalized note that is the best thing the corpus has
  still comes back first, and the CLI refuses a weight of 0 outright.
- **Four lexical variants** — `baseline` (what 2026-08-06 ran), `fields` (a
  separate `aliases`+`tags` column weighted above body), `plain` (no porter
  stemming), `flat` (no title weight).
- **A query mode** — `as-is` (what 2026-08-06 ran) and `or`.

`week1_rank_replay.py` replays a finished run's recorded queries against any
configuration. Deterministic and free, so a 125-point parameter sweep costs 19
seconds instead of $500. It measures the *tool* rather than the answer, which is
both its value and its ceiling: it holds the agent's queries fixed, so it cannot
see how a changed result set changes the next query.

## The corpus

8,700 notes at snapshot time. 84.3% fall into at least one penalty class:
`fragment` 72.1%, `status` 52.2%, `staging` 12.0%.

**Zero of the gold set's 64 target notes carry any class.** Worth stating
plainly: this gold set cannot detect the penalty's main risk, that a demoted
note was the right answer. Every right answer here sits in the promoted 15.7% by
construction, so the measurement below is the benefit side only.

## Result 1 — the penalty works, at +3.75 points

Six control replicates and six penalty replicates against a frozen snapshot of
the corpus, so the only variable is the driver:

| arm | n | mean R@5 | sd | range |
| --- | ---: | ---: | ---: | --- |
| control | 6 | 0.6162 | 0.0085 | 0.603 – 0.628 |
| penalty | 6 | 0.6537 | 0.0340 | 0.617 – 0.706 |

Delta **+0.0375 R@5**, or +2.3 questions of 60. Exact permutation test over all
924 rearrangements: **p = 0.0195**. Welch t = 2.63, df = 5.6.

Per stratum, mean across the six runs of each arm:

| stratum | control | penalty | delta |
| --- | ---: | ---: | ---: |
| negative | 0.500 | 0.625 | **+0.125** |
| episodic-temporal | 0.421 | 0.537 | **+0.116** |
| pure-paraphrase | 0.440 | 0.488 | +0.048 |
| research-density | 0.967 | 0.933 | −0.033 |
| distinctive-token | 0.861 | 0.806 | −0.056 |

The gain is concentrated where the mechanism predicts it. Fragments are
paraphrases of the operator's own speech, so they compete hardest on questions
asked in the operator's words rather than the note's — episodic and paraphrase.
The negative-stratum gain has its own coherent mechanism: demoting fragments
removes the plausible-looking junk that was talking the agent out of concluding
that no memory exists.

The two small negatives are worth watching rather than explaining away. On
distinctive-token and research-density the baseline already scores 0.86 and 0.97,
and reordering a result set that was already right can only cost.

The mechanism is visible directly. Share of served top-5 rows that were flagged:

| arm | junk in the reading surface |
| --- | --- |
| control (6 runs) | 4.2% – 6.3%, mean 5.1% |
| penalty (6 runs) | 0.0% – 0.8%, mean 0.25% |

## Result 2 — the penalty's strength does not matter, only its existence

A 125-point sweep over per-class weights in [0.02, 1.0] produced exactly **four
distinct outcomes**, and every setting with a fragment weight at or below 0.6
gave *identical* rankings:

| configuration | tool hit@5 |
| --- | ---: |
| no class penalized | 0.788 |
| any one class penalized | 0.808 |
| any two or three penalized | 0.827 |

Weight 0.6 and weight 0.02 are the same ranking. This is a real engineering
result for the daemon: no tuning knob, no config surface, no per-class
calibration. Pick a constant and move on.

## Result 3 — P@5 on this gold set is recall wearing a different hat

`score_at_k` computes `P@5 = hits/5` and `R@5 = hits/len(expected)`. For a fixed
question those differ by a constant, and across all 52 non-negative questions
there are **zero** divergences from `P@5 = R@5 × len(expected)/5`.

So the 0.144 paraphrase P@5 in the design's Outcome section is not evidence of
junk padding the answer. A question with one gold note scores P@5 = 0.200 when
the answer is perfect, and research-density sits exactly at that ceiling. The
agent also answers with a mean of 2.6 paths, not 5. **No ranking change can move
P@5 except by moving recall**, which is why every number here is R@5.

## Result 4 — the tokenizer and column-weighting variants were already shipped

Both knobs the brief asked to try were in the 2026-08-06 baseline already:
`tokenize='porter unicode61'` and bm25 weights `(0.0, 4.0, 1.0)`. They were
measured by ablation instead, on the replay harness:

| variant | tool hit@5 | hit@1 | MRR |
| --- | ---: | ---: | ---: |
| `baseline` — porter + 4x title | 0.788 | 0.538 | 0.638 |
| `plain` — porter removed | 0.731 | 0.558 | 0.627 |
| `flat` — title weight removed | 0.788 | 0.500 | 0.615 |
| `fields` — aliases/tags column added | 0.788 | 0.577 | 0.655 |

Porter stemming is worth +5.7 points of hit@5 and is the only knob that moves
hit@5 at all. The 4x title weight is worth +3.8 hit@1 and moves hit@5 not at all.
Both already earn their place; neither is a lever left unpulled.

`fields` is identical to `baseline` at hit@5 in **every stratum** — expected,
since only 56 notes carry `aliases` and 472 carry non-empty `tags`, so 5.5% of
the corpus has anything in that column. It becomes measurable after dreaming's
alias backfill and not before. Its one live run nonetheless swung the episodic
stratum +36 points, which is the clearest available illustration of why the
single-run comparisons in the live batch cannot be read as effects.

## Result 5 — the larger defect is the query semantics, not the ranking

FTS5's bare `docs MATCH 'a b c'` is an implicit AND across every term. A
six-word paraphrase therefore has to find a note containing all six words.

On the 2026-08-06 Opus run that returned **zero results for 32 of 206 queries**
and fewer than five for 62 of them. Ranking cannot rescue an empty result set.

OR-joining the same queries, phrases preserved, empties none of them and
surfaces the gold note in 35 (query, question) pairs where AND did not:

| | AND (as shipped) | OR |
| --- | ---: | ---: |
| no penalty | 0.788 | 0.865 |
| penalty | 0.827 | **0.904** |

The two effects are additive and do not interfere. The penalty matters *more*
under OR — widening the net doubles the junk share, 5.9% to 11.0%, and the
penalty absorbs it back to 0.5%. Live, `or` also cut the agent's tool calls 16%
(3.47 to 2.93), because a query that returns something does not need a retry.

**This is tool-level evidence plus one live run, not the replicated design the
penalty got.** The single live `or` run scored 0.694 against a 0.614 control,
but a single run is exactly what Result 4 shows cannot be trusted. Measure it
the same way before shipping it: six replicates per arm on a frozen corpus.

## Result 6 — the six shared misses are vocabulary failures, not crowding

None of `dt12`, `ep09`, `ep12`, `ng02`, `pp05`, `pp07` were fixed by any
configuration, and they flip between hit and miss across replicates of the same
configuration. Tracing the gold note's depth for every query the agent wrote:

| question | best rank the gold note reached, across all its queries |
| --- | --- |
| `pp05` | never appears; four of five queries returned zero results |
| `ep12` | never appears in any of five queries |
| `dt12` | rank 42, on one query out of three |
| `pp07` | rank 50, on one query out of six |
| `ep09` | rank 238, on one query out of six |

Three never surface the note at any depth. The other two sit behind 40–240
legitimately-matching documents, not behind fragments — no demotion reaches that
far. These are cases where the note never contained the words the operator would
later ask with, which is the gap `aliases` exists to close. They will not move
until the alias backfill runs.

## What the daemon should do

**Implement the penalty.** +3.75 points of R@5 at p = 0.0195, for what is
roughly twenty lines of Go.

1. **Any constant works.** Use 0.3 for shape classes and 0.6 for status. Do not
   build a tuning knob — every weight at or below 0.6 produces identical
   rankings, so a configuration surface here is a surface with nothing behind it.
2. **Multiply, over an over-fetch window.** Fetch ~200 rows, multiply each score
   by the product of its classes' weights, re-sort, take the top k. Re-ranking
   only the top k cannot promote the note the fragments were hiding, which is
   the entire purpose.
3. **Gate the shape rule on status.** 232 of the 234 notes in
   `personal/preferences/` are `status: active` *and* fragment-shaped, because
   the promotion pipeline promoted them verbatim. Sparing every promoted note
   from the shape rule costs 0.000 hit@5 and 0.001 MRR and protects 1,288 notes,
   including all of those. Filing is the signal that overrides the miner's
   fingerprint.
4. **Detect fragments three ways.** No single signal covers the population: body
   opens with a miner lead-in (`User stated:` / `Fix observed:` / `User corrected
   the agent:`) catches 3,413; `mining_confidence` frontmatter catches 5,741;
   a mid-word slug under a miner-filled directory catches the rest. The
   directories contribute 32 files and are a rounding error — implement them for
   completeness, not for effect.
5. **Never exclude.** Unchanged from the design, and the tests pin it: a
   penalized note that is the only match still comes back first.

**Fix the query semantics too, after measuring it properly.** OR-joining with
phrases preserved is worth roughly twice what the penalty is worth at the tool
level, costs nothing, and removes a failure mode — the empty result set — that
no amount of ranking work can reach.

## What this measurement cannot tell you

- **The gold set is blind to the penalty's cost side.** Zero of 64 targets are in
  a penalized class. A gold set containing a genuinely useful `unfiled` note
  would measure what demotion costs; this one measures only what it buys.
- **Single runs cannot be read as effects.** Six same-config replicates spread
  2.5 points, and a variant with provably zero retrieval effect (`fields`) swung
  one stratum 36 points in a single run. The five live variant runs in this
  directory are each n=1 and should be read as illustrations, not results.
- **Cross-day comparisons on this harness are unreliable.** The 2026-08-06 run
  scored 0.725 against today's 0.616 for the same configuration — a 10.9-point
  gap that is far outside the 2.5-point same-day replicate spread and was not
  isolated here.
- **The vault moves while you measure it.** The corpus went 8,599 → 8,687 →
  8,700 → 8,709 during this work, and the newest files are
  `personal/_inbox/workflow-bash-382.md` and siblings — the session's own memory
  hooks depositing miner fragments into the class under test. Freeze the corpus
  before measuring; `--vault-path <snapshot>` is how the replicates did it.

## Reproducing

```bash
# offline, deterministic, free
python3 scripts/health/week1_rank_replay.py \
    --report scripts/health/results/week1/opus-arm-a.json --penalty default
python3 scripts/health/week1_rank_replay.py \
    --report scripts/health/results/week1/opus-arm-a.json --all-variants
python3 scripts/health/week1_rank_replay.py \
    --report scripts/health/results/week1/opus-arm-a.json --sweep

# live, ~$4 and ~21 minutes per run. Snapshot the vault first and point
# --vault-path at the snapshot, or the corpus moves under the comparison.
python3 scripts/health/week1_retrieval_experiment.py \
    --gold-set scripts/health/fixtures/week1-gold/gold-set.json --arm A \
    --driver claude --model opus --penalty default \
    --vault-path <snapshot> --out scripts/health/results/week1/<name>.json

python3 scripts/health/week1_compare_runs.py \
    --baseline scripts/health/results/week1/frozen-control-r1.json \
    --vault-path <snapshot> scripts/health/results/week1/frozen-*.json
```

## The scorecards

| file | configuration | n | corpus |
| --- | --- | ---: | ---: |
| `opus-arm-{a,b}.json`, `fable-arm-{a,b}.json` | 2026-08-06 originals | 1 each | 8,599 |
| `opus-arm-a-control.json` | baseline, live vault | 1 | 8,687 |
| `opus-arm-a-penalty.json` | + penalty | 1 | 8,700 |
| `opus-arm-a-fields.json` | `fields` variant | 1 | 8,700 |
| `opus-arm-a-or.json` | + OR query mode | 1 | 8,700 |
| `opus-arm-a-or-penalty.json` | + OR + penalty | 1 | 8,700 |
| `frozen-control-r1…r6.json` | baseline, frozen snapshot | 6 | 8,700 |
| `frozen-penalty-r1…r6.json` | + penalty, frozen snapshot | 6 | 8,700 |

The frozen pair is the result. The five live single runs are the exploration
that led to it, kept because their `tool_call_log`s are what the replay harness
reads.

This follow-up cost $72.10 across 17 live runs.
