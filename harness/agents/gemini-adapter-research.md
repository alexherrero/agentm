# Gemini CLI adapter research

**Status:** working document for Task 9. Delete once Task 9 lands.
**Date:** 2026-04-19
**Subject:** Google Gemini CLI (the `gemini` command shipped by Google, source at `github.com/google-gemini/gemini-cli`). Public subagent support was added in v0.38.1 (released 2026-04-15) — the adapter below assumes a recent enough version.

The four research questions below map 1:1 to Task 8's verification criterion in `.harness/PLAN.md`.

## (a) Slash commands

**Yes — Gemini CLI has first-class custom slash commands.**

**Location (precedence order; project overrides user):**

1. User: `~/.gemini/commands/*.toml` — available across all projects
2. Project: `<project-root>/.gemini/commands/*.toml` — version-controllable

**File format:** TOML.

**Schema:**

```toml
description = "One-line help-menu text (optional)"
prompt = """
The instruction sent to the model when the command is invoked.
Multi-line strings supported.
"""
```

- `prompt` is the **only required** field.
- `description` is optional but shown in `/help`.

**Naming & namespacing:**

- File path becomes command name: `<project>/.gemini/commands/test.toml` → `/test`.
- Subdirectories create namespaced commands with `:` separator: `<project>/.gemini/commands/git/commit.toml` → `/git:commit`.

**Argument substitution:**

- `{{args}}` placeholder — raw outside shell blocks, shell-escaped inside `!{...}` blocks.
- If no `{{args}}` appears, the full typed argument string is appended after the prompt separated by two newlines (sensible default).

**Dynamic injection inside prompt:**

- `!{cmd}` — shell execution, confirmation required.
- `@{path}` — file or directory content injection; respects `.gitignore` / `.geminiignore`; multimodal (images, PDFs, audio, video).

**Reload:** `/commands reload` to pick up edits without restarting.

**No collision with harness phase names.** Gemini's built-in slash commands are utilitarian (`/help`, `/memory`, `/tools`, `/compress`, `/bug`, `/chat`, `/theme`, `/commands`, `/quit`) — none clash with `/setup`, `/plan`, `/work`, `/review`, `/release`, `/bugfix`.

**Source:** [Gemini CLI docs — Custom commands](https://geminicli.com/docs/cli/custom-commands/); [Google Cloud blog — Custom slash commands](https://cloud.google.com/blog/topics/developers-practitioners/gemini-cli-custom-slash-commands).

**Implication for harness:** phase entrypoints map 1:1 to `.toml` slash commands. No renaming needed (unlike Codex).

## (b) Subagents

**Yes — Gemini CLI has first-class subagent support as of v0.38.1 (April 2026).** This is the closest of the three target surfaces to Claude Code's sub-agent model.

**Location:**

- Project: `.gemini/agents/*.md`
- User: `~/.gemini/agents/*.md`

**File format:** Markdown with YAML frontmatter (same shape as Claude Code).

**Frontmatter schema:**

| Field | Required | Type | Notes |
|---|---|---|---|
| `name` | ✅ | string | Lowercase slug (letters, numbers, hyphens, underscores). |
| `description` | ✅ | string | Used by the main agent to decide when to delegate. |
| `kind` | — | string | `local` (default) or `remote`. |
| `tools` | — | array | Allowlist. Supports wildcards (`*`, `mcp_*`, `mcp_<server>_*`). |
| `mcpServers` | — | object | Inline MCP server configs isolated to this agent. |
| `model` | — | string | Model override for this subagent. |
| `temperature` | — | number | 0.0–2.0; default 1. |
| `max_turns` | — | number | Default 30. |
| `timeout_mins` | — | number | Default 10. |

The markdown body becomes the subagent's system prompt.

**Invocation:**

- **Automatic delegation** — main agent picks based on `description` matching task intent.
- **Explicit forcing** — `@agent-name <prompt>` at the start of a message.

**Context isolation:** each subagent runs in its own context loop with independent conversation history. This IS a fresh-context guarantee (unlike Antigravity skills or Codex subagents, which inherit workspace state).

**Recursion protection:** subagents cannot call other subagents even with `tools: ["*"]` wildcard.

**Built-in subagents present by default:** `codebase_investigator`, `cli_help`, `generalist`, `browser_agent`. Our `explorer` overlaps with `codebase_investigator` but stays — our version has the harness-specific output contract (ANSWER / EVIDENCE / CAVEATS).

**Source:** [Gemini CLI docs — Subagents](https://geminicli.com/docs/core/subagents/); [Google Developers Blog — Subagents have arrived](https://developers.googleblog.com/subagents-have-arrived-in-gemini-cli/); [GitHub Discussion — v0.38.1](https://github.com/google-gemini/gemini-cli/discussions/25562).

**Implication for harness:** the four sub-agents (`explorer`, `adversarial-reviewer`, `adversarial-reviewer-cross`, `documenter`) map 1:1 to `.gemini/agents/*.md`. Tool allowlists + fresh context give us everything Claude Code's sub-agents provide. This is the strongest adapter surface of the three.

## (c) Agent Skills

**Yes — Gemini CLI supports the open Agent Skills standard.**

**Location (precedence):**

1. Workspace: `.gemini/skills/` **or** `.agents/skills/` (both accepted)
2. User: `~/.gemini/skills/` **or** `~/.agents/skills/`
3. Extension-bundled skills

**`.agents/skills/` is a shared alias** across Codex, Gemini CLI, and other tools following the convention. This is load-bearing for the harness: **the `dependabot-fixer` skill delivered to `.agents/skills/` by the Codex adapter block is automatically visible to Gemini.** No duplication needed.

**Format:** `<skill-name>/SKILL.md` + optional bundled assets (scripts, references, templates).

**Activation flow:**

1. Skill `name` + `description` are injected into the system prompt at session start.
2. Gemini autonomously calls an `activate_skill` tool when a task matches.
3. User confirms via UI prompt.
4. On approval, `SKILL.md` body and folder access are granted for the session.

**Source:** [Gemini CLI docs — Agent Skills](https://geminicli.com/docs/cli/skills/).

**Implication for harness:** no separate Gemini skill files needed. The Codex adapter already ships `dependabot-fixer/SKILL.md` to `.agents/skills/`, and Gemini reads that location natively.

## (d) Instruction-loading order

**GEMINI.md with `AGENTS.md` as a configurable alias.**

**Load order (concatenated, sent with every prompt):**

1. Global: `~/.gemini/GEMINI.md`
2. Workspace: walk from cwd up to trusted root (project root or `~/`); each directory checks for context files in order of `context.fileName` setting.
3. Just-in-time: when a tool accesses a file or directory, scan that path and ancestors up to the trusted root.

**`context.fileName` in `~/.gemini/settings.json` or `.gemini/settings.json`:**

```json
{
  "context": {
    "fileName": ["AGENTS.md", "GEMINI.md", "CONTEXT.md"]
  }
}
```

This lets Gemini treat the existing repo-root `AGENTS.md` as a native context source. The harness already ships `AGENTS.md` at the repo root — no pointer file needed on the Gemini side if we document the settings tweak (or ship the settings override).

**Size limits:** not documented. No frontmatter required; plain markdown.

**Inspection:** `/memory show` prints the concatenated context.

**Source:** [Gemini CLI docs — GEMINI.md](https://geminicli.com/docs/cli/gemini-md/); [Gemini CLI configuration](https://geminicli.com/docs/reference/configuration/).

**Implication for harness:** repo-root `AGENTS.md` is already the universal contract (read by Antigravity, Codex, and — via `context.fileName` — Gemini). Adapter should ship a minimal `settings.json` fragment under `.gemini/` that adds `AGENTS.md` to `context.fileName`, or document the one-line manual addition in the README. Decision in Open Question #2.

## Layout proposal for Task 9

```
adapters/gemini/
├── README.md                                   # Adapter docs: mapping table, context.fileName wiring, subagent UX
├── commands/                                   # → target's .gemini/commands/
│   ├── setup.toml
│   ├── plan.toml
│   ├── work.toml
│   ├── review.toml
│   ├── release.toml
│   └── bugfix.toml
├── agents/                                     # → target's .gemini/agents/
│   ├── explorer.md
│   ├── adversarial-reviewer.md
│   ├── adversarial-reviewer-cross.md
│   └── documenter.md
└── settings.json                               # → target's .gemini/settings.json (merged, not overwritten)
```

**No `skills/` directory** — `dependabot-fixer` is already delivered to `.agents/skills/` by the Codex adapter block and Gemini reads that path natively (see §c).

**Destinations:**

- `adapters/gemini/commands/*.toml` → target's `.gemini/commands/`
- `adapters/gemini/agents/*.md` → target's `.gemini/agents/`
- `adapters/gemini/settings.json` → target's `.gemini/settings.json` (merged via `jq`, same pattern as `.claude/settings.json` hook merge in install.sh)

**Install.sh wiring needed (Task 9):**

```bash
# After the Codex block, add:
mkdir -p .gemini/commands .gemini/agents
for f in "$HARNESS_ROOT"/adapters/gemini/commands/*.toml; do
  [[ -e "$f" ]] || continue
  cp_managed "$f" ".gemini/commands/$(basename "$f")"
done
for f in "$HARNESS_ROOT"/adapters/gemini/agents/*.md; do
  [[ -e "$f" ]] || continue
  cp_managed "$f" ".gemini/agents/$(basename "$f")"
done
# settings.json: merge context.fileName addition (requires jq, like --hooks block)
# ... deferred to Task 9 implementation; simple overlay if jq present, README note otherwise.
```

**File count:** 6 TOML + 4 markdown + 1 JSON + 1 README = 12 files.

## Open questions

### Open question #1: Should our `explorer` subagent replace or coexist with Gemini's built-in `codebase_investigator`?

**Options:**

- **(A) Coexist** — ship `explorer.md`; both subagents available. Harness phases explicitly dispatch `@explorer`. Users who want the Gemini-native one can still use `@codebase_investigator`.
- **(B) Rename ours to `codebase_investigator` and override** — replaces the built-in with our harness-aware version.
- **(C) Skip ours; use `codebase_investigator`** — delete `explorer.md` from the adapter, route phase skills to dispatch `@codebase_investigator` on Gemini.

**Answer before Task 9:** **(A) — coexist.**

**Why:**
1. Our `explorer` has a specific output contract (ANSWER / EVIDENCE / CAVEATS with `file:line`) that phase commands rely on. Gemini's built-in makes no such guarantee.
2. Overriding a built-in is surprising to users coming from plain Gemini CLI.
3. The cost of coexistence is one extra subagent definition — cheap.
4. Phase TOML commands dispatch `@explorer` explicitly by name, so there's no ambiguity.

### Open question #2: Ship a `.gemini/settings.json` overlay or document the one-line manual addition?

**Options:**

- **(A) Ship `adapters/gemini/settings.json` with `context.fileName: ["AGENTS.md", "GEMINI.md"]`, merge via jq in install.sh** — matches the `--hooks` block pattern for `.claude/settings.json`. Guarantees AGENTS.md is loaded.
- **(B) Skip it; document in the README** — zero install surface; user opts in.

**Answer before Task 9:** **(A) — ship it.**

**Why:**
1. AGENTS.md is the universal contract; it MUST load for the Gemini adapter to behave identically to the others. "Works if the user remembers to configure it" is a footgun.
2. Install.sh already has a jq-based merge pattern (`.claude/settings.json` hooks) — lift the same helper, idempotent-by-command-signature.
3. If jq isn't present, fall back to "created settings.json if missing; otherwise print the snippet and the user adds it manually" — matches the --hooks fallback behavior.
4. Settings.json is user-editable; we merge rather than overwrite.

### Open question #3: Handle the `adversarial-reviewer-cross` self-reference.

Our cross-model reviewer **is** Gemini — it shells out to `gemini` CLI with a different model flag. On the Gemini CLI adapter, invoking `@adversarial-reviewer-cross` asks Gemini to shell out to Gemini for a second opinion. That's… a mirror.

**Options:**

- **(A) Ship `adversarial-reviewer-cross.md` unchanged** — the script already uses a different model (`-m gemini-3.1-pro-preview`) than the session default. Cross-model can still mean cross-*version*-of-Gemini, which catches some same-model failure modes.
- **(B) Don't ship it on Gemini** — the adapter has only three sub-agents (`explorer`, `adversarial-reviewer`, `documenter`). Phase review skill dispatches only in-process reviewer.
- **(C) Ship it but have it shell out to a non-Gemini model (Claude, Codex)** — true cross-model, but requires detecting which CLI is available.

**Answer before Task 9:** **(A) — ship it, unchanged.**

**Why:**
1. Gemini-3.1-pro-preview vs. the session default is still a real model difference. Same family, different checkpoint and reasoning effort — catches a different slice of defects than same-instance-of-same-model review.
2. Simpler to maintain. Option (C) introduces a CLI-detection branch in `cross-review.sh` that complicates the script for marginal value.
3. Users who want true cross-vendor review can edit `cross-review.sh` locally to invoke `claude` or `codex` instead — the abstraction is in place.
4. Parity count stays clean (four sub-agents across all adapters).

## Summary for Task 9 implementation

- 12 files to create under `adapters/gemini/` (6 TOML commands + 4 markdown subagents + 1 settings.json + 1 README).
- 2 install.sh cp_managed blocks to add + 1 jq-based settings.json merge (reuse --hooks pattern).
- **No skills/ directory** — `dependabot-fixer` is already delivered to `.agents/skills/` by the Codex adapter block.
- Phase commands as native `/setup` / `/plan` / `/work` / `/review` / `/release` / `/bugfix` slash commands (no prefix; no built-in collisions).
- Sub-agents as native Gemini subagents with YAML frontmatter (same shape as Claude Code — straight port).
- `context.fileName` in settings.json adds `AGENTS.md` to the auto-loaded context hierarchy.

Gemini CLI is the richest target surface of the three additional adapters (subagents with tool allowlists + fresh context + slash commands + skills). The adapter is mostly a straight port from the Claude Code adapter with format translations (markdown → TOML for commands; Claude Code sub-agent frontmatter → Gemini sub-agent frontmatter).
