---
title: "Rejection: elicitation on the interactive surface, a labeller on the deliberate path"
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
part_slug: rejection
dependencies: []
estimated_scope: M
---

# Rejection: elicitation on the interactive surface, a labeller on the deliberate path

## Scope

Implements §2 and §3 of the parent design — the whole of Thread A. The two
rungs ship together because they share one story and neither blocks the other:
the layering rule gives each surface a different legal lever, and this part
supplies both.

**§2, the interactive surface.** The daemon's result `note` currently coaches
"answer 'nothing found' only after distinct vocabularies have failed" on
*empty* results, and every one of the 45 recorded failures happened on
*non-empty* results. Across 1,795 served calls, 79.9% never set `mode`, and
the two negatives missed in all six replicates never escalated once. The
`memory_search` tool description and the non-empty result annotation gain the
answerhood check the probe validated: verify a note answers rather than
relates, treat a related note as no answer, and treat "nothing answers this"
as a correct and expected outcome. Exact wording is frozen before the
mini-gate runs. This deliberately exercises the previous design's own
re-audit trigger, which named a description change as the cheapest lever its
investigation had not tried.

**§3, the deliberate path.** One Haiku call per search, taking the **natural
question** and the candidate set. The question matters at the level of
architecture: on the same instrument, a gate reading the reduced tool query
fixed 8.9% of failures where one reading the natural question fixed 86.7%, so
a gate placed where only the query is visible lands in the first row.
Excerpting follows the probe's corrected instrument — IDF-weighted head plus
best-middle plus tail, notes under ~3.5KB shown whole — because the thin
version of that instrument mislabelled 43.2% of its apparent over-rejections.
Output is a verdict attached as a label; every note stays present and
readable. The prompt must handle derived answers, where strict answerhood
preserved only 58.7% of episodic-temporal questions, and it is iterated
offline on that slice before being frozen.

## Dependencies

None. Both rungs are independent of Thread B and of each other.

## Verification criteria

**§2 elicitation** — a mini-gate of the 20 negatives plus a fixed 15-question
answerable canary sample, stratified and chosen before any run, n=6
replicates, ~$21:

- Mean rejection ≥80%, against a 62.5% baseline whose replicate spread was
  55–75% — roughly 2.5σ up.
- Canary answerable mean within one question of its own baseline slice.
- Both clauses hold, with the arm difference checked by exact permutation
  test. `rc02` is this rung's watchlist case.
- Rebuild, reinstall, and launchd kickstart are part of the rung: launchd runs
  the installed binary, so a cutover that skips the reinstall measures the old
  path.

**§3 labeller** — deterministic offline replay on the frozen corpus, corrected
instrument, all six replicates' served candidates:

- ≥80% of negative trials have every candidate labelled drop. Current
  evidence is 82.5% on the two fully re-checked replicates, before the
  derived-answer refinement, which loosens the gate and may push this down.
- ≥90% of answerable trials keep the expected note. Current: 86.6%.
- Episodic-temporal slice ≥80% preserved. Current: 58.7%. This clause is
  where the refinement either works or the rung refutes.
- A consumer whose labeller call fails renders unlabelled output with a
  visible degrade marker, mirroring the embedder's degrade contract.

## Parent design

This part implements one slice of [AgentM Rejection and
Vocabulary](../../agentm-rejection-and-vocabulary.md) (`Status: final`). See
the parent for Context, Alternatives Considered — in particular why the
deleting keep/drop gate was priced and declined — the Quality Attributes
overview, and the Operations strategy. Mid-execution changes to this part's
scope must be appended to the parent's Document History.
