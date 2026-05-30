# AgentMemory context payload reference

> [!NOTE]
> **Status:** implemented
> **Plan:** PLAN.md (V4 #22) task 1 — canonical context payload + self-describing vault copy.
> **Source of truth:** [`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md) — pasteable body starts at [line 19](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L19) (`# Using my Agent Memory`); the lines above are operator instructions, not part of the paste.

The canonical "how-to-use-AgentMemory" payload at `templates/agentmemory-context.md` — the single source of truth the operator pastes into each agent surface (Claude.ai custom instructions, a Gemini Gem's system instructions, the Antigravity rule). A self-describing copy lives at `<vault>/_meta/how-to-use-agentmemory.md` so an agent that reaches the vault finds its own usage instructions. Read-only (v1): agents read + query the vault; they never write — capture = suggest an entry for the operator to paste into Obsidian by hand.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| Where is the canonical payload? | [`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md) (agentm) — the source of truth (DC-5). |
| Where is the self-describing copy? | `<vault>/_meta/how-to-use-agentmemory.md` (written outside the repo, not in git). |
| Which surfaces consume it? | Claude.ai / ChatGPT, Gemini, Antigravity — see [Use AgentMemory in any agent surface](../how-to/Use-AgentMemory-In-Any-Agent). Claude Code instead receives it via SessionStart/UserPromptSubmit hooks (no paste needed). |
| Is it host-specific? | No — host-agnostic; no Claude-Code-specific assumptions. |
| Read or write? | Read-only (v1, DC-2): surfaces read + query; never write. Capture = suggest a paste-ready entry. |
| What do I actually paste? | The body from [line 19 onward](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L19) (`# Using my Agent Memory`); the leading HTML comment is operator-only instructions. |

## Payload sections

The required sections of the payload, in order, as they appear in [`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md).

| Section (heading in template) | Covers |
|---|---|
| Intro (`# Using my Agent Memory`) | Names the vault (`AgentMemory/`, a GDrive-synced Obsidian vault); states read-the-vault-before-own-memory and the read-only stance up front. |
| Where the vault is, on your surface | Per-surface path resolution: Claude.ai / ChatGPT → GDrive connector + pinned `AgentMemory/`; Gemini → native Workspace/Drive access; Antigravity → installer-configured path; Claude Code / local → `MEMORY_VAULT_PATH`. |
| Folder map — what's where | `personal-private/_always-load/` (global conventions, read first), `projects/<slug>/` (`_index.md` / `decisions/` / `open-questions/` / `_harness/`), `_idea-incubator/<slug>/`, `_inbox/`, `_meta/`. |
| How to read it (priority order) | 1) always-load first → 2) project context (`_index.md` + `decisions/`) → 3) query by topic; vault wins over the model's general knowledge. |
| Reading entries correctly | Markdown + YAML frontmatter; core trio `kind` + `status` + `created`; kebab-case slugs/tags; `status: active` vs `superseded`; follow `[[wikilinks]]`. |
| Your boundary — READ-ONLY | Read + query freely, never write; capture = suggest a paste-ready entry and name its home; GDrive sync means you see last-synced state (flag, don't guess). |

## Related

- [Use AgentMemory in any agent](../how-to/Use-AgentMemory-In-Any-Agent) — the setup recipe for every surface (Claude.ai · Gemini · ChatGPT · Antigravity).
