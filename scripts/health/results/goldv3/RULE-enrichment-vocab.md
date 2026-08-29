# RULE — enrichment as a write-side vocabulary bridge (recall-verdict task 7)

**Registered before any run. Template: `../RULE-TEMPLATE.md`; contract:
`wiki/reference/Retrieval-Eval-Contract.md`.**

## Mechanism

The one write-side, gold-blind mechanism never tried, and the only lever the
twelve-refutation record leaves standing: the enrichment pass (grounded
re-distillation, `agentmd enrich`) rewrites a miss's answer note into fuller
prose, and the question is whether grounded rewriting closes the
question-to-note vocabulary gap that three alias strategies could not. The
mechanism sees only the note — gold-blind by construction. It acts on a
**scratch copy** of the live corpus; the live vault and the canonical
snapshots are untouched.

## Population

Of the current baseline's 17 misses, the answer notes of 12 live in `desk/`
or `external/` — outside the memory lane enrichment rewrites — and `dt01`,
`ep10`, `ep12` are `hook_reachable: false` besides. The reachable population
is **5 questions, 8 memory-lane notes**:

| question | answer note(s) enriched in the copy |
|---|---|
| `pp02` | `memory/2026/05/worktrees-never-auto.md` |
| `pp10` | `memory/2026/05/vault-as-canonical-context.md` + 2 preference notes |
| `rc01` | `memory/2026/08/google-cloud-ships-a-memory-agent-….md` |
| `rc03` | `memory/2026/08/the-always-on-agent-reads-…` + 1 sibling |
| `rc12` | `memory/2026/08/desk-documents-outrank-memory-notes-….md` |

Reach floor (fusion precedent, ≥3): **met at 5**.

## A consequence registered now, not discovered later

Even a perfect probe — all 5 questions converted — is **+5 flips, one short of
the MDE (6)**: p = 0.0625, not significant. So there is **no full R@5 column
to buy** whatever the probe says; the contract's own arithmetic forecloses it.
This probe's verdict on the vocabulary thread is therefore final either way,
and its value is mechanistic (does grounded rewriting bridge vocabulary at
all?), not a scorecard number.

## The bar

Per question: **success = any expected memory note moves from outside the
lexical top-50 into the top-50** for the gold query (daemon mode `and`, the
hook's extracted terms, measured in the copy, pre vs post).

**Signal bar: ≥2 of 5 questions move.** One mover is investigated and
recorded but buys nothing — a single fluke under a text edit is not a
mechanism.

**Power note, and an open deviation from contract clause 1:** the instrument
is deterministic rank, not a flip count; a fair-coin model does not describe
it, so `coin_pass_probability` is not quotable here. The honest null is
empirical: three prior gold-blind strategies moved **0 of 8 targets, three
times over** — under that null, any movement is surprising and two
independent movers are far beyond it. This is stated rather than dressed in a
coin number the model doesn't earn.

## Positive control

`pp05`'s answer (`desk/projects/home-tech-next/_index.md` — outside the
population) gets an oracle-style, gold-informed alias line injected in the
copy before reindexing. Its rank **must** move from >50 into the top 5, or
the instrument (copy, reindex, query path) is dead and nothing else counts.
The oracle already proved this exact edit moves ranks (d3c9223).

## Prediction

**0 of 5 move.** Calibration: three gold-blind strategies produced exactly
zero movement on overlapping targets, and enrichment's grounding gate blocks
vocabulary absent from the source — the bridge word the question uses is, by
the residue's own diagnosis, in neither the question's note nor its source.
A null here closes the vocabulary thread completely: every gold-blind
write-side family (aliases ×3, grounded re-distillation) will then have
produced the same zero movement, and the residue is written down as
irreducible without gold leakage or a model-level change.

## Per-question record

Filled by the run, below, as a per-question table — never a net total.

| question | pre | post | moved | note |
|---|---|---|---|---|
| `pp02` | >50 | >50 | no | its note refused: token-preservation |
| `pp10` | >50 | >50 | no | all three notes refused: grounding ×2, token-preservation |
| `rc01` | >50 | >50 | no | **enriched**, and still no movement |
| `rc03` | >50 | >50 | no | one sibling enriched, one refused; no movement |
| `rc12` | >50 | >50 | no | **enriched**, and still no movement |

## Outcome — NULL, 0 of 5, exactly as predicted (run 2026-08-28)

Positive control: the oracle injection moved `pp05`'s note to **rank 1**
before anything else ran — the instrument was alive, so this zero is a
measurement.

The mechanism refused more than it rewrote: 5 of 8 target notes were blocked
by enrichment's own post-gates — grounding ("the rewrite asserts what the
source does not") and token-preservation — which is the prediction's reasoning
executing as code: a grounded rewriter *cannot* introduce vocabulary its
source never contained, because the gate that makes it trustworthy is the gate
that makes it vocabulary-preserving. The three notes that did enrich produced
fuller grounded prose and moved their questions' ranks by exactly nothing.

**The vocabulary thread closes.** Four gold-blind write-side families —
content-prompt aliases, structural aliases, outcome-filtered aliases, and now
grounded re-distillation — have produced the same zero movement on
overlapping targets. The residue is written down as irreducible without gold
leakage (the oracle's +10.9pp stands as the ceiling and the proof it was
never the instrument) or a model-level change (the embedder tier, priced in
the design's re-audit triggers). Per the consequence registered above, no
full column existed to buy either way.
