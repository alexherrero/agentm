# Phase: plan

Turn a brief into `.harness/PLAN.md` — a structured, executable plan with per-task verification criteria. No code is written in this phase.

## Purpose

A plan exists so that:
1. The implementer (a later `/work` session) has a shared contract to work against — not a verbal understanding that evaporates with context.
2. Scope is fixed *before* you're deep in code, where fixing it costs more.
3. Verification criteria are pre-negotiated, which is the single biggest lever on review quality (see [principles.md §5](../principles.md)).
4. The work is broken into pieces small enough that one `/work` session can finish each one.

## Preconditions

- `.harness/` directory exists (if not, run `/setup` first).
- The user has provided a brief — either as free text, a ticket link, or a reference to an existing document. If the brief is ambiguous, interview before planning (see §Interview below).

## Inputs

- **The brief** — what the user wants built or changed.
- **`.harness/PLAN.md`** if it exists — is there a plan in flight? If so, are we continuing it or starting a fresh one?
- **The codebase** — enough context to decompose the work realistically. Use the `explorer` sub-agent for read-only fan-out if the brief spans unfamiliar code.

## Process

### 1. Triage existing state

- Read `.harness/PLAN.md` if it exists.
- If it's in flight (`Status: in-progress`) and the new brief looks related: ask the user "continue the current plan, or replace it?" Do not silently overwrite.
- If it's complete (`Status: done`) or absent: proceed to a fresh plan.
- Read `.harness/progress.md` — what's the last thing that happened in this project? Useful context.

### 2. Interview, if the brief is ambiguous

The single most valuable thing this phase does. Adapted from [Trail of Bits' "interview first, implement second"](https://github.com/trailofbits/claude-code-config).

Before writing the plan, confirm:
- **Scope boundary.** What's explicitly out of scope? Name at least one thing.
- **Success criterion.** How will we know this is done? If the user can't answer, the plan is premature.
- **Non-obvious constraints.** Performance budgets, compatibility requirements, deadlines, regulated behavior.
- **Risk surface.** What part of the system is this most likely to break? What's load-bearing near the change?

Keep the interview to ≤5 questions, batch them, and default to *not asking* if the answer is derivable from the brief or codebase. Interview fatigue is a real failure mode.

### 3. Decompose into tasks

Each task should be:
- **Small enough** that one `/work` session can finish it (rough heuristic: a task a human would commit as one PR).
- **Independently verifiable** — it has its own pass/fail criteria.
- **Ordered** — dependencies explicit. Later tasks may assume earlier tasks are done.
- **Concretely scoped** — "refactor the auth layer" is not a task. "Extract `verifyToken` into `auth/verify.ts` and add unit tests for expired/malformed/valid inputs" is.

**Rule of thumb:** if you can't describe a task's verification in one sentence, split it further.

### 4. Write `.harness/PLAN.md`

Use the template at [`templates/PLAN.md`](../../templates/PLAN.md). Structure:

```markdown
# Plan: <short title>

**Status:** planning | in-progress | done
**Created:** <YYYY-MM-DD>
**Brief:** <1-3 sentence restatement of what we're building>

## Goal

<What success looks like in 2-4 sentences. User-facing, not implementation-flavored.>

## Constraints

- <Non-obvious constraint 1>
- <Non-obvious constraint 2>

## Out of scope

- <Explicit non-goal 1>
- <Explicit non-goal 2>

## Tasks

### 1. <Task title>
- **What:** <1-2 sentences>
- **Verification:** <how we'll know this is done — executable if possible>
- **Status:** [ ] | [x]

### 2. <Task title>
- **What:** ...
- **Verification:** ...
- **Status:** [ ]

## Risks / open questions

- <Risk 1 — what could go wrong, what we'll do if it does>
- <Open question 1 — something we may need to decide mid-work>

Keep this section short. Most plans have 0–2 real risks; padding with generic ones ("what if the API changes?") is noise. If there are no real risks, write "None identified" — don't invent.

## Verification strategy

<Which deterministic gates apply. Any project-specific extras, e.g. "must manually test on iOS Safari.">
```

### 5. Also update `features.json` if appropriate

If the plan introduces net-new user-visible features (as opposed to internal refactors), add entries to `.harness/features.json`:

```json
{
  "features": [
    {
      "id": "feat-auth-token-refresh",
      "description": "Access tokens auto-refresh 60s before expiry",
      "steps": ["detect near-expiry", "call refresh endpoint", "swap token atomically"],
      "passes": false
    }
  ]
}
```

**Features and tasks are not 1:1.** A feature is a user-visible capability; a task is a unit of implementation work. Scaffolding tasks (project setup, refactors, infra) produce no feature entry. A single feature may span multiple tasks. Err on the side of fewer feature entries — features are for things a user would list in a changelog, not every PR.

`passes: true` is set later, by `/review`, never by `/plan`.

### 6. Stop

**Do not start implementing.** Implementation is `/work`. A plan that bleeds into code is not a plan.

Before returning to the user:
- Confirm the plan is written to `.harness/PLAN.md`
- Append a single line to `.harness/progress.md`:
  ```
  <YYYY-MM-DD HH:MM> /plan — created plan "<title>" with N tasks
  ```
- Summarize in ≤5 bullets to the user: the goal, task count, biggest risk, next command to run (`/work` to start on task 1).

## Failure modes to avoid

- **Premature coding.** The urge to "just fix this one thing" while you're in the file. Write it as a task; handle it in `/work`.
- **Tasks too large.** If a task touches >5 files or its verification is "it works", split it.
- **Verification hand-waving.** "Manual QA" is a fallback, not a primary verification. Prefer an executable check.
- **Skipping the interview.** If you're unsure, ask. Five minutes of questions saves an hour of rework.
- **Overwriting an in-flight plan** without asking. Always check first.
- **Forgetting to update `progress.md`.** The next session won't know what happened.

## Output to the user (at end of `/plan`)

Keep it short. Example:

> Plan written to `.harness/PLAN.md`.
> - Goal: add auto-refresh of auth tokens before expiry
> - 4 tasks, smallest first
> - Biggest risk: race between refresh and in-flight requests
> - Next: `/work` to start on task 1

That's it. No essays.
