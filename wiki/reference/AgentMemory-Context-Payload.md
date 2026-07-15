# AgentMemory context payload reference

The AgentMemory context payload is the one "how to use my memory" brief you paste into each agent surface — Claude.ai custom instructions, a Gemini Gem's system instructions, the Antigravity rule. The canonical copy lives in the repo at `templates/agentmemory-context.md`. A self-describing twin sits at `<vault>/_meta/how-to-use-agentmemory.md`, so an agent that reaches the vault finds its own usage instructions waiting. Read/write posture is surface-scoped (DC-2): chat surfaces read and query the vault but never write to it; filesystem agents you actually run (Claude Code, Antigravity) may write. On a read-only surface, when an agent wants to capture something, it suggests a paste-ready entry and you file it in Obsidian by hand.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| Where is the canonical payload? | [`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md) (agentm) — the source of truth (DC-5). |
| Where is the self-describing copy? | `<vault>/_meta/how-to-use-agentmemory.md` (written outside the repo, not in git). |
| Which surfaces consume it? | Claude.ai / ChatGPT, Gemini, Antigravity — see [Use AgentMemory in any agent surface](Use-AgentMemory-In-Any-Agent). Claude Code instead receives it via SessionStart/UserPromptSubmit hooks (no paste needed). |
| Is it host-specific? | No — host-agnostic; no Claude-Code-specific assumptions. |
| Read or write? | Surface-scoped (DC-2): chat surfaces (Claude.ai, Claude Desktop) are read-only — query the vault, never write. Filesystem agents you actually run (Claude Code, Antigravity) may write. Capture on read-only surfaces = suggest a paste-ready entry. |
| What do I actually paste? | The body from [line 21 onward](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L21) (`# Using my Agent Memory`); the leading HTML comment is operator-only instructions. |

## Payload sections

The required sections of the payload, in order, as they appear in [`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md).

| Section (heading in template) | Covers |
|---|---|
| Intro (`# Using my Agent Memory`) | Names the vault (your vault root, a GDrive-synced Obsidian vault); states read-the-vault-before-own-memory and the read-only stance up front. |
| Where the vault is, on your surface | Per-surface path resolution: Claude.ai / ChatGPT → GDrive connector + pinned vault root; Gemini → native Workspace/Drive access; Antigravity → installer-configured path; Claude Code / local → `MEMORY_VAULT_PATH`, falling back to `.agentm-config.json::vault_path` when the env var is unset (SessionStart hooks do not receive `MEMORY_VAULT_PATH` on user-scope installs, so vault-aware hooks resolve via `env → .agentm-config.json::vault_path → none`). |
| Folder map — what's where | `personal/_always-load/` (global conventions, read first), `projects/<slug>/` (`_index.md` / `decisions/` / `open-questions/` / `_harness/`), `external/<slug>/` (third-party projects under review/mentoring — same shape, kept out of `projects/`), `_idea-incubator/<slug>/`, `_inbox/`, `_meta/`. |
| How to read it (priority order) | 1) always-load first → 2) project context (`_index.md` + `decisions/`) → 3) query by topic; vault wins over the model's general knowledge. |
| Reading entries correctly | Markdown + YAML frontmatter; core trio `kind` + `status` + `created`; kebab-case slugs/tags; `status: active` vs `superseded`; follow `[[wikilinks]]`. |
| Your read/write posture | Surface-scoped (DC-2): chat surfaces read + query freely, never write — capture = suggest a paste-ready entry and name its home. Filesystem agents (Claude Code, Antigravity) may write directly. GDrive sync means you see last-synced state (flag, don't guess). |

## Related

- [Use AgentMemory in any agent](Use-AgentMemory-In-Any-Agent) — the setup recipe for every surface (Claude.ai · Gemini · ChatGPT · Antigravity).
