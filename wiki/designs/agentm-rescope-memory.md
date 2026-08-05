---
title: AgentM Rescope — The Memory Engine
status: proposed
kind: design
scope: architecture
area: agentm
parent: agentm-rescope-principles.md
seeded: 2026-08-03
---

# AgentM Rescope — The Memory Engine

## What this document owns

`agentm-rescope-topology.md` decides where truth lives and how the four spaces relate to each other. This document decides what happens inside `Agent/` — how memory is laid out on disk, what a memory is, how one comes to exist, and what dreaming is allowed to do with the pile. Those questions kept forcing their way into the topology's four-spaces section, which is written at the altitude of "where does the vault live" and should stay there.

Everything below is downstream of principle 3: nothing is saved until a fresh session can ask and get it back. Where a choice here looks over-careful, it is usually paying for that principle rather than for tidiness.

## The layout

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

Memory is one flat namespace sharded by **capture date** — the moment the daemon wrote the file, not a `date` field describing when the thing happened. The distinction is the whole point of the choice. Kind changes, status changes, tags change, and a slug can be corrected; capture date cannot, because it records an event in the daemon's own life rather than a claim about the world. Sharding on the one immutable property means the directory a memory is born into is the directory it dies in.

The alternative considered was a genuinely flat `memory/<slug>.md`, which is the more honest expression of "no directory means anything." It was rejected on headroom rather than principle: date-sharding costs nothing, bounds directory size as the corpus grows past six figures, and gives git better locality, since a day's commits touch one directory instead of scattering across a single enormous one.

`Agent/memory/` is excluded from Obsidian's index. Six thousand files the operator never opens turn the graph view into static and pollute every quick-switcher lookup. `Agent/desk/` stays indexed, because he reads `PLAN.md` and `progress.md` daily. Nothing is lost by the exclusion — the daemon checks link integrity across the whole vault regardless, and per principle 2 Obsidian is a viewer with no authority over what's real.

`scratch/` is gitignored and the daemon may delete anything in it without ceremony. This is not a convenience. Dream staging alone currently runs to 1,065 files that distill down to two insights; committing that ratio would make the vault's history mostly exhaust and bury the changes worth reading. Principle 2 already says every index is a deletable cache, and a cache does not belong in the history at all.

## There is no inbox

Capture writes straight into `memory/` with `status: unfiled`. There is no staging directory, and filing never moves a file — it edits frontmatter.

This falls out of ID-stability, but it is worth stating on its own because of what it prevents. The previous system accumulated 4,933 items in `personal/_inbox/`, a directory recall excluded by default, which is how 82% of what the system captured became invisible to the system that captured it. A layout with no inbox cannot repeat that failure: an unfiled memory is in `memory/`, indexed, and searchable the instant capture commits. It is rank-penalized until filing promotes it, and rank-penalized is a very different condition from absent.

The filing queue still exists — it is simply **a query rather than a folder**. The loud-queue mechanism in the topology document (two dashboard numbers, red thresholds, a daily self-probe) measures a query result and works exactly as written. Its thresholds should be age-dominant rather than size-dominant: under a standing daily ingest, fifty fresh unfiled items every morning is an ordinary Tuesday, while the oldest unfiled item being three days old means the pipeline has stalled.

## Two lifecycle classes

Most memories are events. Some are entities, and the difference is structural rather than cosmetic.

An **event memory** is written once at capture and never edited. It may later be superseded or expired by a status change, but its body is a record of a moment and stays that way. A distilled session insight, a fix, a research summary, a fact learned from an email — all events.

An **entity memory** is one living file per thing, whose body accretes over time. A person is the clearest case: "sister, Austin, two kids, changed jobs in June" is not an event, and forcing it into the event model gives two bad options — rebuild-by-supersede on every new fact, which churns filenames and links, or scatter the person across two hundred fragments and re-synthesize on every lookup.

Entity memories are materialized views. The atomic facts remain the source of truth, each with its own `source:` provenance; the entity file is maintained by dreaming, carries `derived_from:` listing the facts it was built from, and is rebuildable from them. In doctrine it is a cache, and deleting one loses nothing. It is persisted as memory anyway because it is what recall should hit first when the question is "who is X."

ID-stability is what makes an evolving body safe: `[[sarah-<surname>]]` never breaks no matter how much the content changes, and the file stays in its birth shard forever, because the shard was always a bucket rather than a meaning. One pleasant consequence is that `git log` on an entity file is the complete history of what was learned about it and when — provenance the design gets for free.

## Types

Six types ship at cutover. The current corpus carries twenty-two, of which seventeen exist in single digits — a taxonomy that grew by accretion rather than design.

| type | absorbs | ≈count |
|---|---|---:|
| `preference` | preferences, preference, feedback | 2,938 |
| `workflow` | workflow, workflow-pattern | 1,626 |
| `idea` | idea, insight | 742 |
| `fix` | fix | 365 |
| `convention` | convention, non-negotiable, design-call, decision | 36 |
| `reference` | domain-reference, reference | 19 |

Everything else retires with the machinery that produced it. `archive` and `capture` were never types at all — they are statuses that ended up in the wrong field.

The field is named `type`, not `kind`, because `type` is the one field the Open Knowledge Format requires. Renaming it during the collapse costs nothing and makes the corpus portable to any other OKF-aware agent.

**The growth rule:** a type is added when a query class needs to rank by it, and not otherwise. `person` is reserved under this rule and lands the day email ingest does — "who is X" is exactly such a query class — but it is not created before there is anything to put in it. Types are declared in the daemon's own configuration rather than in `Filing.md`, which governs the operator's spaces and has no business describing FRIDAY's internals.

## The frontmatter contract

```yaml
---
type: preference                  # one of the six
status: unfiled                   # unfiled → active → superseded | expired
captured: 2026-08-03T14:22:00Z    # immutable; determines the shard
updated: 2026-08-03T14:22:00Z
slug: edit-not-write-for-existing-files
tags: [tokens, tooling]
source: https://…                 # anything ingested from outside
derived_from: [<slug>, <slug>]    # anything dreaming built from other memories
---
```

`source` and `derived_from` are cheap to write now and expensive to reconstruct later. They are what makes "why do you believe this?" answerable, and without them an entity memory is an assertion with no way back to its evidence.

Every operation in the system's normal life is a frontmatter edit. Filing changes `status`. Re-typing changes `type`. Aging out changes `status`. Nothing moves, which means nothing in the system's normal life can break a link.

## Capture doctrine

**Atomic by concept, never by session or source dump.** A research afternoon that scans several hundred pages produces several hundred small memories, one per concept, each tagged with the topic — not one forty-kilobyte summary. Recall works by searching and iterating over small addressable chunks, and a single large blob defeats that whether or not it contains the answer.

**Deliberate capture lands `active`; unattended capture lands `unfiled`.** A session the operator directed produces memories he already approved by asking for them, and routing them through triage would page him about a backlog that is not one. Ambient mining, scheduled ingest, and anything else running without him watching lands `unfiled` and waits for filing.

**Distill and discard.** Raw external material never enters the vault. Ingested email is read, distilled, and dropped — Gmail already archives it, and duplicating a mailbox into the vault doubles the sensitive surface while buying nothing. Facts carry message-id or URL provenance, so the trail back exists without hoarding the bodies. The same rule covers crawled pages and any other bulk source.

**Facts, not transcripts.** The distiller records what was learned, not what was said. "Sarah is switching jobs" rather than four paragraphs she wrote in confidence. This keeps memories small enough to rank well and keeps the corpus far less radioactive than a verbatim archive would be.

Working exhaust from any of the above goes to `scratch/`.

## Dreaming

Dreaming's job list, stated once rather than accumulated across other documents. Nightly it:

1. Drains the `unfiled` queue — promotes to `active`, merges duplicates by superseding the weaker copy, expires noise.
2. Maintains entity rollups from their underlying facts.
3. Extracts cross-memory insights, each written with `derived_from:` provenance.
4. Writes tomorrow's brief.
5. Proposes changes to its own machinery, and never applies them.

Every run commits with attribution, which makes git history the dream journal: what it merged, what it expired, and what it proposed are one `git log` away, and a bad run is one revert away. Dreaming is allowed to be aggressive precisely because undo is total.

Point 5 is the one that needs stating explicitly, because it names a failure this whole rescope exists to avoid. Some insights are not memories. "My filing prompt misclassifies fixes as workflows" is fixed by editing the filing prompt, and no memory write accomplishes that — recording the insight and changing nothing is the same shape of failure as green tests over dead recall, one level up. But the resolution cannot be that dreaming edits its own operating configuration unattended at three in the morning, because an agent that rewrites its own rules while nobody is watching does not have rules. So dreaming proposes the diff in the brief, and a supervised session lands it. Self-improvement is a morning-after apply rather than a midnight mutation.

Behavioral notes-to-self work the same way and for the same reason. Under pull-based context, a memory only changes behavior if something searches for it, so an insight like "I over-capture from short sessions" is written as a `workflow` memory *and* surfaced in the brief — which transcludes into the operator's daily note, so both tomorrow's sessions and the operator himself see what it decided about itself.

## The autonomy dial

Four mechanisms in this design and the topology document turn on the same test, and it is worth naming once rather than restating four times:

- The promotion door's review requirement — the operator watching *is* the review.
- Capture status — deliberate lands `active`, unattended lands `unfiled`.
- Self-modification — proposed in the brief, applied only under supervision.
- Entity enrichment — see below.

**Ceremony scales with who was watching, not with the size of the diff.** A typo fix requested live and a typo fix made at 3am are different operations even when the diff is byte-identical, because the thing being insured against is not the change but the absence of a human at the moment it happened.

Enrichment deserves its own sentence because it is where the dial has teeth. Building profiles from the operator's own correspondence is reorganizing data he already has, and every profile is a readable file on his own disk that he can expire on request. Dreaming reaching out to the open web to enrich a profile from public records is a different act, and it is off by structure rather than by policy: unattended enrichment never runs. If it is ever wanted, it is proposed in the brief like any other self-change.

## Forgetting is not erasure

Expiring a memory is a status flip — instant, invisible to recall, and it moves no files. Git still has it.

True erasure, in the sense of removing something from the vault's history, means rewriting that history, and it is the one genuinely unrecoverable operation this system has. It belongs in principle 5's slow lane: manual, reviewed, rare. The design should not let a status flip impersonate an erasure, because a request to forget something is usually a request for the second thing and would be quietly answered with the first.

## What retires

Principle 1 draws the line at two halves — a memory, and a resident process that keeps it good. Checked against what is actually on disk, that retires roughly 850 files outright rather than rehoming them:

- `personal/_opinions/` — 419 files, six opinions and their supplements.
- `personal/_watchlist/` and `personal/_skill-watchlist/` — 117.
- `_meta/skill-discovery-cache/` — 248.
- `_idea-incubator/` — 22, along with its per-idea folder machinery and research budgets.
- `_moc/` — 47 hand-maintained per-kind indexes. If a map of content is needed to find something, principle 3 has already failed; the dashboard renders the same view from a query, and a generated view cannot go stale.
- `_meta/` health scorecards, forward-learning cache, and orchestration state.

Two directories keep their contents and change homes. `personal/_always-load/` (37) holds durable knowledge about how the operator works and becomes ordinary `convention` memories, even though the always-load broadcast mechanism itself is replaced by a pointer a session can follow. `personal/domains/` (17) becomes `reference` memories.

The incubator is worth one extra sentence, because retiring it is not the same as discarding what it held. Its 22 entries are ideas, and ideas already have a home — there are 742 of them in memory. What retires is the heavier apparatus around them. The rule that replaces it: **an idea is a memory; an idea that needs work is a project.** If a thought has grown enough to need a research folder, it has earned `desk/projects/`.

## Related

- `agentm-rescope-principles.md` — the five principles this engine exists to satisfy.
- `agentm-rescope-topology.md` — the four spaces, the promotion door, and the crickets seam.
- `agentm-rescope-week1-experiment.md` — the retrieval experiment gating the vector-sidecar decision. Its gold set should include a research-corpus case where the question is asked by concept rather than by the words the summaries happen to use; that workload, not preference lookup, is what decides the sidecar.
