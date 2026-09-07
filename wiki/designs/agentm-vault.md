---
title: AgentM Vault
status: draft
visibility: published
kind: design
scope: arc
area: agentm/vault-taxonomy
author: alexherrero
contributors: []
created: 2026-09-06
updated: 2026-09-06
last_major_revision: 2026-09-06
prd:
project:
---

# AgentM Vault

> [!NOTE]
> **DRAFT** — the living design of the vault-perfection series, opened 2026-09-06. It succeeds [Filing v2](agentm-filing-v2.md), inherited unchanged unless a section says otherwise; each session writes its decisions here the same day, and sections marked *open* hold only the inputs already ruled.

## Context

### Objective

The filing system is built: every part Filing v2 named has shipped, the corpus has migrated, and the daemon and the dreaming binary run it nightly. What has not happened is you sitting down with the result and shaping it until it is yours. That means a layout that reads the way you think, a card that holds what you would want it to hold, and machinery you can explain in a sentence. This design is that shaping, one decision at a time, written down with the reason and the alternative beside each. It refines what shipped; it does not rebuild it.

### Background

Filing v2 landed between v9.9.0 and v9.20.0 and a survey on 2026-09-06 read the result the way you would, folder by folder in Obsidian. The bones held: six classes, the contract in `standards/`, the lifecycle axis, the write path, the calendar register, the Go dreaming binary. Around them sat what the build had not reached — five empty `desk/` folders kept alive by Drive's icon files, engine state still inside `Agent/`, a nested `memory/memory/` written by the daemon's own probe, maps named `workflow-2.md` through `workflow-7.md`, a hand-kept `Home.md` eight weeks stale, and ten calendar reviews each saying nothing was recorded. The corpus told the larger story. Of 744 notes in the six classes, 251 are enriched tool tallies, 99 are truncated "User stated" fragments, 115 are one-line skill-discovery blurbs, 31 are mined opinion supplements, and 32 are `~dup` twins. The memory a person would keep is on the order of 200 to 250 notes, and only two `preference` notes exist that are not a fragment. The queue was not behind: 283 of the 356 unfiled notes had already been enriched and scored below the floor, and nothing owned that verdict.

The reference layout is your work vault, a sibling deployment of the same lineage, photographed the same evening and transcribed into the research bundle. It is lowercase throughout, and its agent half holds `desk`, `diagnostics` and `memory` with nothing loose. Its memory classes are a few dozen notes each, named by subject; every project carries the same skeleton; a task flows from `agent/desk/tasks/` into its project's `drafts/` when it closes; its scorecards are daily; its card is ten readable fields. Home already matches on the bones. Where the two differ, this design says which way home goes and why. Some differences are drift to correct. Some are deliberate, because home is a few long projects with many tasks each, and a personal space work does not have.

The series runs seven sessions, each taking one question and ending with a written decision here. You decide anything about how the vault looks and reads, anything touching `personal/` or `projects/` content, every threshold that demotes or forgets, every deletion, and every change to what a surface shows you. The agent decides mechanism, ordering and naming inside the code, and says so in the log so you can reverse it. Design sessions run on the strong model; the builds that follow hand off to `/work` sessions on the cheaper one, one plan per landing group, deployed the same day, migrations under quiesce.

## Design

### Overview

The vault becomes one lowercase tree that reads the same on both machines. `agent/` holds `diagnostics/` and `memory/` and nothing loose. `memory/` holds only the classes, each a few hundred notes at most once the residue is purged, each note named by its subject. A project carries the skeleton work proved — a charter, decisions, plans, research, drafts, a map — and its tasks live inside it, one directory per task with a plan, a progress log and a tracker. `standards/` is the four-file always-load surface. The calendar is facet files under a year map. One root note maps the vault; `Ideas.md` stays yours.

A memory card is what you would write: a title, a type, a one-line summary, the reason it was kept, an importance you read, the filing state, the lifecycle, one confidence tier, the transport it arrived by and the trust that implies, two dates, tags and relations — then a machine block the passes own. A card gets one deep pass, with analysis and research, and light passes afterwards, updated only when new input arrives. Your own spaces — `projects/` and `personal/` — are permanent: never demoted or archived by policy, ranked by importance rather than age, and enriched only in ways that leave the original content untouched. A card is written where the knowledge is: by the model in the room, the moment something durable settles, with the reason it was kept. What a writer cannot know it leaves empty, and an empty `why` is what marks a candidate for the nightly pass to judge.

### Infrastructure

No new component. The series changes the contract, the writers, the generators and the migrations that already run.

| component | role | what the series changes |
|---|---|---|
| `agentmd` | index, retrieval, MCP surface, capture, enrichment, the probe | field stamps, the card shape, ranking by importance in permanent spaces, the probe's root; the tool's four capture fields, one meaning for `status`, enrichment owed by stamp, the eager trigger removed, the tally gate |
| `agentmdream` | the nightly mutation pass, maps, rollups | map naming and pagination, empty-rollup skip, deep-once enrichment state |
| session hooks | recall at start and prompt; the trace and the miner at stop | the trace's shape; the miner files HIGH only and hands the rest to the trace (session 2, decided) |
| the runner | scheduled jobs | the parked health scorecard, a daily corpus scorecard, the nightly enrichment batch |
| the gate battery | deterministic verification | naming, card-shape and class-directory gates |

Everything still runs on your Mac under launchd, triggered at session boundaries and nightly, with no server and no cloud component.

### Detailed Design

#### The layout (session 1, decided)

The tree, with the reason and the alternative beside each move.

```
<vault>/
├── agent/          diagnostics/  memory/            nothing loose
├── calendar/       YYYY/ facet notes · moc-calendar-YYYY.md · rollups only for periods with entries
├── personal/       yours; your Title Case filing inside, untouched by this series
├── projects/       <slug>/ _index.md · decisions/ plans/ research/ drafts/ · tasks/<verb-slug>/ · moc-<slug>.md
├── standards/      storage-rules · user-preferences · security-and-secret-governance · moc-standards · voice/
├── index.md        the one root map
└── Ideas.md        yours
```

**The root spaces are lowercase.** `agent`, `calendar`, `personal`, `projects`, `standards`. Today four are Title Case and one is not, and the other tree you read every day is lowercase. Filing v2 ruled no top-level renames because renames churn; the churn is one landing group with the structural-move tooling that already exists, and a case-only rename on a case-insensitive disk goes through a temporary name. *Why not Title Case everywhere:* it would rename one folder instead of four, but every path in the loader, the contract and the daemon names `standards`, and it would keep the two vaults different for no reason you hold. *Re-audit* if Drive ever fails to follow a case-only rename; the two-step exists for that.

**`agent/` holds `diagnostics/` and `memory/`, nothing loose, nothing hidden.** `desk/` goes: its five folders are empty, scratch left the vault and projects moved out, and a task now lives inside its project. `_dream/`, `_meta/repos.json`, `.heat.json` and `.lifecycle.json` leave for the engine directory, which already holds the same layer's insights, staging and journals; the how-to twin in `_meta/` moves to `standards/`, where an operator-facing rule document belongs. `Home.md` retires (below). *Why not keep `desk/tasks/` for tasks without a project:* none exists today, and this vault's own rule is that a directory is discovered, never conjured; the day one appears, `desk/` returns with it. *Re-audit* when the first project-less task is filed.

**`memory/` holds only the classes.** The pen, `voice-kernel.md`, moves to `standards/`, where the loader already reads; the two watchlists and the three loose settings files move to `projects/agentm/`, because they are that repo's feature state and not memory; the nested `memory/memory/` is deleted once the probe writes at the memory root. *Why not the engine directory for the watchlists:* you edit those files in Obsidian, and the engine directory is not in the vault. *Re-audit* if a second feature grows working state that wants to live beside the classes — the answer is the same, its project.

**`crystallized/` is flat and holds only lessons with `consolidated_from`.** The six opinion lanes retire; they hold 31 mined fragments at `status: proposed`, and the class holds no lesson at all. Work's `crystallized/` is ten syntheses, one per system or task cluster, and that is the shape session 3 designs toward. *Why not keep the lanes elsewhere:* the accumulate loop that read them retired with the inbox triage engine, and a feature with no reader is a directory with no purpose. *Re-audit* if the opinion feature returns with a reader; its data then lives under the feature's project.

**`entities/` folds until something writes it.** The directory has held only its index since 2026-08-19. The class stays in the contract, reserved the way `person` is reserved for email ingest, and the directory is recreated by its first writer. Work's `entities/` is populated and subfoldered because work ingests bugs, changes and chat threads; home ingests nothing yet. When it returns it is flat, per Filing v2's D7. *Why not keep the empty directory as a promise:* an empty shell is how the six-class layout failed once already. *Re-audit* when an ingest source lands.

**`episodic/` stays.** Session traces are its writer now; ingested threads and mail join when ingest arrives. What a trace holds is session 2's question — today it is a first prompt and a list of what recall injected.

**Maps are named for what they map, and `Home.md` retires.** `mocs/` holds `moc-root.md`, `moc-memory.md` and `needs-review.md`; a per-type page exists only past a member threshold, and a page that must paginate paginates inside itself, never into `workflow-2.md`. `moc-root.md` is the entry point, generated nightly; each area keeps its own `moc-<area>.md` beside its files, as diagnostics already does. *Why not a generated `Home.md`:* the map already exists under its own name, and a second entry point is a second thing to keep true. *Why not a hand-kept one:* it has been wrong for eight weeks and was the first link on every map. *Re-audit* if the root map grows past a screen; split by area then.

**`calendar/` is facet files under a year map.** `YYYY/YYYY-MM-DD-<facet>.md`, created only on a day with content for that facet, and `moc-calendar-YYYY.md` beside the year, generated. The bare-date day index Filing v2 drafted is dropped: work runs without one, and home has no entry to prove it by. Weekly and monthly rollups are written only for a period with at least one entry; the ten empty reviews go. The stray day note and template at the calendar root fold into the year or go, and whether your Obsidian daily-note template and the register become one note is session 4's. *Re-audit* if a facet ever needs a per-day summary that the year map cannot carry.

**`standards/` is four files and a voice library.** `storage-rules.md` (the contract), `user-preferences.md` (the pen folded in, and the durable "how I want things done" — few lines, written by you, loaded every session), `security-and-secret-governance.md` (the standing constraints, today scattered across a brief, a follow-ups file and a CLAUDE.md), `moc-standards.md` (generated), and `voice/` holding the nine on-demand voice rules that sit in `projects/_global/wiki-style/` today. The always-load budget these imply is session 6's; the set is decided. *Why `user-preferences.md` matters beyond tidiness:* 99 of the 101 `preference` notes in the corpus are truncated fragments the miner cut from your sentences. The durable preferences are few, and a file you write beats a corpus mined from transcripts. *Re-audit* if the four files exceed the always-load budget session 6 sets.

**One root map.** `Filing.md` folds into `index.md`; they carry the same write-authority table today. `Ideas.md` stays yours and is not touched unless you ask for its paths to be brought forward. *Why not no root notes, as at work:* the map is for you, and the daemon enforces the table whether or not a note restates it.

**A project's tree converges on work's skeleton.** `_index.md` is the charter; `decisions/`, `plans/`, `research/` and `drafts/` are the same folders under the same names; `moc-<slug>.md` is generated; `docs/` appears when something mirrors documents. A task lives at `projects/<slug>/tasks/<verb-slug>/` with `plan`, `progress`, `tracker` and its artifacts flat beside them, and a generated `projects/moc-tasks.md` gives the cross-project view of everything in flight. *Why nest tasks under the project rather than work's flat `agent/desk/tasks/`:* home is a few long projects with many tasks each, and work's flat list has already grown project buckets inside itself within three weeks. `_harness/` becoming `desk/` requires the crickets development-lifecycle repoint; session 4 sequences it. *Re-audit* when a project holds more than about forty task directories — an archive step for closed tasks is the answer, not a flat list.

**Naming.** Directories and files are kebab-case with a subtype word where the directory does not carry it: `protocol-` and `recipe-` in `procedural/`; `doc-`, `plan-`, `brief-`, `research-` and `decision-` in a project; task directories verb-first with any external identifier last (`fix-grad-issue-519271308`, not `b519271308-…`). A gate holds it, because work's rule without a gate drifted within weeks into two spellings of one task. Dates appear in a name only where the date is the identity: calendar, scorecards, session traces.

**Housekeeping the survey found, the agent's to do.** The probe writes at the memory root and the nested directory goes; the vault-root `diagnostics/` was a wrong-root write and goes with its writer repointed; walkers and the empty-directory cleanup treat Drive's `Icon` files as absent; the runner's `health-pass` job, parked at the watchdog's stop rung since 2026-07-25, is revived and the corpus scorecard gets a daily job of its own; the empty reviews stop. Each is recorded in the follow-ups file so a build session can take it.

#### The card (session 1, decided)

What a memory note holds, in the order Obsidian's properties panel shows it: what you read first, what the machinery reads last.

```yaml
---
title: Keep git out of Google Drive's mirrored folders   # the name; the filename is only the address
type: workflow                                            # or kind: for a record; never both
summary: One line — what this is and when it applies.
why: Why this was kept — the reasoning at capture.        # what was happening, and what this decides; only a writer that knows may write it
importance: 7                                             # 1–10; you read it; enrichment proposes, your edit wins
status: active                                            # active | unfiled
lifecycle: active                                         # pinned | active | dormant | archived | superseded
filing_confidence: high                                   # the one confidence tier you read
source: conversation                                      # the transport; source_url / source_id beside it when present
trust: trusted                                            # derived from source, stamped on every note
created: 2026-09-06
updated: 2026-09-06
tags: [git, google-drive]                                 # never an empty list
related: [[drive-upload-staging-churns-transient-files]]  # supersedes / superseded_by live here too

slug: keep-git-out-of-google-drive                        # machine-owned from here down
confidence: 0.91
enriched_by: enrich/1+prompt/a73ff0f4f5dc
enriched_at: 2026-09-06T14:00:00Z
rules_hash: 3c57fd89087c1a25
fingerprint: bd36f748…
importance_proposed: 6                                    # what enrichment proposed; when importance differs, the value is yours
---
```

The body is the memory in prose — your words where they were yours, enrichment's where it wrote them — followed by an optional `## Evidence` block quoting the excerpt it came from. There is no metadata block in the body; the frontmatter already says it.

Field by field, against the corpus of 744 and against the work card (`id · type · title · status · created · updated · importance · tags · related_notes · aliases`):

| field | today | decision |
|---|---|---|
| `title` | on 48% | required on every memory; the miner and `save.py` write one at capture |
| `type` / `kind` | 93% / 7% | unchanged, one or the other, never both |
| `summary` | on 30%, enrichment only | required when the body exceeds a paragraph; written at capture, refined by enrichment |
| `why` | nowhere | new; what was happening when this was kept and what it decides later, one or two sentences. Written at capture by the model in the room or by you, never by enrichment; absent on a candidate (session 2) |
| `importance` | nowhere | new, 1 to 10, the same field you read at work; capture or enrichment proposes it into `importance_proposed`, and a value that differs from the proposal is yours and is never overwritten |
| `status` | 100% | `unfiled` means no judgment has been made, `active` means one has — one meaning for every writer (session 2); `proposed` retires with the opinion lanes |
| `lifecycle` | 97% | unchanged; stamped on the 24 that lack it |
| `filing_confidence` | 14% | the one confidence tier you read, on every note — backfilled `high` where an enrichment scored at or above the contract's floor and `low` below |
| `confidence` | 43% | enrichment's numeric reading, machine block |
| `mining_confidence`, `mining_rationale`, `mining_occurrences` | 15% | retire with the miner's shape (session 2) |
| `source`, `source_url`, `source_id` | 95%, 17%, 2% | unchanged |
| `trust` | 20% | stamped everywhere; it is a table lookup from `source`, so the backfill is deterministic |
| `created`, `captured` | 30%, 26% | one field, `created`; `captured` folds in |
| `updated` | 85% | unchanged |
| `tags` | 84%, 91 empty | an empty list is omitted, not written |
| `slug` | 57% | machine block; always equals the filename |
| `altitude` | 52% | retire; `lifecycle: pinned` is what `canonical` meant |
| `group` | 16% | retire; the path says it |
| `always_load` | 13%, all false | retire; the tier is a directory |
| `evaluator_classification`, `rubric_score` | 15% | move to the watchlist's own record; not a memory field |
| `fingerprint`, `occurrences`, `aliases`, `enriched_by`, `enriched_at`, `rules_hash` | 9% to 43% | machine block |
| `superseded_by`, `supersedes`, `lifecycle_since` | ≤ 7% | unchanged, beside `related` |
| `excerpt_edges_unverified`, `review_flags`, `derived_from` | ≤ 6% | machine; session 3 confirms each still has a reader |

**Slugs.** The slug is the address and reads as one. `~dup`, `~dup2`, `-1`, `-2` and `workflow-bash-259` are counters standing in for a name; the collision rule produces a meaningful word, and a `~dup` the copies job will collapse is never a file. *Why the card is heavier than work's ten fields:* home carries a lifecycle axis, a transport vocabulary and a trust tier that work does not, and each has a reader in the daemon. Everything a reader does not have is in the machine block or retired. *Re-audit* if a field in the operator-read block goes a month without you or a surface reading it.

**Records** (`kind:` notes — traces, scorecards, maps, reviews) keep the same order for the fields they share and add their own; a record never carries `importance`, `why` or `filing_confidence`.

#### Capture (session 2, decided)

What holds for a memory the moment it lands, and who may write which field. Measured on 2026-09-06 against the live writers: enrichment had run once by hand on 2026-08-27; the daemon's eager trigger was never attached and never fired; the Stop hook's candidates landed `status: active` at low confidence, a state enrichment never reads; and 572 of the 769 notes in the classes were residue from the old miner. The queue the daemon was red about was not a pass falling behind. It was a pass nobody runs.

**The in-session card is the primary capture.** When something durable settles in a session — you say how you want a thing done, a decision is made, a fix is found, or a fact is learned that a later session will need — the agent writes the card immediately through `memory_capture`. It fills the fields you read: `title`, `type`, `summary`, `why`, `importance`, `tags`, and `related`. It lands `status: active`, `filing_confidence: high`, because a writer that could say why already judged it. The cost is a few hundred output tokens per card on the model already holding the context. This replaces a cold model reconstructing the same card from a 200-character excerpt. *Why not a model call in the Stop hook:* it reconstructs too, only sooner, inside a 30-second hook budget against a transcript that can reach a megabyte, and it pays on every session boundary whether or not anything durable happened. It stays named as the fallback. *Re-audit:* a month in which sessions write fewer than one card a day while the traces show decisions being made.

**`why` is the reasoning, and only a writer that knows may write it.** One or two sentences: what was happening when this was kept, and what it decides later. You or the model in the room write it at capture; enrichment never writes, rewrites, or removes it. A card without `why` is a candidate — nothing has judged it worth keeping yet — and it lands `status: unfiled`. The firing miner rule is not a `why`; it goes to the trace. *Why not let enrichment write one:* a reason guessed from the note reads exactly like a real one, and the field is only worth having if it is always yours or the room's.

**`status` means one thing.** `unfiled` means *no judgment has been made*; `active` means *someone or something judged this worth keeping*. The Go writer already meant the first. The Python lane meant the second and wrote `active` at low confidence, which enrichment's gate never reads. Every writer now agrees: a knowing writer's card lands `active`; anything else lands `unfiled`; enrichment's floor moves a note up or leaves it. `filing_confidence` stays the tier you read. No ranker reads it, and no document may imply one does.

**The Stop hook keeps the trace and stops writing fragments.** The session trace is the handoff record, written with no model call. Its `title` is the first prompt; `project` and `surface` are stamped; `touched:` (renamed from `entities:`, which held basenames) lists what was captured and recalled. The body contains `## Asked`, `## Outcome` — the closing recap every session already ends with, which is free and the best summary the session produced — followed by `## Captured`, `## Recalled`, and `## Candidates`. `## Candidates` holds every line the miner once filed as a note, kept as a line with the firing rule, excerpt, and count. The miner files a card only for a HIGH candidate: a durability cue typed by you, under the utterance ceiling, whatever the host says about who sent it. It files that card `unfiled` with `why` empty, so the nightly pass judges it. MEDIUM and LOW never become notes. The four rulings from the write path's labeled sample are built as written. The tally template becomes a gate at the write door, so a stale miner in a worktree cannot refill the purged population. *Why not retire the miner:* the candidates list costs nothing and is the record of what a session said in passing; the next session's model, or dreaming, can promote from it with context a nightly pass over a lone note never has. *Why not keep MEDIUM as unfiled notes:* enrichment scored 85 of the 283 fragments it read at 0.2 or below; a fragment does not become a memory by being rewritten.

**The deep pass is owed once, and the stamp says whether it has happened.** Eligibility moves from status to stamp. A card with no `enriched_at` is owed the deep pass whether `active` or `unfiled`. A card whose body changed since its stamp is owed a light pass. A card with neither is skipped at no cost; the fingerprint key (pass version, rules hash, body) already carries this. What the deep pass does is session 3's. What it may not touch is decided here: `why`, the Evidence block, and an `importance` you set. The machine block records `importance_proposed`; when `importance` differs from it, the value is yours and no pass writes it again.

**The "no" is a demotion.** A note enrichment scores below the floor stays `unfiled` and is listed in needs-review. On a second verdict below the floor — which happens only when the body, the prompt, or the contract changed — the note moves to `lifecycle: dormant`, journaled and in the digest, as the contract already allows for demotion. It leaves the queue, stays on disk, and returns on a genuine recall. Purge stays yours, with a manifest. *Why not auto-archive:* `archived` is conspicuous by contract. *Why not leave it unfiled:* 283 notes unfiled forever is what the daemon was red about.

**Enrichment runs as a nightly batch; the eager trigger retires.** This is the agent's call and reversible. The eager trigger never fired and would spend a model call per capture as it lands. The batch does the same work in one place, under a budget, with a report. Session 3 sets the budget, the order, and the prompt, which drops `altitude` and gains `importance_proposed`. The first real batch runs after that, not before, so the corpus is judged once under the new prompt rather than twice. The daemon's queue-age alert stays red until then, and that is the honest reading.

**The Go capture and the MCP tool.** `memory_capture` gains `summary`, `why`, `importance`, and `related`, all optional, and its description says what `why` is. The surface stays two tools. A capture that names its type and gives a `why` lands `active`; one that does not lands `unfiled`. `instructions` moves into the Go door so the one front door carries the one security rule. The daily write cap stays. `captured` is written as `created`.

**The purge, ruled 2026-09-06 by count.** Six non-overlapping manifests live in the series' working directory under `S2-manifests/`: A tool tallies 292 · B "User stated" fragments 107, including the seven TempleCoordination rules because the 2026-09-05 session's own records carry that ground · C "Fix observed" fragments 21 · D skill-discovery blurbs 116 · E opinion supplements 32 · F `~dup` twins 4. That totals 572 notes, leaving 172: 76 ideas, 47 conventions, 36 references, 5 workflows, 3 fixes, 2 preferences, 3 traces. Each manifest runs only when the day's fresh count matches the ruled count, and the tally gate lands in the same group.

#### Dreaming (session 3, open)

Recorded inputs. The binary's jobs are mechanical and cost nothing; the Python stages and enrichment spend model calls, and every step gets a cost and an effect before a budget and an order are set. Enrichment proposes `importance` into `importance_proposed`; an operator's value is never overwritten. The deep pass is owed by stamp, not status; the batch is its only trigger; the prompt drops `altitude` and gains `importance_proposed`; the budget, the order and the first run under the new prompt are this session's. The crystallized class is designed toward work's shape: a synthesis per system or task cluster, with provenance. The fuzzy-similar pair parking removed in v9.20.0 is a candidate to return, as is promotion from the traces' `## Candidates` lists.

#### Projects and tasks (session 4, open)

Recorded inputs. A `tracker` holds the objective, the current status and immediate state, and the next immediate steps; `progress` is the append-only log; `plan` is the intent. Home's plan file carries plan and tracker together today, and the session decides whether it splits. Work flattens a closed task into the project's `drafts/` as `<task>_progress` and `<task>_tracker`; home nests tasks under the project and decides the close step. `_harness/` becoming `desk/` is a crickets paired release.

#### Lifecycle per space (session 5, open)

Recorded inputs. `projects/` and `personal/` are permanent: nothing in them goes dormant or archived by policy, search ranks them by importance rather than by the decay curve, and a finished project's retirement is a project-level act. `personal/` is altered only when you ask; enrichment of your files writes frontmatter only and never the body, and, until session 5 rules otherwise, runs only for areas you name — the contract's `model_exempt_spaces` default stays, because a background model reading `personal/` is the boundary the TempleCoordination records were placed there to have. *This is the agent's assumption, named for you to reverse.* The memory classes keep the decay curve and the lifecycle axis Filing v2 shipped; the thresholds cannot fire on a corpus whose oldest note is 144 days, and the session designs what it cannot yet watch.

#### Surfaces (session 6, open)

Recorded inputs. The memory payload has not been re-pasted into claude.ai or the Gemini Gem since filing v2; the always-load tier is 17 KB today and the four-file `standards/` set grows it; the daemon's surface stays exactly two tools.

## Alternatives Considered

**Amend Filing v2 instead of a successor.** Rejected. Its body is the record of a build, organized by the six parts that shipped, and the series touches three designs; amending one leaves two stale by construction. It stays inherited and pointed forward.

**Work's flat `agent/desk/tasks/`.** Rejected for home. The flat list is already growing project and activity buckets inside itself at work, and home is few projects with many tasks each. The cross-project view work gets from the flat directory, home gets from a generated map.

**Title Case root spaces.** Rejected. Today's mix is the one state nobody chose; lowercase matches the other vault you read and the kebab rule everything below the root follows.

**A generated `Home.md`.** Rejected in favour of the root map that already exists under its own name.

**Keep the empty `entities/` as a promise.** Rejected; discovered, never conjured.

**Keep the bare-date day index.** Rejected; work runs on facet files and a year map, and home has no entry to justify a per-day file.

**Ten fields, exactly the work card.** Rejected; home's lifecycle, transport and trust each have a reader in the daemon. The fields without a reader are retired instead.

**A model call in the Stop hook as the primary capture.** Rejected for now; it reconstructs from the transcript inside a 30-second hook budget and pays on every session boundary. Named as the fallback with its re-audit trigger.

**Enrichment writes `why`.** Rejected; a guessed reason reads like a real one.

**Retire the miner outright.** Rejected; its candidates cost nothing as lines in the trace and are the record of what a session said in passing.

**Auto-archive a twice-rejected note.** Rejected; `archived` is conspicuous by contract. It sinks to `dormant` instead.

## Dependencies

[Filing v2](agentm-filing-v2.md) for every decision inherited unchanged; [Capture](agentm-capture.md) and [Experience & Dreaming](agentm-experience-and-dreaming.md), which sessions 2 and 3 reconcile; the [storage seam](memory-storage-seam.md) for the root-versus-memory-root distinction the probe and the retrieval gate both got wrong; the live contract in `standards/storage-rules.md`; the crickets development-lifecycle plugin for the `_harness/` repoint and the design template and prose pass; Obsidian's basename link resolution and the Drive sync path, which make a case-only rename a two-step; and the work vault reference in the research bundle, `REFERENCE-work-vault-layout.md`, as the transcription this design compares against.

## Migrations

Landing groups, in the order that keeps the live vault consistent at every step; session 7 sequences them into plans and may split or merge them.

1. **Hygiene.** The probe's root, the vault-root `diagnostics/`, the `Icon`-kept empties, `health-pass`, the empty reviews. No design needed; each is in the follow-ups file.
2. **The purge, and the tally gate.** The six manifests ruled in session 2 (572 notes), run through the migration engine under `--confirm-count`, with the write-door gate that refuses the tally template landing in the same group so nothing refills manifest A. Before any backfill, so nothing dresses residue up again.
3. **The capture writers.** The tool's four fields and `instructions` in the Go door; one meaning for `status` in the Python lane; the trace's shape and the miner's HIGH-only filing; the eager trigger and its never-called attachment removed; enrichment eligible by stamp; the nightly batch registered as a runner job with its budget left to session 3. Writers land before the backfill they define.
4. **The memory root trims.** `_always-load` into `standards/`, watchlists and settings into `projects/agentm/`, engine files out of `agent/`, `_dream/` and `_meta/` out. Path repoints ship with the moves.
5. **The card backfill.** Titles and summaries where derivable, `filing_confidence` from the enrichment score, `trust` from `source`, `created` from `captured`, the retired fields dropped, empty tag lists removed; the 74 notes the Python lane filed `active` at low confidence re-stamped `unfiled`. Dry-run counts first; every backfilled note keeps its `enriched_by` so a model-written title is never mistaken for yours.
6. **Maps and root notes.** `moc-root`, `moc-memory`, the retirement of `Home.md` and the paginated pages, `Filing.md` into `index.md`, `moc-calendar-YYYY`.
7. **The root casing.** Last of the structural moves: quiesce, temp-name two-step per folder, the repoint inventory, link check before and after, `agentmd embed` to close.
8. **Projects and tasks.** Session 4's landing group, paired with crickets.

Every group is dry-run first, link-check green before and after, and inherits Filing v2's vault-wins collision doctrine and embed backfill. Rollback for every group but the purge is `git revert` plus the repoints replayed.

## Technical Debt & Risks

- **A case-only rename on a case-insensitive disk under Drive.** Mitigation: the two-step through a temporary name, under quiesce. Re-audit trigger: Drive shows a duplicate folder after the rename.
- **The purge is irreversible.** Mitigation: a manifest per population, an exact count you confirm, the migration engine's `--confirm-count`. Re-audit trigger: any population whose count moves between the manifest and the run.
- **Backfilled titles and summaries are model-written.** A stub enriched into confident prose is how 251 tallies survived the last purge. Mitigation: the purge runs first, and every backfilled field carries `enriched_by`. Re-audit trigger: a backfilled title on a note you would not have kept.
- **Enriching `personal/` against the model-exempt boundary.** Mitigation: frontmatter-only, opt-in per area, default off, until session 5 rules. Re-audit trigger: session 5.
- **The `_harness/` repoint is a paired release.** Mitigation: session 4 sequences it after the crickets side lands, as 2b was. Re-audit trigger: any plan-resolution path that still probes `_harness/` after the move.
- **The always-load budget grows with the standards set.** Mitigation: session 6 sets the budget before the fourth file lands. Re-audit trigger: the tier past 25 KB.
- **The empty-shell failure can repeat.** Mitigation: `entities/` is not recreated until written, and class populations sit on the daily scorecard. Re-audit trigger: any class at zero thirty days after its writer ships.
- **The in-session card may be sparse.** A capture that depends on the agent noticing a durable moment can miss them. Mitigation: the always-load payload names when to capture, the session brief asks at the end what went uncaptured, and the trace's candidates list keeps what the miner saw. Re-audit trigger: fewer than one card a day over a month while the traces show decisions being made; the Stop-hook model call is the next step.
- **The daemon stays red until session 3's first batch.** Nothing judges the queue between the capture writers landing and the budgeted batch. Mitigation: the alert is reading a true state, and the purge removes 316 of the 356 it counts. Re-audit trigger: the first batch not scheduled within a week of session 3.
- **A stale miner in a worktree.** The hook resolves the miner relative to the session's working directory, so a worktree from an older base runs an older miner. Mitigation: the tally gate at the write door. Re-audit trigger: any note matching a retired template after the gate lands.

## Quality Attributes

### Security

The write-authority table is inherited unchanged: `agent/` written freely, `calendar/` shared, `projects/` under a session grant, `personal/` per task, `standards/` and the root notes on instruction. The one new boundary is enrichment of your own spaces, which writes frontmatter and never the body, and reads `personal/` only where you opt an area in. A capture is visible to recall from the moment it lands, at reduced weight while `unfiled`; there is no review window. The guard is that captured content never gains instruction authority, that a fetched or mailed transport carries `trust: untrusted` through every pass, and that recalled memory is data to the surface that reads it.

### Data Integrity

ID-stability holds through every move: basenames survive the casing rename, the trims and the fold of `Filing.md`, so name-resolved links keep resolving. Backfills are additive and stamped; nothing in your spaces has its body rewritten by a pass; the purge is manifested and count-confirmed.

### Privacy

`personal/` stays exempt from the memory contract and from background model reads by default. The TempleCoordination records placed there on that promise keep it. An opt-in is per area, named in the contract, and reversible by removing the line.

### Testability

Each rule lands as a gate: kebab names with a subtype word, the card's field order and required fields, a class directory holding only classes, no empty tag lists, no empty rollups. Every backfill and every move prints its dry-run count before it runs.

## Project management

### Work estimates

| landing group | size |
|---|---|
| hygiene | S |
| the purge and the tally gate | S, ruled |
| the capture writers | M |
| the memory root trims | S |
| the card backfill | M |
| maps and root notes | M |
| the root casing | M |
| projects and tasks | L, paired with crickets |

### Documentation Plan

This design; the pointer row and note in `agentm-filing-v2.md`; `Designs.md` and `_Sidebar.md` entries; the vault's `index.md` rewritten when `Filing.md` folds in; `reference/Memory-Daemon.md` for the probe's root; `reference/CI-Gates.md` for the new gates; the how-to for the vault layout once the casing lands; `Capture` and `Experience & Dreaming` reconciled by sessions 2 and 3.

### Launch Plans

Sessions 2 through 6 amend this design; session 7 reads it end to end with you, then `/design translate` and `/design sequence` produce one plan per landing group for `/work`.

## Operations

### Monitoring and Alerting

The daily corpus scorecard gains: residue count (zero after the purge, and a line that stays), class populations, the card-shape gate's count of non-conforming notes, empty directories under `agent/`, and the health-pass cadence itself.

### Logging Plan

Every migration writes its manifest and its dry-run count to `agent/diagnostics/migrations/`; backfills journal what they stamped; the purge writes its manifest before deleting.

### Rollback Strategy

Each landing group reverts with `git revert` in the vault repository plus its repoints replayed; the casing rename reverts by the same two-step in reverse; the purge does not revert and is bounded by its manifest.

## Document History

| Date | Change | Status |
|---|---|---|
| 2026-09-06 | Session 2 of the series: initial capture. The measurement first — enrichment had run once (every `enriched_at` dated 2026-08-27), the eager trigger had never been attached, the Stop hook's candidates landed `active` at low confidence where enrichment never looks, and 572 of 769 class notes were miner residue; the red queue was a pass nobody runs, not a pass behind. Seventeen rulings taken: the six purge manifests by count (572 out, 172 remain), the tally gate in the purge's landing group, no enrichment batch until session 3's prompt and budget; the in-session card as the primary capture with the Stop-hook model call named as fallback; `why` as what was happening and what it decides, written only by a knowing writer; one meaning for `status`; the trace as the handoff record (Asked · Outcome · Captured · Recalled · Candidates) and the miner filing HIGH only; the deep pass owed by stamp with `importance_proposed` guarding your edits; a second sub-floor verdict sinking to `dormant`; eager retired for the nightly batch; four optional fields on the tool. Migrations gain the capture-writers group. [Capture](agentm-capture.md) reconciled to the write path in the same change. | draft |
| 2026-09-06 | Created in session 1 of the vault-perfection series, authored from the crickets design template as the successor to Filing v2 (which is pointed here). The layout and the card are decided — twenty-one rulings taken in conversation against the survey of the live vault and the work vault photographed the same evening: lowercase roots, `agent/` trimmed to two children, `memory/` to the classes, tasks nested under their project, `crystallized/` flat, `entities/` folded until written, maps named for what they map with `Home.md` retired, the calendar on a year map, the four-file `standards/`, one root map, kebab naming with a gate; the card's field order with `why` and `importance` added and `altitude`, `group`, `always_load` and the mining fields retired. Inputs for sessions 2 to 6 recorded in their sections: deep-once-then-light enrichment, the `tracker` definition, permanent spaces ranked by importance, personal files enriched in frontmatter only under a per-area opt-in. Prose pass: the cross-model step degraded (`agy` returned an empty result twice), so a Claude-only simplification pass ran against the fact-guard list. | draft |
