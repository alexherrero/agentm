---
trigger: always_on
---

# AgentMemory — my durable memory vault

You have access to my durable memory: a Google-Drive-synced Obsidian vault. It holds my conventions, projects, decisions, and recent context. **Before you answer from your own knowledge, read the relevant parts of this vault** — it is the authoritative source for how I work and what I'm working on.

## Where the vault is

Resolve the vault root from, in order:

1. The `MEMORY_VAULT_PATH` environment variable, if set.
2. The `vault_path` key in `.agentm-config.json` — a project-local one wins if present, else the one at the install prefix (`$AGENTM_INSTALL_PREFIX`, default `~/.claude/.agentm-config.json`).

A self-describing copy of these instructions lives at `<vault>/Agent/_meta/how-to-use-agentmemory.md` — read it if you need the in-vault reference.

## Folder map — what's where

My own spaces and the agent's share one vault root. `Agent/` is the agent's half; `standards/` and `Projects/` sit beside it at the root.

- **`standards/`** — my global conventions + preferences (dev-flow rules, commit conventions, changelog shapes, voice/brand). **Read these first, every session.**
- **`Projects/<slug>/`** — per-project context: `_index.md` (anchor + current state), `decisions/` (locked design calls — don't re-litigate), `_harness/` (the project's roadmap / plan / progress).
- **`Agent/memory/`** — the memory corpus, one directory per class. `semantic/` holds facts, principles and learned tool behaviour, `procedural/` holds recipes and protocols, and `episodic/` holds session traces. Three more are derived from those and rebuildable from them: `entities/` keeps a living file per person, system or repo, `crystallized/` keeps the lessons repetition produced, and `mocs/` keeps generated maps of content.
- **`Agent/desk/`** — work in flight: `briefs/`, `projects/`, `tasks/`, `scratch/`.
- **`Agent/_meta/`** — machine files + audit reports (readable, not curated prose).

**There is no inbox.** A capture files straight into its class directory and is searchable the moment it lands, carrying `status: unfiled` and `filing_confidence: low` until filing promotes it — which ranks it lower rather than hiding it. The metadata is the inbox. Read `status: unfiled` as the "unsorted, low-signal" marker a staging folder used to mean. If you meet an `_inbox/` directory in an older vault, it holds legacy content and is not where new captures go.

## How to read it (priority order)

1. **Standards first** — load everything in `standards/`; durable rules that apply to every answer.
2. **Project context** — if the question concerns a project, read that project's `Projects/<slug>/_index.md` + `decisions/` before answering.
3. **Query by topic** — search the vault for the subject *before* falling back to your own general knowledge. If the vault says something, it wins.

## Reading entries correctly

- Entries are markdown with YAML frontmatter. Every entry carries `status` + `created`, plus exactly one of `type` (a memory) or `kind` (an infrastructure record such as a brief or a session trace) — never both. Slugs and tags are kebab-case.
- `status: active` = current; `status: superseded` = historical (don't follow it); `status: unfiled` = captured but not yet filed, which is real content awaiting confirmation rather than content to skip.
- `[[wikilinks]]` cross-reference related entries — follow them when relevant.

## Read / write posture — you are one of my working agents

You (Antigravity) are one of the filesystem agents I run directly, so **you may read AND write the vault** — following my entry conventions (kebab slugs; `status` + `created` plus exactly one of `type` or `kind`; one entry per concern). When you're unsure whether something belongs, prefer suggesting over writing. (My chat surfaces — Claude.ai, Claude Desktop — are read-only; you are not one of them.)

The vault is Google-Drive-synced, so you see the **last-synced** state — very recent local edits may not have propagated yet. If something seems missing, say so rather than guessing.

## Source of truth

This rule mirrors the canonical payload [`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md) in the agentm repo. If the vault structure changes, that file is updated and this rule refreshes on `--update`.
