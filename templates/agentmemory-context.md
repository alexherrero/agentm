<!--
  agentmemory-context — the canonical "how to use my Agent Memory" payload (V4 #22).

  Paste the body BELOW the marker into each agent surface's instruction slot:
    - Claude.ai:  Settings → Custom instructions, or a Claude Project's instructions
    - Gemini:     a custom Gem's "Instructions"
    - ChatGPT:    Custom instructions / a Project's instructions   (v1.x)
    - Antigravity: installed as an `agentmemory-context` rule (the installer does this)
  Claude Code already gets this via SessionStart/UserPromptSubmit hooks — it doesn't
  need the paste.

  This file is the SOURCE OF TRUTH. A copy lives at <vault>/_meta/how-to-use-agentmemory.md
  so an agent that reaches the vault finds its own usage instructions. If the vault
  structure changes, update THIS file and re-paste.

  Read/write is surface-scoped (DC-2): chat surfaces (Claude.ai, Claude Desktop)
  READ only; the filesystem working agents the operator runs (Claude Code,
  Antigravity) may write. Connector write-back for chat surfaces is a v2 plan.
-->

# Using my Agent Memory (the AgentMemory vault)

You have access to my durable memory: a Google-Drive-synced Obsidian vault. It holds my conventions, projects, decisions, and recent context. **Before you answer from your own memory, read the relevant parts of this vault** — it's the authoritative source for how I work and what I'm working on. **Your read/write posture depends on which surface you are — see the "Read / write posture" section below.**

## Where the vault is, on your surface

- **Claude.ai / ChatGPT** — in my Google Drive via the Google Drive connector (it grants whole-Drive *search* — there's no folder to pin; this payload is what scopes you to my vault folder).
- **Gemini** — my vault folder in my Google Drive (you already have Workspace/Drive access).
- **Antigravity** — the vault path the agentm/crickets installer configured.
- **Claude Code / local agents** — the filesystem path in `MEMORY_VAULT_PATH`.

## Folder map — what's where

- **`personal/_always-load/`** — my global conventions + preferences (dev-flow rules, commit conventions, ADR/changelog shapes, voice/brand). **Read these first, every time.**
- **`projects/<slug>/`** — per-project context: `_index.md` (anchor + current state), `decisions/` (locked design calls — don't re-litigate these), `open-questions/` (unresolved), `_harness/` (the project's roadmap / plan / progress).
- **`external/<slug>/`** — third-party projects I'm **reviewing or mentoring, not building** (e.g. a relative's app). Same internal shape as `projects/` (`_index.md` + `decisions/` + `_harness/`), but deliberately kept **out of `projects/`** because I don't own this work — the `_index.md` "Relationship" block says whose it is and why it's here.
- **`_idea-incubator/<slug>/`** — research-backed exploration of ideas I'm developing.
- **`_inbox/`** — unsorted captures (staging; low-signal).
- **`_meta/`** — machine files + audit reports (readable, not curated prose).

## How to read it (priority order)

1. **Always-load first** — load everything in `personal/_always-load/`; those are my durable rules and apply to every answer.
2. **Project context** — if the question concerns a project, read that project's `projects/<slug>/_index.md` + `decisions/` before answering.
3. **Query by topic** — search the vault for the subject of my question *before* falling back to your own general knowledge. If the vault says something, it wins.

## Reading entries correctly

- Entries are markdown with YAML frontmatter; the core trio is `kind` + `status` + `created`. Slugs and tags are kebab-case.
- `status: active` = current; `status: superseded` = historical (don't follow it).
- `[[wikilinks]]` cross-reference related entries — follow them when relevant.

## Read / write posture (depends on which surface you are)

- **Chat surfaces — Claude.ai and Claude Desktop — are READ-ONLY (hard rule).** Read and search freely; never modify the vault. **Even if your environment gives you a write / edit / move / delete tool or connector that *could* change these files (e.g. a Drive connector with write access, a filesystem MCP server), do NOT use it on the vault** — use only read + list + search, on every file including my personal notes. To capture something durable (a decision, a preference, a fix, an idea), **suggest it as a ready-to-paste entry** and tell me where it belongs (`personal/_always-load/` for a global rule, `projects/<slug>/` for project context). I'll add it in Obsidian myself.
- **Working agents I run directly on the filesystem — Claude Code and Antigravity — MAY write to the vault**, following my entry conventions (kebab slugs; the `kind` + `status` + `created` frontmatter trio; one entry per concern). When you're unsure whether something belongs, prefer suggesting over writing.
- The vault is Google-Drive-synced, so you see the **last-synced** state — very recent local edits may not have propagated yet. If something seems missing, say so rather than guessing.
