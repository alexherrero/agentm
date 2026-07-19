---
title: AgentM Auto-Organization
status: final
kind: design
scope: feature
area: agentm/memory
parent: agentm-memory-system.md
governs: [harness/skills/memory/scripts/dream.py, harness/skills/memory/scripts/dream_confirm.py, harness/skills/memory/scripts/lifecycle.py, scripts/health/eval_v6_retrieval.py]
seeded: 2026-07-17
approved: 2026-07-17
---

> [!NOTE]
> **Part 1 of 3 shipped 2026-07-18** ("Old notes out of the way," FRIDAY ladder feature 5) — the tidying stage: the stepped rank-curve retune, the memory archive with its digest preview, the artifact shelf, and the recall-walker change. Parts 2 (write-time linking) and 3 (dedup + lint) stay designed-for; `status` holds at `final`, not `launched`, until all three ship — see [AgentM Memory System](agentm-memory-system.md) and [Experience & Dreaming](agentm-experience-and-dreaming.md) for the landing amendments recording what's actually built.

# AgentM Auto-Organization

## Context

### Objective

The vault accumulates cruft as it grows: cold notes crowd the browse surface, most notes carry no links, and nothing cleans up on its own. **This design makes the vault maintain itself** — work artifacts tuck away at project close or after a year unused, memories fade on a stepped schedule and reach the archive only after five years of silence, new notes arrive linked to their neighbors, and a lint pass repairs the small rot. A shelved artifact stays in everyday search but leaves your browse eyeline; an archived memory answers only an explicit archive search; nothing is ever deleted, and none of it waits on your confirmation. We define the signals, the three actions, the machinery that runs them, and the meters that prove the vault is getting tidier.

### Background

You asked for this directly: cut down the cruft, and archive older notes so they are there when needed but out of the way when you browse agentm's memory. Today the engine scores every volatile note's decay, but that score only lowers the note's rank in search — the browse surface never gets cleaner. The measured baselines say the rest: 13.8% of notes carry an organic link, and three whole kinds hold 880 notes with no links at all.

Every mechanism this design needs already runs. The lifecycle layer already stamps every note's last genuine recall — the clock every schedule here reads. Dreaming walks the whole corpus weekly. It uses staged proposals, a revert log, and an auto-apply lane you ruled on for compression. The archive convention exists at every tier. Recall skips `_archive/` by default. The `--include-archive` flag reopens it. The capture design is on the same arc. It hands newly ingested batches to the linker.

The new work adds three focused pieces. These are an archive stage for dreaming, a write-time linker, and a lint engine. Each piece rides the shipped machinery. Building a second corpus walker duplicates the staging, confirmation, and revert plumbing of dreaming. Everything scheduled here runs as a dreaming stage.

This design is the memory-system design's own lifecycle tail, built out. That design names decay-to-archive and self-healing lint as designed-for; this child design is where they become real. It stands alone because the work spans two parents — the lifecycle belongs to the memory system, the weekly stages ride dreaming in the experience design — and because three plan-sized builds deserve their own document rather than a heavier amendment log. At landing, both parent designs gain an amendment pointing here.

## Design

### Overview

Auto-organization runs three actions over the vault, and this design is organized the same way. First, **linking at write time**: a new note arrives already connected — the engine finds its nearest neighbors and adds the links before the note settles in. Second, **tidying on age**: work artifacts leave the eyeline at project close or after a year of sitting unused, and memories fade on a stepped schedule until, after five years of silence, they reach the archive — where only an explicit archive search finds them. Third, **lint**: a pass that finds orphans, collapses duplicate notes, repairs broken links, discovers missing links between new content and the older notes it relates to, flags contradictions, and scores note quality — fixing and adding the safe things itself and surfacing the rest. Links are only ever added; the single removal case is a link that is already broken.

The machinery is the existing weekly dreaming pass — which also absorbs the inbox triage that used to wait for your command — plus one small write-time hook. Nothing is ever deleted: shelf and archive both mean move, every move auto-applies with a revert-log entry and a digest line, and two meters — organic connectivity and the browse-surface counts — tell you whether the vault is actually getting tidier. A sampled audit, a global mutation budget, and an anomaly breaker watch the automation itself.

### Infrastructure

Everything runs on the existing dreaming pipeline, the runner, and the save path. The new components are:

| Component | What it is |
|---|---|
| write-time linker | a post-save step: finds related notes via the vector index, adds wikilinks to the new note |
| tidying stage | a new dreaming stage: shelves artifacts unused for a year, archives memories silent for five |
| lint engine | orphan, broken-link, contradiction, and quality checks; runs on demand and weekly |
| link graph snapshot | a derived, device-local copy of the vault's typed-edge graph, rebuilt incrementally each cycle |
| dedup guard | a pre-write check: an arriving note that matches an existing one reinforces it instead of duplicating it |
| automation guards | the sampled higher-tier audit, the global mutation budget, and the anomaly breaker |
| backfill batches | link additions for today's unlinked notes, capped per cycle |

| Trigger | What runs |
|---|---|
| every save, and every ingest batch | the write-time linker |
| the weekly dreaming cycle | inbox triage, the tidying stage, dedup, lint, and the backfill batches |
| every write | the dedup guard |
| `/memory lint` | the lint engine, on demand |

The system makes four guarantees. Every shelf, archive, and link move auto-applies, lands in the revert log, and shows in the digest — none of it waits on you. The linker's additions are additive and capped. Judgment calls — contradictions, repairs that are not clearly safe — are surfaced, never acted on. A crashed cycle resumes where it left off.

### Detailed Design

#### The signals

Two clocks and two shipped signals drive every action. The lifecycle layer stamps a note's last genuine recall; this design retunes its rank curve from the original 30-day half-life to your stepped, years-long schedule — full strength for six months of silence, half to a year, an eighth to three years, a sixteenth to five. The retune ships shadow-first: for the first cycles both curves compute and the rank deltas log, and the new curve takes over only after the diff reads sane and the pinned retrieval eval holds. The same touch clock extends to artifacts, where any injection into a conversation counts as a touch. The `fingerprint` field every entry already carries supplies exact-duplicate detection, and the vector index supplies the near-duplicate signal. Durable notes, failure incidents, and decision paths are exempt, and always-load or pinned notes are never candidates for anything here. The vector index supplies nearest neighbors by similarity. The typed-edge graph supplies the link picture, persisted as a derived, device-local snapshot so cycle queries cost nothing. It shows who points at whom and who is an orphan.

#### Linking at write time

*Task 3 of 7 (the write-time linker itself) shipped 2026-07-18 — see the as-built note after the paragraph below for how its timing actually works. The weekly link-improvement sweep, the ingest-batch handoff, the backfill, and the connectivity meter (the rest of this section) remain designed-for.*

When a note is saved, the linker asks the index for its nearest neighbors. It keeps the ones above a similarity floor. It adds up to three wikilinks to the new note under a short "Related" line. It runs after the save returns. It works the same way the embedding queue does, so saving stays fast. It edits only the note that just arrived. A reciprocal link on an older note is batched into the weekly cycle, capped and revert-logged. Ingest batches from the capture design arrive internally linked already. The linker adds their outward connections. The similarity floor and the three-link cap are calibration defaults. They are named in code and tuned from the connectivity meter.

**As built (task 3 — the write-time linker itself):** the linker is real and shipped, but "it runs after the save returns" is truer, and reads differently, than it first sounds. It is not synchronous on the save call's own return path. A version that queried the index and embedded the new note inline in `save.py` was tried first and caused a confirmed regression: it made every single save trigger a real BGE model load, and — on this machine — a live HuggingFace Hub network round-trip even with the model already cached. A follow-up fix forcing `HF_HUB_OFFLINE=1` was tried and also confirmed broken (offline mode fails to resolve an already-cached model from disk on this machine's sentence-transformers/huggingface_hub version, a known revision-resolution quirk). The shipped mechanism instead defers the actual linking to the *next drain of the embedding queue*: `save.py`'s post-save hook is now just `vec_index.enqueue(vault, rel_path, "upsert", text=embed_text, link=True)` — one new `link=True` flag on the same record the embedding queue already writes. `drain_queue()` applies `write_time_linker.apply()` right after computing that record's embedding for the ordinary vec-index upsert, reusing the same embedding rather than a second, save-time-only embed call. So the sentence above — "it works the same way the embedding queue does, so saving stays fast" — is honored exactly, and more literally than it first reads: the save call itself never touches the network or loads a model, but a newly saved note's "Related" links appear on the *next drain cycle* (the idle-time hook, a manual `python3 vec_index.py drain`, or a future `/memory reindex`) rather than before `save_entry()` returns. Applying the link also nudges task 2's persisted graph snapshot for just that one note (`graph_snapshot.rebuild(vault, paths=[rel_path])`), so the graph doesn't go stale waiting on the weekly full rebuild. The reciprocal-link case, the ingest-batch handoff, and the backfill (described above and below) remain designed-for — part 2 tasks 4-6 — and so does the connectivity meter (task 7).

The backfill clears the existing debt. Currently, 880 notes across three kinds carry no links at all. Each weekly cycle links a capped batch of them. The batch limit is 25 per cycle, matching compression's cap. This continues until the pool drains.

#### Deduplication — reinforce, never repeat

The inbox shows the problem plainly: whole families of near-identical memories suffixed `_1`, `_2`, and up. They exist because the write path handles a name collision by suffixing instead of asking whether the content already exists, and because the two shipped cleanup mechanisms each have a hole — triage's merge works on pairs and only runs when you invoke it, and dreaming's dedup stage skips the inbox and still waits on confirmation. This design closes all three gaps by extending what exists rather than adding a third mechanism.

Prevention comes first. The dedup guard runs before any note is written: an exact `fingerprint` match or a strong near-match against the index means the arriving note reinforces the existing one — its occurrence count and `updated` stamp bump, and no new file appears. A suffix now only ever means genuinely different content sharing a title.

The cure runs weekly, inside dreaming, as one pass. Inbox triage folds into the cycle as a stage: it stops waiting for your command, and the inbox joins the same walk as the rest of the corpus. The two already share their staging and merge machinery underneath — this fold closes a historical split, and `/memory inbox` stays as the on-demand door into the same engine, alongside `/memory lint`. Dedup itself becomes cluster-aware: a whole suffix family collapses into the canonical earliest note in one disposition. A collapse is free when fingerprints match exactly — a suffix family is one note repeated. A fuzzy merge, similar but not identical, needs a cheap-model verdict before it applies, and a verdict the judge is unsure of joins the needs-your-eye list. Every collapse keeps the surviving note's content, marks the copies superseded — they then follow the normal tidying lanes — and auto-applies, capped, revert-logged, and digest-reported. Dreaming's corpus dedup was confirm-gated by its own standing ruling; approving this design is the fresh ruling that extends auto-apply to it, under these bounds.

Questionable cases stay put. A pair or family in the ambiguous band is left in the inbox untouched and flagged instead: a needs-your-eye list on the console, a line in the digest, and a count in the morning brief. You resolve those whenever you like.

The backlog drains like the link debt does: each cycle collapses a capped batch of the existing suffix families until none remain.

Linking also improves over time, cheaply. Each weekly cycle, a link-improvement sweep takes the notes that arrived or changed since the last cycle and asks two free questions of the graph snapshot and the vector index: which older notes sit near this one, and which clusters does it touch. Clear candidates get their links added directly — both directions, capped, revert-logged. Ambiguous candidates — similarity in the middle band — go to a cheap model for a yes or no, under a small per-cycle budget declared in the job manifest. When the model tier is unavailable or the budget is spent, the sweep keeps the deterministic links and moves on. Links are only ever added; the single removal case belongs to lint, and it is a link whose target is gone.

#### Tidying on age — artifacts to the shelf, memories to the archive

*Part 1, shipped 2026-07-18 — the section below describes what actually built; see [Experience & Dreaming](agentm-experience-and-dreaming.md#dreaming--designed-and-built-dry-run--opt-in) for the pipeline-level record.*

Tidying treats work artifacts and memories differently, and both lanes are automatic.

Work artifacts — plans, project files, and the other non-memory documents — leave the eyeline two ways. Project artifacts tidy at close-out: the dev workflow already archives a finished plan and buries a finished project after harvest, and this design composes those conventions as they stand. Everything else moves to its tier's `_shelf/` after a full year of never entering a conversation. A shelved artifact changes in no other way: everyday search still finds it, and one use brings it back to its folder on the next cycle. The shelf is a browse convention, not a search boundary — the one small engine change is that the recall walker includes `_shelf/`.

As built, "an artifact" is any corpus entry with no `kind:` frontmatter field at all — a real memory's `kind:` is a required field in `save.py`'s locked entry contract, so its absence is what marks something as "the other non-memory documents" this section means, not a second, separately-invented classification. "Touch" reuses the exact mechanism the memory lane already tracks — a genuine `recall.py` hit — rather than a new "any injection into a conversation" tracker: nothing in this codebase implements that broader sense yet, and building it would mean instrumenting skills and hooks that live in the separate `crickets` repo, disproportionate to this one part. The design's own words above name the fuller intent; this is the scoped, buildable slice of it that shipped.

Memories skip the shelf and simply fade. A memory holds full strength for six months of silence, ranks at half strength to a year, an eighth to three years, and a sixteenth to five. Notes entering the final approach — past four and a half years of silence — appear in the digest one cycle before they move, a preview rather than a gate. Past five years without a single genuine recall, a memory moves to its tier's `_archive/` — where it is finally allowed to be forgotten. An archived memory leaves everyday search, stays in the index, and answers an explicit archive search (`--include-archive`) whenever you ask. Nothing is ever deleted, and one genuine recall at any point resets the clock to zero.

Every move in both lanes auto-applies, lands in the revert log, and shows in the digest.

#### Lint

The lint engine walks the corpus and reports four things. Orphans are notes with no links in either direction. They feed the linker's backfill queue. Broken wikilinks get repaired when the fix is safe. A safe fix is a mis-cased target that gets corrected and revert-logged. The engine surfaces unsafe fixes for you to review. Removal has exactly one case — a broken link; a valid link is never removed. Contradictions include supersedes chains that disagree and status fields that conflict. The engine surfaces these for your manual resolution. Every note gets a quality score based on frontmatter completeness, link presence, and staleness. A vault-wide roll-up of these scores lands in the digest. The engine also flags `kind:` values that sit outside the registry. This list gives the standing canonicalization cleanup its worklist. You can run `/memory lint` on demand. Dreaming runs it weekly.

#### Guarding the automation

Four guards keep the automation honest. A sampled audit runs on a higher model tier: each period it takes a random sample of the cycle's applied links and merges and renders agree or disagree. The disagreement rate is a meter; past a threshold, the ambiguous bands narrow toward deterministic-only on their own, and the console flags the change. A global mutation budget bounds each cycle across every stage combined, on top of the per-stage caps. An anomaly breaker watches the trailing average: a cycle that proposes several times the usual mutation count applies nothing at all and flags the console instead. And lint checks the graph snapshot against a live extraction sample each cycle, since three features now lean on it.

**As built (part 1):** the global mutation budget and the anomaly breaker exist today, scoped to the tidying stage's own moves — `dream_confirm.auto_apply_batch`'s `batch_cap` already bounds every auto-apply stage combined (compression and tidying together), and `dream_confirm.check_tidying_anomaly` trips when a cycle's tidying-proposal count exceeds three times a trailing eight-cycle baseline, applying nothing from that stage rather than a capped partial batch. The sampled higher-tier audit and the fuller guard suite over dedup/lint/linking land with parts 2 and 3.

#### The meters

Two numbers say whether this design is working. Organic connectivity starts at 13.8%. This is the share of notes with at least one real, non-generated link. The meter counts the linker's additions separately. This separate count keeps the main number honest. The browse-surface counts track the eyeline directly. They count three states: live notes per folder, shelved, and archived. Both metrics land in the digest each cycle. They also land in the health dashboard's FRIDAY family. **The acceptance test is your own sentence: browsing agentm's memory shows live, current notes, and aged material sits in the archive, still there on request.**

#### Where things go

The archive convention, the tier layout, and the entry contract live in the memory-system design (`wiki/designs/agentm-memory-system.md`). This design adds the moves that tidy notes and the links that connect them; the notes keep their shape. The shelf is its one addition to the layout, recorded in the landing amendment. The project close-out conventions the artifact lane composes live in the dev workflow's own designs. The staging, confirmation, and revert machinery belongs to dreaming. The experience design describes this machinery (`wiki/designs/agentm-experience-and-dreaming.md`).

## Alternatives Considered

- **A dedicated nightly runner job for archival.** Rejected: Dreaming owns whole-corpus maintenance. It already handles staging, confirmation, reverting, and auto-apply. A second corpus walker duplicates this work. A weekly schedule is fast enough. Nothing about a cold note requires an urgent response.
- **LLM-judged linking and archiving.** Rejected for v1: Deterministic signals like similarity, decay, and the graph are cheap and explainable. The engine already computes them. An LLM judge can act as a second opinion later if the deterministic signal is uncertain.
- **Hard-deleting expired cruft.** Rejected outright: Prune always resolves to archive. It never resolves to delete. We maintain this standing invariant.
- **Obsidian community plugins for auto-linking.** Rejected: The engine owns the corpus. It works the exact same way on every host. A plugin forks the mechanism per host and skips the staging discipline.
- **Do nothing.** Rejected: Connectivity is flat. The browse surface degrades every month. You named this pain directly.

## Dependencies

We rely on several shipped systems. These include the lifecycle decay layer, the vector index, and the typed-edge graph. We also rely on dreaming's staging, confirm, and revert flow, its batch caps, the runner, and the kind registry. The capture design runs on the same arc and hands ingest batches to the linker. Inbox triage re-homes into the weekly cycle — its machinery reused as a dreaming stage, its hand-run command kept as an on-demand door. The cheap-model band and the sampled audit ride the model-and-effort routing tiers; every other action here makes zero model calls. Downstream, the canonicalization cleanup consumes lint's kind report. The auto-organization meters feed the health family on the same arc.

## Migrations

This design requires no destructive migrations. The backfill and the first cycles run capped, revert-logged, and digest-reported; nothing waits on your review to proceed. The decay sidecar and the index are already in place. Existing `_archive/` content stays exactly where it is.

## Technical Debt & Risks

- The stepped rank schedule, the one-year artifact bar, the five-year memory bar, the similarity floor, and the link cap are calibration defaults. They deliberately lean conservative, and they have no real-use data behind them. *Re-audit trigger:* The first two live cycles.
- Retuning the rank curve changes retrieval order vault-wide. The change merges only if the pinned retrieval eval holds — the standing rule for any ranking change.
- Over-linking is the linker's failure mode. Too many weak links make the graph noisy. The cap and the floor bound this behavior. The connectivity meter counts generated links separately. This prevents the linker from inflating its own success number.
- Shelving an artifact you still want in the eyeline is the likeliest miss, and it is low-stakes: it stays in everyday search, the digest names every move, the revert log undoes it, and one use brings it back. Archiving a memory wrongly is rarer still at a five-year bar, and the archive search plus the revert log recover it.
- Contradiction detection is heuristic and advisory. It misses real contradictions and flags false ones. It only surfaces problems and never acts on them.
- The backfill touches old notes at scale. The per-cycle cap and the revert log bound the blast radius for each cycle.
- The cheap-model band could add weak links or run up cost. The band is narrow, the additions are capped and counted separately by the meter, the job manifest carries a hard per-cycle budget, and the sweep degrades to deterministic-only when the budget is spent or the tier is unavailable. The sampled higher-tier audit measures its judgment, and the bands narrow on their own when disagreement climbs.
- A runaway cycle could churn the vault inside the per-stage caps. The global mutation budget and the anomaly breaker bound it: an abnormal cycle applies nothing and flags the console.
- A wrong merge is dedup's failure mode. A collapse keeps the surviving note's content and marks the copies superseded rather than deleting anything, every disposition is revert-logged and named in the digest, and the per-cycle cap bounds each pass. Ambiguous candidates are never forced: they stay in the inbox and go on the needs-your-eye list. *Re-audit trigger:* the first backlog-drain cycles.

## Quality Attributes

### Reliability

The engine caps and revert-logs every scheduled action, and reports each one in the digest. Every action is resumable. A cycle that moves nothing is a normal cycle.

### Data Integrity

Shelf and archive are moves, never deletes. The index covers both, so nothing becomes unfindable. The engine journals every mutation for revert.

### Latency

The linker runs asynchronously after the save returns. It works the same way embedding does. Saving a note never waits on the linker. The weekly cycle carries everything else.

### Testability

Each action gets a deterministic fixture. The linker uses a synthetic corpus with known neighbors. The archive stage uses notes with pinned decay scores. Lint uses seeded rot. The connectivity meter has its own test. The supervised first passes over the real vault provide the acceptance evidence.

## Project management

### Work estimates

This design has three parts. Part one builds the tidying stage — the stepped rank curve (shadow-run, then eval-gated), the memory archive with its digest preview, the artifact shelf, and the recall-walker change (Medium) — **shipped 2026-07-18**. Part two builds the write-time linker, the link-improvement sweep with its graph snapshot and cheap-model band, the ingest handoff, and the backfill (Medium). Part three builds dedup — the write-time guard, the cluster-aware weekly pass with its fuzzy-merge judge, the triage fold into the cycle, the needs-your-eye surface, the suffix-backlog drain — plus the lint engine, `/memory lint`, the meters, and the automation guards (Medium).

### Documentation Plan

This design lifts to `wiki/designs/agentm-auto-organization.md` during part one, and both parent designs gain their pointer amendments in the same landing: the memory-system design's archive-and-prune section flips its designed-for tail to point here, and the experience design's dreaming section records the new stages. The how-to documentation covers *Tune-The-Archive*. This includes the floors, the caps, and bringing a note back. The reference documentation covers runner-jobs and gate updates as new checks land. We add a row to `Completed-Features.md` for each release.

### Launch Plans

We plan three named minor releases. These are "the shelf and the archive", "write-time linking", and "self-healing lint". The ladder lands in ROADMAP-MASTER § FRIDAY. We set dates when each release cuts.

## Operations

### Monitoring and Alerting

The digest carries each cycle's proposals and applies. It also carries both meters. The health dashboard's FRIDAY family carries connectivity and the browse-surface counts. The console carries the needs-your-eye list of ambiguous duplicates, and the morning brief carries its count. The engine reports a quiet cycle as quiet. It never hides a quiet cycle.

### Logging Plan

Dreaming's staging artifacts and revert journal record every proposal and apply. The linker logs its additions for each note. Lint reports land as dated artifacts in `_meta/`.

### Rollback Strategy

Every shelf, archive, and link move reverts through the revert log. A write-time link is plain text on the newly saved note. You delete the "Related" line to undo it. Disabling the three components returns the vault to today's behavior. You have no state migration to unwind.

## Document History

| Date | Change | Status |
|---|---|---|
| 2026-07-17 | Authored (F1 session) from the operator's brief (`AUTO-ORG-BRIEF.md`), the self-healing-lint backlog row, the connectivity audit baselines, and the memory-system design's archive convention — organized in processing order per the capture design's precedent. Vehicle decision: dreaming owns the weekly actions (a second corpus walker would duplicate its staging and revert plumbing). Gating decision: supervised confirm-gated first passes; auto-apply only on a fresh operator ruling at that review. The two-step voice pass (Gemini via `agy`, Claude-verified) ran before first operator review, per the standing process. Quality Attributes walked in full; Security, Privacy, Scalability, Abuse, Accessibility, i18n, and Compliance omitted as low-relevance. First review round (operator, same day): the decay timeline made deliberately conservative — archive floor 0.05→0.02 plus a new 180-day minimum age — and the design's relationship to the memory system stated explicitly (its lifecycle tail built out as a child document, spanning two parents; pointer amendments in both at landing). Second review round (operator, same day): the lifecycle became two-stage — shelf then archive — and every confirmation gate came out: nothing is ever deleted, so every move auto-applies with revert log and digest reporting. Third review round (operator, same day): the timelines stretched to the operator's stepped schedule — a memory holds full strength for six months of silence, half to a year, an eighth to three years, a sixteenth to five, and archives past five years, with no shelf for memories — while the shelf narrowed to non-memory artifacts (project artifacts tidy at close-out per the existing dev-workflow conventions; everything else shelves after one year of never entering a conversation, returning on one use). The rank-curve retune replaces the 30-day half-life and is eval-gated per the standing ranking rule. Fourth review round (operator, same day): linking now improves over time — a weekly link-improvement sweep connects new arrivals to related older content, drawing free candidates from a newly persisted device-local graph snapshot plus the vector index, with a budget-capped cheap-model yes/no reserved for the ambiguous band and deterministic-only degradation when the budget is spent; links are only ever added, removal stays reserved for broken links. Fifth review round (operator, same day): deduplication became a first-class concern after the operator found suffix families (`_1`, `_2`) piling up in the inbox — a write-time dedup guard (fingerprint + near-match reinforces instead of duplicating), a cluster-aware weekly pass extending triage's merge and dreaming's dedup to cover inbox and corpus together, and a capped suffix-backlog drain; approving this design is the fresh ruling that extends auto-apply to dreaming's dedup under these bounds. Sixth review round (operator, same day): inbox triage folds into the weekly dreaming cycle as a stage — automatic, no longer operator-invoked, closing the historical split (the two already shared their merge machinery underneath) — with `/memory inbox` kept as the on-demand door; ambiguous duplicate candidates are never forced, staying in the inbox and surfacing on a console needs-your-eye list, in the digest, and as a morning-brief count. Seventh review round (operator, same day): five risk mitigations folded — a sampled higher-tier audit over applied links and merges with an auto-narrowing disagreement threshold; a cheap-judge verdict required for fuzzy merges while fingerprint-exact collapses stay deterministic; shadow-mode for the rank-curve retune ahead of its eval gate; a one-cycle digest preview before any archive move; and a global mutation budget plus an anomaly breaker that applies nothing on an abnormal cycle. The operator approved the design as final. Later the same day, the FRIDAY-AGENTM build session ran /design translate, splitting the design into 3 parts (`shelf-and-archive`, `write-time-linking`, `dedup-and-lint`; parts/ files at `agentm-auto-organization/parts/` alongside this doc) and /design sequence, landing three executable plans at `_harness/queued-plans/PLAN-auto-org-shelf-and-archive.md`, `PLAN-auto-org-write-time-linking.md`, `PLAN-auto-org-dedup-and-lint.md`, each grounded against the live agentm codebase. Grounding surfaced that two pieces the design assumes as infrastructure don't exist yet as of this pass — a vector-index nearest-neighbor query function and a persisted device-local typed-edge graph snapshot (today's `graph.py::extract_edges()` is stateless, recomputed fresh per caller) — both are named as real build inside `PLAN-auto-org-write-time-linking.md` rather than assumed pre-existing. | final |
| 2026-07-18 | Lifted to `wiki/designs/` (part 1 landing, FRIDAY ladder feature 5). Governance stamp applied (`governs:` populated with the modules part 1 built out — `dream.py`, `dream_confirm.py`, `lifecycle.py`, `scripts/health/eval_v6_retrieval.py`). Added a landing note and inline "as built" callouts in the Tidying-on-age and Guarding-the-automation sections recording what part 1 actually shipped, without rewriting the design's own forward-looking prose elsewhere — parts 2 and 3 remain designed-for, so `status` holds at `final`. Both parent designs (`agentm-memory-system.md`, `agentm-experience-and-dreaming.md`) gained pointer amendments the same day. Cross-model prose pass: **Claude-only** — the `prose-pass` skill's own `prose_pass.py` script (the mechanism that drives `agy`) isn't present in the installed `design` plugin (0.4.0) or the `crickets` source tree despite being referenced by both the skill spec and its how-to page; `agy` itself is installed and authenticated, but the orchestrating script it would run through doesn't exist to invoke. Per the skill's own documented fallback, applied the same discipline by hand instead of the two-step pass. Flagging the missing script as a `crickets`-repo gap, out of scope for this plan. | final |
