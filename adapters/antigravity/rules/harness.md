---
trigger: always_on
---

# agentm operating contract

This project uses [agentm](https://github.com/alexherrero/agentm). The authoritative agent operating contract lives in [`AGENTS.md`](../../AGENTS.md) at the repo root — read it first, every session.

## Invocation surface

Antigravity's native surface maps as follows. Since the V5 unbundling ([ADR 0011](../../wiki/decisions/0011-v5-unbundling-dev-loop.md)) the phase loop and the review sub-agents come from the companion crickets plugins, which install their own Antigravity surface; `agentm` itself ships the always-on operating contract plus the utility skills.

| Surface | Antigravity primitive | Provided by |
|---|---|---|
| Always-on operating contract | Rules (`.agents/rules/*.md`) | `agentm` |
| Utility skills (doctor, memory, console) | Skills (`.agents/skills/<name>/SKILL.md`) | `agentm` |
| Wiki authoring (`wiki-author`, `wiki-init`, `wiki-watch`) | Skills (slash commands `/<name>`) | crickets **wiki** |
| Phase loop (`setup/plan/work/release/bugfix` + `spec/build-it/create-it`) | Skills (slash commands `/<name>`; `/development-lifecycle:plan` for `/plan`) | crickets **development-lifecycle** |
| Review sub-agents (`explorer`, `evaluator`, `adversarial-reviewer`, `adversarial-reviewer-cross`, `security-auditor`) | Agents (`agents/*.md`) | crickets **code-review** / **development-lifecycle** |

Invoke any phase or loop command directly as a slash command (`/<name>`, e.g. `/spec <brief>`, `/development-lifecycle:plan <brief>`, `/work`, `/build-it <what>`, `/create-it <what>`, `/release`). Legacy `.agents/workflows/*.md` files are deprecated in Antigravity. Invoke a skill when its trigger conditions match, or explicitly (*"use the doctor skill to check the install"*).

## Non-negotiables (from [`harness/principles.md`](../../harness/principles.md))

1. **Phase-gated workflow.** Spec & Plan → Work (`/build-it` / `/create-it` + `evaluator` done-check) → Release. Do not skip phases or merge them; use `/build-it --check` to review an existing change.
2. **Full task list per `/work` session, autonomously.** Safety-gate each task before starting it; stop to ask only on a failed check (hard-to-reverse / ambiguous / scope-drifting / unverifiable) or a needed clarification. No scope expansion beyond the plan; single-threaded always.
3. **Gates before commit.** Typecheck, lint, tests must be green before a task is marked `[x]`.
4. **Never edit or delete a failing test to make it pass.** If a test is wrong, surface it and stop.
5. **Adversarial review framing is literal.** The code contains bugs; find them. Rubber-stamp reviews are a failure of rigor.
6. **`/work` and `/release` do not touch `wiki/`.** Documentation updates left the coding pipeline in crickets v7.0.0 — run `wiki-author` on request, or let `wiki-watch` catch up on a schedule.

## State files

- [`.harness/PLAN.md`](../../.harness/PLAN.md) — current plan, per-step anchor/profile/size and verification criteria (or the vault task `plan.md` agentm resolves).
- [`.harness/progress.md`](../../.harness/progress.md) — append-only phase log.
- [`.harness/init.sh`](../../.harness/init.sh) — project boot commands for the harness to run gates.

Read these at the start of every session. Do not edit them outside their owning phase.
