---
trigger: always_on
---

# agentic-harness operating contract

This project uses [agentic-harness](https://github.com/alexherrero/agentic-harness). The authoritative agent operating contract lives in [`AGENTS.md`](../../AGENTS.md) at the repo root — read it first, every session.

## Invocation surface

Antigravity's native surface maps as follows:

| Harness surface | Antigravity primitive | Location |
|---|---|---|
| Phase commands (setup/plan/work/review/release/bugfix) | Workflows | `.agent/workflows/*.md` |
| Sub-agents (explorer, adversarial-reviewer, adversarial-reviewer-cross, documenter) | Skills | `.agent/skills/<name>/SKILL.md` |
| Skills (dependabot-fixer) | Skills | `.agent/skills/<name>/SKILL.md` |

Invoke a workflow by name from the chat (e.g. *"run the plan workflow with brief: …"*). Invoke a skill when its trigger conditions match, or explicitly (*"use the explorer skill to find …"*).

## Non-negotiables (from [`harness/principles.md`](../../harness/principles.md))

1. **Phase-gated workflow.** Plan → Work → Review → Release. Do not skip phases or merge them.
2. **One task per `/work` session.** No "while I'm in here" scope expansion.
3. **Gates before commit.** Typecheck, lint, tests must be green before a task is marked `[x]`.
4. **Never edit or delete a failing test to make it pass.** If a test is wrong, surface it and stop.
5. **Adversarial review framing is literal.** The code contains bugs; find them. Rubber-stamp reviews are a failure of rigor.
6. **`/work` does not touch `wiki/`.** Documentation updates are phase-boundary-only — the `documenter` skill runs post-gates in `/work`, full-pass in `/release`.

## State files

- [`.harness/PLAN.md`](../../.harness/PLAN.md) — current plan, per-task verification criteria.
- [`.harness/features.json`](../../.harness/features.json) — user-visible features, `passes: true` set only by `/review` + `/release`.
- [`.harness/progress.md`](../../.harness/progress.md) — append-only phase log.
- [`.harness/init.sh`](../../.harness/init.sh) — project boot commands for the harness to run gates.

Read these at the start of every session. Do not edit them outside their owning phase.
