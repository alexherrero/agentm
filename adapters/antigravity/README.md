# Antigravity adapter

Full-parity adapter for running agentm in [Antigravity](https://antigravity.google.com/). Every phase command, sub-agent, and skill that Claude Code users get as slash commands and sub-agents is available here as Antigravity workflows and skills.

## Why full parity (was: README-only)

The original version of this adapter was a single `README.md` pointing users at [`AGENTS.md`](../../AGENTS.md) and telling them to invoke phases by prompt (*"run the plan phase per `harness/phases/02-plan.md`"*). That worked, but:

- **Discovery was bad.** The user had to know the phase spec paths and remember the invocation shape.
- **Non-negotiables lived only in the prose.** No always-on rule kept the project operating contract in scope at every turn — it depended on the agent re-reading `AGENTS.md`.
- **Sub-agents weren't surfaced.** The `explorer`, `adversarial-reviewer`, and cross-model reviewer had no native handle; the user had to know to ask the agent to "dispatch the reviewer" — and Antigravity had no native sub-agent primitive to route to anyway.

Full parity fixes all three. Antigravity's native surface maps 1:1 to the Claude Code adapter:

| Claude Code | Antigravity | Purpose |
|---|---|---|
| `.claude/commands/*.md` | `.agent/workflows/*.md` | Phase entrypoints (setup/plan/work/review/release/bugfix) |
| `.claude/agents/*.md` | `.agent/skills/<name>/SKILL.md` | Dispatchable capabilities (explorer, adversarial-reviewer, adversarial-reviewer-cross, documenter) |
| `.claude/skills/*/SKILL.md` | `.agent/skills/<name>/SKILL.md` | Project skills (dependabot-fixer) |
| `CLAUDE.md` pointer | `.agent/rules/harness.md` (`trigger: always_on`) | Always-on operating contract |

The **trade-off:** Antigravity has no native distinction between "sub-agent" and "skill". Both dispatch as skills. This is actually fine — the sub-agents' value is scoped dispatch with a narrow contract, which is exactly what a skill is. The one thing we lose is Claude Code's fresh-context guarantee on sub-agent dispatch (skills share context with the caller). Mitigation: the adversarial-reviewer skills explicitly instruct "do not read the implementer's reasoning trace" — enforced by discipline rather than a context boundary.

## Layout

```
adapters/antigravity/
├── README.md                             (this file)
├── rules/
│   └── harness.md                        (trigger: always_on — operating contract)
├── workflows/                            (6 phase entrypoints)
│   ├── setup.md
│   ├── plan.md
│   ├── work.md
│   ├── review.md
│   ├── release.md
│   └── bugfix.md
└── skills/                               (5 dispatchable skills)
    ├── explorer/SKILL.md
    ├── adversarial-reviewer/SKILL.md
    ├── adversarial-reviewer-cross/SKILL.md
    ├── documenter/SKILL.md
    └── dependabot-fixer/SKILL.md
```

`install.sh` (POSIX) or `install.ps1` (Windows/PowerShell 7+) copies this tree to the target's `.agent/` directory with the same `cp_managed` semantics as the Claude Code adapter: refreshed on `--update` / `-Update`, preserved on fresh install if already present.

## Invocation

From within Antigravity, prompt the agent:

- **Setup:** *"Run the setup workflow."*
- **Plan:** *"Run the plan workflow. Brief: `<your brief>`."*
- **Work:** *"Run the work workflow."* (or *"…on task 3."*)
- **Review:** *"Run the review workflow."*
- **Release:** *"Run the release workflow."*
- **Bugfix:** *"Run the bugfix workflow. Report: `<bug report>`."*

Sub-agent skills are dispatched automatically by the workflows. You can also invoke them directly — *"use the explorer skill to find where X is handled"* — if you need a one-off.

## Single source of truth

Every workflow and skill here points back to the canonical spec under [`harness/phases/`](../../harness/phases/), [`harness/pipelines/`](../../harness/pipelines/), or [`harness/agents/`](../../harness/agents/). If a workflow's non-negotiables drift from the canonical spec, the canonical spec wins — file an issue or fix it.

## Re-audit hook

If Antigravity ships first-class sub-agent support (fresh-context dispatch, explicit tool allowlists per sub-agent, context isolation) later, revisit this adapter and migrate sub-agents off `skills/` — per the re-audit principle ([principles.md §6](../../harness/principles.md)).
