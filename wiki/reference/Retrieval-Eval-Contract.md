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
k=5 against `gold-set-v3.json` (60 scored questions, 20 easy negatives, 10
near-miss negatives; a question carrying `not_counted` is neither run nor scored). Baselines embed a corpus fingerprint (document count,
embedded-in-scope count, gold-set hash, pin date); `--compare` refuses across
fingerprints unless `--drifted-ok` says the drift is understood, and a
gold-set hash mismatch is never overridable. R@5 is the product metric — the
hook injects five — and R@1 rides along as the ordering signal.

## The instrument's resolution

Printed on every report, so nobody can pre-register a bar the instrument
cannot resolve without having seen it refuse the idea:

| scored n | smallest clean gain (6 flips) | Wilson 95% width near 75% |
|---|---|---|
| 31 (the gate, since 2026-11-21) | +19.4pp | ~32pp |
| 64 (the whole set before the split) | +9.4pp | ~21pp |
| 100 | +6.0pp | ~17pp |
| 125 | +4.8pp | ~15pp |
| 250 | +2.4pp | ~11pp |

The gate/report split (below) halved the set the verdict is read on, so the gate now needs a
19-point move to see anything. Growing the gate back toward 60 with new
questions whose answers are stable is how to win that back. A third of the
gate today is `resources/topics` (rd01–rd10), so project designs and
decisions are where new questions are most needed.

Six flips is derived from the exact paired test (two-sided binomial:
2·(½)^k < 0.05 first at k=6), not chosen. A true 5pp improvement at n=64
best-cases p=0.25 — invisible. Per-target rank probes remain the sensitive
instrument for sub-MDE mechanisms: they measure movement exactly, and they are
how three alias strategies were shown to move nothing at all.

## Gate questions and report questions

Since 2026-11-21 each scored question carries a `group`, and only one group can
fail the gate.

- **Gate questions (31).** The answer is a note nothing is meant to demote:
  resources, standards, system pages, living designs, durable decisions, and
  memory cards not merged into a lesson. The exact paired test runs on these
  alone.
- **Report questions (29).** Every expected note is one the vault demotes on
  purpose: finished work in a `completed/` or archive folder, a card merged
  into a lesson, a replaced note, an open task that moves when it closes, or a
  project walled to its own folder (`recall_cwd_match`). They are scored and
  their flips printed beside the verdict, so the cost of those demotions stays
  visible, but they never fail the gate.

A gate question that also names demoted notes lists the ones it is scored on in
`gate_note_paths`. Both fields are part of the question paper and move the
gold-set hash.

**Why.** Before the split, every question gated, and a deliberate filing pass
read as a ranker regression. The content review of 2026-11-03 moved finished
designs into `completed/`, where they rank at ×0.30, and seven questions
flipped to a miss overnight. The Shrimpi wall of 2026-11-20 took one more. The
gate went red on 2026-11-21 with p 0.0225, and nothing in the ranker had
changed. Split the same way, the gate read 23 → 20 of 31 (p 0.375) and the
report 18 → 11 of 29.

**Membership is fixed at the pin.** The groups are written into the gold set,
not worked out each night. If they were worked out from the index's flags, a
classifier bug that flagged notes wrongly would quietly move questions out of
the gate, which is the failure the gate exists to catch. When a gate
question's note is later demoted, the eval names it on a `left the gate:`
line and leaves it out of the paired test until the next re-pin moves it. The
check reads the note itself (its path, `consolidated_into`, `status` and
`lifecycle`, and its project's `project.yaml`), never the index.

## The slide warning is not the gate

The nightly scorecard also prints **hit rate, week by week** (`retrieval_trend.py`,
task 205). It averages the scorecard's live nightly readings by ISO week and
warns when the weekly rate fell in each of the last three week-to-week steps,
or when the newest week is 5 points or more under the best of the last eight.

It exists because a slow slide never reaches six flips on any one night. From
late October to mid-November 2026 the rate went from 42 of 63 to 35 of 60, the
gate's p-value drifted from about 0.5 to 0.09, and every night passed.

It is advisory and sits beside the gate, not inside it:

- the gate's verdict, the exact paired test and its p < 0.05 line are unchanged;
- the pinned baseline is never re-pinned by it;
- it reads rates rather than counts, so retiring a question does not read as a slide;
- it counts only rows that say "measured <date> on <N> documents". Before
  2026-10-31 the scorecard row reprinted the pin, and a reprint is not a reading.

The gate job keeps each night's verdict and p as a row in the health folder's
`history.jsonl` (report `retrieval-gate`), so the p-value's path is on record
rather than only the last night's.

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

## The online metric's own rules

The rules above govern the offline gold-set ladder. Live traffic adds these.
Each one exists because breaking it produced a wrong number once.

| rule | why | breaking it cost |
|---|---|---|
| **No raw prompt text on disk** | the judge's account of a gap quotes the request by construction | a repository holding the operator's prompts |
| **Deterministic comparator runs and is reported apart** | it fired on 7 of 3,004 injected notes — showing that is the point | a floor mistaken for a second opinion |
| **Suppress the comparison when the floor never fires** | the disagreement then equals the judged rate exactly | one number printed twice as though two signals agreed |
| **No rate without the instrument's resolution beside it** | the judge drifts ~5 points across identical runs | a precision claim the instrument cannot support |
| **Always-valid interval, not fixed-horizon** | the number is read whenever someone wonders | over 800 peeked streams, a Wald interval was wrong on 43–46% against a sequence's 0.0% |
| **Measure at the change, paired and interleaved** | the judge's ~5-point drift is common-mode and cancels in a difference | two rates a month apart, each carrying the full drift |
| **A sample too small says so** | ~45 judged turns clear the bar, 274 reach ±0.10 | `[0%, 54%]` rendered as if it were a range |

**κ before publication does not apply here**, and the exemption is recorded
rather than waived. No independent human labels are obtainable from this data —
unaided ones proved error-prone, aided ones are not independent — so the online
row carries a permanent `UNVALIDATED` marker with its reason attached, instead
of a κ it cannot compute.

## Related

[CI-Gates](CI-Gates) · the gate script `scripts/check-retrieval-regression.sh`
· results record `scripts/health/results/goldv3/NOTES.md` · design
[agentm-hybrid-retrieval](../designs/agentm-hybrid-retrieval).
