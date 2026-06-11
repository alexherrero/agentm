# How to use AgentMemory in any agent

> [!NOTE]
> **Goal:** Make any agent surface (Claude.ai, Gemini, ChatGPT, Antigravity) read your GDrive-synced AgentMemory vault natively — so it already knows your conventions, projects, and decisions without you re-explaining. **Surface-scoped access:** chat surfaces (Claude.ai, Claude Desktop) read + query the vault and *suggest* entries for you to paste in by hand — they never write; the filesystem working agents you run directly (Claude Code, Antigravity) may write to the vault, following your entry conventions.
> **Prereqs:** the AgentMemory vault synced to Google Drive (signed into the account that owns it); the context payload (`templates/agentmemory-context.md`, shipped in #22); and operator access to each surface's connector / Gem settings (the agent can't log into your accounts — connector setup is an operator action).

## Before you start (all surfaces)

The one thing you paste into every surface is **the context payload** → **[`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L19)**. Copy its body from **line 19** (`# Using my Agent Memory`) to the end — **skip the leading HTML comment** (operator-only notes). For what each section of **the context payload** means, see the [AgentMemory context payload reference](AgentMemory-Context-Payload).

Prereq for the Google-Drive surfaces: the vault is synced to Google Drive, and you're signed into the **Google account that owns it**.

| Surface | Status | How it reads the vault |
|---|---|---|
| Claude Code | ✅ built-in | local filesystem + SessionStart hooks — no paste needed |
| Claude.ai | ✅ validated | Google Drive connector (*search*) + **the context payload** |
| Claude Desktop | ✅ validated | local **filesystem MCP server** → full navigation (or the Drive connector) + **the context payload** |
| Antigravity | ✅ validated | local filesystem → installed `agentmemory-context` rule (per-project `.agents/rules/` **or** global `~/.gemini/GEMINI.md` at user scope) |
| Gemini · ChatGPT · Codex | deferred → post-FRIDAY | no live file/search access to the vault yet |

**v1 criterion:** a surface only qualifies if it has **live file-or-search access** to the vault — a filesystem agent (Claude Code, Claude Desktop via a filesystem MCP server, Antigravity) or the Drive-search connector (Claude.ai). Chat-only bots that can't reach the vault are deferred.

## Claude.ai

1. **Connect Google Drive.** Settings → Connectors → enable **Google Drive** and finish the OAuth. This grants Claude *search* access to your whole Drive — **there is no folder to pin** (scoping comes from the payload).
2. **Paste [the context payload](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L19)** into Settings → Custom Instructions (applies to every chat) or a Claude **Project's** instructions.
3. **Dogfood** (see below).

*More reliable recall:* search-at-query-time depends on Claude choosing to search. To ground it, create a **Claude Project**, put **the context payload** in the Project instructions, and add the `personal-private/_always-load/` entries to the Project's knowledge.

## Claude Desktop

Best path: give Claude Desktop a **local filesystem MCP server** pointed at the vault — it then navigates the vault like Claude Code (full traversal, no Drive dependency).

1. **Add a filesystem MCP server.** In Claude Desktop's connector/MCP settings, add a standard **filesystem** server scoped to your vault directory (`$MEMORY_VAULT_PATH` / the `AgentMemory/` folder).
2. **Paste [the context payload](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L19)** into a Claude **Project's** instructions (or the desktop custom instructions).
3. **Dogfood** (see below).

*Alternative:* skip the MCP server and use the **Google Drive connector** exactly like Claude.ai above (search-based; same payload).

## Antigravity

Antigravity is a local filesystem agent; it loads [the context payload](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L19) as the installed `agentmemory-context` rule — no manual paste. There are **two install channels**, one per scope:

- **Per-project (`--scope project`, default):** the rule lands in `<project>/.agents/rules/agentmemory-context.md`, scoped to that workspace. The installer dispatches it automatically (`install.sh` / `install.ps1` ship it on `--update`, refreshing alongside the other rules).
- **Global (`--scope user`):** the installer merges the same payload into `~/.gemini/GEMINI.md` — Antigravity 2.0's global rules file, applied across **every** workspace — so Antigravity picks up the vault everywhere with no per-project install. It runs only when `~/.gemini/` already exists, preserves your own `GEMINI.md` content, and is idempotent. Only the `agentmemory-context` payload goes global; the per-project harness operating contract stays per-project.

Unlike the read-only chat surfaces above, Antigravity is a **read-write working agent**: it may read *and* write the vault, following your entry conventions, exactly like Claude Code. Validated on **both the Antigravity CLI and the Antigravity IDE**: with `install.sh --scope user`, Antigravity resolved the vault via the global `~/.gemini/GEMINI.md` rule and recalled `_always-load/` entries correctly across multiple projects, with no per-project install. Dynamic session-start *recall* (vs. this static rule) is a future enhancement.

## Deferred surfaces *(post-FRIDAY — #28)*

**Gemini, ChatGPT, and Codex are not in v1.** Gemini + ChatGPT are chat-only bots with **no live file/search access** to the vault — a plain Gemini chat confirmed it *"can't access or browse your live Google Drive files"* — so the read model can't work yet; revisit when they gain agentic Drive/file access. Codex is deferred completely until FRIDAY lands. The same **context payload** will be reusable for whichever gains access.

## The dogfood (any surface)

Open a **fresh** chat/session and ask — with no priming:

> what's our commit-message convention?

**Pass:** it reaches the vault (a Drive *search* on Claude.ai; a filesystem *read* on Claude Desktop / Antigravity) and answers from `personal-private/_always-load/` — *"no `Co-Authored-By` trailer"* (and Conventional Commits) — not from general knowledge. **Fail:** a generic answer or "can't see the vault" → on Claude.ai confirm you're on the vault-owning Google account (and try the Project approach above); on Claude Desktop confirm the filesystem MCP server's vault path.

## Related

- [AgentMemory context payload](AgentMemory-Context-Payload) — reference for the payload's sections.
