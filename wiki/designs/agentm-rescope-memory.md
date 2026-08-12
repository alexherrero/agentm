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
aliases: [prefer Edit over Write, don't rewrite whole files]   # added at filing
source: https://…                 # anything ingested from outside
derived_from: [<slug>, <slug>]    # anything dreaming built from other memories
---
```

`source` and `derived_from` are cheap to write now and expensive to reconstruct later. They are what makes "why do you believe this?" answerable, and without them an entity memory is an assertion with no way back to its evidence.

`aliases` was the filing pass's answer to vocabulary mismatch, which week 1 measured directly: paraphrase R@5 of 0.472 under the binding driver, and the vector arm made it worse, because the real failure is that a note never contained the words the operator would later ask with. Retrieval cannot find what storage never wrote down. The reasoning still holds. The fix does not, in the form it shipped.

**Note-sourced aliases were measured and reverted.** Dreaming's first real job ran on 2026-08-08, writing aliases into 1,930 notes, and the week-3 retest priced it: **−3.85 points of R@5, p = 0.0411**, six Opus replicates per copy against two copies of one frozen snapshot differing by exactly those alias lines. Every stratum negative or flat. The loss is retrieval-in-context — a gold note reaches the agent's reading surface less often — while selection stays flat at 0.96 against 0.97, so it is not about how results read. Reverted on the live vault the same week; `alias_backfill.py reapply` restores them byte-identically if this reverses again. Full account in `agentm-rescope-week1-experiment.md` and `scripts/health/results/week3-retest/NOTES.md`.

**Why it failed is not the interesting part; where it aimed is.** The obvious explanation — `meta` is weighted 3x above body, so an alias-only match outranks a body match — was tested and does not hold: rebuilding the daemon at body weight moves the surface three points, and per-question alias-only exposure does not predict which questions lost. What the failure does establish is a design constraint. A model reading a note writes aliases that paraphrase **the note**, and the gap week 1 measured is between the note and the operator's **future question**. `pp05` is the demonstration: its target gained "home network project overview" and every query the agent writes for "pending project ideas for the house" still returns nothing.

So `aliases` stays in the schema and stops being a scheduled job. Anything that fills the column again needs a source of *question* vocabulary — past prompts in session transcripts are the obvious one — and needs to clear the same 60-question gold set before it writes at corpus scale. The snapshot and harness are kept for exactly that: `<vault>/_meta/corpus-snapshots/week3-retest-20260808.tar.gz` and `scripts/health/week3_daemon_retest.py`.

Two constraints still bind whatever comes next, and one of them is now load-bearing rather than precautionary. Any backfill must be blind to the retrieval gold set — aliasing its targets specifically would raise the score while improving nothing — and fragments stay excluded, because they get demoted rather than decorated. The blindness constraint is what makes the −3.85 readable: 63 of the gold set's 64 targets carry aliases in the treated copy, so the treatment was fully exercised on the right answers and still lost ground.

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

## What retires — and what only looked like it did

Principle 1 draws the line at two halves — a memory, and a resident process that keeps it good. The first reading of what sits outside those halves called roughly 850 files removable. Preparing the pass corrected that: most of it is **content wearing apparatus**, and the content is exactly the research memory the capture doctrine's first rule promises. The rule that fell out of the correction, stated once because it will be tested again:

**Content always comes back; apparatus must not. New content is always fine. A new place, a new status lifecycle, or a new queue is the old system growing back.**

Any memory system has the same jobs — capture, distill, file, recall, synthesize — so every one of the old system's functions recurs here by necessity. What must not recur are its mechanisms: pens beside the corpus, per-pen review lifecycles, scheduled queues nobody drained. The retirement pass is therefore a triage, not a deletion:

- **Apparatus that dies outright.** `_moc/` (48 generated per-kind indexes — if a map of content is needed to find something, principle 3 has already failed; a query renders the same view and cannot go stale), the incubator's folder machinery and research budgets, the watchlist's pens and review lifecycle, and the scheduled producers that fed them.
- **Content that translates.** The watchlist and skill-watchlist entries (126) become `reference` memories keeping their `source:` URL and evaluator verdicts — each is a distilled research finding that was stuck in a side pen behind a `pending-review` status nobody drained. Incubator ideas (9) become `idea` memories; completed incubator research rehomes to the desk project that owns it. `memory/_always-load/` (37) becomes `convention` — the broadcast mechanism itself is replaced by pull — and `memory/domains/` (17) becomes `reference`.
- **Noise that expires in place.** LOW-confidence opinion supplements (399 of 469) get `status: expired` — a status flip, not a deletion, per the section above: forgetting is a status flip, and deletion is this system's one unrecoverable operation. The HIGH/MEDIUM remainder (70) is read once; what states a durable operator rule is promoted to `convention`, the rest expires.
- **Source material that waits.** `_meta/skill-discovery-cache/` (286 files, 16MB) is raw upstream corpus whose facts were never distilled into memories. Distill-and-discard applies *in order*: the raw dies after a distillation pass extracts the memories, never before, because deleting it first destroys the input to the thing the operator actually wants from it.

The incubator sentence survives the correction unchanged: retiring it is not the same as discarding what it held. **An idea is a memory; an idea that needs work is a project.** If a thought has grown enough to need a research folder, it has earned `desk/projects/`.

One rhyme deserves its guard written down. A dreaming-maintained topic rollup and a retired MOC are close cousins, and a rollup that degrades into a mechanical list of every note carrying a tag is a MOC with better provenance. The differences that keep it legitimate: a rollup *says* something — synthesis in prose, not enumeration — carries `derived_from:`, is rebuildable from its sources, and exists only because a query class asks for it, under the same growth rule that gates new types.

## Related

- `agentm-rescope-principles.md` — the five principles this engine exists to satisfy.
- `agentm-rescope-topology.md` — the four spaces, the promotion door, and the crickets seam.
- `agentm-rescope-week1-experiment.md` — the retrieval experiment gating the vector-sidecar decision, and the standing scorecard that priced this design's alias job. Its gold set should include a research-corpus case where the question is asked by concept rather than by the words the summaries happen to use; that workload, not preference lookup, is what decides the sidecar.

## Amendment log

*Newest first. Collapses to one <=2-paragraph entry at finalization; git holds the granular history.*

- **2026-08-11 · the retirement list was re-read before it ran, and most of it is content, not apparatus.** The operator confirmed that the skill-watchlist and discovery-cache material is research he expects kept — the capture doctrine's first rule working as intended: a paper he has ingested should become a handful to tens of atomic memories that accumulate into expertise. The § What retires body is reconciled to the corrected triage: pens and producers die, watchlist entries translate to `reference` with provenance, incubator ideas become `idea` memories with completed research rehomed to desk projects, LOW supplements expire in place, and the discovery cache is undistilled source material that dies only after distillation. *Why translated entries land `active` rather than `unfiled`:* the doctrine routes unattended capture to triage, but the operator's explicit keep-these ruling **is** the review — and routing 126 items into the post-baseline queue would set the three-day pager counting on day one for material already judged. *Why the litmus test is in the body rather than here:* "does this add a place, a lifecycle, or a queue" is the question every future intake feature must pass, and a rule that gates future design belongs where future designers read. *A gap this entry originally reported here turned out not to exist.* It claimed deliberate capture could not land `active` because the surface took no `type`, `status` or `tags` — but the flags, the MCP schema, the validation and the doctrine sentence in the tool description had all shipped with the daemon. The evidence was a help listing truncated by the reader's own pager and two captures that landed `unfiled` because nobody passed the flags. Verified working end to end on 2026-08-11: `-type reference -status active` writes exactly that frontmatter, an active capture does not increment the unfiled queue, and the wire schema serves all eight parameters. The correction is left visible rather than silently rewritten because the failure shape is worth remembering: the tool was fine, the reading of it was not. *Re-audit triggers:* the research-stratum question already standing in `agentm-rescope-week1-experiment.md` — concept-phrased recall over a grown research corpus is what decides the vector sidecar — and the discovery cache's deletion, which becomes legitimate exactly when dreaming's distillation of it has run.

- **2026-08-09 - the alias backfill was measured, lost ground, and is no longer a scheduled job.** Dreaming's first real job ran on 2026-08-08 and the week-3 retest priced it at **−3.85 points of R@5, p = 0.0411** — six Opus replicates per copy against two copies of one frozen 8,993-note snapshot differing by exactly the 1,930 alias lines. Reverted on the live vault; `alias_backfill.py` gained a `reapply` command so the corpus can return to either state byte-identically, because a corpus you cannot restore is one you cannot measure against twice. **Why the schema keeps `aliases`:** the reasoning that motivated it is untouched — a note that never contained the operator's later vocabulary cannot be found by any ranking change, and the six shared misses still demonstrate it. What failed is the source. **Why not just retune the index:** the obvious mechanism, `meta` weighted 3x above body, was tested and rejected — a daemon rebuilt at body weight moves the surface three points, per-question alias-only exposure does not predict which questions lost, and the loss decomposes into retrieval (0.792 → 0.740) with selection flat at 0.972 → 0.961. **What the failure establishes:** a model reading a note paraphrases the note, and the gap is between the note and the operator's *future question*, so any future generator needs question vocabulary — past prompts in session transcripts are the obvious source — and must clear the 60-question gold set before writing at corpus scale. *Re-audit:* a question-sourced generator measured against the same snapshot, which is kept at `<vault>/_meta/corpus-snapshots/week3-retest-20260808.tar.gz` for exactly that comparison.
