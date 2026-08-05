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

## Deliverables

1. The gold set (60 questions, permanent artifact, versioned in the repo).
2. Per-arm, per-stratum P@5/R@5 scores.
3. The go/no-go call on the vector sidecar, made mechanically by the rule above.
4. A short written note of any question either arm got wrong, with a one-line reason — this is the seed of the failure-pattern log that later weeks' scorecard runs should keep appending to.

## Standing note for every week after this one

The gold set is the ongoing scorecard principle 3 requires, not a one-time artifact. A fixed 60-question set that never changes is itself Goodhart-able — a system can be tuned to do well on exactly those 60 without getting better at recall in general. Add newly-observed real questions (transcript-sourced, as above) to the set on a regular cadence, and never remove a question once it's answered correctly unless the note it targets is gone. The scorecard's value comes from staying coupled to how the operator actually asks, not from being a stable benchmark.

## Related

- `agentm-rescope-principles.md` — principle 3 is what makes this experiment's output the thing a milestone hangs on rather than an optional extra.
- `agentm-rescope-topology.md` — what the daemon does with whichever arm wins.
- `agentm-rescope-memory.md` — the capture doctrine that produces the research-density stratum's workload, and the retirement list the gold set shouldn't label against.
