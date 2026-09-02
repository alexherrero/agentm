# How to read the two nightly scorecards

> [!NOTE]
> **Goal:** Find the corpus-health and dreaming scorecards, read the numbers in the order that makes them mean something, and know which ones are safe to ignore.
> **Prereqs:** a running daemon (`agentmd status` answers), and at least one nightly cycle since install. Both scorecards are written into the vault, not the repo.

## Where they are

Both land under `diagnostics/` in the vault, one file per day plus a stable
pointer at the newest — the corpus card under `diagnostics/health/`, the
dreaming card under `diagnostics/dreaming/`. Diagnostics became a first-class
vault space in filing-v2 part 2a; the old `desk/diagnostics/` location is gone.

```bash
ls ~/Vault/Agent/diagnostics/{health,dreaming}/latest_*_scorecard.md
```

`latest_health_scorecard.md` is about the corpus — what is in it and whether it is
degrading. `latest_dreaming_scorecard.md` is about the pass that maintains it —
what ran, what it cost, and what it could not finish.

## Steps

1. **Read the corpus scorecard top to bottom, not by hunting for red.** The
   sections are ordered so each one gives the next its context: how much is in the
   corpus, whether enrichment kept what the sources said, whether the writing is
   converging, and what the graph looks like. A diversity number means something
   different over 500 notes than over 5.

2. **Check what is *unavailable* before believing what is green.** Every reading
   is either measured directly or marked unavailable-with-a-reason. There is no
   third state where a number is invented to fill a row. A section reporting `the sampled grading
   pass is not built yet` is telling you the truth; a section reporting `0.00`
   would not be.

3. **Read the completeness number with its sample size.** `claim-level coverage`
   is the headline, and the cell beside it says how many notes and how many
   replicates it came from. Five notes at three replicates is a smoke test, not a
   corpus measurement, and the row states that rather than leaving you to assume.

4. **For the diversity meters, read the direction, not the value.** There is no
   good absolute number for trigram concentration. What matters is which way it
   moved since the last card:

   | Meter | Worry when |
   |---|---|
   | trigram concentration | rising — the same phrases recurring |
   | lexical diversity | falling — vocabulary narrowing |
   | pairwise similarity | rising — every note drifting toward every other |
   | nearest-neighbour dispersion | falling — clusters tightening first |

   The last one moves earliest. Convergence starts locally, so a few notes tighten
   around each other while the corpus-wide average is still flat.

5. **If the two embedding meters say they refused, that is correct behaviour.**
   They will not run without the dense arm rather than returning zero. The
   safeguard exists because zero dispersion is what a perfectly converged corpus
   looks like and zero similarity is what a perfectly diverse one looks like, so a
   missing embedder returning zero would report either "everything is fine" or
   "the corpus has collapsed" depending which row you read.

6. **Read the dreaming scorecard for what did *not* happen.** The useful rows are
   the deferred work and the dead-lettered items, not the completed count. A cycle
   that finished everything and a cycle that ran out of budget both look busy; only
   the deferral count distinguishes them.

7. **Regenerate on demand** rather than waiting for the next cycle:

   ```bash
   python3 harness/skills/memory/scripts/corpus_scorecard.py
   ```

## What these scorecards will not tell you

**Whether a specific note is good.** Every number here is over a sample or the
whole corpus. To ask about one note, read it.

**Whether retrieval is working.** That is the pinned evaluation
(`scripts/check-retrieval-regression.sh`), which scores a frozen gold set and is
the only thing that speaks to ranking. A healthy-looking corpus scorecard says
nothing about whether the right note comes back.

**Whether the numbers moved because the corpus did.** A meter can move because
the sample window shifted or because a migration rewrote frontmatter. Before
reading a change as a trend, check whether anything ran between the two cards.

## Related

- [Memory daemon reference](Memory-Daemon) — the subcommands each section reads from, and what the meters refuse to do.
- [CI gates reference](CI-Gates) — the deterministic checks, including the pinned retrieval evaluation these cards deliberately say nothing about.
- [Audit the vault](Audit-The-Vault) — the per-note lint, which asks the opposite question to a corpus-wide meter.
