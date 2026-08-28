# Completeness v1 — the bar was not met, and why

**Validated 2026-08-27 against the bar frozen in `RUBRIC.md` at `740a215`, before
the sample was drawn. The bar was not moved.**

| | measured | bar | |
|---|---|---|---|
| Spearman ρ | 0.1065 | ≥ 0.50 | ✗ |
| p (Monte Carlo, 200,000 resamples) | 0.569 ± 0.001 | < 0.05 | ✗ |
| separation, median `complete` − median `major` | 0.0 | ≥ 0.20 | ✗ |

32 of 32 pairs graded, all resolved against the frozen draw by content hash. The
scorer returned **1.0 for 29 of them**, including both notes the operator graded
`major`. Replicate spread was 0.0 — it is a stable instrument, and stably
uninformative.

## The cause

`meters.MinClaimWords = 4`.

The two `major` notes lost the same thing, and it was not prose. Their sources
carry a mining-metadata block:

```
- **Category**: `idea`
- **Confidence**: `LOW`
- **Rationale**: follow-up marker
- **Occurrences**: 5
```

Every one of those lines is under four words, so the splitter discarded them
before the judge saw anything. The judge was asked about four prose claims, all
four of which the rewrite genuinely carries, and answered 1.0 correctly. The
score is not wrong about the question it was asked. The question left out the
part the operator was grading.

## It predicts the population, not just the two notes read

Counting structured `**Key**: value` lines that are under the word floor *and*
whose value is absent from the rewrite:

| operator grade | n | mean dropped | values |
|---|---|---|---|
| `complete` | 18 | 0.11 | seventeen zeroes and one 2 |
| `minor` | 12 | 2.17 | 1, 2×7, 3×3 |
| `major` | 2 | 2.50 | 2, 3 |

Seventeen of eighteen `complete` notes dropped none. Every marked-down note
dropped at least one. The floor explains which notes the operator marked down,
which is what separates a diagnosis from a plausible story.

## Why the gutted-note check passed anyway

`gutted-check.json` records faithful 1.0 against gutted 0.3333 on four notes,
both directions, and that result stands. It removes whole claims — the ones the
splitter *did* produce — so it measures whether the judge notices an absent
claim. It cannot notice a claim that was never made, and the defect here is
upstream of the judge entirely. A passing check on the wrong stage is exactly as
green as a passing check on the right one, which is the reason to write down what
a check does not cover.

## What v2 needs, and what it must not do

The floor's reasoning is sound for prose: three words or fewer is usually a
heading or the tail of a sentence the capture bug cut in half. It is wrong for a
structured `**Key**: value` line, which is a complete assertion at any length and
is precisely what the rubric's rule 5 calls `major` when lost — *a name, path,
flag, error string, command or number is what someone searches for later.*

So the fix is to exempt structured key-value lines from the word floor rather
than to lower it, which would readmit the fragments it was built to keep out.

**This sample is spent.** It has been used to fail a bar, and a scorer tuned
until it passes on the notes that diagnosed it has been fitted to its own test
set. A v2 gets a fresh draw and fresh grades, or it gets no claim of validation.
