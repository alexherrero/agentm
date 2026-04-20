# Gemini CLI adapter

Full-parity adapter for running agentic-harness in [Google Gemini CLI](https://geminicli.com/). Every phase command, sub-agent, and skill that Claude Code users get as slash commands and sub-agents is available here via Gemini CLI's native primitives.

## Why Gemini is the richest target surface

Of the four adapter targets (Claude Code, Antigravity, Codex, Gemini), **Gemini CLI has the closest parity to Claude Code's native surface**:

- **Native custom slash commands** (TOML) — phase entrypoints map 1:1 with no renaming.
- **Native subagents with fresh context + tool allowlists** — same isolation guarantees as Claude Code's sub-agents.
- **Shared `.agents/skills/` convention** — `dependabot-fixer` is reused from the Codex adapter's install (no duplication).
- **AGENTS.md support** via `context.fileName` setting — the universal harness contract loads natively.

This means the adapter is a near-straight port from Claude Code with format translations (markdown commands → TOML commands; Claude Code sub-agent frontmatter → Gemini sub-agent frontmatter).

## Surface mapping

| Claude Code | Gemini CLI | Purpose |
|---|---|---|
| `.claude/commands/*.md` | `.gemini/commands/*.toml` | Phase entrypoints (setup/plan/work/review/release/bugfix) |
| `.claude/agents/*.md` | `.gemini/agents/*.md` | Sub-agents (explorer, adversarial-reviewer, adversarial-reviewer-cross, documenter) |
| `.claude/skills/dependabot-fixer/` | `.agents/skills/dependabot-fixer/` | Project skill (shared convention — delivered by Codex adapter block) |
| `CLAUDE.md` pointer | `.gemini/settings.json` + repo-root `AGENTS.md` | Operating contract (Gemini loads via `context.fileName`) |

## Layout

```
adapters/gemini/
├── README.md                                   (this file)
├── commands/                                   (→ target's .gemini/commands/)
│   ├── setup.toml
│   ├── plan.toml
│   ├── work.toml
│   ├── review.toml
│   ├── release.toml
│   └── bugfix.toml
├── agents/                                     (→ target's .gemini/agents/)
│   ├── explorer.md
│   ├── adversarial-reviewer.md
│   ├── adversarial-reviewer-cross.md
│   └── documenter.md
└── settings.json                               (→ target's .gemini/settings.json)
```

No `skills/` directory — `dependabot-fixer` is delivered to `.agents/skills/` by the Codex adapter block in `install.sh`, and Gemini reads that path natively per the Agent Skills spec.

## Invocation

From within Gemini CLI, phase commands are native slash commands:

- **Setup:** `/setup`
- **Plan:** `/plan <your brief>`
- **Work:** `/work` (or `/work task 3`)
- **Review:** `/review`
- **Release:** `/release`
- **Bugfix:** `/bugfix <bug report>`

Sub-agents dispatch automatically via the main agent's judgment, or explicitly with `@agent-name` at the start of a prompt:

- `@explorer Find where the auth middleware is registered.`
- `@adversarial-reviewer` (invoked by the `/review` command, but you can call directly)
- `@documenter` (invoked by phase commands at their boundaries)

Hot-reload commands after editing: `/commands reload`.

## settings.json — AGENTS.md loading

The shipped `settings.json` sets:

```json
{
  "context": {
    "fileName": ["AGENTS.md", "GEMINI.md"]
  }
}
```

This makes Gemini CLI treat the repo-root `AGENTS.md` (installed by the harness) as a first-class context file, same as GEMINI.md. No separate GEMINI.md is required — though users can add one for Gemini-specific overrides.

**If you already have `.gemini/settings.json`**, `install.sh` will not overwrite it. Add `"AGENTS.md"` to your `context.fileName` array manually:

```json
{
  "context": {
    "fileName": ["AGENTS.md", "GEMINI.md"]
  }
}
```

## Coexistence with Gemini's built-in subagents

Gemini CLI ships with built-in subagents (`codebase_investigator`, `cli_help`, `generalist`, `browser_agent`). Our `explorer` subagent **coexists** with `codebase_investigator` rather than replacing it — ours has a specific output contract (`ANSWER` / `EVIDENCE` / `CAVEATS` with `file:line` references) that phase commands depend on.

If you prefer the Gemini-native explorer, both remain available:
- `@explorer` — harness-specific, structured output
- `@codebase_investigator` — Gemini built-in, free-form

## Cross-model reviewer note

`adversarial-reviewer-cross` shells out to `gemini -m gemini-3.1-pro-preview` via `.harness/scripts/cross-review.sh`. On a Gemini-CLI-hosted session, this means Gemini invoking Gemini — **cross-version rather than cross-vendor**. Gemini-3.1-pro-preview vs the session default model is still a real model difference; it catches a different slice of defects than same-instance-of-same-model review.

Users who want true cross-vendor review can edit `.harness/scripts/cross-review.sh` locally to invoke `claude` or `codex` instead of `gemini`. The script abstraction remains.

## Single source of truth

Every command and subagent here points back to the canonical spec under [`harness/phases/`](../../harness/phases/), [`harness/pipelines/`](../../harness/pipelines/), or [`harness/agents/`](../../harness/agents/). If an adapter file drifts from the canonical spec, the canonical spec wins — file an issue or fix it.

## Re-audit hook

If Gemini CLI ships first-class per-file verification hooks (matchers on Write/Edit events, not just generic lifecycle events), revisit the adapter and consider shipping a hook config — per the re-audit principle ([principles.md §6](../../harness/principles.md)).
