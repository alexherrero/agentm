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

Underneath both triggers is the thing being enforced: one flat memory namespace sharded by capture date, six types with a rule that stops them multiplying, two lifecycle classes (most memories are events, a few are living entities built from those events), a status ladder that ages a memory by rank rather than by moving it, and three derived indexes — chunks, backlinks and entity references — that are caches and never authoritative.

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

#### The layout

```
Agent/
├── memory/
│   ├── 2026/07/<slug>.md
│   └── 2026/08/<slug>.md
│
└── desk/
    ├── projects/<slug>/          plans · roadmaps · progress · drafts
    ├── briefs/                   daily digests
    └── scratch/                  gitignored
```

Memory is one flat namespace sharded by capture date, meaning the moment the daemon wrote the file rather than any claim about when the thing happened. Type changes, status changes, tags change, and a slug can be corrected; capture date cannot, because it records an event in the daemon's own life. Sharding on the one immutable property means the directory a memory is born into is the directory it dies in, which is what lets every other field be edited freely.

There is no inbox and no staging directory. An unfiled memory sits in `memory/`, indexed and searchable the instant capture commits, rank-penalized until filing promotes it. Rank-penalized is a very different condition from absent: the previous layout accumulated 9,786 items in a directory that recall excluded by default, which is how the majority of what the system captured became invisible to the system that captured it. A layout with no inbox cannot repeat that. The filing queue still exists — it is a query rather than a folder.

`Agent/memory/` is excluded from Obsidian's index, because six thousand files nobody opens turn the graph view into static. `Agent/desk/` stays indexed. `scratch/` is gitignored and the daemon may delete anything in it without ceremony.

#### What a memory is — two lifecycle classes

Most memories are events. Some are entities, and the difference is structural.

An **event memory** is written once at capture and never edited. It may later be superseded or expired by a status change, but its body is a record of a moment and stays that way. A distilled session insight, a fix, a research summary, a fact learned from an email — all events.

An **entity memory** is one living file per thing, whose body accretes over time. A person is the clearest case: "sister, Austin, two kids, changed jobs in June" is not an event, and forcing it into the event model gives two bad options — rebuild-by-supersede on every new fact, which churns filenames and links, or scatter the person across two hundred fragments and re-synthesize on every lookup.

Entity memories are materialized views. The atomic facts stay the source of truth, each with its own `source:` provenance; the entity file is maintained by dreaming, carries `derived_from:` listing the facts it was built from, and is rebuildable from them. In doctrine it is a cache, and deleting one loses nothing. It is persisted anyway because it is what recall should hit first when the question is "who is X." ID-stability is what makes an accreting body safe — `[[sarah-<surname>]]` never breaks no matter how much the content changes.

#### Types, and the rule that stops them multiplying

Six types, collapsed from the twenty-two currently live, of which seventeen exist in single digits.

| type | absorbs | ≈count |
|---|---|---:|
| `preference` | preferences, preference, feedback | 2,938 |
| `workflow` | workflow, workflow-pattern | 1,626 |
| `idea` | idea, insight | 742 |
| `fix` | fix | 365 |
| `convention` | convention, non-negotiable, design-call, decision | 36 |
| `reference` | domain-reference, reference | 19 |

The field is `type`, not `kind`, because `type` is the one field the Open Knowledge Format requires; renaming during the collapse costs nothing and makes the corpus portable.

**The growth rule: a type is added when a query class needs to rank by it, and not otherwise.** That is a warrant test — a term earns its place by demonstrated need rather than by seeming reasonable — and it is the brake the previous taxonomy never had. Fifty-five values accumulated because every addition was individually defensible and nothing ever asked whether the set as a whole was still coherent. `person` is reserved under this rule and lands the day email ingest does, because "who is X" is exactly such a query class. It is not created before there is anything to put in it.

The rule is enforced rather than stated. A change that adds a type must carry, in the same diff, the query class that needs it and the nearest existing type with a sentence on why that type does not fit. A deprecation table maps retired values to their replacements so the collapse is mechanical rather than a rewrite.

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

#### Entities, and how they get found

Entity memories need something to key on, and the cheapest source is deterministic. Every note is scanned at capture for external references — issue and pull-request forms (`#123`, `owner/repo#123`), repository paths, and commit or changelist identifiers — and each match is written to an entity index keyed by a URI. This is regex over text, no model involved, and it creates no new type, so the growth rule is untouched.

That index is what makes an entity timeline possible before any `person` type exists: every note that mentions a given issue or repository is one indexed lookup away, and dreaming builds the rollup from that set rather than from a directory scan.

When a genuine entity type does land, it earns it against four questions, all four of which must answer yes:

1. **Persistent identity** — the referent is referenceable independent of any single note about it.
2. **Accumulating record** — multiple, temporally separated observations will attach to the same referent.
3. **Resolution need** — it is plausibly mentioned under more than one surface string, so without a canonical record the references fragment silently.
4. **Independent attributes** — it carries properties true of *it*, where a note only carries properties of the report.

`person`, `repository` and `organization` clear all four. An arc or roadmap item fails the third and fourth, which is why the existing arc registry treats it correctly as a validated facet rather than a type. A machine or device fails the second. A model name is the close call — it passes identity and partly resolution, but what gets recorded is looked-up facts *about* models, which belongs in a `reference` note.

Resolution is deterministic too: every entity note carries an `aliases:` list, and a new entity is matched against existing names and aliases before it is created. At this corpus size a normalized exact match is very likely sufficient; probabilistic record linkage is real machinery for a problem this vault does not have yet.

#### The derived indexes

Three indexes, all caches, all rebuildable from the files, none of them authoritative. They live in the daemon's existing store.

| index | key | carries | what it buys |
|---|---|---|---|
| chunks | `<path>#<n>` | `header_path`, content, embedding | a focused note stops losing to a long document on term-frequency mass |
| backlinks | `(source, target)` | link text, surrounding context | one-hop graph expansion at lookup cost, in both directions |
| entities | `(entity_uri, path)` | — | every note mentioning an issue, repo or person, without a scan |

Chunking splits along markdown header boundaries and records the heading path — `Architecture > Ingestion Pipeline` — so a match inside a long document points at the section rather than the file. This is the direct fix for the measured failure where a 38KB design document took all five top slots over a 1.1KB focused note. Atomic capture turns out to be a retrieval strategy and not only a filing one, and chunking is what extends that benefit to documents that were never atomic.

Backlinks are extracted from both wikilink and markdown-link forms, with path-suffix disambiguation so `[[capture]]` resolves correctly when two files share a basename.

#### Altitude, and what ranking does with it

`altitude` carries the one signal this design borrows from AgentKV's directory structure. There, a note under `/meetings/` or `/cl_descriptions/` is dampened on a general question and boosted on a question that asks for it by name, while canonical specifications get a flat lift. That works, and it is worth having.

Here the same distinction is a field, because the path cannot carry it — memory shards by date so directories mean nothing, and that is deliberate. A field is also more precise: a note can be a `workflow` at `artifact` altitude and change altitude later without moving.

`canonical` means the note states something durable — a convention, a decided rule, a reference fact. `artifact` means it records a moment — session exhaust, a distilled meeting, a one-off observation. Ranking dampens `artifact` on a general question and lifts it when the question asks for that shape. The default is `artifact`, so a note earns `canonical` rather than assuming it.

#### The lifecycle, and how a memory ages

Status is the whole lifecycle. Nothing moves; every transition is a frontmatter edit.

```
unfiled ──filing──▶ active ──┬──▶ superseded   (a newer memory replaced it)
                             └──▶ expired      (it was noise, or its window closed)
```

Alongside status, a decay score governs rank rather than existence. A memory holds full strength through six months of silence, ranks at half to a year, an eighth to three years, and a sixteenth to five — **and a sixteenth is a floor, not a waypoint.** The curve never reaches zero, because a memory that nobody has needed in four years is not worthless, only cold, and a floorless curve makes it unreachable rather than merely unlikely.

One genuine recall resets the clock to zero. Only a real recall does — a lint walk, an index rebuild or a dreaming pass touching the file must never count, or the maintenance machinery quietly refreshes everything it inspects and decay stops working.

Two classes never decay. Failure incidents are exempt because the whole value of an incident record is being there on the one day, years later, when the same failure recurs. Decisions are exempt for the same reason. Crystallized memories — the distilled lessons dreaming promotes out of repeated observation — are exempt because a lesson that survived being learned three times is the durable kind.

Past five years of silence a memory moves to the archive, where it leaves everyday search, stays indexed, and answers an explicit archive query. **Nothing is deleted.** All three institutional reasons for mandated destruction are absent here: no discovery exposure, no meaningful storage cost, and no third-party erasure duty, since the sole subject and the sole controller are the same person. Git is the recoverability net. Deletion, if it ever happens, is the operator's own act outside the system.

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
| quality verdict | answerability-shaped rather than aesthetic | a failed verdict marks the note, it never deletes it |

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

Templates are fine. What is not fine is a note that fills a template and says nothing. The detector reads content and ignores shape.

Template-residual ratio is the cheapest signal and the safest: how much body exists beyond the skeleton. Nearest-neighbour novelty runs in two stages, shingle overlap first and an embedding check only on what the first stage flags, which bounds the cost. A drift monitor compares a trailing window of new notes against a frozen historical baseline and answers a different question — whether the agent itself has gone formulaic. A length floor participates only as an AND-gate, never alone, because this vault's best notes are often its shortest.

Two bands rather than one cutoff: a review band that surfaces through the existing staging machinery, and a narrow auto-expire band for the unfilled skeleton that is also a near-copy. Even the narrow band runs confirm-gated for one supervised pass first, which is the path inbox triage already took.

## Alternatives Considered

**No model at ingestion; all judgment deferred to nightly dreaming.** This was the first proposal in this arc and it was wrong. It conflated the capture transaction with when filing runs — the doctrine says filing happens "later and asynchronously," which an immediate out-of-band pass satisfies completely. It also argued from cost using a statistic (most captures never promote) drawn from the very pipeline being fixed. Quality at birth is what should change that number.

**Reject low-quality notes at write time.** Rejected, and the distinction matters: enrichment improves a note, rejection discards one. An unvalidated rejection is unrecoverable at the moment it matters, and it breaks the never-silently-dropped contract. Deletion decisions stay reap-later; improvement decisions happen as early as possible.

**Carry filing meaning in paths, as AgentKV does.** Rejected. It is where AgentKV's recall gains come from, but agentm shards by capture date specifically so directories carry no meaning and IDs stay stable. Frontmatter gets the same signal, allows several axes at once, and never requires a move.

**Keep `kind` and add an orthogonal maturity field.** This was this arc's own earlier verdict and it loses to the rescope's answer. Collapsing twenty-two live types to six, with a growth rule that admits a type only when a query class needs to rank by it, is simpler and comes with its own brake. The three-axis diagnosis behind the original verdict still explains why the taxonomy grew to fifty-five; it just is not the fix.

**Model-written aliases as a scheduled job.** Refuted on measurement. No argument was needed. Reverted on the live vault; `alias_backfill.py reapply` restores them byte-identically if the question ever reopens.

## Dependencies

The daemon must be the thing that triggers enrichment, because it already owns the write path and the index. A cheap model tier has to be reachable — `cheap_model_tier_available` and `higher_tier_model_available` are named seams in the codebase today and both return `False`, so this wires an existing seam rather than inventing one. The frozen sixty-question gold set and its harness gate anything that touches ranking. The rescope's principles, topology and memory documents are upstream of this one and it does not restate their decisions.

## Migrations

Nothing is swept before the work. Existing content moves onto the contract through hand passes as the parts land, and the two populations need different handling.

**The 9,786 notes under `status: inbox`** need re-statusing to `unfiled`. That is a frontmatter edit, so no link breaks and no ID changes. The measurement says this population is roughly ninety days of live churn rather than a stuck backlog — the oldest dated entry is 86 days against a 90-day expire window — so most of it drains on its own once the queue is a query instead of a folder. What matters is that nothing is invisible in the meantime, which the no-inbox layout guarantees.

**The 2,821 already-searchable notes** are the ones that repay hand attention, because they are what recall returns today. Within them, `memory/2026`'s 197 notes are the sharpest case: not one carries a `kind:` field, so they sit outside the current validation contract entirely and the walkers either skip them or process them partially.

The type collapse runs as a mapping rather than a rewrite:

| retired value | becomes | note |
|---|---|---|
| `preferences`, `feedback` | `preference` | plural/singular pair plus a near-synonym |
| `workflow-pattern` | `workflow` | the pattern/instance split never earned its keep |
| `insight` | `idea` | one holds a single note today |
| `non-negotiable`, `design-call`, `decision` | `convention` | all four name a decided rule |
| `domain-reference` | `reference` | |
| `archive`, `capture` | — | never types at all; statuses in the wrong field |
| the four `idea-incubator-*` variants | `idea` | four phases of one process |
| the five `*-index` variants | `reference` at `canonical` altitude | navigational pages |

Everything not in the table retires with the machinery that produced it, and a deprecation map records the pairing so the collapse can be applied mechanically and audited afterwards. Ordering matters: the contract and its deterministic extraction land first, so a re-typed note is validated against the new contract as it is touched rather than needing a second pass.

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

Each part ships behind a named measurement, written down before the build, on a frozen corpus, with replicates where a model sits anywhere in the scoring path. A plan whose first step is "build it" is the failure this rescope exists to correct.

| part | the number that says it worked |
|---|---|
| contract + deterministic extraction | acronym and identifier expansion against the frozen 60-question gold set, blind to it — the same harness the alias revert was measured on |
| enrichment pass | promotion yield before and after; the share of captures reaching `active`, measured on a fixed window |
| dreaming extensions | the slop detector's precision and recall against a hand-labelled stratified sample of 150–200 notes, reported by type |
| existing content | contract conformance across the searched corpus, and the round-trip probe holding steady through the collapse |

Above all of them sits principle 3's own test, and it is the only one allowed to mark anything done: save a fact, start a fresh session, ask sideways, get it back — on the real corpus, on a schedule, as a number that can go down.

Two measurements run before any of it, because either can kill a recommendation. The first splits the gold set by note age and asks whether decay actually buries old notes, which decides whether the floor matters or is cosmetic. The second computes each memory's derivation depth and compares the depth of what recall returns against the depth of the corpus as a whole — if dreaming's own output systematically outranks the facts it was built from, the loop concentrates on itself, and nobody has measured whether it does.

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
| 2026-08-18 | Initial draft, bootstrapped from the memory-ingestion research arc and reconciled against the rescope designs and AgentKV's architecture. Same-day revision after operator review found Detailed Design covered the enforcement mechanism but not the filing system it enforces: added the layout, the event/entity lifecycle split, the six types and their growth rule, entity resolution and its admission test, the three derived indexes, altitude, and the status-and-decay lifecycle. Migrations gained the 22→6 mapping; Launch Plans gained the per-part measurements. | draft |
