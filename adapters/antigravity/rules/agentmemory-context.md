---
trigger: always_on
---

# AgentMemory — my durable memory vault

You have access to my durable memory: a Google-Drive-synced Obsidian vault named **`AgentMemory/`**. It holds my conventions, projects, decisions, and recent context. **Before you answer from your own knowledge, read the relevant parts of this vault** — it is the authoritative source for how I work and what I'm working on.

## Where the vault is

Resolve the vault root from, in order:

1. The `MEMORY_VAULT_PATH` environment variable, if set.
2. The `vault_path` key in `.agentm-config.json` — a project-local one wins if present, else the one at the install prefix (`$AGENTM_INSTALL_PREFIX`, default `~/.claude/.agentm-config.json`).

A self-describing copy of these instructions lives at `<vault>/_meta/how-to-use-agentmemory.md` — read it if you need the in-vault reference.

## Folder map — what's where

- **`personal-private/_always-load/`** — my global conventions + preferences (dev-flow rules, commit conventions, ADR/changelog shapes, voice/brand). **Read these first, every session.**
- **`projects/<slug>/`** — per-project context: `_index.md` (anchor + current state), `decisions/` (locked design calls — don't re-litigate), `open-questions/` (unresolved), `_harness/` (the project's roadmap / plan / progress).
- **`external/<slug>/`** — third-party projects I'm **reviewing or mentoring, not building** (e.g. a relative's app). Same internal shape as `projects/` (`_index.md` + `decisions/` + `_harness/`), but deliberately kept **out of `projects/`** because I don't own this work — the `_index.md` "Relationship" block says whose it is and why it's here.
- **`_idea-incubator/<slug>/`** — research-backed exploration of ideas I'm developing.
- **`_inbox/`** — unsorted captures (staging; low-signal).
- **`_meta/`** — machine files + audit reports (readable, not curated prose).

## How to read it (priority order)

1. **Always-load first** — load everything in `personal-private/_always-load/`; durable rules that apply to every answer.
2. **Project context** — if the question concerns a project, read that project's `projects/<slug>/_index.md` + `decisions/` before answering.
3. **Query by topic** — search the vault for the subject *before* falling back to your own general knowledge. If the vault says something, it wins.

## Reading entries correctly

- Entries are markdown with YAML frontmatter; the core trio is `kind` + `status` + `created`. Slugs and tags are kebab-case.
- `status: active` = current; `status: superseded` = historical (don't follow it).
- `[[wikilinks]]` cross-reference related entries — follow them when relevant.

## Read / write posture — you are one of my working agents

You (Antigravity) are one of the filesystem agents I run directly, so **you may read AND write the vault** — following my entry conventions (kebab slugs; the `kind` + `status` + `created` frontmatter trio; one entry per concern). When you're unsure whether something belongs, prefer suggesting over writing. (My chat surfaces — Claude.ai, Claude Desktop — are read-only; you are not one of them.)

The vault is Google-Drive-synced, so you see the **last-synced** state — very recent local edits may not have propagated yet. If something seems missing, say so rather than guessing.

## Source of truth

This rule mirrors the canonical payload [`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md) in the agentm repo. If the vault structure changes, that file is updated and this rule refreshes on `--update`.
