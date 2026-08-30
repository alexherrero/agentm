# RULE — when a string counts as evidence a note was used

**Frozen:** 2026-08-29, before the run that tested it.
**Amended:** 2026-08-29, twice, after hand-reading the run's own output.
**Applies to:** `_candidates` / `rare_evidence` / `slug_evidence` / `used_slugs`
in `scripts/health/recall_traffic.py`.

## What moved and what did not

The **bar never moved**: a string counts only if the corpus produces it in
fewer than 1% of turns, and that threshold is the one written down before the
first run. What changed twice is *which strings are held to it*, each time
because reading real verdicts by hand found the candidate set too generous.

Every revision made the reported number **worse** — 14.0% → 1.8% → 0.9% →
0.5% → 0.2%. Nothing here was tuned toward a flattering outcome, and the
sequence is recorded so a later reader can check that.

## What went wrong, three times

**First: a hand-written stoplist.** Any word over six characters counted as
distinctive unless it was on a list I wrote. A hand-check of six turns found
`"carry header_path, content, and embedding together"` scoring a hit for the
note `i-want-to-put-together-to`. Measured over the whole corpus, **372 of 419
verdicts (88.8%) rested on a single word**, led by `progress` — which names 86
notes and appears in 22% of answers. A stoplist would have to enumerate English
to catch that, so it was replaced by measured rarity.

**Second: an exemption for the slug itself.** The rewrite exempted a note's own
name from the rarity test, on the reasoning that a name is an identifier rather
than a word. True of `agentm-auto-organization`; false of `design-doc`, whose
spaced form is the ordinary phrase "design doc" — and which collected **ten of
the twenty-six verdicts then standing**. Single-word slugs were worse still:
`progress` and `recommendations` scored whenever those words appeared. The
exemption was removed; the name is held to the same bar as anything else.

**Third: fragments.** With the exemption gone, 15 verdicts remained and
hand-reading all of them found **7 false**. Every false one was a fragment:
`observability` matched inside an unrelated `observability-email-daily.yaml`,
`20260813` inside a different timestamp, `notifications` and `influences` as
plain English sitting just under the 1% bar. Every true one had the note's
whole name in the text. Fragment matching was removed.

## The rule

A string is evidence that a note was used when **both** hold:

1. **It is the note's whole name** — verbatim, or with `-`/`_` as spaces.
   A piece of a name is not the name.
2. **The corpus rarely produces it anyway** — present in fewer than 1% of
   turns, measured by substring over turns, the same way the match is made.

**Where 1% comes from — a contamination budget, not a knob.** Traffic carries
~4.5 injected notes per turn. If a name fired at background rate *p*, expected
false positives are `4.5 × turns × p`; holding that under a tenth of the
verdicts observed puts *p* near 0.014, rounded down to 0.01 for strictness.

**No corpus, no evidence.** With rarity unmeasurable, `slug_evidence` returns
nothing. A floor computed without the means to check should come out too low
rather than too high.

**A corpus under 101 turns is refused, not scored.** The smallest share *n*
turns can express is `1/n`, so below `1/0.01` turns nothing can clear the bar
and every run would report zero. Reporting that zero would state a fact about
the corpus as a fact about recall, so `overlap_summary` returns a refusal
instead.

## What it predicted, and what happened

Written before the first run: *the rate will fall, and most of the 372
single-word verdicts will disappear.* It fell to 1.8%. The two later
amendments, each also written before its re-run, predicted further falls; the
final figure is **7 named notes out of 3,004 injected, 0.2%**, and all 7 were
read by hand and stand up.

## How it can fail

- **A note genuinely used but never named.** Scored zero. This is the dominant
  case, not an edge one: models rarely write the names of notes they read. The
  number is a naming rate, and the gap between naming and using is precisely
  what the judge in task 5 exists to measure.
- **A note used so often its name becomes common.** Its name's background share
  climbs past 1% and it stops being able to score at all. The bias is toward
  under-counting the most-used notes, which is conservative but real.
- **Background rates drift as traffic grows.** Recomputed per run from the same
  corpus being scored; each run reports how many names cleared the bar, so two
  runs over different corpora are visibly different instruments.

## Why it is worth keeping at 0.2%

As a comparator it is nearly floored, and that is itself the finding: the
deterministic signal cannot carry this measurement, so the judge has to. Kept
because it is honest, cheap, and it fails loudly — if a change ever makes
models name their sources, this is the number that moves.
