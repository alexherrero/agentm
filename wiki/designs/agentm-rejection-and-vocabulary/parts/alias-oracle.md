---
title: "Alias oracle: the ceiling before the mechanism"
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
part_slug: alias-oracle
dependencies: []
estimated_scope: S
---

# Alias oracle: the ceiling before the mechanism

## Scope

Implements §1 of the parent design — rung 0 of Thread B, and the gate on
whether Thread B has a mechanism worth building at all.

Copy the frozen `goldv2-20260812` corpus. Hand-write ideal `aliases:`
frontmatter, in question vocabulary, for exactly the eight unreachable notes
(`pp05`, `pp07`, `pp09`, `pp15`, `pp16`, `pp17`, `rc01`, `rd01`). All eight
are already verified admitted by recall's hygiene filter, which places their
failure in vocabulary. Rebuild a scratch index, assert its integrity, and
re-score the `+question` arm and the hook-shaped path.

The oracle answers one question: if the alias were perfect, would the note be
found? A hand-written ideal alias is the most favourable input any alias
engine could ever produce, so this bounds the whole thread from above before
a line of engine code exists. It mirrors the term-subset oracle that bounded
the lexical thread at 82.8% in the previous arc.

## Dependencies

None. This part is deliberately first: it is deterministic, needs no LLM, runs
in an afternoon, and its result decides whether `alias-pilot` is built or
closed unbuilt.

## Verification criteria

- At least 6 of the 8 target questions convert to top-5 hits.
- No currently-passing question is lost. The per-question gain/loss diff is
  published by id — added alias text changes both BM25 statistics and dense
  vectors, and reciprocal-rank displacement has cost this project hits three
  times across the previous arc.
- No stratum regresses by more than one question.
- Index integrity asserted before scoring: 9,971 docs, and a file-level diff
  against the archived snapshot showing exactly the eight edited notes differ.
- Nothing derived from this oracle ships. It is gold-informed by construction,
  under the same diagnostic licence as the candidacy analysis and the k=20
  reachability count.

**If the rule fails (<6/8):** the part closes as *refuted*, recorded with the
numbers. The misses are then not vocabulary-shaped, `alias-pilot` closes
unbuilt, and the recall clause of the arc-close gate is re-priced before any
money is spent on it.

## Parent design

This part implements one slice of [AgentM Rejection and
Vocabulary](../../agentm-rejection-and-vocabulary.md) (`Status: final`). See
the parent for Context, Alternatives Considered, the Quality Attributes
overview, and the Operations strategy — in particular the gold-blindness
boundary in §1, which governs this part and every part downstream of it.
Mid-execution changes to this part's scope must be appended to the parent's
Document History.
