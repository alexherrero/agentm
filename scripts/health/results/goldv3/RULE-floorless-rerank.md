# Pre-registered rule — floorless rerank (reorder the question-shaped pool)

**Written and committed before any probe or rerank code exists.** Governing
design: `wiki/designs/agentm-hybrid-retrieval.md`. Brief:
`_harness/BRIEF-floorless-rerank.md`. Plan: `_harness/PLAN.md` ("floorless
rerank — reorder the question-shaped pool").

## The finding this rests on

The post-baseline reach probe (`goldv2/NOTES.md` § "Post-baseline reach
probe") measured that 11 of the 14 `+question` misses already sit in the
k=50 candidate pool, most at rank 6–14. The residue is ordering inside the
pool, not failure to retrieve into it — R@20 is 92.2% against R@1's 43.8%.

## The mechanism

Re-order the existing hybrid candidate pool (k=20) before truncating to the
caller's k. **No floor, no filter, no dropping** — the pass emits a
permutation of the pool it was handed and nothing else. This is what makes
it a different experiment from step 3 (`+rerank+floor`, refuted — dropped
below a floor) and from the refuted answerhood labeller (dropped
non-`answers`): reordering cannot evict a candidate that is already
reachable, so no question currently reachable at k=20 can become
unreachable by this mechanism.

## The five clauses

**(a) Primary — R@1.** ≥ 37/64 (57.8%), at least +9 over today's 28/64. The
reorder ceiling is 59/64; a mechanism that cannot capture a third of the
available headroom is not worth its latency.

**(b) Non-regression — R@5.** ≥ 50/64. The current top-5 set may be
re-ordered freely; it may not shrink.

**(c) No stratum regresses** by more than one question on R@5.

**(d) Per-question diff published** by id, gained and lost listed
separately, never a net. (The goldv3 changeover's own correction — a
reported +2 that hid a +5/−3 split — is why this clause is explicit.)

**(e) Latency.** Hook-arm p50/p90 stays under the 300ms budget with the
rerank pass in path, measured through the installed hook, or the rung ships
deliberate-path-only and says so.

Ship only if all five clauses meet their bar. On any failure, the floorless
code is kept inert behind its default-off flag, exactly as the outcome
filter was kept in section 5.

## Pre-flight probe's own bar (task 3), registered here before it runs

**The rung's live risk is step 3's own structural diagnosis, which this
rule does not claim to have refuted:** a cross-encoder scores query-document
*similarity*, and ranks 1–5 are already occupied by highly similar notes. If
similarity cannot separate the answer from a similar non-answer *within one
question's own pool*, a better pool does not help and the mechanism fails
for the same underlying reason step 3 did.

The distinction the probe tests, stated precisely so a failure cannot be
mistaken for a re-discovery of step 3's finding: step 3's measured ranges
(0.003–0.959 against 0.267–0.906) are about **cross-question score
comparability** — whether one fixed threshold works across questions, which
is what a floor needs. The probe below tests **within-question ordering** —
whether, for one question at a time, the labeled answer's score beats its
own pool's competitors — a strictly weaker property that measurement never
touched.

**Target set (9 of the 14 `+question` misses, those whose labeled answer
sits at k ≤ 20):** `pp05` (rank 6), `pp06` (rank 6), `ep04` (rank 6), `rc03`
(rank 7), `pp02` (rank 8), `dt10` (rank 11), `pp17` (rank 13), `pp07` (rank
14), plus `rc01` (rank 27) as the stretch case.

**Procedure:** score the labeled answer and that question's current top-5
occupants with the cross-encoder, via `scoreDocuments` (the shipped scorer,
not a reimplementation). Report each question's raw scores.

**Instrument-liveness check, required before either verdict is trusted:**
confirm the reranker is actually serving and that scores differ across
documents for at least one probed question — a flat read (every document
scoring identically) is what a dead or misattached server produces, not a
verdict about ordering.

**Probe passes** if the labeled answer outscores a majority of the current
top-5 occupants on **≥5 of the 9**.

**Probe fails** otherwise. On failure: the rung closes **refuted at the
probe**, tasks 4–6 are not bought, and the close-out records that
"similarity is not answerhood" now holds for ordering and not only for
thresholding — a deeper confirmation of the design's existing finding, not
a new one and not a contradiction of it. Section 5 closed at exactly this
kind of probe and saved the full run's cost; same shape.
