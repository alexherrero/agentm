# RULE — <mechanism name> (<task/arc>)

**Registered before any run.** The contract this template serves is
`wiki/reference/Retrieval-Eval-Contract.md`; a RULE file missing any section
below is not a pre-registration.

## Mechanism

What is being tried, in one paragraph, and the layer it acts on (write-side /
read-side / model-level). Name the diagnosis it rests on and where that
diagnosis was measured.

## Population

Which questions/targets the mechanism can reach, counted. If fewer than the
reach floor the design sets, the rung closes *refuted for want of reach*
before anything runs.

## The bar

> Probe passes if <outcome> on ≥ B of N.

**Power check (mandatory):** `coin_pass_probability(B, N) = <value>` — computed
by `scripts/health/eval_retrieval_shipped.py`'s function of that name, quoted
here, and ≤ 0.05. Both of the arc's coin-flip bars (≥5/9 and ≥3/5, each 0.50)
were registered before this check existed; neither refutation survived on its
bar alone.

## Positive control

What in this run fails loudly if the instrument is dead, named now. The eval's
standing controls (schema, canary, spread) cover the shipped harness; anything
else this probe touches gets its own control here.

## Prediction

The honest expected outcome, with its calibration ("two prior prompts moved
zero of these same targets, so predicting movement would be over-confident").

## Per-question record

The outcome lands here as a per-target table — never a net total. A "+2" once
hid a +5/−3 split.

## Outcome

Filled after the run, whatever it says, in the same file the prediction sits
in. A refuted rung closes here with its numbers; a passed probe names the full
run it buys.
