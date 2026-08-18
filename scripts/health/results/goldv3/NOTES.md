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
