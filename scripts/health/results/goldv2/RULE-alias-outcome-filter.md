# Pre-registered rule — outcome-filtered alias generation (section 5)

Retrieval-competition arc, section 5. **Written and committed before any alias
text was generated**, so no clause can be tuned to the result. The conditional
section the arc's brief licensed only if sections 1–4 left the alias oracle's
eight unconverted — which they did; every rung that touched any of them was
reverted, so all eight remain unconverted in the live system.

## The mechanism

Generate aliases gold-blind over the fixed structural scope, then **keep an
alias only if it demonstrably works**: with all candidate aliases applied and
indexed on a scratch corpus copy, query the lexical arm with the alias's own
text and keep the alias only if its own note enters the **lexical top-5**.
Everything that fails that test is dropped, and a second index is built from
the survivors alone.

This is structurally different from both prior alias pilots, which applied
every generated alias unfiltered, and from Doc2Query--'s relevance filter,
which the SIGIR 2024 reproducibility study found *harms* recall-based metrics
(R@5 is recall-shaped). The filter here is an **outcome** filter — it asks
whether the alias moved retrieval, not whether a model judges it relevant.

**The filter is evaluated under competition, not in isolation.** All candidate
aliases are present in the index the filter reads, so an alias must win against
the other aliases the same run generated — the condition it will actually face.
Scoring a candidate in isolation would over-state it.

`k = 5` on the lexical arm (`agentmd search -mode fusion -no-embedder`), chosen
to match the metric under test (R@5) rather than the looser `k = 50` prior
rungs used for diagnosis.

## Target set — re-derived against this corpus, not assumed

Re-derived by `21-derive-reach.py` from the corpus itself: the eligible set
under `alias_backfill.survey_corpus`'s own unmodified rule, intersected with
`alias_pilot.in_pilot_scope`'s fixed structural patterns. It reproduces
alias-pilot's recorded **120-note scope exactly**, which is the cross-check that
the derivation is sound.

**REACHED (the registered target set): `pp05`, `pp09`, `pp15`.**

**UNREACHED: `pp07`, `pp16`, `pp17`, `rc01`, `rd01`** — every one of them
outside the fixed structural scope (`_index.md`, `external/`, `PLAN.archive.*`).

This is the outcome the plan's Risks section named in advance. The hard residue
the session brief hoped to reach (`pp07`, `pp16`, `pp17`, `rc01`) is **not
reachable by this mechanism at all**, and the scope is *not* widened to reach
it: choosing a widening after seeing which targets it would recover is the
gold-informed back door alias-pilot's own task 1 declined. Registered honestly
here instead of quietly reaching.

Two consequences stated up front, so a positive result cannot be over-read:

- `pp07` is independently known to be unreachable by *any* alias mechanism — its
  hand-written ideal alias already won the lexical arm at rank 1 and still lost
  to reciprocal-rank fusion at 7th. That is fusion friction, not vocabulary.
- The three reachable targets are exactly the three both prior alias pilots
  failed to convert (0 of 3, twice, with zero lexical-rank movement), and the
  three HyDE converted in its own unshipped measurement. So this rung tests
  precisely the cases the alias thread has already failed twice — which is the
  point, since the outcome filter attacks that specific failure — but a
  conversion here does not generalize to the residue.

## The five clauses

**(a) Non-regression.** `+question` ≥ **48/64** and hook ≥ **47/64**, and no
stratum regresses by more than one question on either arm. (Baselines
reproduced row-for-row in task 1: 48/64 and 47/64.)

**(b) Conversion.** At least **1** of the registered target set
{`pp05`, `pp09`, `pp15`} converts to a top-5 hit.

**(c) Prediction, in two halves — registered before scoring.**

- **Positive half:** at least **1 of 3** targets converts. A positive half of
  **0 closes the rung refuted regardless of the negative half.** On a
  three-target set this coincides with clause (b) by construction; recorded as
  a redundancy rather than manufacturing artificial separation between them.
  Honest calibration: two prior prompts converted 0 of these same 3 with *zero*
  lexical-rank movement, so predicting 2 or 3 would be over-confident.
- **Negative half:** the remaining **61 answerable questions hold** (64 minus
  the 3 targets) — no unpredicted regressions.

**(d) Negatives.** All 20 negatives' `correct_rejection` values unchanged, per
id.

**(e) Latency.** The hook stays inside **300 ms**. Live for this rung, unlike
HyDE: alias generation is a corpus-write-time operation and the query path is
unchanged, so the hook floor genuinely applies and is measured, not declared
moot.

## Pre-flight gate

Before any full-corpus scoring run, a pre-flight probe measures whether
outcome-filtered aliases move the **lexical-arm rank** of the registered targets
at all (`-mode fusion -no-embedder`, baseline index vs filtered index, the same
diagnostic both prior pilots ran, where every target read `>50 → >50`). **If
every probed target still reads `>50`, the rung closes refuted at the probe**
and the full run is not bought.

## What a refutation licenses

If this refutes, all five sections of the retrieval-competition arc are
accounted for — four refuted or closed without a run, one conditional refuted —
and the arc-close gate's release condition ("at least one rung that moves the
deterministic retrieval-layer number") has gone unmet across every section. That
is the strongest single data point for re-pricing the gate, and the close-out
says so plainly rather than leaving it implicit.
