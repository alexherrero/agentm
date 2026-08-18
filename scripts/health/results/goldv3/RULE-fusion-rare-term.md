# Pre-registered rule — fusion rare-term selection

**Written and committed before any probe or mechanism code exists.** Governing
design: `wiki/designs/agentm-hybrid-retrieval.md`. Brief:
`_harness/BRIEF-fusion-rare-term.md`. Plan: `_harness/PLAN.md` ("fusion
rare-term selection — make the lexical arm privilege the term that
distinguishes").

## The finding this rests on

Three independently pre-registered alias strategies returned the same null, and
the third's trace is the finding (`goldv2/NOTES.md` § section 5): the corpus
**already ranks the answer note first for the rare, distinguishing term** a
question uses — `primos` alone returns `pp09`'s note at rank 1 — but the gold
query `kept notes primos` reads past 50, because fusion's two-term subsets carry
the common words and the max-score comparison lets a subset built from
`kept`/`notes` outbid the one carrying `primos`. The residue is
query-formulation-shaped rather than vocabulary-shaped, and the design's own
arc-close names a ranking-side rung as what belongs next.

The floorless-rerank refutation narrows it further: reordering the pool with a
cross-encoder cannot fix ordering either (4 of 9 at its probe), so the remaining
lever is making the lexical arm itself prefer the term that distinguishes.

## The mechanism, locked before any code

**Candidate 1 — rare-term single sub-queries.** Alongside the existing two-term
subsets, `fusionRanked` issues a **one-term sub-query for every extracted term
whose document frequency falls below the threshold registered below**. Common
terms stay excluded, which is what the threshold is for: a one-term query on
`notes` is noise. Flag-gated and off by default, exactly as `-lex3` was.

This changes the **set of sub-queries**, never the **scoring rule**.
Max-score-across-subsets beat RRF here by measurement, and that comparison is
left exactly as it is; the new sub-query simply becomes another candidate source
feeding the same max.

**Candidate 2 — rarity-weighted subset scoring — is not this rung.** If
candidate 1 refutes, candidate 2 is a separate rung with its own rule, not a
fallback to reach for inside this one.

## The rarity threshold, fixed here and not revisable

**A term is rare when its document frequency is below 150 documents — under
1.0% of the 15,029-document corpus.**

Derived from the corpus's own document-frequency distribution over the extracted
terms of **all 84 gold queries**, measured before any eligibility or outcome was
computed. The derivation reads question text and the corpus and nothing else: no
`expected` paths, no hit/miss column, no scored artifact. The measured
distribution, over 348 distinct terms:

| statistic | DF | share of corpus |
|---|---:|---:|
| all terms, p25 | 131 | 0.87% |
| all terms, median | 344 | 2.29% |
| all terms, p75 | 787 | 5.24% |
| per-query rarest term, median | 65 | 0.43% |
| per-query rarest term, p75 | 164 | 1.09% |

Why 150, on those grounds alone. It is a round corpus-relative figure (1%)
rather than a constant fitted to anything. It lands essentially on the p25 of
the all-terms distribution (131), so it admits roughly the rarest quartile of
the query vocabulary — the tail, not the body — and it sits at well under half
the median term's 344, so a term below it is genuinely uncommon rather than
merely below average. Against the per-query rarest-term distribution it falls
between the median (65) and p75 (164), which means it fires on 59 of the 84
queries: enough surface for a measurable column, and short of firing on
everything, which would make "rare" mean nothing.

As a confirmatory instrument check and **not** as the reason for the value,
`primos` — the term the whole diagnosis rests on — reads DF 58 (0.39%) and
clears the line with room. So would a threshold of 100, 75, or 60; the value is
not tuned to admit it.

**The threshold may not be revised after eligibility is computed.** Choosing a
widening after seeing which targets it would recover is the gold-informed back
door `alias-pilot`'s own task 1 declined, and it is declined here too. If the
line later looks wrong, that is a new rung with a new rule, not an edit to this
one.

**Transparency without a back door:** the close-out must report every miss's
rarest-term DF, so a reader can see how near each eligibility call ran. A near
miss is a thing to disclose, never grounds for moving the line.

## Eligibility is gold-blind; the probe is the gold-dependent test

These are two different measurements and this rule keeps them apart, because
collapsed into one they cannot fail.

The brief words the target-set derivation as "does the labeled answer win the
lexical arm for its rare term alone," and then words the probe as "does the
labeled answer land in the lexical top-5 for its rare term alone." Those are the
same measurement, and a probe that restates its own selection criterion passes
by construction whatever the corpus does.

**Eligibility** therefore depends only on query text and corpus document
frequency — *would the mechanism fire on this question at all?* — and never
reads a label. A miss is eligible when its rarest extracted term falls below
150. This is also exactly what the mechanism knows in production, where it fires
on a DF threshold and has no idea what the right answer is.

**The probe** then asks the outcome question against the labels, and can
genuinely come back negative.

**Reach floor:** if fewer than **3** of the misses are eligible, the rung closes
**refuted for want of reach** at that task — a mechanism firing on two of
fourteen questions cannot produce a measurable column whatever its accuracy.
`dt02` is out of scope by the plan's own boundary (its dilution traces to
skill-discovery-cache machine exhaust, an indexing-policy question); its DF is
reported, and it is not a target.

## The two pre-flight gates

Both are lexical-only, need neither embedder nor reranker, and run **before any
Go change exists**. Gate B runs only if Gate A passes. Either gate failing
closes the rung refuted at the probe, tasks 5–7 unbought.

**Gate A — isolation.** For each eligible question, query the lexical arm with
its **rarest** term alone at k=50, through the shipped path (`agentmd search
-mode fusion <term>`, which falls back to the single-term ranking by
construction — one term has no two-term subset). **Passes when the labeled
answer lands in the lexical top-5 on strictly more than half the eligible set.**
This is the precision the mechanism proposes to stop wasting; absent it, no
selection change can surface anything.

**Gate B — competition.** Gate A scores the sub-query *in isolation*, but the
mechanism makes it *contend*: its results join the same max-score pool the
two-term subsets feed, and a rare-term hit that wins alone can still lose to a
common-word subset's own BM25 score for a different document. A candidate
measured only in isolation over-claims in exactly the way this project has been
burned by before.

Gate B simulates the shipped competition without buying the implementation: take
the existing two-term fusion result for the gold query with per-path scores
(`-json` emits `Score`), take **every** sub-threshold term's one-term result
with scores — all of them, matching what the mechanism actually issues, not just
the rarest — merge by max score per path, and re-rank. The penalty is a
per-document constant applied after the max, so merging post-penalty scores is
equivalent to the shipped arithmetic; that is `fusionRanked`'s own argument for
itself. **Passes when the labeled answer reaches the merged lexical top-5 on
strictly more than half the eligible set — the same denominator as Gate A**, so
B cannot be flattered by A's filtering.

Note the deliberate asymmetry: Gate A tests only the single strongest candidate
(the rarest term), while Gate B simulates every sub-threshold sub-query the
mechanism will really issue. Gate A is thus the conservative reading and Gate B
the faithful one.

**Instrument-liveness check, required before either verdict is trusted:**
confirm `which agentmd` resolves to the scratch binary, the docmeta count still
reads 15,029, and scores differ across documents within a single result set. A
flat or empty read is what a misbuilt index or a stale `PATH` produces, not a
verdict — the chunk-lexical rung lost a run to exactly that.

**What a Gate B failure would mean, registered now so it cannot be reframed
later:** Gate A passing and Gate B failing says the corpus holds the precision
the diagnosis claimed and fusion's max-score comparison eats it anyway. That
would make candidate 2 (rarity-weighted subset scoring) the honest next rung
rather than a null, and the close-out must say so.

## The five clauses

Scored only if both gates pass and the mechanism is built.

**(a) Conversion.** At least half the eligible set converts to top-5 on the
shipped `+question` arm.

**(b) Non-regression.** `+question` R@5 ≥ 50/64 and hook ≥ 48/61; no stratum
drops by more than one question. `+lex3` is the cautionary precedent — it
converted 3 and displaced 2 to reciprocal-rank churn, and its net +1 against a
required +3 is what refuted it.

**(c) Negatives.** All 20 `correct_rejection` values unchanged, per id. This
rung adds candidates; it must not become a filter.

**(d) Per-question diff.** Published by id, **gained and lost as two lists,
never a net.** The goldv3 changeover's own correction — a reported +2 that hid a
+5/−3 split — is why this clause is explicit.

**(e) Latency.** Hook p50/p90 under 300ms through the installed hook, measured
rather than assumed. Fusion measured cheaper than the AND baseline and the extra
sub-queries should be cheap, but the DF lookup is itself part of the mechanism's
cost and is measured with it.

**Ship only if all five meet their bar.** On any failure the mechanism code is
kept inert behind its default-off flag, exactly as the outcome filter was kept
in section 5.

## Ops constraints this rung runs under

- Fixture frozen at `goldv3-20260817` / `gold-set-v3.json`. No relabeling.
- Integrity triple by direct `sqlite3` count on every run: **15,029** docmeta /
  **14,529** embedded notes / **17,407** chunk vectors. The goldv2 triple does
  not apply.
- Scratch restored to a location no prior rung has used
  (`~/.agentm/corpus-snapshots/fusion-rare-term/`).
- Embedder attached at `127.0.0.1:8901`, never spawned. `degraded: []` on every
  scoring run — checked per row, which is where the field lives.
- `bin-sig`/`bin-main` split is live because this rung changes Go code: the
  control binary is built from untouched HEAD and copied aside **before** the
  first edit exists, and flag-off must be proven byte-identical against it
  before any flag-on scoring call.
- `PATH` pinned to the scratch binary with `which agentmd` confirmed before
  every scoring call and the docmeta count re-asserted after.
