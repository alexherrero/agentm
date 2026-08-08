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

**A larger defect sits upstream of ranking.** FTS5's bare `MATCH` ANDs every term, so a six-word paraphrase must find a note containing all six words. On the 2026-08-06 run that returned nothing at all for 32 of 206 queries and fewer than five results for 62. OR-joining the same queries, phrases preserved, empties none of them and is worth roughly twice the penalty at the tool level, with the two effects additive. That has one live run behind it rather than twelve, so it is a strong lead rather than a result — measure it the same way before shipping it.

**The six shared misses are vocabulary failures, and no ranking change touches them.** For three of them the gold note never appears at any depth for any query the agent wrote; for the other three it sits at rank 42, 50, and 238, behind legitimately-matching documents rather than behind fragments. They wait on `aliases`.

*Two cautions this run earned.* Single runs of this harness cannot be read as effects: six same-config replicates spread 2.5 points, and a variant with provably zero retrieval effect swung one stratum 36 points in a single run. And the vault moves while you measure it — the corpus grew from 8,599 to 8,709 over the course of the follow-up, partly from the measuring session's own capture hooks depositing fragments into the class under test. Snapshot the corpus before comparing anything.

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

*Newest first. Collapses to one <=2-paragraph entry at finalization; git holds the granular history.*

- **2026-08-07 - the rank penalty was measured rather than assumed, and it earns its place; two knobs a follow-up would reach for turned out to be already shipped, and a bigger defect turned up upstream of ranking.** Added a status-and-shape rank penalty to the Arm A corpus layer, plus three lexical variants and an OR query mode, each switchable alone so a score change attributes to one of them. Twelve Opus runs on a frozen corpus snapshot — six control, six penalty — put the penalty at **+3.75 points of R@5, p = 0.0195** by exact permutation test. **Rank-penalty follow-up section added above.** **Why a constant rather than a tuned weight:** a 125-point sweep produced four distinct outcomes and every weight at or below 0.6 ranked identically, so strength is not a parameter and a config surface here would have nothing behind it. **Why not the tokenizer and column-weighting variants the brief named:** both were already in the 2026-08-06 baseline, so they were priced by ablation instead — porter is worth +5.7 tool-level hit@5, the 4x title weight +3.8 hit@1, and a dedicated aliases column changes nothing until the alias backfill gives it something to index. **Why the OR finding is not yet a ruling:** it has one live run behind it against the penalty's twelve, and this run also established that one run of this harness cannot be read as an effect. Four things the build forced, each recorded because it would otherwise mislead silently: this gold set's P@5 is R@5 rescaled by label size, so the 0.144 paraphrase figure never meant answers came back padded; six same-config replicates spread 2.5 points while a variant with provably zero retrieval effect swung a stratum 36 points in one run; the corpus grew 8,599 → 8,709 mid-experiment, partly from the measuring session's own capture hooks writing into the class under test; and zero of the gold set's 64 targets carry a penalty class, so this measures what demotion buys and never what it costs. *Re-audit:* a gold set that includes a genuinely useful `unfiled` note, which is the only way to price the cost side.
- **2026-08-06 - the experiment ran; the rule fired FTS5-only, and the driver model turned out to be a free variable nobody had pinned.** Built the runner (`scripts/health/week1_retrieval_experiment.py`, plus an FTS5/vector corpus layer, a warm-embedder daemon, and an MCP shim), then ran all 60 questions across both arms under Opus and again under Fable. **Outcome section added above** with the numbers and the ruling: Opus binds, ship FTS5-only, no vector sidecar in the v0 daemon. **Why not the sidecar:** for Opus it measured a loss rather than a gain (0.725 -> 0.672), and it would reintroduce either a model call in the capture write path or an asynchronous embedding queue - the mechanism whose silent stall cost four months of dead recall. **Why not let Fable's opposite verdict decide:** it was declared a sensitivity check after Opus was declared the run, and promoting it once both numbers are visible is the post-hoc selection pre-registration exists to block. Two things the build itself forced, both recorded because they would have invalidated the numbers silently: the operator's `UserPromptSubmit` recall hook injects vault content into a `claude -p` driver and only `--settings '{"disableAllHooks":true}'` stops it without breaking OAuth; and `--disallowedTools` does not cover Claude Code's deferred tool surface, so a driver can reach `Monitor` through `ToolSearch` and grep the vault directly - closed with `permissions.deny` plus a transcript audit that fails the run on any tool outside the arm's set. *Re-audit:* adopting a driver weaker than Opus for `memory_search`, a small local model above all - re-run both arms against the gold set before assuming FTS5-only still holds.
