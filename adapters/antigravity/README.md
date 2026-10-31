# Antigravity adapter

Adapter for running agentm in [Antigravity](https://antigravity.google.com/). Since the V5 unbundling this adapter is **slim**: it ships the always-on operating-contract rules plus the shared utility skills. The phase-gated dev loop (setup/plan/work/review/release/bugfix) and the review sub-agents are no longer vendored by agentm — they're provided by the crickets development-lifecycle / code-review plugins, which install their own Antigravity surface.

## What this adapter ships

| Claude Code | Antigravity | Purpose |
|---|---|---|
| `CLAUDE.md` pointer | `.agents/rules/harness.md` (`trigger: always_on`) | Always-on operating contract |
| — | `.agents/rules/agentmemory-context.md` (`trigger: always_on`) | AgentMemory vault context |
| `.claude/skills/<name>/SKILL.md` | `.agents/skills/<name>/SKILL.md` | Shared utility skill (doctor) |
| — | `agy mcp add agentmemory …` (user level, by `install.sh`) | The `memory_search` and `memory_capture` tools |

The MCP registration happens only when `~/.gemini/` exists, `agy` is on the path and the memory daemon answers `/health` (the daemon is macOS-only, so `install.ps1` has no counterpart). It uses the daemon's own `daemon.port`.

The shared skill is delivered to `.agents/skills/` by `install.sh` / `install.ps1` (sourced from `adapters/claude-code/skills/`, the parity-enforced single copy). Antigravity reads that path natively per the Agent Skills standard.

## Why the dev loop isn't here (V5 unbundling)

agentm's repositioning in V5 is "storage-agnostic memory OS + plugin host". The phase-gated dev loop that used to live in `adapters/antigravity/workflows/` + `adapters/antigravity/skills/` (the `explorer` / `adversarial-reviewer` / `adversarial-reviewer-cross` review sub-agents) moved to the crickets **development-lifecycle** and **code-review** plugins. A bare agentm install is intentionally unaware of that dev loop: it's optional, provided by crickets when installed, with no pointer and no requirement from agentm's side.

If you want the full plan/work/review/release loop in Antigravity, install the crickets developer plugins alongside agentm. `agy` plugins have no commands folder, so crickets ships each phase as a skill. A bare `/plan` is agy's own planning mode; type `/development-lifecycle:plan` for the crickets one.

## What Antigravity does not get yet

Claude Code gets automatic recall from agentm's hooks (the session-start brief and the per-prompt recall). Those are not registered on Antigravity. `agy` 1.4 has the events they need, but the session-start hook writes a crash-recovery marker naming the host's transcript, which a later job reflects on in Claude Code's transcript format, and an `agy` transcript is a different one. Until that is settled, Antigravity reads the vault through the always-on rule and searches it with the `memory_search` tool when it decides to.

## Layout

```
adapters/antigravity/
├── README.md                             (this file)
└── rules/
    ├── harness.md                        (trigger: always_on — operating contract)
    └── agentmemory-context.md            (trigger: always_on — AgentMemory vault context)
```

`install.sh` (POSIX) or `install.ps1` (Windows/PowerShell 7+) copies the rules to the target's `.agents/rules/` and delivers the shared skills to `.agents/skills/`, with the same managed-file semantics as the Claude Code adapter: refreshed on `--update` / `-Update`, preserved on fresh install if already present.

## Single source of truth

The shared skills here point back to their canonical specs under [`harness/skills/`](../../harness/skills/). If an adapter copy drifts from the canonical spec, the canonical spec wins — file an issue or fix it. `scripts/check-parity.sh` pins the canonical set.
