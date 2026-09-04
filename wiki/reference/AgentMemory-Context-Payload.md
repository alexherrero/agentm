# AgentMemory context payload reference

The AgentMemory context payload is the "how to use my memory" brief. You paste it into each agent surface. These surfaces include Claude.ai custom instructions. They include a Gemini Gem's system instructions. They include the Antigravity rule. The canonical copy lives in the repo at `templates/agentmemory-context.md`. A self-describing twin sits at `<vault>/Agent/_meta/how-to-use-agentmemory.md`. An agent that reaches the vault finds its own usage instructions waiting. Nothing regenerates that twin, so it is copied over by hand whenever the template changes. The read/write posture is surface-scoped (DC-2). Chat surfaces read and query the vault. They never write to it. Filesystem agents you run (Claude Code, Antigravity) may write. On a read-only surface, an agent suggests a paste-ready entry when it wants to capture something. You file it in Obsidian by hand.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| Where is the canonical payload? | [`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md) (agentm) — the source of truth (DC-5). |
| Where is the self-describing copy? | `<vault>/Agent/_meta/how-to-use-agentmemory.md` (written outside the repo, not in git; copied by hand — no installer step deploys it). |
| Which surfaces consume it? | Claude.ai / ChatGPT, Gemini, Antigravity — see [Use AgentMemory in any agent surface](Use-AgentMemory-In-Any-Agent). The Antigravity mirror at `adapters/antigravity/rules/agentmemory-context.md` is also merged into `~/.gemini/GEMINI.md` by `install.sh`, so it is a deployment surface rather than a copy. Claude Code instead receives it via SessionStart/UserPromptSubmit hooks (no paste needed). |
| Is it host-specific? | No — host-agnostic; no Claude-Code-specific assumptions. |
| Read or write? | Surface-scoped (DC-2): chat surfaces (Claude.ai, Claude Desktop) are read-only — query the vault, never write. Filesystem agents you actually run (Claude Code, Antigravity) may write. Capture on read-only surfaces = suggest a paste-ready entry. |
| What do I actually paste? | The body from [line 23 onward](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md#L23) (`# Using my Agent Memory`); the leading HTML comment is operator-only instructions. |

## Payload sections

The payload contains required sections. They appear in order. They appear exactly as written in [`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md).

| Section (heading in template) | Covers |
|---|---|
| Intro (`# Using my Agent Memory`) | Names the vault (your vault root, a GDrive-synced Obsidian vault); states read-the-vault-before-own-memory and the read-only stance up front. |
| Where the vault is, on your surface | Per-surface path resolution: Claude Code / local → `MEMORY_VAULT_PATH`, falling back to `.agentm-config.json::vault_path` when the env var is unset (SessionStart hooks do not receive `MEMORY_VAULT_PATH` on user-scope installs, so vault-aware hooks resolve via `env → .agentm-config.json::vault_path → none`); Antigravity → installer-configured path; Claude.ai / ChatGPT → GDrive connector (whole-Drive search; the payload is what scopes it to the vault folder); Gemini → native Workspace/Drive access. |
| Folder map — what's where | the always-load tier read as one — `standards/` (the filing contract) plus `Agent/memory/_always-load/` (the house voice), read first — and `Projects/<slug>/` (`_index.md` / `decisions/` / `_harness/`) at the vault root, beside the agent's own `Agent/` half: `Agent/memory/` (the six classes — `semantic/`, `procedural/` and `episodic/` written from observation, `entities/`, `crystallized/` and `mocs/` derived from them), `Agent/desk/` (work in flight), `Agent/_meta/` (machine files). States that there is no inbox: a capture files into its class directory at `status: unfiled` / `filing_confidence: low` and is searchable immediately, ranked lower until filing promotes it — the metadata is the inbox, and an `_inbox/` directory in an older vault is legacy content. |
| How to read it (priority order) | 1) the always-load tier first (`standards/` + `Agent/memory/_always-load/`) → 2) project context (`Projects/<slug>/_index.md` + `decisions/`) → 3) query by topic; vault wins over the model's general knowledge. |
| Reading entries correctly | Markdown + YAML frontmatter; `status` + `created` plus exactly one of `type` (a memory) or `kind` (an infrastructure record) — never both; kebab-case slugs/tags; `status: active` vs `superseded` vs `unfiled` (captured but not yet filed — content awaiting confirmation, not content to skip); follow `[[wikilinks]]`. |
| Your read/write posture | Surface-scoped (DC-2): chat surfaces read + query freely, never write — capture = suggest a paste-ready entry and name its home (the always-load tier for a global rule, `Projects/<slug>/` for project context). Filesystem agents (Claude Code, Antigravity) may write directly. GDrive sync means you see last-synced state (flag, don't guess). |

## Related

- [Use AgentMemory in any agent](Use-AgentMemory-In-Any-Agent) — This is the setup recipe for every surface (Claude.ai · Gemini · ChatGPT · Antigravity).
