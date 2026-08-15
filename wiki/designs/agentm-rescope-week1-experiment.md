---
title: AgentM Rescope — Week 1 Retrieval Experiment
status: proposed
kind: design
scope: feature
area: agentm
parent: agentm-rescope-principles.md
seeded: 2026-08-02
---

# AgentM Rescope — Week 1 Retrieval Experiment

## Purpose

This experiment settles one question before any daemon code is written: does FTS5 lexical search, driven by an agent that iterates (query, read results, re-query), retrieve well enough on this vault, or does the daemon need a vector search sidecar from day one.

It runs on the existing Python stack, in the current agentm repo, with no new code beyond a runner script and a gold set. It produces two durable artifacts — the gold set and the scorecard it generates — that carry forward as the ongoing recall-quality measurement principle 3 requires for every week after this one.

## Why not the simpler version

An earlier version of this experiment was proposed as: expand each question into keywords with a single LLM call, `grep` the vault for those keywords, and measure how often the target note is found. That version was rejected on review for four compounding flaws, and the rejection matters enough to record so nobody re-proposes it by accident.

`grep` has no ranking — it returns a match or nothing, which understates what FTS5 with BM25 ranking will actually do. A single blind keyword expansion is not how the real client behaves — an agent with a search tool reads the first round of results and refines its query, the same way Claude Code finds things in a codebase with nothing but ripgrep. "Measure recall" without a hand-labeled gold set measures nothing, because there's no ground truth to score against. And a single-arm number is uninterpretable on its own; the decision needs a delta between lexical-only and lexical-plus-vector, not an absolute score.

This experiment fixes all four: real ranking, an iterating agent, a hand-labeled gold set, and two arms scored against the same set.

## The gold set

Sixty questions, hand-labeled with the note (or notes) that correctly answers each one. Source them from two places: real prompts pulled from past session transcripts, and questions the operator writes fresh, from memory, without looking anything up first. Both sources matter — transcript-sourced questions are how people actually ask, and cold-written questions catch the paraphrase gap that transcripts (where the operator already half-remembers the vocabulary) tend to under-represent.

Split across five strata:

- **12 distinctive-token** — the question shares at least one uncommon, specific term with the target note (a proper noun, a package name, a slug). This is the stratum lexical search should win outright.
- **18 pure-paraphrase** — the question shares no content word with the target note's text. The operator remembers the concept, not the vocabulary the note actually used. This is the stratum that decides the experiment on recall.
- **12 episodic/temporal** — the question references when or in what context something happened ("what did we decide about the vault path the week the GDrive conflicts started"), not just what.
- **10 research-density** — the question targets one note inside a dense cluster of near-identical notes on the same topic, the shape a research sweep produces: several hundred atomic `reference` memories that all share vocabulary because they're all about the same subject. Every other stratum tests whether the right note can be *found*; this one tests whether it can be *distinguished*, which is a precision failure rather than a recall failure and the one lexical ranking is least equipped for. If the vault has no such cluster yet, build one first by running the scenario — point a session at a topic and let it capture — because this workload is coming either way and the experiment should meet it before the daemon does.
- **8 negative** — the true answer is "no such memory exists." These score whether each arm can correctly return nothing instead of a confident wrong guess.

Two labeling constraints. Don't hand-label an expected answer that points at a file scheduled for retirement (`agentm-rescope-memory.md` lists what goes) — the gold set outlives this experiment and shouldn't carry references to notes that won't exist at cutover. And expect inbox-staged items to rank low: they are rank-penalized by design rather than by accident, so a low-ranked `status: inbox` result is the system working, not a miss to score against.

Each gold-set entry: `{id, question, expected_note_paths: [...], stratum, source: "transcript" | "cold"}`.

A P@5/R@5 harness already exists at `scripts/health/eval_v6_retrieval.py`, built for a prior 22-query set (`scripts/health/fixtures/v6-eval/query-set-v0.json`). Reuse its scoring code. Do not reuse its query set — all 22 of those queries target `projects/agentm/_harness/designs/` documents, which is the system's memory about itself, not a sample of the operator's actual recall needs. Build a new 50-question set against the full vault instead.

## The two arms

Both arms are the same driver: `claude -p`, given a system prompt describing a search tool, allowed **at most 6 tool calls** per question, instructed to answer with the note path(s) it believes answer the question (or "no answer found").

**Arm A — lexical only.** One tool: FTS5 + BM25 search over all 8,030 files in the vault, using SQLite's built-in FTS5 (no loadable extension required — this runs even under Apple's system Python, unlike the existing sqlite-vec path). The agent can call it repeatedly with different query formulations within its 6-call budget.

**Arm B — lexical plus vector.** The same FTS5 tool, plus a second tool backed by the existing `embed.py` (sentence-transformers, warm-loaded once for the whole experiment run, not per-question) doing brute-force cosine similarity over the same 8,030 files. Brute-force is fine at this scale — no ANN index needed for a one-time experiment.

Run both arms against all 50 questions. Score each with P@5/R@5 against the gold set's `expected_note_paths`, broken out per stratum.

## The decision rule

Pre-registered before the results are seen, so the outcome can't be rationalized after the fact:

**If Arm B beats Arm A by fewer than 5 points of R@5 overall, and by fewer than 10 points on the paraphrase stratum specifically, ship FTS5-only.** The vector sidecar is not in the v0 daemon. If either threshold is crossed, the daemon's v0 build includes the supervised vector-search sidecar from the start (see `agentm-rescope-topology.md` for what that sidecar is — a llama.cpp-served small embedding model, not the current sentence-transformers/PyTorch stack).

FTS5 ships in either outcome. This experiment decides whether it ships alone.

## The outcome

Run 2026-08-06 against the full 60-question gold set, both arms, under two drivers. Every integrity check returned zero across all four runs:

- No hook fired into the driver's context.
- No tool was used outside its arm's permitted set.
- The daemon's call count and the transcript's agreed on every question.
- No budget leaks.

| driver | Arm A R@5 | Arm B R@5 | delta | rule says |
| --- | ---: | ---: | ---: | --- |
| Opus | 0.725 | 0.672 | −5.3 | FTS5-only |
| Fable | 0.631 | 0.686 | +5.5 | sidecar |

The rule gives opposite answers depending on the driver, and the pre-registration never fixed which driver counts. That is a real gap in the rule as written, not a tie to break on preference.

**Ruling: the Opus run binds. Ship FTS5-only; no vector sidecar in the v0 daemon.** Opus was the declared first run and Fable was a sensitivity check. Picking which run counts after both numbers are visible is the exact move pre-registration exists to prevent, so the later result cannot promote itself to primary.

Adding the sidecar anyway would hurt the driver actually in use. Opus measured a drop, 0.725 down to 0.672, with the agent spending 92 of its 201 calls on semantic search that displaced lexical retries Opus does well.

The cost also runs past the child process. Embedding every captured note either makes capture wait on a model, which breaks the offline, no-judgment-in-the-write-path property the topology design turns on, or it restores an asynchronous embedding queue. That second mechanism is the one whose silent stall left recall returning nothing for four months.

What the Fable run establishes is still worth keeping. Arm B is nearly driver-independent, 0.672 against 0.686, while Arm A is not, 0.725 against 0.631. Lexical-only quality depends on how well the driver guesses the vocabulary a note actually used, so the sidecar buys consistency rather than accuracy. That makes the finding conditional, not wrong.

*Re-audit trigger:* adopting a driver weaker than Opus for `memory_search`, a small local model above all. Arm A degrades in that case and this ruling stops holding. Re-run both arms against the gold set with the candidate driver before assuming FTS5-only survives. The run costs roughly $15 and an hour, and any backfill it calls for is a one-time corpus embed.

Per-arm scorecards and per-question miss lists live in `scripts/health/results/week1/`. Six Arm A questions missed under both drivers — `dt12`, `ep09`, `ep12`, `ng02`, `pp05`, `pp07`. A miss shared across drivers points at the corpus or the tool rather than the model, so those six seed the failure-pattern log.

## The rank-penalty follow-up

Run 2026-08-07 to settle a question this experiment left open: `agentm-rescope-memory.md` calls for rank-penalizing miner fragments, and nobody had measured whether that helps before the daemon builds it in. Full write-up in `scripts/health/results/week1/RANK-PENALTY-NOTES.md`.

**The penalty works. Ship it: +3.75 points of R@5, p = 0.0195.** Six control replicates against six penalty replicates on a frozen corpus snapshot — 0.6162 against 0.6537, by an exact permutation test over all 924 rearrangements. The gain concentrates in the episodic and negative strata, which is where the mechanism predicts it: fragments are quotations of the operator's own speech, so they compete hardest on questions asked in his words rather than the note's.

**The penalty's strength is not a parameter.** A 125-point sweep produced four distinct outcomes, and every weight at or below 0.6 gave identical rankings. Only whether a class is penalized matters. The daemon gets a constant, not a tuning knob.

**One correction to the numbers above.** The 0.144 paraphrase P@5 is not evidence that answers came back padded with junk. `score_at_k` divides hits by a fixed k for precision and by the label size for recall, so on this gold set the two are the same measurement rescaled — zero divergences across all 52 non-negative questions. A question with one gold note scores P@5 = 0.200 when the answer is perfect. Read R@5.

**The two knobs a follow-up would reach for first were already in the baseline** — porter stemming and a 4x title weight, both shipped on 2026-08-06. Ablation prices them: porter is worth +5.7 points of tool-level hit@5 and is the only knob that moves hit@5 at all; the title weight is worth +3.8 hit@1. A dedicated `aliases`/`tags` column changes nothing yet, because 5.5% of the corpus has anything to put in it. It becomes measurable after dreaming's alias backfill.

**A real defect sits upstream of ranking, and the obvious fix is the wrong one.** FTS5's bare `MATCH` ANDs every term, so a six-word paraphrase must find a note containing all six words. On the 2026-08-06 run that returned nothing at all for 32 of 206 queries and fewer than five results for 62. OR-joining the same queries looked like the largest win available — roughly twice the penalty at the tool level, and +8 points on a single live run. Replicated six times it adds **+1.25 points at p = 0.46**, and loses **18.8 points of correct rejections at p = 0.0087**, with the two arms not overlapping across six runs each. OR never returns an empty result set, so an agent that would have concluded that no memory exists is handed five plausible notes and names one. Do not ship it. The empty result set is still worth solving; a minimum-score floor, below which the rewrite returns nothing rather than its best partial match, is the untested candidate.

**The six shared misses are vocabulary failures, and no ranking change touches them.** For three of them the gold note never appears at any depth for any query the agent wrote; for the other three it sits at rank 42, 50, and 238, behind legitimately-matching documents rather than behind fragments. They wait on `aliases`.

*Three cautions this run earned.* A single run of this harness is not an effect: the OR rewrite measured +8 points once and +1.25 on replication, and a variant with provably zero retrieval effect swung one stratum 36 points. Six same-config replicates spread 2.5 points, so an effect smaller than that needs replication to see at all. And the vault moves while you measure it — the corpus grew from 8,599 to 8,709 over the course of the follow-up, partly from the measuring session's own capture hooks depositing fragments into the class under test. Snapshot the corpus before comparing anything.

## The week-3 retest — standing-scorecard run #2

Run 2026-08-08/09, the first scorecard run the standing note above calls for.
Two questions: what did dreaming's alias backfill buy, and does the gold set
still behave the same way through the daemon's own `memory_search` rather than
the experiment's Python layer. Full write-up in
`scripts/health/results/week3-retest/NOTES.md`.

**The backfill costs 3.85 points of R@5. It is a real, negative result.** AL
0.6032 against NO 0.6417, p = 0.0411 by exact permutation test over all 924
rearrangements — six Opus replicates per copy against two unpacked copies of one
frozen 8,993-note snapshot, differing by exactly the 1,930 `aliases` lines and
nothing else. Every stratum is negative or flat, paraphrase most of all, which
is the stratum aliases were written for. Fourteen runs, every integrity check
zero.

**The cause is open, and the obvious explanation does not survive checking.**
The daemon weights `meta` (aliases plus tags) 3x above body, and the backfill
filled that column across 21.5% of the corpus with model-written paraphrase.
Replaying the 206 queries the 2026-08-06 agent wrote: the aliases add 46 rows to
the top-5s and displace 41, and 21 of the 46 matched a query term appearing only
in the alias line and nowhere in the note's body. That reads like the mechanism
and is not one. A daemon rebuilt with the meta weight at body weight moves
top-5-identical-to-control only from 70.4% to 73.8%; per-question alias-only
exposure does not predict which questions lost, with the unexposed questions
losing too and the single biggest winner heavily exposed; and searching less
does not predict it either, at r = +0.088 across the 60 questions. `snippet()`
reads the body column, so the agent never sees alias text.

**The loss is retrieval-in-context, and the runs are instrumented well enough to
say so.** Splitting the score into whether a gold note reached the agent's
reading surface and whether the agent then named it: retrieval falls 0.792 to
0.740 and selection is flat at 0.972 against 0.961, near ceiling in both arms.
An agent that sees the right note names it either way. What it sees less often
is the right note. Static index quality is not the explanation — replaying a
fixed query set, the aliased index scores 54 gold-containing top-5s against 50 —
and neither is agent behaviour, since the ~1,200 queries each arm wrote match on
term count, empty rate, `k`, and iteration. The aliases reorder results without
changing how the agent asks, and on live queries that reordering lands worse
more often than better. Why is open, and a fixed-query replay structurally
cannot answer it.

**The paraphrase gap is not where the aliases were aimed.** They are written by
a model reading the note, so they paraphrase the note. The gap that made week 1
miss is between the note and the operator's *future question*, and a paraphrase
of the note does not cross it. `pp05` is the clean demonstration: its gold note
now carries "home network project overview", and every query the agent actually
writes for "pending project ideas for the house" still returns nothing.

**The negatives guard reports a direction, not a verdict.** Correct rejections
went 0.521 to 0.458, −6.25 points at p = 0.4719 — the same shape as the OR
rewrite's failure but a fifth the size, with the arms almost entirely
overlapping. Six replicates of eight questions cannot resolve six points.

*What to do:* write no more aliases until the cause is known, and treat reverting
the 1,930 already written as the default rather than a decision that needs
further argument. Indexing them at body weight is **not** the next experiment —
that was the first proposal here and the deterministic pre-check retired it,
since the weight change moves the surface by three points and the weight is not
implicated. The open question is why a tool-level improvement becomes an
answer-level loss.

*Re-audit trigger:* an alias generator that writes from observed questions
rather than from note content. This result prices these aliases, not aliases.

Two findings the retest produced that are not about aliases. Through the daemon
and without them, the gold set scores 0.642 overall and 0.531 on paraphrase
against the 2026-08-07 penalty replicates' 0.654 and 0.488 on a corpus 3%
smaller — directional, since both the corpus and the surface moved, but the
daemon's ranking is not worse than the layer it replaced. And the daemon beat
that layer's median query time by 13% while losing its tail badly, because
FTS5's `snippet()` was computed inside the ranking query for all 200
over-fetched rows before the re-rank discarded most of them — 3.1ms against
1784ms on one measured query whose matched set includes megabyte-sized notes.
That is fixed in `perf(daemon): rank first, snippet only the k rows that
survive` (#423), after which the daemon wins the median by 9.5x and p90 by 2.8x.
The retrieval numbers here were measured on the pre-fix build and were
re-verified against the post-fix daemon rather than assumed unaffected: over the
same 205 recorded queries the result paths, their order, and their scores are
identical. The fix moved latency and nothing else.

## Deliverables

1. The gold set (60 questions, permanent artifact, versioned in the repo).
2. Per-arm, per-stratum P@5/R@5 scores.
3. The go/no-go call on the vector sidecar, made mechanically by the rule above.
4. A short written note of any question either arm got wrong, with a one-line reason — this is the seed of the failure-pattern log that later weeks' scorecard runs should keep appending to.

All four delivered 2026-08-06. The gold set is at `scripts/health/fixtures/week1-gold/gold-set.json`, the runner at `scripts/health/week1_retrieval_experiment.py`, and the four scorecards (two arms x two drivers, each carrying its own per-question miss list) at `scripts/health/results/week1/`.

## Standing note for every week after this one

The gold set is the ongoing scorecard principle 3 requires, not a one-time artifact. A fixed 60-question set that never changes is itself Goodhart-able — a system can be tuned to do well on exactly those 60 without getting better at recall in general. Add newly-observed real questions (transcript-sourced, as above) to the set on a regular cadence, and never remove a question once it's answered correctly unless the note it targets is gone. The scorecard's value comes from staying coupled to how the operator actually asks, not from being a stable benchmark.

## Related

- `agentm-rescope-principles.md` — principle 3 is what makes this experiment's output the thing a milestone hangs on rather than an optional extra.
- `agentm-rescope-topology.md` — what the daemon does with whichever arm wins.
- `agentm-rescope-memory.md` — the capture doctrine that produces the research-density stratum's workload, and the retirement list the gold set shouldn't label against.

## Amendment log

- **2026-08-14 - standing-scorecard run #3: the step-6 non-regression gate the
  entry below promised is discharged, and it is refuted.** `agentm-hybrid-
  retrieval.md`'s ladder measured every retrieval-layer rung against the frozen
  `goldv2-20260812` corpus and shipped the hook cutover; this run is this
  design's own contribution — re-running the driver harness below (`claude -p`,
  6-call budget, this file's own scoring code, unmodified since 2026-08-06)
  against a daemon serving that same corpus. Six Opus replicates, all 84
  questions each: mean R@5 **0.6799** against the required ≥0.725 — refuted,
  five of six replicates below the bar. Read the same way the harness has
  always read it (blended: negatives scored into the same average as
  answerable questions, which is how 0.725 itself was computed), not the
  retrieval-ladder's separate R@5-plus-rejection convention. The shortfall is
  concentrated in negative rejection (87.5% in the original 2026-08-06 run,
  measured directly from `scripts/health/results/week1/opus-arm-a.json`, down
  to 62.5% here) rather than answerable-question recall, which is
  flat-to-improved. Two confounds are real and named rather than hidden: the
  negative stratum grew from 8 to 20 and was deliberately hardened between the
  two runs (this design's own §2, adopting AgentKV's ask), and the corpus grew
  too — so this is not a clean two-arm comparison on one frozen population,
  the way the week-3 retest below was. The obvious mechanism (agent access to
  `fusion`/`hybrid`, ~0% negative rejection at the retrieval layer since
  goldv2's own step 1) does not explain it — negatives where the agent used
  those modes rejected *better* (77.2%) than negatives where it stayed on
  plain `and` (14.3%). Root cause open. Full write-up:
  `agentm-hybrid-retrieval.md`'s own 2026-08-14 amendment and
  `scripts/health/results/goldv2/NOTES.md` § "Task 6". *Re-audit trigger:*
  same as the sibling entry — a change to `memory_search`'s escalation
  guidance, since under-use of the published `mode`/`question` parameters
  (79.9% of served calls set no mode at all) is the more actionable finding
  than anything about retrieval quality.

- **2026-08-12 - the re-open condition fired on our own scorecard, and the ruling is superseded for the hook path.** This design's rule — FTS5-only, sidecar declined — always carried one exit: a driver weaker than Opus calling `memory_search`. The prompt-submit hook is that driver taken to zero, and the goldv2 campaign measured it: 10.9% R@5 single-shot against 0.725 with the agent iterating, an 82.8% oracle over term subsets no implementable selection policy can reach half of, fusion arms that buy recall only by zeroing rejection, and a floor sweep proving BM25 cannot price answer-existence because negatives outscore answerables. The decision this document deferred is now made in `agentm-hybrid-retrieval.md`: dual retrieval with RRF, a cross-encoder floor for fast-path rejection, models as supervised children of the daemon, `memory/`-scoped vectors. **What stands unchanged here:** the agent-layer 0.725 and the ruling it produced for that layer; the alias-backfill refutation and its question-vocabulary diagnosis (now standing capture practice); the scorecard discipline itself, which is how the exit condition got observed instead of asserted. The ladder's step 6 re-runs this design's own driver harness, n>=6, as the non-regression gate.

- **2026-08-11 - the research stratum has its first real data, and the large-corpus failure mode is worse than a miss.** Two article ingests, distilled into atomic `reference` memories landing `active`, then probed with concept-phrased questions. **What works:** every alias written at capture time returns its own note at rank 1, twelve for twelve. **What fails, and how it fails depends on corpus size.** Against a handful of notes, a question in unanticipated vocabulary returns nothing — FTS5 ANDs its terms, so one out-of-vocabulary content word empties the result set before ranking is consulted. Against the live 9,900-note corpus the same question returns a confident wrong neighbor instead: "how often should consolidation run" answered with a prose style guide, "why did we decide against a vector database" answered with a storage-seam design doc while the note that actually answers it never reached the top five. **The competitor is identified and it is not fragments.** `Agent/desk/` documents took all five slots on every unanticipated-phrasing probe. A 38KB design document accumulates more BM25 term-frequency mass than a 1.1KB focused note, and desk carries no rank penalty — the penalty classes cover miner fragments, unfiled status and dream staging, so a finished design doc is legitimately unpenalized and that is precisely why it wins. **Why this sharpens rather than settles the sidecar question:** an empty answer is honest and a wrong neighbor is not, so the cost of lexical-only recall is higher than the earlier small-corpus probe suggested — but it also surfaces a cheaper lever than embeddings, a space-aware signal ranking `memory/` above `desk/` for memory-shaped questions, which nothing has measured. **What it still does not establish:** fifteen notes and two dozen probes is a demonstration, not a measurement. The standing FTS5-only rule is unchanged. *Re-audit:* a scored research-stratum block in the gold set, which should now carry both treatments — question-sourced aliases and a space-aware rank signal — since this run cannot separate them. *Note on the corpus:* the first ingest's six notes were removed at the operator's request (wrong source article); the second ingest's nine remain, and the alias result held identically across both.

*Newest first. Collapses to one <=2-paragraph entry at finalization; git holds the granular history.*

- **2026-08-10 - the Python vector stack was removed, and the ruling above is now what the code does.** This design declined the sidecar on 2026-08-06 and the daemon shipped without one, but the Python half stayed in the tree and kept running: `save.py` and four other writers enqueued embeddings on every write, the idle hook fired a drain, and `recall.py` opened the index on every query. **What the diagnosis found:** the index keyed to the resolved memory root held **0 rows against 8,684 notes**, and the 10-entry queue was not a backlog to drain but the visible tail of a corpus that was never enqueued. The 47-row, 4.2MB `vec-index.db` sitting in the vault is an orphan the V5-3 device-local move left behind, and reading it is what made an earlier pass report the index as merely stale. **Why removal rather than repair:** the pre-registered rule already fired FTS5-only under the driver that binds (0.725 against 0.672), the daemon `#434` put in front of recall is lexical, and `VEC_COLD_EMBED_MIN_BUDGET_MS` (5,000ms) exceeds `PROMPT_SUBMIT_BUDGET_MS` (300ms), so the vec half declined on every interactive call even in the daemon-absent fallback — the only surface that could still reach it was the 10s `query` CLI. Repair meant a full 8,684-note backfill to restore a path this design had already priced as a loss. **Why the "case against" did not hold:** `--filter` never depended on the index (`_bm25_search` and `_grep_search` each apply the criteria as they walk; the SQL join was an optimization over a table with no rows), and `verify-vec-index` gated the subsystem rather than justifying it, so it went with it. **What went with it, deliberately:** the write-time linker and the weekly link-improvement sweep, which ranked Related-line candidates out of the same index and whose whole design premise was reusing the drain's embedding rather than paying a second model load — operator-confirmed. The `**Related:**` lines already on disk are untouched, and the markup parsers `dream.py` and `lint.py` need moved to `markdown_spans.py`. **Two capabilities are genuinely gone, not relocated:** `dedup_guard.find_vault_duplicate`, which resolved a fingerprint through the index's own `entry_meta` and was the only vault-wide duplicate check on the save path (a scan over 8,684 notes per save is not a substitute, and the weekly cluster pass now owns those duplicates); and `ingest`'s doc-dedup short-circuit, which rode on it. Both had already been inert in production for as long as the index was empty — this makes the tree honest about it rather than changing behavior. `embed.py` stays: `notes_link_discovery --embeddings` and the week-1 corpus layer still use it. *Re-audit:* unchanged from the 2026-08-06 entry — adopting a driver weaker than Opus for `memory_search`. The removal makes that re-audit more expensive to act on, since restoring a vector path would now start from a full corpus embed rather than a stalled queue.

- **2026-08-09 - the alias backfill was measured through the daemon and it loses ground; standing-scorecard run #2 recorded.** Ran the 60-question gold set against `agentmd`'s own `memory_search` — the real surface, through a budget-enforcing MCP proxy rather than the experiment's Python layer — on two unpacked copies of one frozen 8,993-note snapshot differing by exactly the 1,930 `aliases` lines. Six Opus replicates per copy, plus a Fable pair as a labelled sensitivity check. **Week-3 retest section added above:** aliases cost **3.85 points of R@5 at p = 0.0411**, every stratum negative or flat. **Why the mechanism is recorded as open:** the obvious explanation was tested and failed. `meta` (aliases plus tags) is weighted 3x above body, and 21 of the 46 rows the aliases promoted matched a term found only in the alias line — which reads like the cause until a daemon rebuilt at body weight moves top-5-identical-to-control only 70.4% → 73.8%, per-question alias-only exposure fails to predict which questions lost, and searching less fails too at r = +0.088. That first explanation appeared in this entry's first draft as settled and is withdrawn; it was built from a suggestive share without checking that it predicted anything. **Why not conclude aliases are worthless:** search a note's own aliases and the daemon returns it at rank 1, and at the *tool* level the aliased corpus is slightly better — 54 of 206 recorded queries surface a gold note against 50. The loss appears between the tool's output and the agent's conclusion, on fewer tool calls, which is the shape the OR rewrite had. **Why the negatives guard is reported but not ruled on:** correct rejections fell 6.25 points at p = 0.4719, the OR rewrite's shape at a fifth its size, and six replicates of eight questions cannot resolve that. Two things this run established that the alias question does not depend on: the daemon's ranking is not worse than the layer it replaced (0.642 against the penalty replicates' 0.654 overall, 0.531 against 0.488 on paraphrase, on a 3% larger corpus — directional, both the corpus and the surface moved); and the daemon beat that layer's median query time by 13% while losing its tail to `snippet()`, which the ranking query computed for all 200 over-fetched rows before the re-rank discarded most of them — 3.1ms against 1784ms on one measured query — since fixed in #423, after which the daemon wins the median by 9.5x and p90 by 2.8x with ranking provably unchanged (identical paths, order and scores over the same 205 queries). The corpus is archived at `<vault>/_meta/corpus-snapshots/week3-retest-20260808.tar.gz` (content fingerprint `0a4c2fe1cc1c153c`), and unlike the 2026-08-07 campaign this gold set can see its own variable: 63 of 64 targets carry aliases in the treated copy against 3 in the control. *Re-audit:* an alias generator that writes from observed questions rather than from note content, which is a different treatment this result does not bind.
- **2026-08-08 - the OR query rewrite did not survive replication, and the shape rule now gates on status.** Six more Opus replicates on the frozen corpus put OR's marginal effect over the penalty at **+1.25 points, p = 0.46** — against **-18.8 points of correct rejections at p = 0.0087**, the two arms not overlapping across six runs each. **Why not ship it:** OR never returns an empty result set, so an agent that would have concluded no memory exists is handed five plausible notes and names one; it buys paraphrase recall and pays in the one stratum that tests whether the system knows what it does not know. It stays in the tree behind `--query-mode or`, off by default. **Why this reverses the 08-07 entry's read:** that rested on one live run showing +8 points, which the same day's own replicate finding said could not be trusted — the caution applied to everything except the lead it was written next to. Separately, `classify_document` now emits `fragment-promoted` for a fragment-shaped note that filing already promoted, so the recommended weights spare it (1,287 notes, 229 of them in `personal/preferences/`) for 0.0009 MRR; `AS_MEASURED_PENALTY_WEIGHTS` reproduces the twelve committed scorecards exactly. The corpus those runs used is archived at `<vault>/_meta/corpus-snapshots/week1-corpus-20260807.tar.gz` (fingerprint `0267330aa68dade2`) — as a tarball, because the corpus walk takes every `.md` under the vault root and an unpacked snapshot would double the corpus it is meant to measure. *Re-audit:* a minimum-score floor under the OR rewrite, which is the untested way to keep its recall without its cost.
- **2026-08-07 - the rank penalty was measured rather than assumed, and it earns its place; two knobs a follow-up would reach for turned out to be already shipped, and a bigger defect turned up upstream of ranking.** Added a status-and-shape rank penalty to the Arm A corpus layer, plus three lexical variants and an OR query mode, each switchable alone so a score change attributes to one of them. Twelve Opus runs on a frozen corpus snapshot — six control, six penalty — put the penalty at **+3.75 points of R@5, p = 0.0195** by exact permutation test. **Rank-penalty follow-up section added above.** **Why a constant rather than a tuned weight:** a 125-point sweep produced four distinct outcomes and every weight at or below 0.6 ranked identically, so strength is not a parameter and a config surface here would have nothing behind it. **Why not the tokenizer and column-weighting variants the brief named:** both were already in the 2026-08-06 baseline, so they were priced by ablation instead — porter is worth +5.7 tool-level hit@5, the 4x title weight +3.8 hit@1, and a dedicated aliases column changes nothing until the alias backfill gives it something to index. **Why the OR finding is not yet a ruling:** it has one live run behind it against the penalty's twelve, and this run also established that one run of this harness cannot be read as an effect. Four things the build forced, each recorded because it would otherwise mislead silently: this gold set's P@5 is R@5 rescaled by label size, so the 0.144 paraphrase figure never meant answers came back padded; six same-config replicates spread 2.5 points while a variant with provably zero retrieval effect swung a stratum 36 points in one run; the corpus grew 8,599 → 8,709 mid-experiment, partly from the measuring session's own capture hooks writing into the class under test; and zero of the gold set's 64 targets carry a penalty class, so this measures what demotion buys and never what it costs. *Re-audit:* a gold set that includes a genuinely useful `unfiled` note, which is the only way to price the cost side.
- **2026-08-06 - the experiment ran; the rule fired FTS5-only, and the driver model turned out to be a free variable nobody had pinned.** Built the runner (`scripts/health/week1_retrieval_experiment.py`, plus an FTS5/vector corpus layer, a warm-embedder daemon, and an MCP shim), then ran all 60 questions across both arms under Opus and again under Fable. **Outcome section added above** with the numbers and the ruling: Opus binds, ship FTS5-only, no vector sidecar in the v0 daemon. **Why not the sidecar:** for Opus it measured a loss rather than a gain (0.725 -> 0.672), and it would reintroduce either a model call in the capture write path or an asynchronous embedding queue - the mechanism whose silent stall cost four months of dead recall. **Why not let Fable's opposite verdict decide:** it was declared a sensitivity check after Opus was declared the run, and promoting it once both numbers are visible is the post-hoc selection pre-registration exists to block. Two things the build itself forced, both recorded because they would have invalidated the numbers silently: the operator's `UserPromptSubmit` recall hook injects vault content into a `claude -p` driver and only `--settings '{"disableAllHooks":true}'` stops it without breaking OAuth; and `--disallowedTools` does not cover Claude Code's deferred tool surface, so a driver can reach `Monitor` through `ToolSearch` and grep the vault directly - closed with `permissions.deny` plus a transcript audit that fails the run on any tool outside the arm's set. *Re-audit:* adopting a driver weaker than Opus for `memory_search`, a small local model above all - re-run both arms against the gold set before assuming FTS5-only still holds.
