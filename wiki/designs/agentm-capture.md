---
title: AgentM Capture
status: launched
kind: design
scope: feature
area: agentm/memory
parent: agentm-hld.md
governs: [scripts/memory_mcp_tools.py, harness/skills/memory/scripts/capture.py, harness/skills/memory/scripts/save.py, harness/skills/memory/scripts/ingest.py, harness/skills/memory/scripts/ingest_sweep.py]
seeded: 2026-07-16
approved: 2026-07-17
---


# AgentM Capture

## Context

### Objective

We need a way to get a thought or an article into agentm's memory from your phone, your browser, or a chat. The existing write tool skips the inbox and files straight into permanent memory. **This design creates a single front door for all captured content.** Everything lands in a staging folder first. The machinery on your machine takes it from there. We define the paths in, how each kind of capture is handled, the machinery that does the processing, and the security boundary everything crosses.

### Background

We need a way to capture information from any device. The system already has a read-side staging area, where our automated triage system automatically promotes, merges, or expires notes. We never built the write side of this process. The existing write tool, `memory_append`, bypasses this staging area completely and writes directly to permanent memory.

Two transports already work. The claude.ai Capture project writes candidates into the staging folder from the phone or any chat through the Google Drive connector. The Obsidian Web Clipper writes candidates from desktop Chrome. Neither transport needs a credential on any device. Our privacy rules shape every choice here. Data moves only through local and first-party tools. All captured content is treated as untrusted until it crosses one audited doorway.

Building this takes little new effort. We reuse many existing components. We already have the tools to write files safely, split text into chunks, evaluate confidence, run scheduled tasks, and provide server capabilities. The new work consists of a staging interface, tracking where data came from, a command to process long articles, and a scheduled job that fully processes forwarded links. We leave automatic cross-linking across the entire vault to the auto-organization design (`agentm-auto-organization.md`). That design ships in the same arc. Newly ingested notes get connected soon after they land.

## Design

### Overview

Capture works in three stages. This design is organized the same way. 

First are the ways in. Several doors deliver a candidate note into the staging folder (`personal/_inbox/`). These doors include your phone, your browser, and your desktop.

Second is the handling. Every candidate is absorbed into memory. Links and articles get fully processed into small linked reference notes. Ideas route to the ideas ledger. A capture can carry your instructions for the agent to act on after it absorbs the note.

Third is the machinery. A small set of tools and one scheduled job on your machine do all the work. The phone and browser only drop off a note.

Ingestion produces both forms always. It keeps the full article stored intact as one document note. It also splits the article into small chunk notes for retrieval. Neither replaces the other. This is the only path that produces more than one note per capture. Everything else stays one note per item. Where the finished memories live is the memory-system design's territory. How they surface automatically in later sessions is also covered there. The last Detailed Design section points there.

### Infrastructure

Everything in this design runs inside our existing memory engine, server, and task runner. The design adds a second tool beside `memory_append`. We add two commands, a few tracking fields, and one scheduled job.

| Component | What it is |
|---|---|
| `memory_capture` | new MCP tool: appends a candidate to `_inbox/` (staging only) |
| `/memory capture` | the same door as a CLI verb, for in-process callers and scripts |
| `/memory ingest` | URL or file → chunked `domain-reference` notes, provenance-stamped |
| provenance fields | `source_url` · `source_fetched` join `save.py`'s optional field order |
| ingest sweep | a runner job: forwarded links and documents in `_inbox/` → the ingestion pipeline |
| the bridge guard | the one audited boundary all captured content crosses |

| Trigger | Path |
|---|---|
| the Capture project (phone / any chat) | Drive connector `create_file` → `_inbox/` |
| Obsidian Web Clipper (desktop Chrome) | clip → `_inbox/` |
| MCP call from any connected host | `memory_capture` → `_inbox/` |
| CLI / in-session verb | `/memory capture` · `/memory ingest` |
| runner schedule | ingest sweep, cadence-bound (default hourly) |
| session hooks (as today) | reflection keeps routing MEDIUM/LOW to `_inbox/` |

Saves either succeed completely or fail cleanly. The system alerts you immediately if a capture fails. Your thoughts are never lost silently. The automated triage system logs every decision it applies, and you can always undo them.

### Detailed Design

#### The ways in

| Door | Status today | Notes |
|---|---|---|
| Claude app / claude.ai chat — the **Capture** project + Google Drive connector | works today | the primary phone-and-chat path; the recipe and standing instructions live in the findings memo; the capture→recall probe rides this path |
| Obsidian Web Clipper (desktop Chrome) | works today | the one-click desktop article path; template recorded in the findings memo; clips arrive with full content, so the sweep skips the fetch |
| any file into `_inbox/` (desktop) | works today | any app that writes the folder is a door — Obsidian's new-note button can default there; zero build |
| `/memory capture` · `memory_capture` (in-session) | this design builds them | the explicit door for sessions and scripts |
| reflection's MEDIUM/LOW lane | shipped, as today | the agent's own session learnings arrive through the same folder |
| Dispatch / Remote Control | live, research preview | secondary in-session phone paths; Dispatch reaches the vault only when its message explicitly asks for a Code session |

From your phone, the flow works today. You open the **Capture** project in the Claude app. You send `capture this: <link>`, a bare thought, or `idea: <thought>`. You can include a trailing instruction like "add to my ideas ledger". The project uses standing instructions. Claude reads the link and builds the candidate. The claude.ai Google Drive connector creates the file straight in `_inbox/`. The file rides Anthropic's servers to Drive. Drive syncs the file to your machine. The phone requires no harness. The process needs no credentials. Your machine can be asleep. Drive delivers the file when the machine wakes. The local machinery processes the file from there. You complete one initial setup step. You approve the connector's Create-file permission with "Always allow". A failed capture appears in the chat reply. You always know the exact outcome.

The Obsidian Web Clipper runs in your desktop browser. It saves a full article into the staging folder with one click. Clipper paths resolve relative to Obsidian's own vault root. This root sits one level above the engine's `vault_path()`. You prefix the clipper paths with `Agent/`. The staging folder is an ordinary folder in the vault. Any app that writes a file there is a door. You can set Obsidian's new-note button to default there. You configure this path via Settings, then Files and links, then Default location for new notes. Obsidian on the phone cannot open a Google-Drive-synced vault. The Claude app provides the phone path.

#### What arrives — the candidate shape

We add new fields to the existing format for staged notes. We record the transport in `source:`, the exact time in `captured:`, and the device in `surface:`. A candidate also declares its `kind`. We use `capture` for links, articles, and thoughts. We use `idea` for entries headed to the ideas ledger. A candidate includes any `tags` you attached. It also holds an `instructions` field. The rules for instructions appear in the next section. These new fields join the `created` field the triage system already uses.

The system writes these notes through the direct atomic-write path reflection already uses. This write goes around `save_entry`. The `save_entry` tool requires kebab-case folder names, which `_inbox` intentionally fails. This bypass is the standing convention. We document it in the code. It keeps staged items separate from permanent memory. The new notes use our standard file naming scheme. The triage system processes all staged notes together.

#### How candidates are handled

The system absorbs every candidate first. The content lands, gets processed, and becomes recallable. Processing depends on what arrived.

- *A thought or note* stays one candidate and rides the existing triage machinery. The system promotes it to a canonical memory entry, merges it with a near-duplicate, or expires it when stale. Inbox triage auto-applies these decisions. It logs all changes for reverts. Until promotion, the note remains hidden from the agent's searches. The staging folder is recall-excluded by default.
- *A link or a document* goes through the ingestion pipeline. **Ingestion produces both forms of the article.** It stores the complete text intact as one document note. You can review the original whole whenever you need it. It also creates one small `domain-reference` note per paragraph-sized chunk for agentic retrieval. Neither form replaces the other. These chunk notes are source-stamped. They link in reading order and point back to the full document. The system places them all under `personal/domains/<topic>/`. Ingestion is the only path that produces more than one note per capture. Everything else is one note per item. A phone capture is one candidate. An explicit save is one note. Reflection extracts one small note per durable fact from a session. The session itself is never stored. Conversations are not rebuildable from memory. This is by design.
- *An idea* (`kind: idea`) preserves your words verbatim. The system records the raw text exactly as submitted. The machinery folds idea candidates into the ideas ledger's real format. One mechanism serves both operator-typed ideas and the ideas reflection already mines into the inbox. The ledger's own shape lives in its pending design. This design only delivers candidates to it.
- *A capture with instructions* triggers an action after the absorb step succeeds. Examples include "add to my ideas ledger" or "research this further, then file". On a chat surface, the acting happens in the same turn. The Capture project already does this. A clipper capture arrives with its instruction unexecuted. The machinery runs the act step on your machine. It stamps the candidate with `instructions_acted` when done.

**The instructions field may only carry text you typed at capture time.** The security rule is mechanical. It applies without exception. Text inside a fetched article is data. Phrases like "ignore previous instructions" are inert body content. This holds true no matter how they are phrased. A fetched article never gains instruction authority. This is the trust boundary's authority rule applied field by field.

#### The machinery

**The front door tools.** The `memory_capture` tool takes your content and optional details. It writes them to the `_inbox/` staging folder. The tool returns an identifier. It marks any candidate carrying a link for full processing by the ingest sweep. The `memory_append` tool continues to work exactly as it does today. It handles explicit writes directly to permanent memory. The design adds `memory_capture` as a second tool beside it. We update both tool descriptions so agents understand this division. Secondary devices default to the capture tool. A prototype of the chat-surface front door already exists. The claude.ai Capture project uses standing instructions to implement this exact contract. The instructions absorb content first and limit writes to one per message. We use the prototype text as the starting specification for the cloud-side `memory_capture` behavior when the tool gets built.

**Article ingestion.** You use the `/memory ingest <url|file>` command to process web pages or files directly. The system reads the content. It stores the full article intact as one document note. It also splits the content with `chunk_text()` and writes small chunk notes for retrieval. Neither form replaces the other. The system writes all these notes via `save_entry()`. That function queues every note for search indexing automatically. You can omit the `--topic` flag. When you do, the agent suggests a title-based slug for you to confirm. Ingested notes take the default volatile decay — the right treatment for reference material. The auto-organization design builds vault-wide linking. Ingestion hands each batch to that system once it ships.

**The ingest sweep.** A scheduled job on the runner gives forwarded content its full processing. The job scans the staging folder each cycle. It looks for candidates that are links or whole documents. It fetches each one and runs it through the `/memory ingest` pipeline. The sweep marks the original candidate as ingested. It points the candidate at the new batch. A fetch can fail on a paywall or a dead link. The sweep leaves a failed candidate in place and records the error. Your digest reports the failure. The sweep runs hourly by default. Four more duties ride the same job. First, a clipped article arrives with its full content. The sweep skips the fetch and goes straight to chunking. This sidesteps paywalls since you clipped what you could see. Second, the sweep runs the act step for any candidate carrying an unexecuted instruction. Third, the sweep folds `kind: idea` candidates into the ideas ledger. Fourth, the sweep corrects timestamps. Chat-surface captures come from a model with no live clock. The sweep validates the `captured:` field against the file's creation time. It corrects the field when they disagree. The Drive connector can create files but never update or delete them. An uncertain capture sometimes lands twice as near-duplicate candidates. The sweep checks sibling `_inbox/` candidates for an exact `source_url` match before fetching — a resend is marked `status: ingest_duplicate` and pointed at the original, never re-fetched or promoted separately. This exact-match check is the sweep's own: inbox triage's broader near-duplicate dedup runs on no automatic schedule in this repo today, and the sweep's own batch processing would otherwise defeat it for a same-cycle resend.

**Provenance plumbing.** We formally add `source_url` and `source_fetched` as optional fields in `FRONTMATTER_FIELD_ORDER`. We update the `save_entry` and `vault_lint` tools to accept these new fields. We apply this small change independently. The ingestion command categorizes notes under the `personal` group. A few older notes use a different `group:` name. We treat those as legacy files.

#### The trust boundary

The capture door provides the single security checkpoint between untrusted content and your agent's environment. The system treats all captured content purely as data. Text that reads like an instruction gets no authority at capture time. The system records exactly where every piece of data came from. The system excludes the `_inbox/` folder from the agent's memory searches by default. You can include it with the `--include-inbox` flag. We reuse the MCP server's `_validate_path_segment` tool to ensure files go to the right place.

Two mechanisms carry candidates across this boundary, both landing on the same trust guarantee through different machinery. A `memory_capture` candidate — a thought, a link, or an idea typed at capture time — stays hidden from recall until the inbox triage system (`inbox_triage.py`) promotes, merges, or expires it, logging every decision for revert. A forwarded link or document the ingest sweep fetches on its own schedule crosses the same boundary through its own staging mechanism instead: the sweep patches the candidate in place, still recall-invisible, and only calls `ingest.ingest()` to promote it to permanent memory after the candidate survives one full sweep cycle — the sweep's own review window, not inbox triage's. Explicit, human-invoked `/memory ingest` and `memory_append` sit outside both: you named the source yourself, so they write directly to permanent memory at the same trust level as any other deliberate save.

We discuss the remaining risk of malicious content entering your active memory in the Risks section.

#### The team-vault seam

The multi-user team vault initiative opens after this arc as its own project (ruled 2026-07-16). We design the current capture process so it can adapt to teams when the time comes. We track the device in `surface:` and the transport in `source:` for every note today. An `author:` field can join them later when multiple people share the vault. The `personal/` folder structure provides a clear boundary. A future permissions system can enforce this boundary. We rely on the current system's assumption that only one person writes to the vault. When we build support for multiple simultaneous writers, this capture tool inherits those improvements.

#### Where the memories live

A promoted candidate becomes an ordinary memory entry. This design adds nothing to how the system shapes or stores those entries. The memory-system design (`wiki/designs/agentm-memory-system.md`) holds the entry contract. That contract requires one atomic note per fragment, frontmatter keys, `kind` as the folder, and dense wikilinks. The same design document details the vault layout, the three ownership tiers, and the archive convention. That convention keeps cold notes out of the way. The recognized `kind:` values live in the Kind-Taxonomy registry (`wiki/reference/Kind-Taxonomy-Registry.md`). Ingested reference notes land under `personal/domains/<topic>/` following the entry contract. Ideas land in the ledger. The ledger's shape lives in its own pending design (`wiki/designs/memoryvault/parts/idea-ledger.md`). Read those documents to understand where everything this design captures ends up.

The recall-loop section of the memory-system design describes how these memories come back on their own. The always-load set assembles at session start. A search runs over the index on every prompt. A captured article surfaces automatically in any later session that needs it. We test this exact behavior toward the end of this arc. A capture made from your phone must surface automatically in a fresh session. The arc's acceptance probe covers capture, ingestion, and automatic recall together.

## Alternatives Considered

- **Change `memory_append` to write to `_inbox/`.** We reject this because you still need a way to save files directly and handle exact imports. We prefer two separate commands to clarify your intention. An explicit save goes directly to permanent memory.
- **Use the old Telegram and ntfy.sh notification tools.** We reject this because it violates our privacy rule against third-party push services. Our local tools provide the same convenience safely.
- **Run a continuous launchd background watcher to monitor the staging folder.** We reject this because we already have a reliable scheduling system. A continuous background daemon wastes resources to save a few minutes. The capture process works well as a delayed background task.
- **Expand the `forward_learning` tool to handle article ingestion.** We reject this because that tool works best for single feeds without breaking them into chunks. Modifying it for ingestion requires too much work and confuses its purpose.
- **Do nothing and keep capture restricted to the terminal.** We reject this because capture-from-anywhere is the first goal of this arc. The way `memory_append` skips the staging area is a flaw we must fix regardless.

## Dependencies

We reuse our existing tools without changes: `save.py`, `vault_lock`, `chunking.py`, reflection's lanes, `inbox_triage`, `dream_confirm`, the runner, and the MCP server. The claude.ai Google Drive connector carries phone and chat captures. Its traffic routes through Anthropic's servers. No credential lives on any device or sandbox. The Obsidian Web Clipper is operator-installed. Chrome blocks automated extension installs by design. We use the Dispatch and Remote Control preview tools as the in-session phone paths. We coordinate with the auto-organization design (`agentm-auto-organization.md`, same arc) to handle cross-linking your notes.

## Migrations

We add new fields, one new tool, and two new commands. Your existing notes retain their current format. The `memory_append` tool keeps working exactly as it does today. We add a second tool beside it. The triage system processes your older staged notes normally. You can manually fix the group name on the two older domain notes. No systems depend on them.

## Technical Debt & Risks

- The secondary in-session phone paths (Dispatch, Remote Control) are preview surfaces. If a preview surface is pulled, capture keeps working through the connector and the desktop doors.
- Phone-started cloud sessions run in Anthropic's sandbox. They cannot reach the vault today. Two developments would change that. The memory server's remote tier (scheduled for the mobile/web wave) would let a cloud session call `memory_capture` over a tunnel. Anthropic's self-hosted runner program, organization-only today, would let a session target this machine directly. *Re-audit trigger:* self-hosted runners opening to individual plans.
- We ship the ingestion tool before the auto-organization design's vault-wide linking finishes. The ingestion tool creates local links immediately. Auto-organization handles the rest. *Re-audit trigger:* the auto-organization design gets delayed.
- Content promoted out of staging eventually becomes visible to your agent. We protect you by tracking data sources. We hide staged items from searches. A candidate reaches permanent memory only through one of two staging mechanisms — inbox triage for `memory_capture` candidates, the ingest sweep's own review window for its forwarded-link fetches — never straight from untrusted content. *Re-audit trigger:* any real injection incident.
- Capturing content easily from your phone might increase the number of items landing in the staging folder. The automated triage system handles this volume by auto-applying its expire decisions to old notes. Your weekly summary tracks these numbers.
- A forwarded link can fail to fetch due to a paywall, a dead link, or your machine being offline. The candidate stays in the inbox with the failure recorded. Your digest surfaces it. Nothing disappears silently.
- The Drive connector can create files but never update or delete them. We design around this limit. We write once per message. Corrections travel inside that one file. *Re-audit trigger:* the connector gaining update or delete tools.
- Chat-surface timestamps are model-estimated and approximate. The sweep's re-stamp against the file's own creation time fixes this. Until that sweep lands, the `captured:` field from chat surfaces is approximate.

## Quality Attributes

### Security

We expand the write surface. We secure this by routing everything through one audited doorway. We reuse our tested path validation tools. We stage all casual captures for the automated triage system. We rely entirely on local and first-party tools to move your data safely.

### Reliability

The system must never silently lose a thought you captured. It reports success or failure clearly back to the device you used. We make it safe for the system to retry failed saves. The ingest sweep marks every candidate it processes. A crashed cycle resumes where it left off. Retries are safe.

### Data Integrity

We use our existing safe write tools to ensure file completeness. A bad capture lands in the staging area and never corrupts your permanent memory. The triage system logs every promotion it makes. This allows you to undo any mistake.

### Privacy

Your data stays entirely on your device and inside your synced vault. We restrict all data movement to first-party and local tools. Your captured notes live securely in your private vault. Our privacy scanner continues to check anything you later move into a public repository.

### Latency

The capture process runs in the background. The system promises only that your captured items appear in the vault before your next working session. The ingest sweep runs every hour by default. We can make it run more often if you need faster results.

### Testability

We can run automated tests on every component. This includes the file formats, the capture tool, the text splitter, and the scheduled jobs. We add a test that tracks a captured note all the way through to a successful search result. We add an arc-end check that a captured article surfaces automatically in a fresh session through the standing recall loop. The ingestion tool uses a sample article to verify it stores the full article intact as one document note, and creates the separate small chunk notes for retrieval.

## Project management

### Work estimates

We split the work into three parts. First, we build the core format and the new capture tools (Medium). Second, we build the article ingestion command, including automatic topic naming and linking (Medium). Finally, we build the ingest sweep and write the device guides (Medium). The sweep handles fetching, chunking, the act step, the idea-ledger fold, and the timestamp re-stamp.

### Documentation Plan

We publish `wiki/designs/agentm-capture.md` when we finish the first part of the work. We write two new how-to guides: *Capture-From-Your-Phone* and *Ingest-An-Article*. We update the frontmatter-field reference and the memory SKILL.md verb-table to document the new tracking fields and commands. We record each finished feature in `Completed-Features.md`.

### Launch Plans

We release each of the three parts independently as its own named minor update when the code passes tests. The updates are "capture contract + front door", then "article ingestion", then "forwarded links + the phone path". We record this ladder in ROADMAP-MASTER § FRIDAY. We set the specific dates when each release actually cuts.

## Operations

### SLAs

N/A: Single-operator local memory tool with no service level agreements.

### Monitoring and Alerting

You monitor your capture activity in the same places you monitor all memory activity. Your weekly summaries show the staging folder counts and triage decisions. You see live memory actions directly on your console. The capture→recall probe's result lives in the plan's evidence. The FRIDAY health family on the dashboard lights up at the end of the arc, per its locked acceptance shape.

### Logging Plan

Every note records the device and source you used to capture it. The automated triage system logs what it decides to do with each note. The ingest sweep logs its progress through our standard task runner.

### Rollback Strategy

We add new files and one optional tool. You can delete a bad capture by hand, or the automated triage system expires it. You can undo a bad promotion through the revert log. If you remove the new tool, the system returns exactly to how it works today. There is no schema or state migration to unwind.

## Document History

| Date | Change | Status |
|---|---|---|
| 2026-07-18 | **Ingest sweep ships (part 3 of 3): the automated phone path.** `harness/skills/memory/scripts/ingest_sweep.py` is the hourly runner job that gives forwarded links and documents in `_inbox/` full processing with no manual step — six duties in one job: fetch (or, for an already-complete Obsidian Web Clipper capture, skip straight to chunking), a closed deterministic act-step grammar (`tag:`/`file-under:` only — never free-text instruction execution), the idea-ledger fold, and a `captured:` timestamp re-stamp against the file's own creation time. Automated fetches land staged in place on the originating candidate — recall-invisible, exactly as before the sweep touched it — and only promote to permanent memory via `ingest.ingest()` (part 2, unmodified) after surviving one full sweep cycle; this staging/promotion mechanism is the sweep's own, distinct from `inbox_triage.py`'s triage process that already handles `memory_capture` candidates (the Trust Boundary section above is corrected to name both mechanisms explicitly, replacing an overclaim of one uniform triage path). A pre-merge `/review` found and fixed five real bugs before the cut, the most consequential in this whole ladder: `_vec_search`/`_vec_search_filtered` (semantic recall) and `find_drifted_entries` never respected the `_inbox` exclusion the way keyword recall already did — a pre-existing gap in `recall.py`/`vec_index.py`, not introduced by this plan, but the first plan to put untrusted external content somewhere the gap mattered; closed by excluding `_inbox` from the drift walk and adding a `_is_inbox_path()` backstop on both vec-search result paths. The other four: `vault_mutex` protected only 1 of 7 read-modify-write call sites (closed — every candidate-patching function now re-reads fresh and writes inside the lock, narrowed to the minimum span); a body/marker collision could corrupt both the promoted document and the surviving candidate (closed with a matched start/end HTML-comment marker pair); permanently-failing promotions never surfaced in the digest (closed — `promote_failures` added to `SweepResult`); and an ordering bug left restamp comparing against the file's own just-updated mtime instead of the true original creation time (closed by reordering restamp first). Windows CI then caught a sixth, unrelated bug on the same PR: `os.mkdir`'s concurrent-acquire race raises `PermissionError` on Windows where POSIX always raises `FileExistsError` — fixed in both `vault_lock.py` copies (top-level + vendored) per the DC-9 byte-identity convention. Two new how-to pages (`Capture-From-Your-Phone`, `Ingest-An-Article` — the latter closing a gap from part 2). `governs:` gains `ingest_sweep.py`. All three capture parts are now shipped — `status` flips `final` → `launched`, per this design's own Documentation Plan criteria. | launched |
| 2026-07-18 | **Article ingestion ships (part 2 of 3): `/memory ingest`.** `harness/skills/memory/scripts/ingest.py` reads a URL or a local file and writes it straight into permanent memory — one intact full-document note plus reading-order-linked chunk notes for retrieval via `chunking.py`'s `chunk_text()`, both through `save_entry()` from the start (unlike `/memory capture`'s `_inbox/`-only staging write). Omitting `--topic` returns a title-based suggestion and writes nothing until you confirm it — a real confirmation step, not an auto-accept. A new `SKILL.md` section documents invocation, flags, and failure modes; `governs:` gains `ingest.py`. A pre-merge `/review` — run before the PR this time, not retroactively — found and fixed two real bugs before the cut: a mid-sequence slug collision could leave the document note and every chunk written before it permanently orphaned on disk while the call still reported failure (closed with a pre-flight existence check across every target slug, plus a rollback that unlinks anything the call itself wrote on a later failure); and the HTML sniff only recognized a full-document wrapper (`<html>`/`<body>`/`<title>`), so an HTML fragment with no wrapper tag slipped through as plain text and left raw markup in a saved note (closed with a matching-tag-pair fallback scan). A third review finding — that writing straight to permanent memory here sits in tension with the design's own staged/triage trust model — is not a code defect; it is logged to the vault's `FOLLOWUPS.md` for an operator ruling before part 3 ships, deliberately left open rather than silently resolved. Parts 1 and 2 are now shipped; part 3 (forwarded links + the phone path) remains open — `status` stays `final`, not `launched`, until all three ship. | final |
| 2026-07-18 | **Lifted from confidential (`_harness/designs/friday/agentm-capture.md`) to published, on landing capture-front-door (part 1 of 3).** All 6 tasks shipped: the candidate contract's `source_url`/`source_fetched` provenance fields, the direct-atomic-write candidate writer, the `memory_capture` MCP tool (the fourth tool on the memory MCP server, alongside `memory_search`/`memory_append`/`memory_forget` — see `agentm-memory-system.md`'s own amendment for the count correction), the `/memory capture` CLI verb + SKILL.md documentation, the capture-time-instructions security invariant (mechanically enforced and adversarially tested — `content` never gains instruction authority), and confirmation that this design's single-write-funnel already satisfies the F1-REAUDIT-named "E17 single-audited-bridge injection guard" (one audited bridge — `capture()` — between the trusted plane, operator-typed `instructions`, and the untrusted plane, `content`; both the MCP tool and the CLI verb funnel through the same function, no second write implementation). `governs:` stamped with the new/touched modules per the AG lift precedent. Parts 2 (article ingestion) and 3 (forwarded links + the phone path) remain open; `status` stays `final`, not `launched`, until all three ship. | final |
| 2026-07-17 | Folded the cloud-capture exploration findings (`cloud-capture-findings.md`, tested end to end with the operator): the claude.ai Capture project + Google Drive connector becomes the primary phone/chat transport and the Obsidian Web Clipper the desktop-Chrome path; Dispatch/Remote Control demoted to secondary; Channels parked. The candidate contract gains `kind`/`tags`/`instructions` with the absorb-then-act rule (instructions only ever from operator capture-time text); the sweep gains the act step, the idea-ledger fold, clip-skips-fetch, and the `captured:` re-stamp; risks gain the connector's create-only ceiling and the model-clock finding. Same day, on operator feedback: Objective/Background/Overview de-staled (dropped transports no longer mentioned) and the Detailed Design reorganized into processing order — ways in → what arrives → how candidates are handled → the machinery → trust boundary → team-vault seam → where the memories live (with pointers to the memory-system design, the Kind-Taxonomy registry, and the idea-ledger design). Second feedback round the same day: the auto-organization design named wherever "a separate project" appeared; test-history narration removed from the body; "Mac" generalized to "your machine"; parked/dropped transport rows removed from the doors table (Alternatives keeps the rejections); ingestion now stores the full article intact as one document note AND chunks it for retrieval; automatic recall in later sessions referenced via the memory-system recall loop, with an arc-end test noted. Contents approved by the operator, then the two-step voice pass ran (Gemini 3.1 Pro via `agy`, sectioned to dodge a stream-truncation quirk on the literal `<thought>` token, Claude-verified) — its sentence-level polish adopted, ten guard-sentence leakage insertions and two wording drifts stripped in verification. Status advanced to review, and the operator approved the design as final the same day — unlocking translate and sequence. Later the same day, the FRIDAY-AGENTM build session ran /design translate, splitting the design into 3 parts (`front-door`, `article-ingestion`, `phone-ingest-sweep`; parts/ files at `agentm-capture/parts/` alongside this doc) and /design sequence, landing three executable plans at `_harness/queued-plans/PLAN-capture-front-door.md`, `PLAN-capture-article-ingestion.md`, `PLAN-capture-phone-ingest-sweep.md`, each grounded against the live agentm codebase (file:line references, not paraphrased prose). One real design/code discrepancy surfaced during grounding, not silently resolved: the design states candidates are written "through the direct atomic-write path reflection already uses" — live-code inspection found `reflect.py::_save_candidate_to_inbox` writes via a raw `write_bytes()`, no `vault_lock`/mutex; the actually-atomic write-around-`save_entry` pattern in this codebase lives in `inbox_triage.py`'s promote step instead. `PLAN-capture-front-door.md`'s Constraints section records the call (default to genuine atomic writes via `vault_lock.atomic_write`, favoring the design's Data Integrity intent) and flags it for an explicit operator/build-time confirmation rather than assuming either reading silently. | final |
| 2026-07-16 | Initial draft authored (F1 session) against the operator's goal rulings and confirmed adjudications (V9-after; #33→#34 post-arc); sources: the F0 surface audit re-verified at `f3fd62a`, the article-ingestion assessment memo, FRIDAY-PRE-VERDICT riders P2/P5, the standing privacy ruling. Same day: voice-conformance pass against the design-doc prose overlay (positive framing, volatile IDs replaced with plain names, fragments expanded, bold reserved), operator added the SLAs statement, and a first cross-model readability pass (Gemini via `agy`) was selectively applied; then, on the operator's direction, a full simplification pass (Gemini 3.1 Pro via `agy`, the voice pack inlined verbatim) was adopted wholesale with eleven fact-and-voice corrections (the `save_entry` bypass restored, the injection guarantee un-overclaimed, part sizes, release names, the health-family exit line, and the operator's own SLAs wording reinstated). Later the same day, operator rulings folded in: the Voice Memos/iCloud path cut entirely; forwarded links become the primary phone flow, fully processed by a new ingest sweep; the chunking boundary got one Overview sentence plus its own Detailed Design section; a from-your-phone walkthrough landed under Transports. Quality Attributes walked in full; Scalability, Abuse, Accessibility, i18n, and Compliance consciously omitted as low-relevance per the 2026-06-09 convention. | draft |
