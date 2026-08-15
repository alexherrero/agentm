---
title: "Arc close: one agent-layer run, two clauses"
status: draft
visibility: published
author: alexherrero
contributors: []
created: 2026-08-14
updated: 2026-08-14
last_major_revision: 2026-08-14
prd:
project:
parent_design: ../../agentm-rejection-and-vocabulary.md
part_slug: arc-close
dependencies: [rejection, alias-oracle, alias-pilot]
estimated_scope: S
---

# Arc close: one agent-layer run, two clauses

## Scope

Implements §5 of the parent design — the single paid gate this arc budgets,
and the close-out that follows it.

Run `week3_daemon_retest.py` unmodified, n=6 replicates, against a frozen
corpus carrying every shipped rung: the elicitation text and any confirmed
pilot aliases. The labeller is deliberate-path infrastructure and does not
participate, because this gate drives the interactive MCP surface — stating
that is part of the rule rather than a caveat on it. The run costs ~$50.68
and ~3 hours, and the arc budgets exactly one.

Close-out follows the harness convention once the run lands: flip the plan to
done, append the end-of-plan summary to `progress.md`, move the ROADMAP item
to Completed with its full narrative, publish the measured columns to
`scripts/health/results/goldv2/NOTES.md`, and archive the plan to
`archive/PLAN.archive.YYYYMMDD-arc-close.md` as the final step.

## Dependencies

Depends on `rejection`, `alias-oracle`, and `alias-pilot`. The gate scores
whatever those parts actually shipped — including the case where the oracle
refuted and `alias-pilot` closed unbuilt, which changes what clause 2 can
reach and is recorded rather than worked around.

## Verification criteria

**Clause 1 — non-regression:** blended mean ≥0.725, computed by the same
unmodified aggregation code as the historic baseline (fractional `r_at_5`,
negatives folded at 1.0/0.0).

**Clause 2 — the goal:** answerable-only binary R@5 mean ≥90% across the same
six replicates, with negatives reported separately per the ladder's own
reading.

Both clauses are reported independently, with per-question diffs by id. The
arc closes only if both hold. A run clearing one clause closes that clause's
story and records the other as open, without relaxing either.

Before the numbers are trusted, the run asserts what every rung in the
previous arc asserted: corpus and index integrity against the archived
figures, the embedder warm and attached, no stray `llama-server` processes,
and `INTEGRITY: clean` on all six replicates. Per-question detail and
replicate scorecards land vault-side.

Clause 2 is priced honestly as the ambitious one. Converting the four
always-missed answerable questions alone reaches ~84%, and the remainder
rides consistency gains that are not individually pre-measured.

## Parent design

This part implements one slice of [AgentM Rejection and
Vocabulary](../../agentm-rejection-and-vocabulary.md) (`Status: final`). See
the parent for Context, Alternatives Considered, the Quality Attributes
overview, and the Operations strategy. Mid-execution changes to this part's
scope must be appended to the parent's Document History.
