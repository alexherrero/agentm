---
title: AgentM Rescope — The Filing Contract
status: draft
visibility: published
kind: design
scope: arc
area: agentm
author: alexherrero
contributors: []
created: 2026-08-18
updated: 2026-08-18
last_major_revision: 2026-08-18
prd:
project:
---

# AgentM Rescope — The Filing Contract

## Context

### Objective

The vault captures a great deal and files almost none of it. This design gives it one filing contract — the fields every memory carries and the quality bar it has to meet — and enforces that contract twice: once when a memory is written, and again every night when dreaming runs. The two enforcements are the same code with different triggers, so there is only one definition of a well-filed memory and nothing to drift against. Existing content moves onto the contract through hand passes during the build rather than a sweep beforehand.

### Background

The measured state, taken 2026-08-18 against the live vault. There are 15,096 markdown files. Of those, 2,821 are in the default search surface — `_inbox` (9,786), `desk/scratch` (2,208) and `_archive` (281) are excluded by `recall.py` and by `_daemon_admissible`, which applies the same rules to daemon-returned paths. The searched surface is 40.9% `memory/_opinions` and 34.6% `desk/projects`. The curated memory folders hold 42 notes between them: `feedback` has one, `insight` has one, `domains` has none. `memory/_always-load` is empty, so the heat policy currently governs nothing. Capture runs at roughly 250 notes a day and the inbox holds about ninety days of it, with the oldest dated entry at 86 days — the expire drain works, so this is churn rather than a stuck backlog. Almost nothing graduates.

Three things constrain the answer. The rescope principles bind: files are truth, git is undo, every index is a deletable cache; there is no inbox, and filing is a frontmatter edit rather than a move; six types ship at cutover with a growth rule; and principle 3 — a fresh session can ask sideways and get the fact back — is the only test that marks anything done. The other machine's system, AgentKV, is the shape we are moving toward, and it earns its recall from its filing structure rather than from its ranker alone. And the vault holds real history: opinions stay, nothing is retired ahead of the work, and existing content is corrected by hand passes as we build.

The costs are known and modest. The deterministic half of enrichment is regex and parsing, so it is free at any volume. The judgment half runs on a cheap model tier at roughly 250 calls a day. The expensive part is not the compute — it is the measurement discipline, which costs days rather than dollars and is the part that cannot be skipped, because the failure this whole rescope exists to correct was four months of green gates over dead recall.

## Design

### Overview

A memory arrives, and two things happen to it. The capture transaction writes the file, extracts everything a regex can extract, and returns — offline, synchronous, under a hundred milliseconds, never dropped. Then an enrichment pass runs immediately afterwards, out of band, and does the work that needs judgment: it summarizes the note, assigns its type, splits it if it is a blob, and decides whether it is good enough to be worth keeping. If that pass fails or the model is unavailable, the note simply stays `unfiled` and the nightly dreaming run picks it up. The model is never on the critical path.

Dreaming runs the same enrichment code over anything still `unfiled`, and then does the work that is only possible with the whole corpus in view: deduplicating against everything, building entity rollups from their underlying facts, extracting cross-memory insights, detecting slop and drift, and reconciling files that violate the contract. One definition of well-filed, two triggers — eager and batch.

The filing structure itself moves toward AgentKV's, with one deliberate difference. AgentKV reads intent from path segments: notes under `/meetings/` and `/cl_descriptions/` are dampened on general queries and boosted on matching ones, and canonical specifications get a flat lift. That works, and it is worth having. But agentm files memory by capture date precisely so that directories mean nothing and links can never break. So the same signal moves into indexed frontmatter, where it is more precise than a path — a note can sit on several axes at once, and nothing ever has to move.

### Infrastructure

The daemon is the platform. It already watches the vault, keeps a warm index, and answers search; the work here extends what it does at index time and adds an enrichment job it triggers and supervises.

| component | what it does | new or existing |
|---|---|---|
| capture endpoint | writes the file, runs deterministic extraction, returns | existing, extended |
| enrichment worker | summarize, type, split, quality-verdict — cheap model tier | new |
| dreaming pass | drains `unfiled`, rollups, insights, slop, reconciliation | existing, extended |
| chunk index | markdown-header chunks with `header_path` | new |
| backlink index | wikilinks and markdown links, indexed both directions | new |
| entity index | regex-extracted references (`#123`, `owner/repo#123`) | new |

| trigger | fires | runs |
|---|---|---|
| capture commit | per note, immediately | deterministic extraction, then enrichment |
| nightly dreaming | once a day | enrichment over `unfiled`, plus corpus-wide work |
| contract reconcile | inside dreaming | finds and repairs contract violations in existing files |

Four guarantees hold across all of it. A capture is never lost, because the transaction does not depend on the model. Every derived index rebuilds from the files, so a drifted cache heals on rebuild rather than forking. Every automated write commits with attribution, so `git log` is the record and a bad run is one revert away. And nothing moves, so no operation in the system's normal life can break a link.

### Detailed Design

#### The contract

Every memory carries the rescope's frontmatter, plus one field this design adds:

```yaml
type: preference          # one of the six, enum-enforced at write
status: unfiled           # unfiled → active → superseded | expired
captured: 2026-08-18T…    # immutable; determines the shard
updated: 2026-08-18T…
slug: edit-not-write
tags: [tokens, tooling]
aliases: [...]            # deterministic at capture; question-vocabulary later
source: https://…         # provenance in
derived_from: [<slug>…]   # provenance across
altitude: canonical       # canonical | artifact
enriched_by: sonnet-…     # which pass produced the judgment fields
enriched_at: 2026-08-18T…
```

`altitude` is the axis AgentKV's dampening actually rides. A convention that states a durable rule and a note distilled from one session's exhaust are both `type: workflow` today, and they should not rank alike on a general question. agentm already does a small version of this — `recall.py` carries an abstraction-altitude boost for `_index` and `_summary` anchor files — so this generalizes an idea already in the ranker rather than introducing one.

`enriched_by` and `enriched_at` exist so the first prompt we write is not permanent. A better model, or a corrected prompt, can re-run enrichment over anything stamped with an older version, and the pass is idempotent by construction so re-running is always safe.

#### What the capture transaction does

All of it is deterministic, offline, and incapable of failing on a network. It belongs in the transaction because it is free.

1. Validate the schema. Required fields present, `type` constrained to the six, `status` to the four.
2. Extract acronyms and compound identifiers into `aliases`. Two-way regex — `Term (ACRONYM)` and `ACRONYM (Term)` — plus compound-slug decomposition, so `idx_timestamp_desc` also indexes as `idx`, `timestamp`, `desc`. This is structural, not paraphrase, and it is the class of token that embeddings mangle and BM25 splits.
3. Extract wikilinks and markdown links into the backlink index, with path-suffix disambiguation.
4. Extract entity references into the entity index. Deterministic regex over issue, PR and repo forms. No new type is created, so the growth rule stays intact.
5. Chunk along markdown header boundaries and record each chunk's `header_path`.
6. Fingerprint the content and check the existing dedupe guard.
7. Stamp `captured`, which is immutable and fixes the shard.
8. Stamp provenance. A capture the operator directed lands `active`; ambient mining and scheduled ingest land `unfiled`.

#### What the enrichment pass does

It runs on a cheap model tier, immediately after the transaction commits, and again inside dreaming for anything still `unfiled`. Every judgment it makes is paired with something deterministic that enforces it.

| judgment | deterministic guard | on disagreement |
|---|---|---|
| summarize | extractive grounding — every claim traceable to source text | ungrounded span drops; grounding beats confidence |
| assign `type` | enum-locked to the six, validated at write | unknown fails hard; arguable goes to the queue |
| assign `altitude` | enum-locked; defaults to `artifact` | default wins when the judgment is absent |
| tag | vocabulary check against existing tags | novel tags allowed, surfaced in the brief |
| split a blob | size and concept ceiling measured deterministically | over-ceiling and unsplit is flagged, never silently admitted |
| quality verdict | answerability-shaped, not aesthetic | a failed verdict marks the note, it never deletes it |

The quality verdict deserves a note, because it is the one that can go circular. A model asked whether its own output is good will say yes. The bar that is not circular is principle 3's: does this note contain what a future question would need to find it. So the verdict is answerability-shaped — could a fresh session, asking sideways, land here — rather than a judgment about prose.

#### What dreaming does

The rescope's five jobs, plus four that come from this arc and from AgentKV:

1. Drain the `unfiled` queue — promote, merge by superseding the weaker copy, expire noise.
2. Maintain entity rollups from their underlying facts.
3. Extract cross-memory insights, each carrying `derived_from`.
4. Write tomorrow's brief.
5. Propose changes to its own machinery, and never apply them.
6. Inject backlink reference footers into notes.
7. Synthesize stubs for referenced-but-missing targets, so a link points at something.
8. Reconcile the contract — find existing files that violate it, fix what is safe, surface what is not. This is the automated half of the hand passes.
9. Detect slop and drift.

#### Aliases split three ways, and the split is measured

This is the one place where the evidence is unusually good, because three independent measurements agree.

| approach | result | disposition |
|---|---|---|
| a model reads the note and writes aliases | −3.85 R@5, p = 0.0411, six replicates | **banned** |
| aliases sourced from the asker's intent at capture | 12/12 at rank 1 across two ingests | promising, ungated |
| deterministic acronym and identifier extraction | AgentKV's measured gain, sub-60ms | **adopted, at capture** |

The pattern is that a model reading a note paraphrases the note, and the gap is between the note and the operator's future question. Deterministic extraction sidesteps that entirely: it does not invent vocabulary, it surfaces vocabulary the note already contains in a form the indexes can match. Question-vocabulary accretion stays a candidate rather than a scheduled job, and it needs three guards before it writes at scale — gate on a real usage signal rather than a retrieval event, cap or decay the list, and stay blind to the gold set.

#### The slop detector

Templates are fine. What is not fine is a note that fills a template and says nothing. The detector targets content, not shape.

Template-residual ratio is the cheapest signal and the safest: how much body exists beyond the skeleton. Nearest-neighbour novelty runs in two stages, shingle overlap first and an embedding check only on what the first stage flags, which bounds the cost. A drift monitor compares a trailing window of new notes against a frozen historical baseline and answers a different question — whether the agent itself has gone formulaic. A length floor participates only as an AND-gate, never alone, because this vault's best notes are often its shortest.

Two bands, not one cutoff: a review band that surfaces through the existing staging machinery, and a narrow auto-expire band for the unfilled skeleton that is also a near-copy. Even the narrow band runs confirm-gated for one supervised pass first, which is the path inbox triage already took.

## Alternatives Considered

**No model at ingestion; all judgment deferred to nightly dreaming.** This was the first proposal in this arc and it was wrong. It conflated the capture transaction with when filing runs — the doctrine says filing happens "later and asynchronously," which an immediate out-of-band pass satisfies completely. It also argued from cost using a statistic (most captures never promote) drawn from the very pipeline being fixed. Quality at birth is what should change that number.

**Reject low-quality notes at write time.** Rejected, and the distinction matters: enrichment improves a note, rejection discards one. An unvalidated rejection is unrecoverable at the moment it matters, and it breaks the never-silently-dropped contract. Deletion decisions stay reap-later; improvement decisions happen as early as possible.

**Carry filing meaning in paths, as AgentKV does.** Rejected. It is where AgentKV's recall gains come from, but agentm shards by capture date specifically so directories carry no meaning and IDs stay stable. Frontmatter gets the same signal, allows several axes at once, and never requires a move.

**Keep `kind` and add an orthogonal maturity field.** This was this arc's own earlier verdict and it loses to the rescope's answer. Collapsing twenty-two live types to six, with a growth rule that admits a type only when a query class needs to rank by it, is simpler and comes with its own brake. The three-axis diagnosis behind the original verdict still explains why the taxonomy grew to fifty-five; it just is not the fix.

**Model-written aliases as a scheduled job.** Refuted on measurement, not argument. Reverted on the live vault; `alias_backfill.py reapply` restores them byte-identically if the question ever reopens.

## Dependencies

The daemon must be the thing that triggers enrichment, because it already owns the write path and the index. A cheap model tier has to be reachable — `cheap_model_tier_available` and `higher_tier_model_available` are named seams in the codebase today and both return `False`, so this wires an existing seam rather than inventing one. The frozen sixty-question gold set and its harness gate anything that touches ranking. The rescope's principles, topology and memory documents are upstream of this one and it does not restate their decisions.

## Migrations

Nothing is swept before the work. Two populations, handled differently.

The 9,786 notes under `status: inbox` need re-statusing to `unfiled`, which is a frontmatter edit rather than a move. The measurement says this population is ninety days of live churn, so most of it drains on its own once the queue is a query instead of a folder.

The 2,821 already-searchable notes are the ones worth hand passes, because they are what recall returns today. Within them, `memory/2026`'s 197 notes are the sharpest case: none of them carries a `kind:` field at all, so they sit outside the current validation contract entirely. The twenty-two live types collapse to six as part of the same passes.

## Technical Debt & Risks

**Enrichment homogenizes the corpus.** This is the risk the design creates rather than inherits. If every note is written by one model with one prompt, the corpus converges on one voice and one shape, and the diversity that makes retrieval work erodes. The drift monitor is not optional for that reason. *Re-audit trigger: the first month the drift monitor moves in the wrong direction.*

**Derived memories may outrank the facts they came from.** Dreaming builds entity rollups and cross-memory insights carrying `derived_from`. If those systematically outrank their sources, the loop concentrates on its own output. The mechanism is structural and confirmed; its live magnitude is unmeasured. *Re-audit trigger: the generation-depth measurement, before the entity rollups ship.*

**The first enrichment prompt is not permanent, and must not become so.** `enriched_by` and `enriched_at` are what keep it re-runnable. *Re-audit trigger: the first prompt revision that cannot be applied retroactively.*

**Shadow-mode work stalls without a named reader.** The stepped decay curve has sat unwired behind a promotion criterion that nothing consumes; `eval_v6_retrieval.py` computes `merge_gate_passed` and its own comment says nothing gates on it. Anything shipped shadow-first here names its reader and its bar in the same change. *Re-audit trigger: any new shadow-mode mechanism.*

## Quality Attributes

### Reliability

The capture transaction cannot fail on model or network availability — that is the load-bearing property, and it is why enrichment is triggered rather than inlined. An enrichment failure leaves the note `unfiled`, which is a state the nightly pass already handles. The pass is idempotent, so a retry is always safe and a partial run leaves nothing inconsistent.

### Data Integrity

Files are truth and every index rebuilds from them, so a corrupt or drifted cache costs a rebuild rather than data. Every automated write commits with attribution and routes through the revert log, so a bad enrichment run is one revert away. `source` and `derived_from` are what make "why do you believe this" answerable, and an entity rollup without them is an assertion with no way back to its evidence.

### Privacy

Distill and discard already governs external material: raw email and crawled pages never enter the vault, facts carry message-id or URL provenance instead. Enrichment increases what a model reads, so the existing privacy scrubber runs before enrichment rather than after, and the cheap tier sees the same scrubbed text every other stage does.

### Latency

Capture stays under a hundred milliseconds because nothing on its path waits. Enrichment is out of band and has no interactive budget. The chunk, backlink and entity indexes are built at index time and read at query time, which is where AgentKV's sub-60ms p95 comes from.

### Testability

The deterministic half is straightforwardly testable — regex extraction, schema validation and chunking all have exact expected outputs. The judgment half is not, and pretending otherwise is the failure this rescope exists to correct. It is tested by the round trip: save a fact, start a fresh session, ask sideways, get it back. Deterministic checks block a merge; only the round trip marks anything done.

### Scalability

Sharding by capture date bounds directory size as the corpus grows past six figures and gives git better locality. Enrichment is per-note and therefore linear in capture rate rather than corpus size. The corpus-wide work in dreaming is the part that scales with the vault, and it inherits the existing batch caps, mutation budget and anomaly breaker.

*Security, Abuse, Accessibility, Internationalization and Compliance are not addressed here: this is a single-operator local system with no external surface, no untrusted input path, no rendered UI, and no regulatory scope.*

## Project management

### Work estimates

Four parts, sequenced so that each is measurable before the next depends on it.

| part | scope | size |
|---|---|---|
| contract and deterministic extraction | schema, acronym and identifier extraction, link and entity indexes, chunking | M |
| enrichment pass | cheap-tier worker, guards, idempotency, both triggers | M |
| dreaming extensions | footers, stubs, reconciliation, slop and drift | M |
| existing content | status rewrite, type collapse, hand passes | L |

### Documentation Plan

`wiki/reference/Memory-Daemon.md` gains the enrichment trigger and the new indexes. `wiki/reference/CI-Gates.md` gains whatever gates land with the contract. This document is the canonical account of the filing contract; the rescope memory document gains an amendment pointing here.

### Launch Plans

Each part ships behind its own measurement. Nothing ships on argument.

## Operations

### Monitoring and Alerting

Two numbers with red thresholds, both age-dominant rather than size-dominant, because fifty fresh unfiled items on a Tuesday morning is ordinary and the oldest unfiled item being three days old means the pipeline has stalled. Alongside them: enrichment failure rate, the drift monitor's trailing diversity score, and the round-trip probe's own number, which is the one that is allowed to mark things done.

### Logging Plan

Every automated write already commits with attribution, which makes `git log` the operational record — what was promoted, what was merged, what expired, and what dreaming proposed are one command away. Enrichment stamps `enriched_by` and `enriched_at` on the note itself, so a note's own frontmatter says which pass produced its judgment fields.

### Rollback Strategy

The revert log covers every automated mutation that routes through it, and git covers everything else. Enrichment is idempotent and re-runnable, so rolling back a bad prompt means reverting the commits and re-running the pass rather than repairing notes by hand. Nothing in this design deletes, so no rollback has to recover deleted content.

## Document History

| Date | Change | Status |
|---|---|---|
| 2026-08-18 | Initial draft, bootstrapped from the memory-ingestion research arc and reconciled against the rescope designs and AgentKV's architecture. | draft |
