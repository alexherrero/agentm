# Gemini CLI adapter

Adapter for running agentm in [Google Gemini CLI](https://geminicli.com/). Since the V5 unbundling this adapter is **slim**: it ships the `AGENTS.md` context wiring (`settings.json`) plus the shared utility skills. The phase-gated dev loop (setup/plan/work/review/release/bugfix) and the review sub-agents are no longer vendored by agentm — they're provided by the crickets developer-workflows / code-review plugins.

## What this adapter ships

| Claude Code | Gemini CLI | Purpose |
|---|---|---|
| `CLAUDE.md` pointer | `.gemini/settings.json` + repo-root `AGENTS.md` | Operating contract (Gemini loads via `context.fileName`) |
| `.claude/skills/<name>/` | `.agents/skills/<name>/` | Shared utility skill (doctor) — delivered by `install.sh` per the Agent Skills standard |

The shared skill is delivered to `.agents/skills/` by `install.sh` / `install.ps1` (sourced from `adapters/claude-code/skills/`, the parity-enforced single copy), and Gemini reads that path natively — no duplication in a `.gemini/` skills dir.

## Why the dev loop isn't here (V5 unbundling)

agentm's repositioning in V5 is "storage-agnostic memory OS + plugin host". The phase commands that used to live in `adapters/gemini/commands/` (TOML) and the sub-agents in `adapters/gemini/agents/` (`explorer` / `adversarial-reviewer` / `adversarial-reviewer-cross`) moved to the crickets **developer-workflows** and **code-review** plugins. A bare agentm install is intentionally unaware of that dev loop: it's optional, provided by crickets when installed, with no pointer and no requirement from agentm's side.

If you want the full plan/work/review/release loop in Gemini CLI, install the crickets developer plugins alongside agentm.

## Layout

```
adapters/gemini/
├── README.md                                   (this file)
└── settings.json                               (→ target's .gemini/settings.json)
```

No `commands/`, `agents/`, or `skills/` directory — the dev loop is crickets-provided, and shared skills are delivered to `.agents/skills/` (read natively by Gemini).

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

**If you already have `.gemini/settings.json`**, neither `install.sh` nor `install.ps1` will overwrite it. Add `"AGENTS.md"` to your `context.fileName` array manually:

```json
{
  "context": {
    "fileName": ["AGENTS.md", "GEMINI.md"]
  }
}
```

## Single source of truth

The shared skills here point back to their canonical specs under [`harness/skills/`](../../harness/skills/). If an adapter copy drifts from the canonical spec, the canonical spec wins — file an issue or fix it. `scripts/check-parity.sh` pins the canonical set.
