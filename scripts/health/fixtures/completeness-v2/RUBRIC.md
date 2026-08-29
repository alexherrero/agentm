# Completeness rubric v2 — reused from v1, unchanged

**The rubric is `../completeness-v1/RUBRIC.md`, verbatim. Nothing in it was
changed, added or removed for this round.**

It was frozen 2026-08-27 before any note in the v1 sample was drawn or read, and
it stays frozen here. Its three grades (`complete` / `minor` / `major`) and all
of its hard-case rules apply exactly as written.

## Why it was not rewritten

v1 missed its bar, and the obvious move after a failure is to revise the
instrument. That would have been the wrong move, and the diagnosis says why: the
judge "answered correctly about the claims it received." The rubric was never
the fault. `MinClaimWords = 4` discarded the structured `**Key**: value` lines
that were the only thing being graded, so the judge was never asked about them.
The splitter was fixed in #497; the rubric needed nothing.

Rewriting a rubric after seeing which notes it failed on is how an instrument
gets fitted to its own test set. The v1 sample is already spent for that reason.
Holding the rubric fixed is what makes this round an independent test rather
than a second look at the same one.

## What is different in v2

Only the sample, and how it is recorded:

- 60 pairs rather than 32, drawn with seed 20260901 from a population that has
  grown to 350.
- Disjoint from v1 by `rel`. The residual risk is stated in `sample.json`
  rather than hidden: 10 of v1's 32 paths no longer exist, so a renamed v1 note
  could recur here undetected.
- A **defined, recorded hash recipe** — v1's was never committed, which is why
  disjointness here had to fall back to paths.
- Randomised presentation order, so grading a prefix is still a random sample.

The bar is unchanged: rho ≥ 0.50, p < 0.05, separation ≥ 0.20.
