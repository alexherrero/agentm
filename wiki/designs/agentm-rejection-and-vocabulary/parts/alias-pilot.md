---
title: "Alias pilot: targeted filing, measured against a refuted precedent"
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
part_slug: alias-pilot
dependencies: [alias-oracle]
estimated_scope: M
---

# Alias pilot: targeted filing, measured against a refuted precedent

## Scope

Implements §4 of the parent design. **Builds only if `alias-oracle` licensed
it.** If the oracle refuted, this part closes unbuilt and the arc records why.

A batched Sonnet 5 pass over a bounded, targeted scope — the spaces where
vocabulary misses concentrate: project `_index` files, `external/`, and
decision summaries, capped at ≤300 notes for the pilot. It proposes
question-vocabulary aliases from the note and its project context alone,
blind to the gold set per the parent's §1 boundary. Propose→confirm, behind
the corpus-write gate, landing as ordinary markdown frontmatter that the
daemon's committer already handles and git already reverts.

The scope discipline is the point. This vault has already run the untargeted
version of this idea: the 2026-08-08 bulk alias backfill wrote generated
aliases into 1,930 notes, cost 3.85 points of R@5 (p = 0.0411, exact
permutation over six replicates per arm), and was reverted. That result is
this part's explicit null hypothesis rather than a cautionary anecdote.

## Dependencies

Depends on `alias-oracle`: the oracle establishes that a perfect alias
converts these questions at all, and supplies the validated target set this
pilot's own rule is scored against. Building the engine before knowing the
ceiling would repeat the mistake the bulk backfill already paid for.

## Verification criteria

Apply the pilot mechanism to a frozen-corpus copy, rebuild, and score:

- Converts ≥3 of the eight oracle-validated targets.
- Loses zero currently-passing questions net, with the per-question diff
  published by id.
- Leaves the 20 negatives' behaviour unchanged.
- The mechanism is constructed blind to the gold set. Its inputs are the note
  and its project context; never a gold question, and never anything shaped
  by one. An alias engine that reads the answer sheet is disqualified
  regardless of its score.
- A pilot that cannot beat "do nothing" on the same scorecard closes as
  *refuted*, and the alias story returns to capture-time practice only.

## Parent design

This part implements one slice of [AgentM Rejection and
Vocabulary](../../agentm-rejection-and-vocabulary.md) (`Status: final`). See
the parent for Context, Alternatives Considered — in particular the bulk
alias backfill entry — the Quality Attributes overview, and the Operations
strategy, including the rollback path this part's writes travel. Mid-execution
changes to this part's scope must be appended to the parent's Document
History.
