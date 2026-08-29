# goldv3 results — the v3 fixture's own results home

`gold-set-v3.json` / `goldv3-20260817.tar.gz` results land here, not
accreted onto `goldv2/NOTES.md`. `goldv2/NOTES.md` is a closed historical
record as of the goldv3 changeover entry (2026-08-17) — see its own
"goldv3 changeover" and "Post-baseline reach probe" sections for the
opening baseline this fixture starts from: `+question` 50/64 (78.1%), hook
e2e 48/61 (78.7%), integrity triple 15,029 docmeta / 14,529 embedded notes
/ 17,407 chunk vectors.

Entries below are appended per landed rung, oldest first, same shape as
`goldv2/NOTES.md`: what ran, the measured result, and the verdict against
its own pre-registered rule.

# Floorless rerank — refuted at the pre-flight probe

Reorder the question-shaped pool. Rule pre-registered and **committed
before any probe or rerank code existed** (`RULE-floorless-rerank.md`).

## The mechanism

Re-order the existing hybrid candidate pool (k=20) with the cross-encoder
before truncating to the caller's k — **no floor, no filter, no dropping**,
just a permutation of the pool it was handed. Motivated by the post-baseline
reach probe (`goldv2/NOTES.md` § "Post-baseline reach probe"): 11 of the 14
`+question` misses already sit in the k=50 pool, most at rank 6–14, so the
residue looked like ordering rather than recall.

Structurally different from step 3 (`+rerank+floor`, refuted — dropped
below a floor) and the answerhood labeller (refuted — dropped
non-`answers`): reordering cannot evict a candidate already reachable, so
no currently-reachable question could become unreachable by this
mechanism, whatever its accuracy.

## The pre-flight probe

Before buying a full implementation + scoring run, the rule required a
cheaper measurement first: for the 9 `+question` misses whose labeled
answer sits at k ≤ 20, score the labeled answer and the question's current
top-5 occupants with the shipped cross-encoder (jina, the step-3 bake-off
winner), and check whether the labeled answer outscores a majority of its
own pool on ≥5 of 9 questions.

Ranks re-derived fresh against this rung's own scratch index before
spending a single rerank call, and reproduced exactly: `pp05` 6, `pp06` 6,
`ep04` 6, `rc03` 7, `pp02` 8, `dt10` 11, `pp17` 13, `pp07` 14, `rc01` 27.

## The result

| id | query | labeled answer beats N of 5 occupants | majority? |
|---|---|---:|---|
| `pp05` | list pending project ideas house | 5/5 | yes |
| `pp06` | broke sherwood cuased decide move | 5/5 | yes |
| `ep04` | setup cross model pass prose | 5/5 | yes |
| `dt10` | coord through wave completely | 3/5 | yes |
| `rc03` | stops system handing model everything remembers | 1/5 | no |
| `pp02` | store worktree rules change depending agentm | 2/5 | no |
| `pp17` | developer workflows don automatically | 1/5 | no |
| `pp07` | agentm never fully realize vault vision | 2/5 | no |
| `rc01` | outside project decided against embeddings way | 0/5 | no |

**4 of 9 pass, the rule required ≥5. Probe fails; the rung closes refuted
at the probe.** The implementation (task 4), the full scoring run (task
5), and the five-clause evaluation (task 6, as originally scoped) are not
bought — the probe exists precisely so a flat mechanism is not paid for at
full price. Full raw scores (sigmoid + raw logit per candidate) archived at
`<vault>`-adjacent scratch, not the repo, per this rung's own convention;
the table above is the complete pass/fail record.

## The instrument was proven live before the null was believed

Two unrelated one-sentence strings scored against a fixed query returned
**-3.1409 and -3.7396** — different, as required. Every per-question score
set above shows genuine spread across documents (see e.g. `pp07`'s
occupant range from -0.97 to +2.17) — a flat read is what a dead or
misattached reranker produces, and this is not that.

## What this settles

The rule's own framing distinguished two properties before either was
measured: step 3's refutation (0.003–0.959 against 0.267–0.906) is about
**cross-question score comparability** — whether one threshold works
across questions, which a floor needs. This probe measured
**within-question ordering** — whether the labeled answer's own score
beats its own pool's competitors, one question at a time — a strictly
weaker property step 3 never touched.

It fails too, on 5 of 9 questions. `pp05`/`pp06`/`ep04` are a clean
positive signal (perfect 5/5, the reorder ceiling's easiest cases) and
`dt10` clears the bar narrowly, but the failures are not marginal:
`rc01`'s labeled answer is beaten by every one of its 5 occupants, and
`pp07`'s occupant — a dream/consolidation dedup-proposal note quoting a
purged decoy's own contaminating text verbatim (the sixth FOLLOWUP the
goldv3 changeover entry filed) — outscores the labeled answer by nearly
2.5 raw points, the single largest margin in either direction across all
54 scored pairs.

**"Similarity is not answerhood" now holds for ordering as well as
thresholding.** Not a re-discovery of step 3's finding and not a
contradiction of it — a deeper confirmation, at the one property this
rung's mechanism actually needed and step 3 never tested. See the
`agentm-hybrid-retrieval.md` amendment log for the design-level statement.

---

# Fusion rare-term selection — refuted at the pre-flight probe, and the
diagnosis it rested on does not survive contact with its own population

Make the lexical arm privilege the term that distinguishes. Rule pre-registered
and **committed before any probe or mechanism code existed**
(`RULE-fusion-rare-term.md`, `35d60f2`).

## The mechanism

Alongside fusion's existing two-term subsets, issue a **one-term sub-query for
every extracted term whose document frequency falls below a registered
threshold**. Motivated by section 5's `primos` trace: the corpus returns
`pp09`'s note at rank 1 for `primos` alone, while the gold query `kept notes
primos` reads past 50, because the two-term subsets carry the common words and
the max-score comparison lets a `kept`/`notes` subset outbid the one carrying
`primos`. The precision is already in the corpus; today it is thrown away
because no one-term sub-query exists.

This changes the *set of sub-queries*, never the *scoring rule* —
max-score-across-subsets beat RRF here by measurement and was left untouched.
Lexical-only: no embedder, no reranker, no model of any kind.

## Two corrections the plan made to its own brief, before measuring

**Eligibility was split from the probe.** The brief derived the target set by
"does the labeled answer win the lexical arm for its rare term alone," then
gated on "does the labeled answer land in the lexical top-5 for its rare term
alone" — the same measurement twice, a check that cannot fail. Eligibility was
re-defined to read only query text and corpus document frequency (*would the
mechanism fire at all?*), which is also all the mechanism knows in production.
The probe then asks the outcome question against the labels, and can come back
negative — which it did.

**A second gate was registered for the competition.** Scoring a sub-query in
isolation over-claims when the mechanism makes it contend; Gate B was to merge
every sub-threshold sub-query into the two-term result by max score and re-rank.
It was never reached.

## The threshold, fixed outcome-blind

**DF < 150 documents — under 1.0% of the 15,029-document corpus.** Derived from
the corpus's own DF distribution over the extracted terms of all 84 gold
queries, by a script reading question text and corpus only: no `expected` paths,
no hit/miss column, no scored artifact, and no per-question output, so the value
could not be chosen for what it recovers. Over 348 distinct terms: all-terms p25
131 (0.87%), median 344 (2.29%); per-query rarest-term median 65 (0.43%), p75
164 (1.09%). 150 sits essentially on the all-terms p25, at well under half the
median term, and fires on 59 of 84 queries.

`pp05` missed the line by **one document** (`house`, DF 151). Disclosed as the
rule requires; the line was not moved.

## Eligibility, and the first surprise

Five of the fourteen `+question` misses are eligible: `dt10`, `pp06`, `pp07`,
`rd01`, `rc03`. Reach gate required ≥3.

**`rc01` — one of the brief's two named live candidates — never reached the
probe.** Its rarest extracted term is `outside` at DF 254, well above the line.
Of the `pp09`/`dt10`/`rc01` "family" the FOLLOWUP named, only `dt10` survives a
threshold registered before anyone looked.

## The result

**Gate A: 1 of 5, the rule required ≥3. Refuted at the probe.** Gate B was not
run — it is pre-registered to run only if Gate A passes, and running it anyway
would be fishing for a second opinion the rule did not buy. Tasks 5–7 as
originally scoped were not bought; no Go code was written.

| id | rarest term | DF | rank of labeled answer, term alone, k=50 |
|---|---|---:|---|
| `dt10` | `coord` | 18 | **1** — pass |
| `pp06` | `cuased` | 1 | not in top 50 |
| `pp07` | `vision` | 85 | not in top 50 |
| `rd01` | `ranker` | 3 | not in top 50 |
| `rc03` | `remembers` | 146 | not in top 50 |

## The probe was broken first, and that is why the null is believable

The first run reported a clean **0 of 5, every answer "not in top 50."** It was
reading `expected` / `expected_prefixes` — the field names the scorecard's
*output rows* use — from the *gold set*, where they are `expected_note_paths` /
`expected_note_prefixes` (`retrieval_scorecard.py:205,210`). Every comparison
ran against an empty list. **This is the same instrument bug section 5 recorded
for inheritance, reproduced on the first attempt**: a silent, total null
indistinguishable from a real refutation.

It was caught because a uniform zero is what a broken probe looks like. The fix
added the **positive control** the rule should have demanded up front: `pp09`,
the diagnosis's own worked case, whose answer the section-5 trace records at
rank 1 for `primos` alone. The corrected probe reproduces that rank 1 exactly,
and now refuses to report at all if the control does not reproduce. Two
independent proofs it can see a positive — the `pp09` control at rank 1 and
`dt10` at rank 1 — plus real score spread (`primos` returns 50 rows with 50
distinct scores, 3.39–10.73).

## Why the four failed, which is the actual finding

Traced per question rather than assumed. For `pp06`, `pp07`, `rd01` and `rc03`
the rare term **does not match the labeled answer note at all** — not "matches
and ranks low," but absent from the term's entire FTS match set (1, 85, 3 and
146 documents respectively, none of them the answer). No sub-query on a term can
surface a document that term does not retrieve.

`dt10` is the single case behaving exactly as the thesis predicts: `coord`
matches 18 documents, the answer is among them and ranks **1** in the term's own
ranking, while the two-term gold query buries it at rank **20**. That is the
`primos` trace reproduced on a second question — one question out of five.

**Section 5's diagnosis generalized from one case and does not hold across its
own population.** "The corpus already ranks the answer note first for the rare,
distinguishing term a question uses" was measured on `pp09` alone and then
carried into the FOLLOWUP as a three-member family. Measured across the eligible
five it holds for `dt10` (and `pp09`) and fails for the other four, because the
question's rare term is simply not in the answer note. For four of five, the
residue is a genuine question-to-answer vocabulary gap — the thing the alias arc
attacked from the write side and could not close — rather than a fusion
term-selection defect.

## What this also settles, without a further rung

**Candidate 2, rarity-weighted subset scoring, is refuted by the same
measurement.** Re-weighting a sub-query cannot promote a document the sub-query
never returns. For the four failures the answer is not in the rare term's result
set at any weight. The rule registered in advance that a Gate-B-only failure
would make candidate 2 the honest next rung; that reading does not apply,
because the failure landed at Gate A for a stronger reason. **The rare-term
selection family is closed, both candidates, on measurement rather than
argument.**

One detail worth carrying: `pp06`'s rarest term `cuased` is a **typo in the
question** (DF 1), and the one document it matches is not the answer. The
mechanism would have fired a sub-query on a misspelling and surfaced an
unrelated note — adding noise, not merely failing to help. A rarity threshold
cannot separate a distinguishing rare term from a typo, because both are rare.

## Ops

Fresh `goldv3-20260817` restore to `~/.agentm/corpus-snapshots/fusion-rare-term/`,
a location no prior rung has used. Integrity triple exact by direct `sqlite3`
count: **15,029 / 14,529 / 17,407**. Control binary built from untouched HEAD
(`95e49b0`) before any edit existed and frozen aside as `bin-main`; since no Go
code was ever written, no `bin-sig` was built and the flag-off byte-identity
proof was never needed. Both arms scored twice before anything else: 0 of 84
rows differ between replicates and 0 of 84 against the historical
`question-20260817.json` / `hook-e2e-20260817.json`. `degraded: []` on all four
runs, checked per row — it is a per-row field, and a top-level read of it is a
vacuous pass. `PATH` pinned to the scratch binary with `which agentmd` confirmed
before every scoring call. Embedder attached at `127.0.0.1:8901`, never spawned;
neither embedder nor reranker was needed for the probe itself. Probe reproduces
byte-identically across two runs. Derivation and probe scripts kept scratch-side
at `~/.agentm/corpus-snapshots/fusion-rare-term/scratch-scripts/`, matching how
section 5's own scripts stayed out of the repo.

## What remains open

`dt10` is a real instance of the mechanism's thesis and remains unconverted; one
question is not a rung. **`dt02`** keeps its own FOLLOWUP (skill-discovery-cache
machine exhaust, an indexing-policy question) and was reported but never
targeted here. The **answerhood-reranker** rung
(`_harness/BRIEF-answerhood-reranker.md`) is a sibling lever at a different
layer, untouched by this result. Neither was built.

# Hook parity, and the drift the baseline could not see (task 1, recall-verdict)

The eval's `search()` docstring claimed "one query, exactly as the recall hook
issues it," and the function did none of three things the hook does: no ×2
over-fetch before filtering, no `_daemon_admissible` post-filter, and no
temporal bound from `_extract_temporal_bound`. The three questions the gold set
marks `hook_reachable: false` (`dt01`, `ep10`, `ep12`) were counted as hits in
`shipped-baseline.json` because of exactly that gap. This entry lands the fix
and re-pins the baseline; the number drops and the drop is honesty.

## The re-run, and a split that had to be earned

Against today's live corpus the fixed eval scores **47/64 (73.4%)** where the
pinned baseline said 50/64 (78.1%). Nine questions flipped — six of them ones
the parity fix does not predict. Each of the nine was then run as a controlled
pair on the *same corpus at the same moment*: once through the old code path
reproduced verbatim, once through the new one. That separates the two causes
cleanly, because a parity-caused flip reproduces under the code change alone
and a drifted question disagrees with the old baseline before the new code is
even involved.

| question | baseline | old code, today | new code, today | verdict |
|---|---|---|---|---|
| `dt01` | hit | hit | miss | parity — dropped `_index.md`, 3× `digest.md` from the old top-5 |
| `ep10` | hit | hit | miss | parity — dropped 2 inadmissible paths |
| `ep12` | hit | hit | miss | parity — dropped 1 |
| `pp05` `pp10` `rc12` | hit | **miss** | miss | drift — lost before the new code runs |
| `dt10` `ep04` `pp06` | miss | **hit** | hit | drift — gained before the new code runs |

Exactly the three predicted questions are parity-caused, and nothing else is.
The other six are the corpus moving underneath the instrument: since the
baseline was pinned this arc retired 2,633 notes, rewrote 311 through
enrichment and 32 through the reference backfill, and the daemon's in-scope
count halved (15,029 documents at the goldv3 changeover, 7,412 at this run).
Three losses and three gains, symmetric — drift is not a regression, it is
noise the baseline had no way to even report.

A correction recorded rather than smoothed over: mid-investigation I claimed a
flip *to* a hit could not be the parity change's doing. Backwards — dropping an
`_inbox` path out of the top-5 promotes a deeper admissible note, so
parity-caused gains are expected wherever mining noise sat above the answer.
The controlled pair shows the three gains here happen to be drift instead, but
the claim was wrong before it was tested, and the table is what settled it.

## What the baseline learns from this

`shipped-baseline.json` recorded seven numbers and no provenance — no corpus
size, no embedder state, no gold-set identity, no date. Two baselines from two
different corpora were therefore silently comparable, which is how 0.781 → 0.734
nearly got read as a code regression. The re-pinned baseline now carries a
corpus fingerprint (document count, embedded-in-scope count, gold-set content
hash, pin date), and `--compare` **refuses** a baseline whose fingerprint does
not match the corpus being scored — `--drifted-ok` overrides for the standing
tripwire against the live vault, and prints the drift beside the verdict so a
regression on a moved corpus is never attributed to code by default. The old
baseline is archived beside the new one, unchanged, with this entry as its
obituary: its number was true when measured and unprovable ever after.

## The canary's first live fire caught the fusion competition, not a dead index

Worth its own paragraph because it happened within minutes of the control
existing. The canary was planted, verified at rank 1 by a bare `agentmd search`,
and the eval's first full run aborted: through the *shipped* query shape —
hybrid mode, term extraction splitting the token to `canary eval liveness
q7g3xz` — the planted note ranked **sixth**, fused score 0.016 against 0.029,
under four `desk/` archive PLAN documents. The unique token still made the
lexical arm rank it first; fusion normalization drowned that under
long-document dense mass. That is the corpus's known desk-outranks-memory
competition demonstrating itself on a nonsense token, and it is exactly the
distinction the control has to respect: liveness must be a deterministic
question. The canary now probes the lexical arm alone — a unique token through
FTS is rank 1 whenever the index is alive — while the dense arm's liveness
stays `require_warm_embedder`'s job and the hybrid path's sanity is the spread
control's.

## Floorless rerank: refutation withdrawn as unproven (recall-verdict task 6)

The entry above records 4 of 9 against a bar of ≥5 and closes the rung
refuted. The instrument audit computed what that rule never did: a fair coin
clears ≥5-of-9 with probability exactly 0.50, and a 60%-effective mechanism
fails it 27% of the time. A result one short of a coin-flip bar distinguishes
nothing in either direction, so the verdict is corrected in place:
**withdrawn as unproven, not overturned** — 4/9 is also exactly what a coin
does, and no one should read this withdrawal as evidence the mechanism works.

Fusion's Gate A (≥3 of 5) fails the same arithmetic — 0.50 — and its
refutation stands anyway, because it never rested on the bar: four of the
five eligible rare terms retrieve the answer note nowhere in their entire
FTS match set, a deterministic fact per target. The difference between those
two closures is the whole lesson, and it is now a requirement:
`coin_pass_probability(bar, n) ≤ 0.05` before a bar may be registered,
computed by the eval's own function and quoted in the RULE file. Contract:
`wiki/reference/Retrieval-Eval-Contract.md`; template:
`scripts/health/results/RULE-TEMPLATE.md`.

# Enrichment as a vocabulary bridge — NULL at the probe, and the thread closes

The last untried gold-blind write-side mechanism, run under
`RULE-enrichment-vocab.md` (pre-registered at a5e3444, population 5 questions
/ 8 memory-lane notes, prediction 0 of 5, positive control mandatory). The
control — a gold-informed oracle alias on an out-of-population target — moved
its note to rank 1 before the probe ran; the zero that followed is therefore a
measurement, not a dead instrument.

Outcome: **0 of 5 moved**, and the mechanism's own gates wrote the diagnosis.
Five of eight target notes were refused by enrichment's grounding and
token-preservation post-gates — a grounded rewriter cannot add vocabulary its
source never held, because the gate that makes it safe is the gate that makes
it vocabulary-preserving. The three notes that did enrich gained fuller
grounded prose and moved nothing.

That makes four families with the same zero: three alias strategies and
grounded re-distillation. The vocabulary residue is now closed as
**irreducible gold-blind** — what remains is the oracle's ceiling (+10.9pp,
gold-informed by construction) and the model-level lever (embedder tier),
both parked as priced re-audit triggers in the design. Registered before the
run and worth repeating after it: even a perfect probe was +5 flips against
an MDE of 6, so no significant column existed to buy. The thread ends on
mechanism, with the arithmetic having foreclosed the number in advance.
