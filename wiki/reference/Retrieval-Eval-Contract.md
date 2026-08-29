# Retrieval Eval Contract

> [!NOTE]
> Reference — the measurement contract for `eval_retrieval_shipped.py` and
> every pre-registered retrieval experiment. What the instrument can resolve,
> what a rule must contain before a run, and the two false nulls that made
> these requirements standing rather than ad hoc.

## What the harness measures

One number with provenance. The eval issues each gold-set question exactly as
the recall hook does — hybrid mode, term extraction, ×2 over-fetch, the
admissibility filter, temporal bounds — and scores exact-path membership at
k=5 against `gold-set-v3.json` (64 scored questions, 20 easy negatives, 10
near-miss negatives). Baselines embed a corpus fingerprint (document count,
embedded-in-scope count, gold-set hash, pin date); `--compare` refuses across
fingerprints unless `--drifted-ok` says the drift is understood, and a
gold-set hash mismatch is never overridable. R@5 is the product metric — the
hook injects five — and R@1 rides along as the ordering signal.

## The instrument's resolution

Printed on every report, so nobody can pre-register a bar the instrument
cannot resolve without having seen it refuse the idea:

| scored n | smallest clean gain (6 flips) | Wilson 95% width near 75% |
|---|---|---|
| 64 (today) | +9.4pp | ~21pp |
| 100 | +6.0pp | ~17pp |
| 125 | +4.8pp | ~15pp |
| 250 | +2.4pp | ~11pp |

Six flips is derived from the exact paired test (two-sided binomial:
2·(½)^k < 0.05 first at k=6), not chosen. A true 5pp improvement at n=64
best-cases p=0.25 — invisible. Per-target rank probes remain the sensitive
instrument for sub-MDE mechanisms: they measure movement exactly, and they are
how three alias strategies were shown to move nothing at all.

## Pre-registering an experiment

A RULE file (template: `scripts/health/results/RULE-TEMPLATE.md`) is committed
before any run, and it must contain:

1. **A power-checked bar.** `coin_pass_probability(bar, n) ≤ 0.05`, computed
   by the eval's own function and quoted in the file. Both of this arc's
   pre-flight bars — floorless rerank's ≥5-of-9 and fusion's ≥3-of-5 — sit at
   exactly **0.50** by this arithmetic: coins with paperwork. The floorless
   refutation is withdrawn as unproven because the bar was its only evidence;
   fusion's stands only on its deterministic per-question trace.
2. **A positive control.** Something in the run that fails if the instrument
   is dead, named in advance. The standing controls (schema assertion, planted
   canary, score spread) cover the eval itself; a probe touching anything else
   supplies its own.
3. **Per-question diffs.** A net "+2" once hid a +5/−3 split; totals are not
   results.
4. **A written prediction.** Checked against the outcome either way, in the
   same file.

## The false nulls these rules answer

**The field-name null, twice.** `expected` read where the fixture says
`expected_note_paths` parses every expectation to an empty list, and a run
reports a clean "0 of N" that is really N comparisons against nothing. It
shipped one refutation and reproduced on the next rung's first attempt. The
schema control now aborts it (exit 4).

**The keyword-soup null.** The cross-encoder rung fed both models
concatenated terms rather than natural questions; the technique looked flat
because the query representation was. Re-measured with real phrasing before
the verdict was trusted — which is what clause 2 generalizes.

## LLM-judged metrics

None are load-bearing here, deliberately — the scoring path is deterministic.
Any future judged metric must first measure operator agreement on a sampled
set and publish that number beside its own. The completeness-v1 scorer is the
standing reason: its gutted-note check was green in both directions while it
agreed with the operator at ρ = 0.11, because the check exercised a stage
downstream of the defect.

## Related

[CI-Gates](CI-Gates) · the gate script `scripts/check-retrieval-regression.sh`
· results record `scripts/health/results/goldv3/NOTES.md` · design
[agentm-hybrid-retrieval](../designs/agentm-hybrid-retrieval).
