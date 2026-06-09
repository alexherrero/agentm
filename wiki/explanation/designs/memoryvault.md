---
title: "MemoryVault — Permanent agent memory via Obsidian-vault-folder + reflection sidecar"
status: final
visibility: published
author: Alex Herrero
contributors: []
created: 2026-05-15
updated: 2026-05-20
last_major_revision: 2026-05-20
prd:
project:
---

<!--
  Authored via /design author memoryvault --visibility published.
  First real dogfood of plan #6's design skill.

  Status lifecycle: draft → review → final → launched.
  Currently: draft (Phase A of /design author walk).
-->

# MemoryVault — Permanent agent memory via Obsidian-vault-folder + reflection sidecar

## Context

### Objective

Built-in agent memory (Claude memories, Gemini personal context, etc.) is per-platform, opaque, lossy, and not composable across tools — bouncing between models fragments context every session. Today, every new Claude Code chat starts cold even on the same project; manual re-priming wastes tokens and loses nuance. MemoryVault is a file-based, agent-curated permanent memory layer that captures durable preferences / workflows / fixes via a reflection sidecar, recalls relevant entries automatically into every new prompt, and adapts the agent's behavior over time without explicit configuration. The goal: compound learning — each conversation makes the next one better, because the agent never forgets what already happened.

### Background

The idea evolved through three stages:

1. **Initial framing — persistent memory on a single machine.** Frustration with every agent session starting cold on the same project. The first sketch was a local file-based vault on disk: agent writes durable preferences to a known directory; agent reads them at session start. Better than built-in opaque memory, but bounded to one machine.

2. **Cross-model sync.** As work moved between different agent surfaces and tools, the single-machine assumption broke — context captured in one tool was invisible in another. The vault shape extended to model-agnostic markdown-on-disk so any tool that could read files (or talk to a thin server fronting the files) could load the same memory.

3. **Cross-machine + integration with the user's own contextual memory.** The final pivot: the user already runs a personal note-taking surface for their own thinking, with sync between devices. Rather than maintaining a separate agent-only vault, MemoryVault becomes a folder inside that existing surface — the agent's memory and the user's notes coexist in the same place, the user can inspect and edit what the agent learned, and the cross-device sync the user already has solves the multi-machine problem for free.

**Base-skill dependencies (all shipped via prior plans):** [#3 evaluator](decisions/0002-evaluator-subagent.md) (per-step review), [#4 kill-switch + steer](decisions/0003-base-hooks.md) (long-running execution control), [#5 commit-on-stop](decisions/0003-base-hooks.md) (crashed-session recovery), [#6 design skill](decisions/0004-design-skill.md) (this design doc is the first real dogfood of that skill).

## Design

### Overview

MemoryVault is a single `memory` skill in `crickets` with four sub-commands and four Claude Code hooks. The skill's on-disk format is **markdown + YAML frontmatter inside a folder of the user's existing Obsidian vault**, synced between devices via the user's existing setup. Writes default to `MemoryVault/`; reads default to **everywhere in the Obsidian vault** (rich grounding context from the user's existing notes); writes outside `MemoryVault/` happen only on explicit user request or agent-proposed + user-confirmed (the permeable boundary).

Three architectural pillars drive the design:

1. **Reflection sidecar (write loop)** — aggressive end-of-session sweep mines the conversation for 3 extraction categories (Successful Workflows / User Preferences / Fixes & Workarounds) → writes MemoryVault entries; a parallel sweep mines for follow-ups / project ideas / research candidates → writes to a single `Ideas.md` file at the user's vault root. Confidence-rated **tri-modal routing**: HIGH-confidence candidates auto-save; MEDIUM-confidence go through an interactive approve/edit/reject review prompt; LOW-confidence land in `_inbox/` for batch review. Stop-event + idle-time hooks fire the sidecar; idle hook also recovers crashed sessions where Stop didn't fire (via `.harness/session-id-<uuid>.{start,reflected}` marker files).

2. **Hook-driven recall (read loop)** — two-hook recall pattern: SessionStart loads `MemoryVault/personal-private/_always-load/` (top ~20 high-priority always-relevant entries — dev-flow conventions, top preferences); UserPromptSubmit does a fresh relevance query against the rest of the vault on every user message and injects matches. Recall mechanism is **sqlite-vec primary + grep+frontmatter alongside (merge results)** — semantic recall for paraphrased relevance, keyword + frontmatter filter for high-precision queries, dedup before injection. Embeddings computed locally via `sentence-transformers` on save (async, non-blocking) — see [ADR 0001's 2026-05-20 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20) for the v0.9.2 local-only refactor.

3. **Two-tier idea capture (the user-facing dividend)** — when the reflection sidecar surfaces a project-idea candidate (not a memory-entry candidate), it writes a 2-sentence summary + Obsidian wikilink to `~/Obsidian/Ideas.md` at the vault root, AND simultaneously does deep research (web search + cross-reference against existing MemoryVault entries + scan existing Obsidian notes) writing the rich context to `MemoryVault/personal-private/_idea-incubator/<idea-slug>/`. When the user decides to progress an idea to a real project, the incubator entry graduates to `MemoryVault/personal-projects/<idea-slug>/`. Incubator entries get garbage-collected after N months without engagement.

### Infrastructure

- **Vault storage**: filesystem-direct access to the synced Obsidian vault path. The agent uses Read/Write/Edit on the canonical path. The vault itself remains a user-curated Obsidian vault; MemoryVault is one folder inside it.

- **Vector index**: `sqlite-vec` — a SQLite loadable extension written in C (`.so`/`.dylib`/`.dll`) by Alex Garcia; language-agnostic at its core, callable from any language that can load SQLite extensions. v1 calls it from Python (via the `sqlite-vec` pip wheel, which ships prebuilt binaries for all 3 OSes — no compile step). Hook scripts that touch the index become Python scripts; Claude Code supports any executable hook. **The C-extension origin is load-bearing for future flexibility**: if hook latency becomes a problem, we can swap the caller layer to Rust or Go (each has working bindings to load sqlite-vec) without touching the on-disk format. The data is portable across caller languages.

  Index lives at `MemoryVault/_meta/vec-index.db` (a small binary file, sync-friendly). The index **supplements** the markdown files rather than replacing them — the `.md` files in Obsidian remain the canonical, human-readable, human-editable source of truth; the vec-index is a query-acceleration layer storing `(path, embedding_vector, last_updated_ts)` per file for fast semantic nearest-neighbor lookup. This separation matters in several ways:
  - **Index is derivable, content is not.** If the index gets corrupted or out of sync, it can be rebuilt deterministically from the `.md` files via `/memory reindex`. The reverse isn't true — losing the `.md` files loses the memory.
  - **User-side edits don't need the agent.** The user can open Obsidian, edit any entry directly, and the next save / reindex picks up the change. The index updates incrementally on each `/memory save` and `/memory evolve` call (async — doesn't block the save UX), and an on-demand `/memory reindex` handles user-side edits made outside the skill.
  - **Sync surface stays simple.** Only the `.md` files need conflict-resolution; the vec-index is local-machine-rebuildable, so cross-device sync conflicts in the binary file are non-fatal (resolution = rebuild on the device that lost the conflict).
  - **Obsidian's existing tooling still works.** Backlinks, tags, graph view, search — all of Obsidian's native features apply to MemoryVault entries because they're just markdown files in the vault. The vec-index is invisible to Obsidian; it's an agent-only concern.

- **Embeddings**: local `sentence-transformers` is the only production mode as of v0.9.2 (2026-05-20) — see [ADR 0001's 2026-05-20 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20) for the rationale (dual-mode API + local was the v1 design; collapsed to local-only because the primary operator is a Claude Ultra subscriber without a separate API key, and modern small-to-mid models deliver near-SOTA quality on desktop-class hardware). Embedding text = entry title + frontmatter tags + first paragraph of body. Default model: `BAAI/bge-large-en-v1.5` (1024-d native; ~1.3GB on disk + ~1.5GB RAM; downloads lazily on first invocation; cached at `~/.cache/crickets/sentence-transformers/`). Operators on low-spec hosts swap to a smaller model via the `AGENT_TOOLKIT_EMBEDDING_MODEL` env var escape hatch (still local — no API option). `EMBEDDING_DIM = 1024` (bumped from 384 in v0.9.2).

- **Claude Code hooks** (the orchestration surface):
  - **SessionStart** — load `_always-load/` entries into the session's initial context.
  - **UserPromptSubmit** — do a relevance query against the prompt; inject matches; dedup against already-loaded entries.
  - **Stop** — fire reflection sidecar; mine the session for 3 categories + idea candidates; route via tri-modal logic.
  - **Idle-time** (a new crickets primitive added by this plan) — fire reflection sidecar if a session went silent > N minutes; also scan `.harness/session-id-*.start` marker files for orphans (crashed sessions where Stop didn't fire) and run reflection retroactively.

- **Runtime state**: `.harness/session-id-<uuid>.start` written on SessionStart hook; renamed to `.reflected` after Stop hook reflection completes. All markers in `.harness/` (gitignored, runtime-only). Markers GC'd at 30 days.

- **Skill home**: `crickets/skills/memory/SKILL.md` with sub-command bodies; hooks in `crickets/hooks/memory-recall-session-start/`, `crickets/hooks/memory-recall-prompt-submit/`, `crickets/hooks/memory-reflect-stop/`, `crickets/hooks/memory-reflect-idle/`.

### Detailed Design

Detailed Design splits into 8 subsections; each candidate-part for `/design translate` to consider. Subsections 1-7 are #7a (core); subsection 8 is #7b (discovery + mining), shipped after #7a is dogfooded for 1-2 weeks.

#### 1. Write primitives — `/memory save` + `/memory evolve`

`/memory save <kind> <slug> [--group <group>] [--always-load]`: writes an entry at `MemoryVault/<group>/<kind>/<slug>.md` with frontmatter (`kind`, `status: active`, `created`, `updated`, `tags`, `supersedes` optional). Body = markdown content (free-form). On save: synchronously write file; asynchronously embed + index in sqlite-vec.

`/memory evolve <old-path> <new-content> <reason>`: atomic supersede. Steps: (1) Read the old entry; (2) write new entry with `supersedes: <old-path>` frontmatter + content from `<new-content>`; (3) atomically `git mv` old entry to `MemoryVault/personal-private/_archive/<original-path>.YYYYMMDD.md` (or filesystem rename equivalent — Obsidian-vault isn't necessarily a git repo); (4) update old entry's frontmatter `status: superseded` + `superseded_by: <new-path>` cross-link; (5) trigger vec-index update for both. Recall filter skips `status: superseded` entries by default.

Tri-modal confidence routing applies when these primitives are called by the reflection sidecar — direct user invocation always writes immediately.

#### 2. Reflection sidecar — `/memory reflect` + Stop + idle hooks

Three trigger surfaces, all running the same reflection logic:

- **Manual `/memory reflect [--session <path>]`** — user-initiated; runs against the current Claude Code session transcript (default) or a specified transcript path.
- **Stop-event hook** — fires automatically on session Stop. Same logic, scoped to the just-ended session's transcript.
- **Idle-time hook** — fires when Claude Code has been idle > N minutes (N TBD, default 30); also scans for orphan `.harness/session-id-*.start` markers (crashed sessions) and runs reflection on those transcripts retroactively.

**Reflection logic** (shared across all three triggers):

1. Read the session transcript (Claude Code stores them at `~/.claude/projects/<repo>/<session-id>.jsonl`).
2. Two parallel mining passes:
   - **3-category mine** (MemoryVault destination): scan for Successful Workflows ("I noticed the agent successfully did X for Y reason"), User Preferences ("user said 'always do X' / user manually corrected the agent's output"), Fixes & Workarounds ("hit error Z, resolved by W"). Each candidate gets a confidence rating (HIGH/MEDIUM/LOW per heuristics in the tri-modal section).
   - **Idea-candidate mine** (user-vault destination): scan for follow-ups ("we should also do X later"), future project ideas ("this could be its own project"), research candidates ("worth investigating Z"). Each candidate gets a 2-sentence summary + the agent does deep research (per subsection 5).
3. **Tri-modal routing** for 3-category candidates:
   - HIGH-confidence (explicit user signal: "always X" phrasing / locked-design-call / user-corrected-agent) → auto-save via `/memory save`.
   - MEDIUM-confidence (pattern-inferred: 3+ occurrences) → interactive review prompt with approve / edit / reject / skip / supersede-existing-X options. Controlled by `memory.review_mode: interactive (default) | silent`.
   - LOW-confidence (single-instance inference) → write to `MemoryVault/personal-private/_inbox/<slug>.md` for batch review later.
4. **Idea-candidates** always go to two destinations simultaneously: 2-sentence summary appended to `~/Obsidian/Ideas.md` + deep-research entry written to `MemoryVault/personal-private/_idea-incubator/<idea-slug>/`. Ideas.md never goes through tri-modal routing — all ideas surface in user-facing inbox.

#### 3. Recall hooks — SessionStart + UserPromptSubmit

Two-hook recall pattern. SessionStart loads the always-load core; UserPromptSubmit injects task-shifted relevance per user prompt.

**SessionStart hook**:
1. Glob `MemoryVault/personal-private/_always-load/*.md`.
2. Read each, format as a single Markdown block, inject into session context.
3. Output: a "Loaded N MemoryVault always-load entries" line for transparency.

**UserPromptSubmit hook**:
1. Take the user's prompt as the query.
2. Run the recall engine (subsection 4) → returns top-K (K=5 default) relevant entries.
3. Dedup against already-loaded `_always-load` entries (by path).
4. Inject the remaining matches as a system message before the agent processes the prompt.
5. Output: a "Loaded N relevant entries: <slug-list>" line for transparency (this is how the user knows what memory shaped the response).

Both hooks have a hard time-budget (SessionStart 500ms, UserPromptSubmit 300ms) — if exceeded, log warning and proceed with partial results rather than blocking.

#### 4. Recall engine — sqlite-vec + grep+frontmatter merge

Query path:

1. Embed the query (local `sentence-transformers` only as of v0.9.2; synchronous; on time-budget overrun, fall back to grep-only).
2. **Vec search**: query sqlite-vec for top-K nearest entries by cosine similarity. Returns (path, similarity_score).
3. **Grep + frontmatter search** (parallel to vec): scan entry titles + tags + first-line content for keyword matches; filter by frontmatter (status: active only; respect `group` filter if query specifies).
4. **Merge results**: union the two result sets; rank by combined score (similarity_score * 0.7 + keyword_match_count * 0.3); dedup.
5. Return top-K (default K=5).

Filtering:
- Always filter `status: superseded` (recall never surfaces superseded entries by default).
- Optional `--group <group>` flag to scope recall to one group.
- Optional `--include-inbox` flag for explicit access to `_inbox/` entries (default excluded).

#### 5. Idea ledger — `Ideas.md` + `_idea-incubator/`

When the reflection sidecar surfaces an idea candidate:

1. **Surface entry** (user-facing): append a section to `~/Obsidian/Ideas.md` at the vault root. Section format:
   ```
   ## YYYY-MM-DD: <Idea Title>
   <2-sentence summary of the idea>
   See deep research: [[MemoryVault/personal-private/_idea-incubator/<idea-slug>/_index.md]]
   ```
2. **Deep research** (agent-facing): create `MemoryVault/personal-private/_idea-incubator/<idea-slug>/` directory with:
   - `_index.md` — full agent reasoning about the idea, frontmatter (`kind: idea`, `status: incubating`, `surfaced_in_session: <session-id>`).
   - Additional research files (web fetch dumps, cross-references to existing MemoryVault entries, scan of existing Obsidian notes for related content).
3. **Research depth budget**: cap research at 5 minutes wall-time / 3 web fetches / 5K tokens per idea (TBD — to settle during /design author walk; conservative defaults to avoid runaway).

**Promotion path**: when the user decides to progress idea X to a real project:
- `/memory promote idea <slug>` command moves `_idea-incubator/<slug>/` → `personal-projects/<slug>/`.
- Updates `Ideas.md` section: appends `→ promoted YYYY-MM-DD to MemoryVault/personal-projects/<slug>/`.
- Recalculates vec-index entries for moved files.

**Garbage collection**: incubator entries get GC'd after N months without engagement (N default 6; configurable). GC presents the user with a list before deletion: "These ideas haven't been promoted or referenced in 6+ months: <list>. Keep / Archive / Delete?" — never silent deletion.

#### 6. Crash recovery — session-id markers

The idle-time hook recovers crashed sessions where the Stop hook never fired (Claude Code force-quit, OS crash, etc.):

1. **SessionStart hook** writes `.harness/session-id-<session-uuid>.start` (one file per session, contents = session start timestamp + transcript path).
2. **Stop hook** (after running reflection successfully) renames `.start` → `.reflected`. If reflection fails, file stays as `.start` (idle hook will retry).
3. **Idle-time hook** scans for `.start` files older than 1 hour (idle threshold for assuming session is truly dead) → runs reflection retroactively on those transcripts → renames to `.reflected` on success.
4. **GC**: `.reflected` markers older than 30 days get deleted on next idle pass.

All markers in `.harness/` (gitignored, runtime-only).

#### 7. Manual seed pass — task 1 of #7a

The user provides additional context + ideas during this task to seed both MemoryVault AND the user-vault idea ledger before the reflection sidecar's first autonomous run. Task flow:

1. **Seed MemoryVault `_always-load/` core** (~10-20 entries): distill from `~/.claude/CLAUDE.md` (dev-flow conventions) + AGENTS.md sibling-repo imports + locked design calls from plans #3-#6. Each entry hand-written, validated, vec-embedded.
2. **Seed `personal-projects/` for in-flight projects**: agentm, crickets, plus operator-private siblings. Each gets a project-index entry referencing the locked decisions from each repo's prior plans.
3. **Seed `Ideas.md` and `_idea-incubator/`** (user-provided + agent-extracted): user provides loose ideas; agent extracts from recent Claude Code transcripts; co-curated.
4. **Validate by running a sample recall**: pose a sample query, confirm the SessionStart + UserPromptSubmit hooks return sensible matches.
5. **Initial migration of `~/ContextVault/`** contents (domains/ and projects/ subdirs) into the new MemoryVault structure: this happens here, as part of the seed pass.

This task is genuinely large (probably a full session's worth of work) and is *deliberately* co-created — the agent can't seed the vault alone, and the quality of the seed determines whether the loop pays off in the first weeks of use.

#### 8. Discovery + mining (plan #7b scope)

Three sub-components, all shipped in plan #7b after #7a has been dogfooded for 1-2 weeks:

1. **Transcript reflection pass** (one-time + ongoing): run reflection sidecar against the historical Claude Code transcripts at `~/.claude/projects/*/` to retroactively populate MemoryVault from past sessions. After the one-time pass, the Stop + idle hooks handle ongoing sessions.

2. **Internet skill-discovery**: periodic scan (cadence TBD — weekly default) of curated sources for SKILL.md-shaped patterns worth adopting. Sources whitelist (TBD): GitHub trending with `claude-code` / `agent-skills` tags + Anthropic Cookbook + specific awesome-lists + named blog feeds. **Adapt-don't-import principle**: when a relevant pattern is found, the agent writes a `personal-skill` entry in MemoryVault capturing what to adopt — never wholesale forks the SKILL.md into `crickets/`. The entry is human-reviewed before any actual skill code is written.

3. **Personal-skills auto-indexer**: walks `crickets/skills/` + `agentm/.claude/skills/` (plus any other repos the user installs) at install time + on `/release` and writes one MemoryVault entry per SKILL.md to `personal-skills/`. Pre-hook injection then merges entries from `personal-private/` AND `personal-skills/` at query time — the agent learns "we have a `/design author` skill" without being told every session.

## Alternatives Considered

1. **Built-in vendor memory only** (Claude memories, Gemini personal context, similar offerings from other agent platforms). Rejected: per-platform, opaque, lossy, not composable across tools. Captures some signal but invisible to the user, can't be inspected or edited, doesn't survive vendor changes, fragments when you switch tools. MemoryVault keeps the file-based + user-inspectable property that built-in offerings lack.

2. **Filesystem-only with keyword search, no semantic recall**. Rejected: too thin for the compound-learning goal. Markdown + grep is good for "find the entry titled X" but misses paraphrased relevance — *"I prefer paragraph-long narratives"* won't match *"write a long summary"*. We keep the filesystem substrate (human-inspectable, version-control-friendly) but layer semantic vector recall on top so the agent finds what you meant, not just what you said.

3. **Corporate-scale managed memory architecture** (centralized vector database service + retrieval API + team-scoped memory groups). Rejected: infrastructure overkill for personal-scale use. Single user, single machine cluster, no team isolation requirements — most of the heavy infrastructure exists to solve coordination problems we don't have. We adopt the *mechanics* (multi-phase loop, extraction categories, atomic evolve primitive, separate skill-discovery memory group) but use personal primitives (Obsidian-folder, sqlite-vec, Claude Code hooks).

4. **v1 design — private GitHub repo + three separate skills** (`context-recall`, `context-save`, `context-search`). Rejected (superseded 2026-05-15). Two problems: (a) standalone repo means no human-inspectable surface — vault contents only visible via the agent or by manually navigating the repo; (b) three skills creates artificial boundaries — recall happens via hooks (not user-invoked), save and search collapse into the broader `memory` skill with sub-commands.

5. **Obsidian REST API plugin as access mechanism**. Rejected for v1: requires Obsidian running, adds a moving piece, doesn't pay off until cross-surface read (web / mobile access) becomes a felt need. Filesystem-direct is simpler and covers desktop use end-to-end.

6. **Personal MCP server with custom recall logic** (`recall_context(query)`, `save_context(path, content)` primitives). Rejected for v1: real engineering up front; the skill-level abstraction is sufficient for the loop. MCP server becomes a follow-up if cross-surface-read pressure surfaces.

7. **No auto-recall — manual `/memory recall` invocation only**. Rejected: agents forget; humans forget; a memory system that nobody actively loads is worthless. Manual recall is theater; the auto-injection at hook boundaries is what makes the loop pay off.

8. **`status: superseded` frontmatter only — no atomic `evolve` primitive**. Rejected (decision C4): the tri-modal interactive review flow needs the atomic-supersede primitive because "supersede existing entry X?" is one of the four approve/edit/reject/supersede options at review time. Status-frontmatter-only forces a manual two-step (edit old + write new) that doesn't map cleanly.

## Dependencies

**Internal** (all shipped via prior plans):
- [#3 fresh-context evaluator](decisions/0002-evaluator-subagent.md) — consumed by reflection sidecar's optional per-entry grading.
- [#4 kill-switch + steer](decisions/0003-base-hooks.md) — long-running execution control (reflection sidecar respects `.harness/STOP`).
- [#5 commit-on-stop](decisions/0003-base-hooks.md) — crashed-session safety branch (orthogonal but complementary to MemoryVault crash recovery — commit-on-stop handles dirty git trees, MemoryVault crash markers handle missed reflection).
- [#6 design skill](decisions/0004-design-skill.md) — this design doc is the first real dogfood.

**External**:
- *(historical: v1 spec listed Anthropic API for embeddings; v0.9.2 dropped this — see [ADR 0001's 2026-05-20 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20). `sentence-transformers` below is now the only embedding path.)*
- **Obsidian** + the user's existing cross-device sync (not installed by this plan — assumed precondition).
- **`sqlite-vec`** — SQLite C loadable extension. Called from Python in v1 via `pip install sqlite-vec` (prebuilt wheels for all 3 OSes); on-disk format is caller-language-agnostic, so future swap to Rust/Go binary callers is non-breaking.
- **Python 3.10+** — already an implicit dependency of crickets (`validate-manifests.py`, `check-wiki.py`, `check-no-pii.sh` all require it). MemoryVault makes this explicit + adds `sqlite-vec` + `sentence-transformers` to the toolkit's pip-install set.
- **Claude Code hook lifecycle** — SessionStart, UserPromptSubmit, Stop, idle. SessionStart + UserPromptSubmit + Stop are documented hooks; idle-time may require a new crickets primitive (the **commit-on-stop hook already establishes the Stop-event hook pattern**; idle is similar shape).

## Migrations

1. **`~/ContextVault/` → `MemoryVault/` content migration**: existing files at `~/ContextVault/domains/` and `~/ContextVault/projects/` migrate to the new vault structure as part of the manual seed pass (task 1 of #7a). The migration is:
   - `~/ContextVault/domains/*.md` → `MemoryVault/personal-private/domains/*.md` (reorganized into `personal-private` group with `kind: domain-reference` frontmatter).
   - `~/ContextVault/projects/ai-context-system/conversations/*.md` → `MemoryVault/personal-projects/memoryvault/conversations/*.md` (the prior design conversation lands inside the MemoryVault project itself).
   - After migration, `~/ContextVault/` can be deleted; ROADMAP references to `~/ContextVault/` paths get updated in a follow-up doc pass.

   **Additional source paths (flagged for follow-up discussion at the seed-pass task)**: the `~/ContextVault/` tree is just one source of prior knowledge worth pulling into MemoryVault. There are at least three other sources to inventory + decide what migrates: (a) the user's own Obsidian vault has existing notes that may overlap with what MemoryVault would otherwise auto-capture; (b) a GitHub experimental repo has a README and supporting files describing prior context-system / memory-related explorations; (c) prior decisions / preferences / conventions are scattered across the synced GitHub repos already on this device (CLAUDE.md fragments, AGENTS.md sections, PLAN.archive narratives, ADRs, ROADMAP locked design calls). **Defer the full inventory + per-source migration decisions to the seed-pass task** — at that point the user walks through each source with the agent, decides what's worth pulling in, what to leave in place, and what to summarize-rather-than-duplicate. Capture as a sub-task list when planning #7a task 1.

2. **No on-disk-format migration**: the new vault uses the same markdown + YAML frontmatter shape as the v1 prior design, so content carries over cleanly. Frontmatter schema is extended (new fields: `group`, `kind`, `always_load`, `supersedes`, `superseded_by`, `confidence_rating_at_capture`) — existing entries get reasonable defaults during migration.

3. **Skill name renames**: `context-recall` / `context-save` / `context-search` are **deprecated** (never shipped as actual skills — they were planned in v1 design but pre-empted by v2 pivot). New skill: single `memory` with sub-commands. No user-visible breakage because v1 skills never existed on disk.

4. **ROADMAP item rename**: `ContextVault` → `MemoryVault` rename applied globally 2026-05-15 in `.harness/ROADMAP.md` (the active-plan-tracking file in agentm). #14 (`learn` skill) folded into #7a scope. Plan #7 split into #7a (core) + #7b (discovery + mining).

## Technical Debt & Risks

1. **Python becomes an explicit toolkit dependency**. Today crickets uses Python informally (validate-manifests, check-wiki, check-no-pii) — Python 3.10+ is an *implicit* requirement we haven't called out in the README. MemoryVault formalizes this + adds `sqlite-vec` + `sentence-transformers` pip deps. Mitigation: document Python as a first-class requirement in the toolkit README + Agent M's [Use-The-Memory-Skill](https://github.com/alexherrero/agentm/wiki/Use-The-Memory-Skill) page (skill moved to Agent M in v2.0.0); graceful-skip if pip deps are missing — vault stays read-via-grep + write-via-file (no embeddings, no semantic recall) until user installs the deps. **Future optimization**: if Python hook latency becomes a problem, swap the caller layer to Rust or Go (sqlite-vec is C-extension, language-agnostic on disk — non-breaking swap). Captured as a deferred follow-up.

2. **First-run model download cost (~1.3GB)**. v0.9.2 ships BGE-large as the default local model (see [ADR 0001's 2026-05-20 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20)). On first `/memory save` or `embed.py --mode local` invocation, sentence-transformers downloads the BGE-large checkpoint (~1.3GB) into `~/.cache/crickets/sentence-transformers/`. Subsequent invocations are offline + fast. Risk: operators on slow / metered connections feel the first-run cost; operators on low-spec hosts may not have disk + RAM headroom. Mitigation: `AGENT_TOOLKIT_EMBEDDING_MODEL` env var lets operators swap to a smaller model (e.g. `all-MiniLM-L6-v2` at 80MB) without code changes; `--no-python-deps` install flag defers the install entirely; graceful-skip path (no sentence-transformers installed → grep+frontmatter recall only) keeps the toolkit usable until the operator decides to pay the download.

3. **Cloud sync conflicts**. If the agent writes to the vault from one device while the user edits from another, the user's cross-device sync layer could create conflict-marker files (most cloud sync providers handle this similarly). Mitigation: single-user-mostly-desktop assumption (user rarely edits MemoryVault contents directly — that's the agent's job; user-side edits typically happen on the human-facing parts of the vault, not the agent-curated MemoryVault folder). If conflicts bite, escalate to a sync-mediating access mechanism (Obsidian REST API plugin or similar) — captured as a deferred alternative.

4. **Interactive review fatigue**. Tri-modal routing reduces prompt frequency but the MEDIUM-confidence pool may still feel like noise. Mitigation: `memory.review_mode: silent` escape hatch + adjustable confidence thresholds in skill config; default `interactive` is intentional friction during the trust-building phase.

5. **Vault bloat**. Aggressive sweep + `_inbox/` could accumulate cruft if user doesn't do weekly inbox review. Mitigation: `/memory inbox` command shows inbox count + age; `/memory reflect` end-of-session output reminds user when inbox > N entries; incubator GC at 6 months gives a soft cleanup deadline.

6. **Cross-machine config sanitization** (the follow-up added 2026-05-15 — see `.harness/ROADMAP.md` §7 Still open). MemoryVault skill config + hooks + crickets settings ought to be backed up to an operator-private sibling repo, but the **vault contents are private**. Need a redaction boundary — what's safe to commit (skill source, hook source, schema, templates) vs. device-local (real vault paths on disk, sync-provider identifiers, account emails, any project-specific overrides). Three candidate shapes flagged in the ROADMAP follow-up; decision deferred to a small follow-up plan.

7. **Recall-quality uncertainty**. We've never run this loop personally. The relevance heuristic (vec_similarity * 0.7 + keyword_match_count * 0.3) is a guess. Mitigation: ship instrumented — every recall logs which entries were injected + the user can manually inspect via `/memory inspect` to validate; tune weights based on real use.

8. **Skill-discovery (#7b) "adapt-don't-import" principle is hand-wavy**. The line between "adopt this pattern's idea" and "fork their SKILL.md" is fuzzy in practice. Mitigation: ship #7b conservatively — the agent always proposes a personal-skill entry FIRST, human approves the entry, and only then does the user (not the agent) decide whether to author an actual skill in `crickets/`.

9. **Single-library embedding lock-in (v0.9.2)**. v0.9.2 narrowed embeddings to a single mode: local `sentence-transformers` (see [ADR 0001's 2026-05-20 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20)). The library is widely-used + permissively-licensed + actively maintained, but the toolkit is now coupled to its API + model-loading semantics. Risk: if `sentence-transformers` is abandoned or pivots incompatibly, swap candidates include direct PyTorch + tokenizers integration or the `transformers` library. **Re-audit trigger**: `sentence-transformers` stops shipping releases for 6+ months OR drops support for the BGE-large family. Mitigation: the abstraction at `embed.py` is thin (~50 lines wrapping `SentenceTransformer.encode()`); replacement effort is bounded.

## Quality Attributes

### Security

Vault contents are private — they may include PII (project names, internal preferences, fixes that mention real systems). Three layers of access control:

1. **Filesystem permissions**: the vault lives at the user's synced storage path; only the user's OS account has read/write access. Agent inherits via the user's session.
2. **Network surface**: the agent never makes the vault contents accessible via network. As of v0.9.2, MemoryVault makes **zero external network calls** during normal operation — embeddings are computed entirely on-device via local `sentence-transformers` (see [ADR 0001's 2026-05-20 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20)). The only network access is the one-time model download (~1.3GB BGE-large from HuggingFace Hub) on first invocation, after which the toolkit is fully offline-capable.
3. **Tool allowlist**: the `memory` skill is allowed `Read, Write, Edit, Glob, Grep` only — no Bash, no network primitives, no shell exec. Reflection sidecar uses a sub-agent (per the evaluator pattern) which inherits the same restricted allowlist.

PII guardrails: the `crickets/` pre-push hook + CI gate covers PII detection on toolkit-committed content (skill source + templates + how-to docs). Vault contents themselves are NOT in crickets — they're in the user's private Obsidian vault. No public surface for vault contents.

API keys for the embedding provider live in environment variables (existing agent-surface convention) — never in MemoryVault entries.

### Reliability

Failure modes + mitigations:

- **Cloud sync failure** (network down, sync paused): agent can still read/write the local cached vault path; changes propagate when sync recovers. No data loss.
- **sqlite-vec index corruption**: recall falls back to grep+frontmatter-only (degraded but functional); index can be rebuilt from scratch by re-embedding all entries.
- **Local-embedding failure** (rare — sentence-transformers not installed, or PyTorch MPS regression): save still succeeds (file write is unconditional); embedding queue stays pending until deps are restored. UserPromptSubmit hook falls back to grep+frontmatter when sentence-transformers is unavailable. No external network dependency exists post-v0.9.2 ([ADR 0001 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20)) so there's no rate-limit / API-failure class of incident.
- **Hook crash mid-reflection**: `.harness/session-id-*.start` marker stays in place; idle-time hook will retry reflection retroactively. `commit-on-stop` covers any dirty git tree from interrupted writes.
- **Vault path missing** (sync layer not mounted, drive disconnected): hooks log error + graceful-skip; agent continues without memory injection rather than failing the session.

### Data Integrity

File writes are atomic at the filesystem level (single `Write` call). `/memory evolve` is a two-step rename + write; transactional integrity relies on filesystem atomicity (good on macOS APFS).

Frontmatter `status` field is the supersession discipline: `active` (default), `resolved`, `superseded`. Recall filters skip non-`active` by default. `superseded_by` and `supersedes` cross-link the supersession graph.

No database transactions (file-based by design). Risk: simultaneous writes to the same entry from two devices via the user's sync layer. Accepted as a known limitation under the single-user assumption; if it bites, escalate to a sync-mediating access mechanism (e.g. Obsidian REST API).

### Privacy

Vault contents are PRIVATE — assumed to contain PII. Storage = user's local + synced storage (user's account). No content leaves the user's control except:

1. **Embeddings to the configured provider**: entry title + tags + first paragraph (~50 tokens per entry) sent over TLS for embedding. Privacy posture = same as any agent-surface interaction that ships file contents to its provider for inference.
2. **Local-only by default** (as of v0.9.2): embeddings are computed entirely on-device via local `sentence-transformers` ([ADR 0001 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20)). No external API calls. If sentence-transformers is unavailable (not installed, or PyTorch MPS issue), recall degrades to grep+frontmatter-only. The previous `memory.use_api_embeddings: false` opt-out is no longer needed since local IS the only mode.

No analytics, no telemetry, no third-party sharing.

### Scalability

sqlite-vec scales to 100K+ entries trivially with sub-second query times. Realistic personal-use estimates: no more than ~20K entries by year 5 (reflection sweep + idea-incubator + personal-skills index combined). Grep scales linearly with vault size; at 10K entries grep walltime is ~1-2 seconds (acceptable for the UserPromptSubmit time budget). Headroom is comfortable — partitioning the vec-index by group or by year only becomes worth considering if vault grows past 20K entries faster than projected.

### Latency

Hook time budgets (hard limits — exceed → log warning + proceed with partial results):
- **SessionStart**: 500ms. One filesystem walk of `_always-load/` (~20 files) + file reads. Achievable.
- **UserPromptSubmit**: 300ms. Embed query (local BGE-large on M-series ~50-100ms via PyTorch MPS; CPU-only ~150-300ms — operator-config-dependent per [ADR 0001 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20)) + vec query (10ms) + grep merge (50ms) + format + inject (10ms). Tight on CPU; consider caching common embeddings (e.g. tokenize prompt → check embedding cache).
- **Stop hook reflection**: no time budget (runs in background after session ends; user-perceived latency = 0).
- **Idle hook reflection**: no time budget (runs in background).

Save latency: file write synchronous (<50ms); embedding async (doesn't block agent).

### Abuse

N/A: single-user personal tooling. No external surface, no rate-limiting needs, no anti-spam, no malicious-input handling beyond standard Claude Code sandboxing. The vault is trusted-source-only.

### Accessibility

N/A: text-only on-disk format; no UI provided by this design. The user accesses the vault via Obsidian (which provides its own accessibility support per Obsidian's WCAG compliance). Agent-side surface is Claude Code's standard text-based UX.

### Testability

The skill is documentation + sub-command bodies; tests follow the established crickets pattern:

- **Smoke install tests** (existing `smoke-install-bash.sh` + `.ps1`): extended to verify `memory` skill + 4 hooks install correctly at the 2 host destinations (Claude Code + Antigravity; gemini-cli removed in v0.9.0 per [ROADMAP item #15](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) / [ADR 0006](../decisions/0006-gemini-cli-host-removal)).
- **Manual end-to-end walks**: per established pattern (manual fill-out verification for `/design author`, manual hook fire for `/work` step verifications). Each sub-command walked through a synthetic 5-minute scenario.
- **Recall-quality tests**: manual via seeded vault — fixture vault with 50 entries, fixed query set, expected recall set; run as periodic regression. Vec-quality regressions surface here.
- **Hook tests**: manual via fixture session — write a fixture transcript, fire the Stop hook, inspect the resulting MemoryVault diff. Deterministic enough for CI.
- **Tri-modal routing tests**: unit-level per heuristic (HIGH/MEDIUM/LOW) — given a candidate string, assert routing decision. Lives in `crickets/scripts/test-memory-routing.py`.

Deterministic verification per gate per agentm conventions; LLM-judge augmentation only for recall-relevance gating (not as a primary check).

### Internationalization & Localization

N/A: vault content is English-only (single user, English-speaking). No locale-aware date formatting; dates use ISO 8601 (`YYYY-MM-DD`) which is locale-neutral. Future expansion possible if user wanted to capture content in another language, but no current demand.

### Compliance

N/A: personal tooling, user-owned data, no regulatory framework applies. GDPR-style "right to be forgotten" is satisfied trivially by deleting the vault directory; no third-party data processing.

## Project management

### Work estimates

**Plan #7a (MemoryVault Core)** — Large, estimated 8-10 tasks, 2-3 weeks calendar:

1. (L) Manual co-created seed pass + `~/ContextVault/` content migration.
2. (M) Skill scaffold + `memory save` write primitive + sqlite-vec dependency wiring.
3. (M) `memory evolve` atomic supersede primitive.
4. (M) Reflection sidecar logic + 3-category mine + tri-modal routing.
5. (L) Stop-event hook + idle-time hook (new crickets primitive) + crash recovery.
6. (M) SessionStart + UserPromptSubmit recall hooks + dedup logic.
7. (L) Recall engine — sqlite-vec + grep+frontmatter merge + local embedding integration (BGE-large via sentence-transformers; see [ADR 0001 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20) for the v0.9.2 local-only refactor).
8. (M) Idea ledger — `Ideas.md` + `_idea-incubator/` two-tier capture + permeable boundary enforcement.
9. (S) Documentation pass — how-to + ADR 0005 + cross-refs.
10. (M) Release pair `crickets v0.9.0` + (if harness integration needed) `agentm v2.4.0`.

**Plan #7b (MemoryVault Discovery + Mining)** — Medium, estimated 5-7 tasks, 1-2 weeks calendar, ships after 1-2 weeks of #7a dogfood:

1. (M) Transcript reflection one-time pass over `~/.claude/projects/*/`.
2. (M) Personal-skills auto-indexer (toolkit + harness SKILL.md → `personal-skills/` group).
3. (L) Internet skill-discovery component with adapt-don't-import workflow.
4. (S) Documentation pass — how-to update + ADR 0006.
5. (M) Release pair `crickets v0.9.2` + harness if needed.

### Documentation Plan

**Agent-toolkit wiki additions** (#7a):
- **New how-to**: `crickets/wiki/how-to/Use-The-Memory-Skill.md` — comprehensive page covering 4 sub-commands + worked scenarios (capture flow / recall flow / idea promotion / supersede flow) + tri-modal routing explanation + interactive-review mode setting + troubleshooting (sqlite-vec install / cloud sync issues / API embedding fallback / vault bloat). *(Moved to [Agent M wiki — Use-The-Memory-Skill](https://github.com/alexherrero/agentm/wiki/Use-The-Memory-Skill) in v2.0.0 per V4 #36.)*
- **New ADR**: `crickets/wiki/explanation/decisions/0005-memoryvault.md` — locked design calls from the 4 groups × 13 questions, alternatives considered, consequences (positive / negative / assumptions to re-audit).
- **Updated**: `Home.md` + `_Sidebar.md` (add memory skill to reader-intent sections); `README.md` "What's inside" table (bump version + add memory skill row); `Customization-Types.md` (add memory as concrete example link in skill row).
- **This design doc itself** (`memoryvault.md`) becomes the canonical "Why we built this" wiki entry point per the locked design call from plan #6.

**Agent-toolkit wiki additions** (#7b):
- **Update**: `Use-The-Memory-Skill.md` — add transcript-reflection + skill-discovery sections. *(Skill page moved to [Agent M wiki](https://github.com/alexherrero/agentm/wiki/Use-The-Memory-Skill) in v2.0.0.)*
- **New ADR**: `0006-memoryvault-discovery.md` — design calls specific to #7b (adapt-don't-import, source whitelist).

**Harness wiki additions**: None for #7a (toolkit-only). #7b: same. Plan #8 (auto context integration into harness phases) is when harness wiki adds memory references.

### Launch Plans

Phased rollout via the locked dev-flow convention:

1. **#7a release**: `crickets v0.9.0` + `agentm v2.4.0` (if any harness integration; likely not — toolkit-only). Coordinated cross-repo if needed; toolkit-first per the locked order from plans #3-#6.
2. **Dogfood window** (1-2 weeks): user runs MemoryVault in real Claude Code sessions. Inbox review + interactive-review tuning + recall-quality measurement happen here.
3. **#7b release**: `crickets v0.9.2`. Lands transcript reflection + personal-skills indexer + internet skill-discovery.
4. **Re-audit trigger** (built into ADR 0005): after 1 month of real use, re-audit the locked design calls. Capture-threshold + recall-quality + cross-device sync conflicts get re-evaluated; ADR 0005 amendments authored if any decisions flipped.

No feature flags; no phased rollout to user segments (single user). The escape hatches are: `memory.review_mode: silent` (cuts MEDIUM-tier prompts), `AGENT_TOOLKIT_EMBEDDING_MODEL` env var (swap default BGE-large for a smaller model on low-spec hosts — added in v0.9.2 per [ADR 0001 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20); still local-only — no API option), `memory.enabled: false` (kill switch — disables all hooks + auto-recall, vault becomes read-only).

## Operations

### SLAs

N/A: personal tooling, no external SLA exposure. The hooks have soft time budgets (SessionStart 500ms, UserPromptSubmit 300ms) but exceeding them is logged + degraded-graceful, not paged.

### Monitoring and Alerting

Minimal personal-only monitoring:

- **Hook execution log**: `.harness/memoryvault.log` (rotating, gitignored) — one structured JSON line per hook invocation with timestamp, hook name, duration, result (success / partial / error), entries-injected count. User-readable; supports `tail -f` for debugging.
- **Vault health snapshot**: `/memory health` command outputs entry count per group + last-reflection timestamp + sqlite-vec index size + inbox count + incubator count + API embedding spend (estimated from save count).
- **Alerts** (personal — no PagerDuty): the `/memory reflect` Stop-event output warns when inbox > 50 entries or incubator > 20 unpromoted entries. Idle-time hook surfaces a "no reflection in 7+ days, something might be broken" notice if vault is silent.
- **Disk + memory usage**: BGE-large is ~1.3GB on disk + ~1.5GB RAM at runtime per [ADR 0001 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20). Monitor via `~/.cache/crickets/sentence-transformers/` (disk) and process RSS (RAM). If footprint becomes a problem, swap to a smaller model via `AGENT_TOOLKIT_EMBEDDING_MODEL` env var.

### Logging Plan

Structured JSON logs at `.harness/memoryvault.log` (gitignored, runtime-only):

```json
{"ts": "2026-05-15T18:30:00Z", "hook": "user-prompt-submit", "duration_ms": 245, "entries_injected": 5, "vec_hits": 4, "grep_hits": 3, "deduped_count": 2}
{"ts": "2026-05-15T19:00:00Z", "hook": "stop-reflect", "duration_ms": 12500, "candidates_mined": 8, "auto_saved": 2, "interactive_reviewed": 4, "inboxed": 2}
```

Retention: 30 days, rotated weekly. Log rotation handled by a simple `logrotate`-style discipline in the hook scripts.

Log levels: per-hook duration (always), errors (always), debug-trace (opt-in via `memory.log_level: debug`).

### Rollback Strategy

Three rollback levels, depending on what's broken:

1. **Soft disable** (most common): set `memory.enabled: false` in skill config → all hooks become no-ops; auto-recall + auto-save stop; vault contents untouched. Reversible by flipping back.

2. **Skill uninstall**: `bash crickets/install.sh --uninstall memory` removes the skill + 4 hooks from the host destinations. Vault contents untouched (intentional — vault is the user's data, not the skill's data).

3. **Vault rollback**: vault contents are versioned via Obsidian's file versioning + the user's cloud sync provider's restore-deleted-files history + Obsidian's optional git plugin (if user enables — not required by this design). If the vault gets corrupted by a runaway reflection sidecar, the combination of sync-side restore + Obsidian versioning provides recovery; worst case, restore the vault to a snapshot before the runaway and replay the sidecar with stricter confidence thresholds.

No schema migrations are involved in rollback — the markdown + YAML format is backwards-compatible by design.

## Document History

| Date | Change | Status |
|---|---|---|
| 2026-05-15 | Initial draft created via `/design author`. Pre-filled all sections from the 4-group architectural lock conversation (A1-A4 / B1-B3 / C1-C4 / D1-D3) settled 2026-05-15. First real dogfood of plan #6's design skill. | draft |
| 2026-05-15 | Walk-sections pass complete (6 chunks: Context / Design / Alternatives + Dependencies / Migrations + Tech Debt / Quality Attributes / Project management + Operations). Edits applied: voice scrubbed of internal-source citations and "load-bearing" phrasing; sync mechanism details (specific cloud provider names) generalized to "the user's existing sync setup" / "cloud sync"; embedding pricing scrubbed of specific dollar amounts; vendor-name references for the embedding API generalized to "the configured embedding provider" in design-call-agnostic contexts (lock to specific vendor kept only where it's the v1 lock); migration #1 expanded with three additional source-paths flagged for follow-up discussion at the seed-pass task (Obsidian vault overlap + GitHub experimental repo + scattered synced-repo CLAUDE.md / AGENTS.md / PLAN.archive / ADR / ROADMAP content); local sentence-transformers fallback promoted from "documented but not shipped" to "ships in v1 alongside API path"; sqlite-vec framed as C-extension language-agnostic on-disk format with v1 Python caller + Rust/Go future swap captured as ROADMAP follow-up; cross-host embedding support (Gemini / OpenAI / Voyage / Cohere) captured as separate ROADMAP follow-up; scalability projection tightened to "no more than ~20K entries by year 5". | draft |
| 2026-05-15 | Author signaled ready for review. Doc locked from further authoring edits; next `/design author memoryvault` invocation runs the review-pass flow (Step 6 — approve/revise/skip per section). | review |
| 2026-05-15 | **Approved as final via fast-path** (per-section review pass skipped per author signal "approve as final immediately"). Walk-sections pass deemed thorough enough that per-section ratification would have been ceremonial. Doc is now immutable until either `/design translate` runs (next step) or a human manually edits the file + reverts Status to `review`. Unblocks `/design translate` (split into structural parts) and `/design sequence` (generate PLAN.md per part). | final |
| 2026-05-15 | **Translated to 6 parts via `/design translate`**: `write-primitives` (DD §1, foundational), `recall-loop` (DD §3 + §4 merged — hooks + engine ship together), `reflection-and-recovery` (DD §2 + §6 merged — sidecar + crash-recovery markers share Stop/idle scaffolding), `idea-ledger` (DD §5, first real consumer of A3 permeable boundary), `seed-pass` (DD §7, co-created, deliberately last in #7a to validate the loop end-to-end), `discovery-mining` (DD §8, single part for plan #7b). 8 Detailed Design subsections grouped into 6 parts to fit under the skill's soft cap. Part files at `wiki/explanation/designs/memoryvault/parts/<part-slug>.md`. Status stays `final` (translate doesn't transition Status — only `/design author` and harness `/release` do). | final |
| 2026-05-16 | **Sequenced into 6 plans via `/design sequence`**; first plan active at `.harness/PLAN.md` (`write-primitives`), 5 queued at `.harness/designs/memoryvault/queued-plans/`. Topological order via Kahn's algorithm with alphabetical tie-breaking on `idea-ledger` vs. `seed-pass` (both in-degree 0 at round 4): `write-primitives` → `recall-loop` → `reflection-and-recovery` → `idea-ledger` → `seed-pass` → `discovery-mining`. Each PLAN.md derives Brief from parent part's Scope; Goal from Verification criteria rephrased as user-visible outcomes; Constraints from parent's Quality Attributes; Out of scope from other part slugs; Tasks as DRAFT decomposition (operator typically runs harness `/plan` to refine before `/work`); Risks from parent Tech Debt; Verification strategy verbatim from part's Verification criteria; Locked design calls cross-ref + key extracts. As each plan completes (Status: done) via harness `/release` §1b lifecycle hook (shipped in plan #6 task 5), the next queued plan promotes automatically. Design hand-off complete — execution phase begins with `/work` on plan #7a part 1 (`write-primitives`). | final |
| 2026-05-16 | **Host-scope correction**: `memory` skill manifest excludes `gemini-cli` from `supported_hosts` (ships with `[claude-code, antigravity]` only). Triggered by ROADMAP item #15 (Gemini-CLI host removal) being added to `.harness/ROADMAP.md` 2026-05-16, after this design was finalized 2026-05-15. Rather than ship the memory skill with `gemini-cli` only to have #15 strip it back out, the first new skill post-#15-decision ships with the post-#15 host scope from day 1. Existing skills (`pii-scrubber`, `design`, `dependabot-fixer`, `ship-release`, `evaluator`, base hooks) retain `gemini-cli` in their manifests until #15 sweeps them in one coordinated patch. This correction is small enough that re-running `/design translate` is not required — the change is captured in the `write-primitives` PLAN.md task 1 + this Document History entry; downstream parts (recall-loop / reflection-and-recovery / idea-ledger / seed-pass / discovery-mining) inherit the corrected host scope automatically when their PLAN.md task 1 generates the relevant manifest sections. Surfaced during `/plan` refinement of `write-primitives` PLAN.md (2026-05-16) which had inherited the original three-host manifest spec. **Operator-driven amendment per the skill's escape-hatch convention** (Status stays `final`; mid-execution change documented here per the parent's Migrations §3 pattern). | final |
| 2026-05-17 | **Host-scope correction fleet-wide via ROADMAP item #15** (plan #15 in flight). Toolkit-side gemini-cli surface fully closed for forward-looking content: installer dispatch arms removed (plan #15 task 1, commit e1b477e), all customization manifests swept (task 2, commit 5af1a59), validator tightened + smoke install negative-existence assertions added (task 3, commit b216043), wiki + ADRs swept + new [ADR 0006](../decisions/0006-gemini-cli-host-removal) created (task 4, commit 13109fa). The "until #15 sweeps them in one coordinated patch" note in row 7 is now resolved — all existing customizations (pii-scrubber, design, dependabot-fixer, ship-release, evaluator, base hooks, example bundle) match the memory skill's 2-host scope. Plan #15 release pair `crickets v0.9.0` + `agentm v2.4.0` ships in plan #15 task 7. **No Status change** for this design — the host-scope fleet-wide sweep is operator-driven implementation detail, not a parent-design architectural pivot. Memory skill's architecture, 4 sub-commands, recall hooks, reflection sidecar, idea ledger, tri-modal routing, sqlite-vec + grep+frontmatter merge, Anthropic API embeddings + local fallback, two-tier idea capture — all unchanged. Document History row 8 closes the host-scope thread that row 7 opened. | final |
| 2026-05-20 | **Embedding-mode collapse to local-only (v0.9.2 via ROADMAP item #18 + plan #18).** Locked design call **C2** (dual-mode Anthropic API + local sentence-transformers fallback) **superseded by [ADR 0001's 2026-05-20 amendment](../decisions/0001-crickets-purpose.md#amendment-2026-05-20)**: the toolkit now ships local sentence-transformers as the only production embedding mode. Default model upgraded `all-MiniLM-L6-v2` (384-d, MTEB 56.3) → `BAAI/bge-large-en-v1.5` (1024-d, MTEB 64.2); `EMBEDDING_DIM` bumped 384 → 1024; new `AGENT_TOOLKIT_EMBEDDING_MODEL` env var as escape hatch (still local). Operator-config-assumption locked: desktop-class hardware (M-series + 64GB-RAM or equivalent). **Design-doc body rewritten in-place** to match v0.9.2 state across 12 substantive references: Overview point 2 (Hook-driven recall), Infrastructure § Embeddings (line 68 — the central one), Detailed Design § Recall engine (query path), Dependencies (Anthropic API entry replaced with historical note), Tech Debt #2 (Embedding API cost → First-run model download cost), Tech Debt #9 (Single-vendor lock-in → Single-library lock-in), Security § Network surface (zero external calls post-v0.9.2), Reliability § Embedding failure (Local-embedding failure), Privacy § Opt-out (Local-only by default), Latency budget (UserPromptSubmit), Project management § Detailed Design subsection 7, Operations § Monitoring (API spend → Disk + memory). Each rewritten section cross-links to ADR 0001's amendment for the full rationale + load-bearing assumptions + re-audit triggers. **No Status change** — the design's central architecture (file-based vault, hook-driven recall, reflection sidecar, idea ledger, tri-modal routing, sqlite-vec + grep+frontmatter merge, two-tier idea capture, 4 sub-commands, A3 permeable write boundary) is unchanged. Plan #18 was inserted mid-flight of plan #7a part 5 (seed-pass) because task 6 (validate via sample recalls) needs a worthwhile embedding model; resumes seed-pass at task 6 with the new model. | final |
| 2026-05-22 | **Discovery + mining shipped (#7b via plans #7b parts 1-7).** Part 6 (`discovery-mining`) — the second roadmap item under the parent design — completes with four new sub-commands: `/memory index-skills` (auto-indexer for installed `SKILL.md`s — task 1), `/memory reflect corpus` (historical-transcript-backlog mining with dry-run-default + state-file-resume — task 2), `/memory discover-skills` (cadence-checked internet skill-discovery scan against 4-source operator-confirmed whitelist — task 3), `/memory adapt-skills` (Pass 1 Python rubric + Pass 2 LLM sub-agent judgment with GitHub metadata enrichment + trustworthiness signals — task 4), `/memory watchlist` (promote/dismiss/defer review surface — task 5). New [ADR 0007](../decisions/0007-memoryvault-discovery.md) captures 7 locked design calls (auto-detect repo names via .git/AGENTS.md ancestor walk; dry-run-by-default corpus; operator-confirmed 4-source whitelist seed; weekly cadence with idle-hook self-throttle; two-pass adapt-don't-import; promote-as-annotation + dismiss-as-archive; stdlib-only no-new-deps) and 4 load-bearing assumptions with re-audit triggers. New `adapt-evaluator` sub-agent (read-only with write allowlist physically scoped to `_skill-watchlist/<source-slug>/<pattern-slug>.md`) **architecturally enforces** the adapt-don't-import principle — agents physically cannot fork into `crickets/skills/`. No new third-party deps (urllib + json + os via stdlib; GitHub API unauthenticated with graceful-skip on 60/hr rate limit). **No Status change** — discovery-mining is an additive layer on top of the unchanged Detailed Design; the four new sub-commands extend the existing skill rather than rewriting any part of it. Plan #7b shipped as paired release `crickets v0.10.0` + `agentm v2.4.2` (paired-doc-only per established pattern). | final |
