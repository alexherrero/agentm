# How to use AgentMemory in any agent

> [!NOTE]
> **Status:** implemented
> **Plan:** PLAN.md (V4 #22) — cross-surface vault access, read-only v1.
> **Goal:** Make any agent surface (Claude.ai, Gemini, ChatGPT, Antigravity) read your GDrive-synced AgentMemory vault natively — so it already knows your conventions, projects, and decisions without you re-explaining. **Read-only:** surfaces read + query the vault; they never write to it (they suggest entries for you to paste in by hand).
> **Prereqs:** the AgentMemory vault synced to Google Drive (signed into the account that owns it); the context payload (`templates/agentmemory-context.md`, shipped in #22); and operator access to each surface's connector / Gem settings (the agent can't log into your accounts — connector setup is an operator action).

## Before you start (all surfaces)

The one thing you paste into every surface is **the context payload** → **[`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L19)**. Copy its body from **line 19** (`# Using my Agent Memory`) to the end — **skip the leading HTML comment** (operator-only notes). For what each section of **the context payload** means, see the [AgentMemory context payload reference](AgentMemory-Context-Payload).

Prereq for the Google-Drive surfaces: the vault is synced to Google Drive, and you're signed into the **Google account that owns it**.

| Surface | Status | How it reads the vault |
|---|---|---|
| Claude Code | ✅ built-in | SessionStart / UserPromptSubmit hooks — no paste needed |
| Claude.ai | ✅ validated | Google Drive connector (search) + **the context payload** |
| Gemini | ⬜ pending dogfood | custom Gem + native Drive access + **the context payload** |
| ChatGPT | deferred → v1.x | GDrive connector + **the context payload** |
| Antigravity | deferred → DC-7 | installed `agentmemory-context` rule |

## Claude.ai

1. **Connect Google Drive.** Settings → Connectors → enable **Google Drive** and finish the OAuth. This grants Claude *search* access to your whole Drive — **there is no folder to pin** (scoping comes from the payload).
2. **Paste [the context payload](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L19)** into Settings → Custom Instructions (applies to every chat) or a Claude **Project's** instructions.
3. **Dogfood** (see below).

*More reliable recall:* search-at-query-time depends on Claude choosing to search. To ground it, create a **Claude Project**, put **the context payload** in the Project instructions, and add the `personal-private/_always-load/` entries to the Project's knowledge.

## Gemini

1. **Create a custom Gem.** Gem manager → New Gem; name it (e.g. *AgentMemory*).
2. **Paste [the context payload](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L19)** into the Gem's **Instructions**.
3. **Confirm Drive access** — Gemini reads Drive natively for the signed-in account; make sure that's the vault-owning account.
4. **Dogfood** with the Gem selected (see below).

## ChatGPT *(deferred to v1.x)*

Same shape — enable ChatGPT's Google Drive connector and paste **the context payload** into Custom Instructions or a Project. Tracked as a follow-up; not part of read-only v1.

## Antigravity *(deferred — DC-7)*

Antigravity loads **the context payload** as an installed `agentmemory-context` rule (no manual paste). The installer wiring + its repo home are a later task.

## The dogfood (any surface)

Open a **fresh** chat/session (Gem selected, for Gemini) and ask — with no priming:

> what's our commit-message convention?

**Pass:** it reads the vault (you'll see a Drive search / cited file) and answers from `personal-private/_always-load/` — *"no `Co-Authored-By` trailer"* (and Conventional Commits) — not from general knowledge. **Fail:** a generic answer or "can't see the folder" → confirm you're on the vault-owning Google account (and for Claude.ai, try the Project approach above).

## Related

- [AgentMemory context payload](AgentMemory-Context-Payload) — reference for the payload's sections.
