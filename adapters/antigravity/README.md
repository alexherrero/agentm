# Antigravity adapter

Adapter for running agentm in [Antigravity](https://antigravity.google.com/). Since the V5 unbundling this adapter is **slim**: it ships the always-on operating-contract rules plus the shared utility skills. The phase-gated dev loop (setup/plan/work/review/release/bugfix) and the review sub-agents are no longer vendored by agentm — they're provided by the crickets developer-workflows / code-review plugins, which install their own Antigravity surface.

## What this adapter ships

| Claude Code | Antigravity | Purpose |
|---|---|---|
| `CLAUDE.md` pointer | `.agents/rules/harness.md` (`trigger: always_on`) | Always-on operating contract |
| — | `.agents/rules/agentmemory-context.md` (`trigger: always_on`) | AgentMemory vault context |
| `.claude/skills/<name>/SKILL.md` | `.agents/skills/<name>/SKILL.md` | Shared utility skill (doctor) |

The shared skill is delivered to `.agents/skills/` by `install.sh` / `install.ps1` (sourced from `adapters/claude-code/skills/`, the parity-enforced single copy). Antigravity reads that path natively per the Agent Skills standard.

## Why the dev loop isn't here (V5 unbundling)

agentm's repositioning in V5 is "storage-agnostic memory OS + plugin host". The phase-gated dev loop that used to live in `adapters/antigravity/workflows/` + `adapters/antigravity/skills/` (the `explorer` / `adversarial-reviewer` / `adversarial-reviewer-cross` review sub-agents) moved to the crickets **developer-workflows** and **code-review** plugins. A bare agentm install is intentionally unaware of that dev loop: it's optional, provided by crickets when installed, with no pointer and no requirement from agentm's side.

If you want the full plan/work/review/release loop in Antigravity, install the crickets developer plugins alongside agentm — they ship their own `.agents/` workflow + skill surface.

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
