# Week 3 retest — what did the alias backfill buy?

Run 2026-08-08/09 against the Go daemon, standing-scorecard run #2. The same 60
questions, the same 6-call budget, the same driver protocol and the same
integrity checks as the 2026-08-06 and 2026-08-07 runs. Fourteen runs report
zero hook violations, zero tool escapes, zero call-count mismatches, zero budget
leaks and zero driver errors.

Two things changed from week 1, and only two. Measurement now goes through
`agentmd`'s own `memory_search` over HTTP — the tool a real session calls, with
the daemon's ranking, its penalty constants and its tool description — instead
of the Python layer built for the experiment. And the variable is the alias
backfill: two copies of one frozen corpus, differing by exactly the 1,930
`aliases` lines dreaming's first job wrote, and by nothing else.

**Answer: the backfill costs 3.85 points of R@5, and why is an open question.
Write no more aliases until it is answered.** AL 0.6032 against NO 0.6417,
p = 0.0411 by exact permutation test over all 924 rearrangements, p = 0.0938 on
the paired secondary. Every stratum is negative or flat.

The mechanism is *not* established, and Result 3 records three candidate
explanations that were tested against the data and failed — including the column
weight, which the first draft of these notes asserted as the cause before
checking whether it predicted anything. What makes the result hard to read is
that at the tool level the aliased corpus is slightly *better*: it puts a gold
note in the top-5 for 54 of 206 recorded queries against 50 without them. The
loss appears between what the tool returns and what the agent concludes.

---

## What was measured, and why it is exact

`week3_daemon_retest.py` drives `claude -p` at the daemon through
`week3_daemon_shim.py`, a stdio MCP proxy that forwards `initialize`,
`tools/list` and `tools/call` unchanged. The shim does no searching. It adds the
two things a scorecard needs and the daemon deliberately does not do: a
six-call ceiling per question, held in the log file rather than in the process
so a second shim instance shares the budget instead of doubling it, and a record
of every call for the transcript audit to check against. `memory_capture` is
filtered out of `tools/list` — the corpus is frozen, and a write tool has no
business in front of a driver whose job is to search it.

The corpus was snapshotted once, before anything ran: 8,993 `.md`, content
fingerprint `0a4c2fe1cc1c153c`, archived to
`<vault>/_meta/corpus-snapshots/week3-retest-20260808.tar.gz`. It unpacks twice,
outside the vault. **AL** is as-is. **NO** is the same bytes with
`alias_backfill.py revert` replayed from the write journal: 1,929 notes
restored, one refused because an `_inbox` note had been edited since the
backfill wrote it. AL carries 1,986 notes with aliases, NO carries 57, and a
file-by-file diff finds exactly 1,929 differences, every one an `aliases:` line
or the frontmatter block created to hold one.

**This gold set can see the variable, which is new.** 63 of its 64 target notes
carry aliases in AL against 3 in NO. The 2026-08-07 penalty measurement could
only price the benefit side, because zero of the same 64 targets carried a
penalty class. This one exercises the treatment on nearly every right answer.

## Result 1 — the backfill costs 3.85 points of R@5

Six Opus replicates per copy. AL and NO ran concurrently inside each round, so
both copies met the same machine and the same API conditions at the same moment.

| arm | n | mean R@5 | sd | range |
| --- | ---: | ---: | ---: | --- |
| AL — aliases | 6 | 0.6032 | 0.0327 | 0.550 – 0.633 |
| NO — reverted | 6 | 0.6417 | 0.0233 | 0.617 – 0.669 |

Delta **−0.0385 R@5**, or −2.3 questions of 60. Exact permutation test over all
924 rearrangements: **p = 0.0411**. The paired sign-flip test over the six
rounds gives the same −0.0385 at p = 0.0938; it is reported second and is not
the verdict, because week 1's ruling rests on the unpaired test and choosing
between them after both are visible is the move pre-registration exists to
block.

Answer accuracy moves with it: 0.6778 against 0.7361, −5.84 points, p = 0.0411.

Per stratum, mean across the six runs of each arm:

| stratum | AL | NO | delta | p |
| --- | ---: | ---: | ---: | ---: |
| pure-paraphrase | 0.468 | 0.531 | −0.063 | 0.162 |
| negative | 0.458 | 0.521 | −0.063 | 0.472 |
| distinctive-token | 0.805 | 0.833 | −0.028 | 0.669 |
| episodic-temporal | 0.509 | 0.537 | −0.028 | 0.444 |
| research-density | 0.833 | 0.833 | 0.000 | 1.000 |

No stratum reaches significance alone, and none of them should be read as a
finding on its own. What matters is that all five point the same way. Paraphrase
was the stratum aliases were written for, and it is the one that lost most.

## Result 2 — the negatives guard, and what it does and does not say

Correct rejections on the negative stratum: **AL 0.458 against NO 0.521, −6.25
points, p = 0.4719.** Directionally this is the OR-rewrite failure returning
through the side door — added matchable surface talking the driver out of
concluding that no memory exists — but it is nowhere near significant, and six
replicates of eight questions cannot resolve a six-point difference. Reported
because the brief asked for it either way.

The honest reading: aliases did not cost correct rejections the way the OR
rewrite did. OR lost 18.8 points with the two arms not overlapping across six
runs each. This is a fifth of that, with the arms overlapping almost entirely.

## Result 3 — the surface changes a lot, and the mechanism is not identified

Read this section as three things that are measured and one that is not. The
first draft of these notes claimed the 3x `meta` column weight was the cause.
That claim was built from the 45.7% figure below without checking whether it
predicts anything, and when checked it does not. It is withdrawn.

The daemon weights `bm25()` at path 0, title 4, **meta 3**, body 1, where `meta`
is aliases plus tags. The backfill filled that 3x column across 21.5% of the
corpus with model-written paraphrase of each note.

Replaying all 206 queries the 2026-08-06 Opus run wrote, against both copies:

| | |
| --- | ---: |
| top-5 changed by the aliases | 61 of 206 (29.6%) |
| rank-1 changed | 32 (15.5%) |
| top-5 rows AL gained | 46 |
| top-5 rows AL dropped | 41 |
| **gained rows matching a query term found only in the alias line** | **21 of 46 (45.7%)** |
| queries returning nothing | AL 36, NO 39 |
| rows matched before ranking | AL 4,575, NO 4,541 |

Nearly half the rows the aliases promoted have no body evidence for the term
that promoted them. `pp12` is the clearest case: the question asks about
worktree auto-spawn authority, and three copies of a
`never-runs-silently-without-that-authority` note — archive, preferences and
inbox — get promoted on aliases containing "worktree", "auto" and "spawn", none
of which appear in their bodies. They crowd out the note that answers it.

**And that phenomenon does not explain the result.** Three tests, all free, all
negative:

| test | result |
| --- | --- |
| build the daemon with `weightMeta = 1.0` and re-serve AL | top-5 identical to NO goes 70.4% → 73.8%; alias-only rows 21 → 18 |
| per question, does alias-only exposure predict the loss? | exposed −0.051, partly exposed −0.111, not exposed at all −0.018. No dose-response |
| per question, does searching less predict the loss? | Pearson r = +0.088 over 60 questions; AL searched *more* on 7 questions and lost 0.095 on them |

The weight is a minor contributor, not the cause. The biggest winner (`pp13`,
+1.00) carries high alias-only exposure and the loser `pp16` carries none. And
`snippet()` reads the body column only, so a "the aliases read as more relevant"
story was never available — the agent never sees alias text.

**Where the loss lives, decomposed.** Every run records what the tools served,
so the score splits cleanly in two: did a gold note reach the agent's reading
surface at all, and having seen it, did the agent name it. Over the 52
answerable questions:

| | AL | NO | delta | p |
| --- | ---: | ---: | ---: | ---: |
| **retrieval** — a gold note reached the reading surface | 0.740 | 0.792 | −0.051 | 0.128 |
| **selection** — of those, the agent named it | 0.961 | 0.972 | −0.011 | 0.344 |

The loss is retrieval, essentially all of it. Selection is flat and near
ceiling — an agent that sees the right note names it 96-97% of the time in both
arms, so nothing here is about judgment or about how results read.

**And it is not static index quality.** Replaying a fixed query set, the aliased
index is if anything slightly better: 54 of 206 recorded queries put a gold note
in the top-5, against 50 without aliases — a four-query difference on a set that
small, which is noise, and certainly not a degradation. The live agents retrieve
worse anyway.

**The agents did not change how they search.** Comparing the ~1,200 queries each
arm actually wrote:

| | AL | NO | p |
| --- | ---: | ---: | ---: |
| terms per query | 4.34 | 4.39 | 0.167 |
| share of calls returning nothing | 0.117 | 0.115 | 0.846 |
| `k` requested | 5.87 | 5.79 | 0.063 |
| distinct queries per question | 0.999 | 0.997 | 1.000 |

Same query shapes, same iteration, same empty rate. So the aliases reorder
results without changing how the agent asks — and on the queries agents actually
write, that reordering lands worse more often than better.

That is as far as this data goes. The effect is real, negative, and located in
retrieval-in-context; the reason a reordering that helps a fixed query set hurts
a live one is not established, and the fixed-query replay is structurally unable
to answer it — it holds the agent's queries constant, which is exactly the
variable that matters here.

Two things this does rule out. It is not that the aliases are badly written:
search a note's own aliases and the daemon returns it at rank 1 nearly every
time. And it is not that they failed to reach the index: 29.6% of a real agent's
queries came back different.

Note also what barely moved. Queries returning nothing went 39 to 36, and rows
matched before ranking rose 0.7%. FTS5 still ANDs every term, so four alias
phrases rarely complete an all-terms match. Aliases do not fix the empty result
set; that defect is still open and still wants a minimum-score floor.

## Result 4 — the six shared misses are unresolved, and four got worse

Correct across the six replicates of each copy:

| question | AL | NO |
| --- | ---: | ---: |
| `dt12` | 1/6 | 3/6 |
| `ep09` | 2/6 | 3/6 |
| `ep12` | 4/6 | 6/6 |
| `ng02` | 0/6 | 0/6 |
| `pp05` | 0/6 | 1/6 |
| `pp07` | 0/6 | 0/6 |

None resolved. Four are worse with aliases than without.

**`pp05` and `ep12` — does their vocabulary exist now?** Partly, and it does not
help. `ep12`'s gold note gained "why switched from antigravity to claude", and
replaying the agent's own query "switched from Antigravity to Claude Code" now
finds it at rank 1 in AL and nowhere in NO — the alias did exactly its job at
the tool level. The question still scores worse in AL, because the other five
queries the agent writes for it now return alias-promoted notes instead.
`pp05`'s gold note gained "home network project overview" and siblings, which
paraphrase what the note says rather than what the operator asked ("pending
project ideas for the house"), and all five of the agent's recorded queries
still return nothing at any depth.

That is the structural limit worth recording: **aliases are generated by a model
reading the note, so they paraphrase the note. The paraphrase gap is between the
note and the operator's future question, and a paraphrase of the note does not
close it.** `pp07` shows the other edge — one of its two gold notes carries no
aliases at all, because it sits in a penalized class the backfill skips by
design.

One question genuinely won. `pp13` goes 0/6 in NO to 6/6 in AL. It is the only
one of its size in the set, and it does not cover the losses.

## Result 5 — against week 1, directional only

The corpus grew 8,599 → 8,700 → 8,993, and the search surface changed from the
experiment's Python layer to the daemon. Only the AL-against-NO pair above is
exact. These are for orientation:

| run | corpus | overall R@5 | paraphrase R@5 |
| --- | ---: | ---: | ---: |
| 2026-08-06 live Opus, Arm A | 8,599 | 0.725 | 0.472 |
| 2026-08-07 control replicates (n=6) | 8,700 | 0.616 | 0.440 |
| 2026-08-07 penalty replicates (n=6) | 8,700 | 0.654 | 0.488 |
| week 3, NO — daemon, no aliases (n=6) | 8,993 | 0.642 | 0.531 |
| week 3, AL — daemon, aliases (n=6) | 8,993 | 0.603 | 0.468 |

The daemon without aliases lands within a point of the penalty replicates
overall and four points above them on paraphrase, on a corpus 3% larger. The
daemon's ranking is not worse than the harness it replaced. The 2026-08-06
figure of 0.725 remains an outlier no run since has reproduced, which the
2026-08-07 notes already flagged as an unexplained cross-day gap.

Fable, one run per copy, excluded from every test: AL 0.700, NO 0.694. It is
flat where Opus is negative, and it scores above Opus on both copies — the
reverse of week 1, where Opus led Fable on Arm A by nine points. One run each is
an illustration, not a result.

## Result 6 — calls, wall time, and where the daemon is actually slow

Tool calls per question: AL 3.39 against NO 3.51, −0.12, p = 0.0433. The aliases
buy about a tenth of a call, which is the agent stopping sooner because
something plausible came back. Given the score went down, stopping sooner is not
a saving.

Wall time per question: 23.36s against 23.35s, p = 0.98 — the model dominates,
and the corpus does not move it. Week 1's Opus Arm A run sat at 22.2s per
question on a smaller corpus.

**Is the daemon faster than the Python shim? As measured, at the median only.
After the fix this run produced, at every point of the distribution.**

The in-run figures cannot answer this — they time a full HTTP round trip while
two Opus drivers stream on the same laptop — so both were re-measured on an idle
machine over the same 206 queries, with the transport floor measured separately:

| | median | mean | p90 | max |
| --- | ---: | ---: | ---: | ---: |
| daemon `ping` (transport floor) | 0.22ms | 0.31 | 0.26 | 4.4 |
| daemon `memory_search` — as the retest ran it | 56.5ms | 256 | 360 | 5,654 |
| daemon `memory_search` — after PR #423 | **7.0ms** | **62** | **82** | **2,750** |
| week-1 Python, penalty on — like for like | 66.5ms | 133 | 228 | 2,139 |
| week-1 Python, no penalty — what week 1 timed | 11.6ms | 36 | 87 | 946 |

Read the penalty-on Python row as the comparison: the daemon's penalty is
compiled in, and a penalty means fetching 200 rows and re-ranking rather than
fetching five. Against that, the daemon as measured won the median by 13% and
lost the tail badly; after the fix it wins the median by 9.5x, the mean by 2.1x
and p90 by 2.8x. Both Python arms moved under 2% between the two benchmark runs,
so the machine was in comparable shape for both.

The tail had one cause. FTS5's `snippet()` was computed inside the ranking
query, for every one of the up-to-200 over-fetched rows, before the penalty
re-rank discarded most of them. Run against the daemon's own index:

```
SELECT path, bm25(...)                          FROM docs WHERE docs MATCH 'homelab server'
    →    3.1ms
SELECT path, bm25(...), snippet(docs,3,…)       FROM docs WHERE docs MATCH 'homelab server'
    → 1784.3ms
```

575x, on a query that matches 43 rows. The matched set includes notes of 1.0 to
1.3 MB, and `snippet()` scans the document. Six queries in the set cost the
daemon four to six seconds each, reproducibly — re-running the slowest returned
5,748ms against 5,773ms, so it was not a cold cache.

**Fixed in PR #423**, which ranks first and snippets only the surviving k rows.
The retrieval numbers above were all measured on the pre-fix build, so they were
re-verified against the post-fix daemon rather than assumed unaffected: over the
same 205 recorded queries, result paths, their order, and their scores are
identical to six decimal places. The fix moved latency and nothing else.

Index build, for completeness: the daemon indexed 8,993 notes in 2.6s from cold,
the Python layer in 0.8s. Neither is the daemon's real advantage, which is that
the index already exists when a session starts.

## What this measurement cannot tell you

- **It prices these aliases, not aliases.** Every one was written by Sonnet
  reading the note. A different generator — one writing from observed questions
  rather than from note content — is a different treatment and this result does
  not bind it.
- **It cannot separate the column weight from the content.** Aliases entered a
  3x-weighted column, and the mechanism section says that weight is what makes
  wrong hits expensive. Whether aliases indexed at body weight would help, hurt
  or do nothing is untested, and it is the cheapest next experiment: no new
  writes, one constant, replayable offline.
- **The negatives stratum is eight questions.** Six replicates of eight
  questions cannot resolve six points. The guard reports a direction, not a
  verdict.
- **Fable is n=1 per copy.** Two runs, no inference.
- **Cross-day comparisons remain unreliable.** The 2026-08-06 run's 0.725 has
  never been reproduced, on either harness, and was not isolated here either.

## Reproducing

```bash
# restore the two copies (see the snapshot README in the vault for the revert)
agentmd serve --vault ~/.agentm/corpus-snapshots/week3-AL \
    --index ~/.agentm/week3-retest/index-AL.db --port 0

# one live run, ~20 minutes and ~$5
python3 scripts/health/week3_daemon_retest.py \
    --gold-set scripts/health/fixtures/week1-gold/gold-set.json \
    --daemon-url http://127.0.0.1:<port>/mcp --copy-name week3-AL \
    --label al-opus-r1 --model opus --out <raw-dir>/al-opus-r1.json

# free and deterministic
python3 scripts/health/week3_analyze.py --raw-dir <raw-dir>
python3 scripts/health/week3_miss_probe.py --al-url … --no-url … --al-vault … \
    --gold-set … --week1-report … --surface-replay
python3 scripts/health/week3_latency_bench.py --daemon-url … --vault … --week1-report …
```

## The scorecards, and what is worth committing

| file | what it holds |
| --- | --- |
| `week3-retest.json` | all 14 runs — configuration, integrity, scores, one line per question — plus every test above |
| `miss-probe.json` | the six shared misses traced query by query, and the 206-query surface replay |
| `latency-bench.json` | the latency arms as the retest ran, on the pre-#423 daemon |
| `latency-bench-post423.json` | the same benchmark after the snippet fix landed |

Raw scorecards are ~200KB each and stay on the machine that ran them, under
`~/.agentm/week3-retest/raw/`. The convention is unchanged from 2026-08-07: the
summary is the durable artifact and the raw is regenerated by re-running.

The reading surface is worth one line. AL served 213 `fragment-promoted` rows in
its top-5s against NO's 44, out of ~5,400 each. That class is classified but not
penalized — filing already promoted those notes — so this is not demoted junk
resurfacing. Genuinely penalized rows were 4 in AL and 1 in NO. The rank penalty
is working in both copies.

This retest cost $98.59 across 14 live runs.
