# How to read the morning note and the nightly scorecard

> [!NOTE]
> **Goal:** Read what last night did in the morning note, then read the corpus scorecard's numbers in the order that makes them mean something, and know which ones are safe to ignore.
> **Prereqs:** a running daemon (`agentmd status` answers), and at least one night since the `morning-note` and `corpus-scorecard` jobs were registered. Both reports are written into the vault.

## Where they are

Both land under `diagnostics/` in the vault, one file per day plus a copy of the newest at a stable name. The morning note is `diagnostics/morning/YYYY-MM-DD.md`, and the corpus scorecard is `diagnostics/health/YYYY-MM-DD-health-scorecard.md`. Diagnostics has been its own vault space since filing-v2 part 2a; the old `desk/diagnostics/` location is gone.

```bash
ls ~/Vault/Agent/diagnostics/morning/latest_morning_note.md ~/Vault/Agent/diagnostics/health/latest_health_scorecard.md
```

`latest_morning_note.md` is about last night: what ran, what needs you, and what it cost. `latest_health_scorecard.md` is about the corpus: what is in it and whether it is degrading. The dreaming scorecard is gone, and the morning note carries what it used to report.

## Steps

### The morning note

The `morning-note` job writes it as the last step of the night, order 5 in the `02:00-06:00` window, after enrichment, the dreaming binary, the Python cycle and the corpus scorecard. Each of its four sections appears only when it has something to say.

1. **Read the last line of *What ran* first.** `Did not run last night:` names each nightly step that did not run, with the reason from the runner's last cycle: `disabled`, `dry run`, `not registered`, `outside-window 02:00-06:00`, `budget-ceiling`, and the rest. A step you ran by hand inside the window counts as having run. When the line is missing, every step ran.

2. **Read the enrichment line left to right.** The format is `N judged · N filed active · N below the floor · N sank · N calls · N tokens against the line · <model>`. Judged is how many notes the batch sent to the model, and the next three counts are what their verdicts decided. A note that sank is counted below the floor as well. `N failed` and `Stopped by <reason>` follow when a run had failures or reached one of its limits.

3. **Check the dreaming binary's gate before its table.** The binary's line names the pass's mode, its outcome and the gate's reason, and a table gives one row per job: lifecycle, copies, refile, promote, calendar, mocs, dates. When the runner started the binary and its gate held, the line reads `ran, and its gate held; the last pass was N ago` and there is no table. Nothing was due that night: too little time had passed since the last applying pass, or nothing had happened since.

4. **Fix `filing is halted` before anything else.** The Python cycle's line counts possible twins, shared keys, proposed facets, and three lint findings. When it reads `filing is halted` instead, the filing contract did not parse and every stage after that check stopped. The line carries the parse error.

5. **Work *What needs you* from the top.** Each list gives a count and its first five items: unfiled notes the batch judged below the floor, possible twins with their similarity, shared keys, proposed facets, the binary's archive candidates, and what sank in the last seven days. The last line links the needs-review map, which holds the full lists. See [Review flagged memories](Review-Flagged-Memories) for working it.

6. **Read *The corpus* as one sentence, then follow its link.** It gives the class populations, `N awaiting a judgment, the oldest <age>`, and `coverage N of M stamped at this pass`, then links the day's corpus scorecard. When the daemon does not answer, the line says `not measured` and why. It never prints a zero for a number it could not read. When coverage reads `not measured (the ledger answered 0 eligible twice over a corpus of N cards — read it again)`, run `agentmd ledger --pending --limit 0` yourself a minute later. An empty corpus still reads `coverage 0 of 0`.

7. **Check *Spend* against the operator's lines.** `Last night:` gives the tokens the night added against the line, each tier as `<tier> N of <line>` (2,000,000 a night on either tier), then the tokens it processed, calls against the 250-call guard, and dollars. The line counts what a call adds: input, cache writes and output. The processed figure also counts the cached prefix every call re-reads, so at a card's measured cost it runs about three times larger. The note holds the night to these numbers even when a run by hand lowered its own. `Seven days:` gives both token figures and dollars across the week's enrichment runs. `Sessions, the last day:` appears when the observability rollup recorded session spend.

8. **If the session-start line warns that notes stopped, look at the runner.** The line normally reads `[agentm] Morning — <headline> (written <age>)`. The headline is *What ran* in one line, with a count of the lists that need you. Once the newest note is two days old, the line reads `⚠ Morning note — none in N days (last: <date>); the night has stopped finishing — see runner.` Set `AGENTM_DIGEST_DEADMAN_DAYS` to change the two-day threshold.

### The corpus scorecard

1. **Read the corpus scorecard top to bottom, not by hunting for red.** The sections
   are ordered so each one gives the next its context: how much is in the corpus,
   whether enrichment kept what the sources said, whether the writing is converging,
   and what the graph looks like. The first line is **class populations** — flat
   memory counts per class directory (`semantic`, `procedural`, `episodic`,
   `entities`, `crystallized`, `mocs`), the accumulate loop's supplement lanes
   reported apart so a class holding only lanes doesn't read as populated. It exists
   because the six class directories once sat built and empty for months while the
   corpus lived in a staging area, with nothing counting them — filing-v2 part 3
   populated them, and this is the line that keeps that failure from going invisible
   again. `entities` and `mocs` commonly read zero: they're derived classes nothing
   routes to directly yet, not a sign anything is missing. `episodic` holds one
   session-trace note per session, written at session end — it populates as soon as a
   session runs on this vault. A diversity number means something different over 500
   notes than over 5.

2. **Read "needs review" and "writes per day" right after class populations.**
   Both replace what the old staging directory used to make visible just by
   its own size.

   **Needs review** counts notes still waiting for a judgment — filed at low
   confidence, still `unfiled`, or flagged as a probable duplicate or a
   same-key update — broken down by reason, with a link to the generated
   page. See [Review flagged memories](Review-Flagged-Memories) for how to
   work it.

   **Writes per day** is today's count against the volume gate's cap: the
   7-day mean, this week against last week, the fortnight's peak, and the
   headroom left before the gate refuses the next write.

3. **Check what is *unavailable* before believing what is green.** Every reading
   is either measured directly or marked unavailable-with-a-reason. There is no
   third state where a number is invented to fill a row. A section reporting `the sampled grading
   pass is not built yet` is telling you the truth; a section reporting `0.00`
   would not be.

4. **Read the completeness number with its sample size.** `claim-level coverage`
   is the headline, and the cell beside it says how many notes and how many
   replicates it came from. Five notes at three replicates is a smoke test, not a
   corpus measurement, and the row states that rather than leaving you to assume.

5. **For the diversity meters, read the direction, not the value.** There is no
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

6. **If the two embedding meters say they refused, that is correct behaviour.**
   They will not run without the dense arm rather than returning zero. The
   safeguard exists because zero dispersion is what a perfectly converged corpus
   looks like and zero similarity is what a perfectly diverse one looks like, so a
   missing embedder returning zero would report either "everything is fine" or
   "the corpus has collapsed" depending which row you read.

7. **Regenerate either report on demand** rather than waiting for the next night:

   ```bash
   python3 harness/skills/memory/scripts/morning_note.py
   python3 harness/skills/memory/scripts/corpus_scorecard.py
   ```

   A morning note run by hand covers everything since the most recent 02:00, and
   it replaces that day's note and the `latest_morning_note.md` copy.

## What these reports will not tell you

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

- [Memory daemon reference § the morning note](Memory-Daemon#the-morning-note) — every line the note can carry, where each is read from, and who reads the note.
- [Memory daemon reference](Memory-Daemon) — the subcommands each section reads from, and what the meters refuse to do.
- [Enable the daily email](Enable-Email-Digest-Delivery) — get the morning note by mail.
- [Review flagged memories](Review-Flagged-Memories) — working the needs-review page this scorecard's line counts.
- [CI gates reference](CI-Gates) — the deterministic checks, including the pinned retrieval evaluation these cards deliberately say nothing about.
- [Audit the vault](Audit-The-Vault) — the per-note lint, which asks the opposite question to a corpus-wide meter.
