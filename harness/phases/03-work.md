# Phase: work

Implement exactly one task from `.harness/PLAN.md`. Stop when that task is done and its verification gates are green. Do not start the next task.

## Purpose

The `/work` phase exists to keep implementation single-threaded and coherent. From [principles.md §3](../principles.md): parallel implementers produce mutually-inconsistent decisions; single-task sessions let the implementer hold the full context of what they're changing and why.

"One task per session" is not a suggestion. It's the load-bearing constraint that makes everything else in the harness work.

## Preconditions

1. `.harness/PLAN.md` exists with `Status: planning` or `in-progress` and at least one unchecked `[ ]` task. If not, stop — run `/plan` first.
2. Working tree is clean, or contains only intentional in-progress work from an earlier `/work` session that was interrupted.
3. `.harness/init.sh` exists. If the project's verification commands aren't obvious from it, stop and ask — better than guessing `npm test` and missing the real command.

## Inputs

- `.harness/PLAN.md` — the next unchecked task.
- `.harness/progress.md` — what happened last.
- `AGENTS.md` / `CLAUDE.md` — project-specific conventions.
- The codebase — use the `explorer` sub-agent for read-only fan-out when the task spans unfamiliar code.

## Process

### 1. Read state

- Read `PLAN.md`. Find the first unchecked task (`[ ]`). If the user specified a different task (e.g. `/work task 3`), honor that instead.
- Read `progress.md` — was a previous `/work` session interrupted? If so, confirm whether to resume or restart.
- Read `AGENTS.md` + `CLAUDE.md` for conventions (commit style, test runner, formatting).

### 2. Confirm scope

Before writing any code, state the task to the user in one sentence and confirm:

> About to work on **task N: <title>**. Verification: <one-line criterion>. OK?

Skip this confirmation only if the user explicitly said "just work on the next task." The confirmation is cheap and catches plan/intent drift.

### 3. Gather context (optional)

If the task touches unfamiliar code, dispatch the `explorer` sub-agent:
> "Where is X handled? What tests exist for Y? Return a structured summary with file:line references."

Fan out if there are multiple independent questions. Do not fan out for a single question — that's slower than just reading.

### 4. Implement

- Write code that satisfies the task's "What" clause.
- Write tests that satisfy the task's "Verification" clause, in the same session. Tests-after is an anti-pattern here; verification must land with the implementation.
- Follow project conventions (formatters, naming, file layout). If the task implies a deviation, flag it and ask.
- **Do not touch `wiki/`.** Documentation updates are phase-boundary-only (see [documentation.md](../documentation.md#the-documenter-sub-agent)). Writing docs alongside code biases the implementer toward confirming the plan rather than reporting what actually shipped. Docsub runs in step 8 once gates are green.
- If mid-implementation you realize the task is bigger than planned, **stop**. Don't silently expand scope. Surface it: "Task N turned out to be bigger than planned because [reason]. Options: (a) finish the original task narrow, (b) expand the task and re-plan, (c) split into N' and N''. Your call." Then wait.

### 5. Run deterministic gates

Run these in order, short-circuit on failure:

1. **Typecheck** — project's typecheck command.
2. **Lint** — project's linter.
3. **Tests** — at least the tests for the new code; full suite if the project is small enough to run in <60s, otherwise the relevant subset.
4. **Build** — only if the task affects build output (most don't).

Commands come from `.harness/init.sh` / package scripts / Makefile — whatever the project uses. If a gate isn't configured, note it and skip, don't invent one.

### 6. Iterate on failures

On gate failure:
- **Feed the full error output back** into the next reasoning pass. Do not summarize — the exact error is the signal.
- Cap at **5 iterations** per gate. If a gate isn't green after 5, stop and report. Loops of 20+ iterations almost always indicate a misunderstanding, not a fixable bug.
- If a test fails and the test itself is wrong — stop. Do not edit or delete the test to make it pass. Surface the test defect and ask. This is non-negotiable (see [principles.md §5](../principles.md)).

### 7. Update state

Once all gates are green:

- Edit `PLAN.md`: mark the task `[x]`. If `Status: planning`, change to `in-progress`. If this was the last task, change to `done`.
- Do NOT set `features.json` entries to `passes: true` — that's `/review`'s job, after adversarial inspection.
- Append to `progress.md`:
  ```
  <YYYY-MM-DD HH:MM> /work — completed task N: "<title>" (<filesChanged> files, <testsAdded> tests added)
  ```

### 8. Update the wiki (post-gates)

Dispatch the `documenter` sub-agent (full spec: [`harness/agents/documenter.md`](../agents/documenter.md)) with: the task's title + What + Verification from `PLAN.md`, the diff (`git diff` scoped to the task's commits or staged changes), and the matching pending wiki entries. Docsub's job:

- Flip `Status: pending → implemented` on the matching Feature/Subsystem page(s) **only if the diff proves it** — speculative flips are a worse failure than missed ones.
- Fill `## Implementation` with real `file:line` references (GitHub URLs if a remote is set).
- Update `## Design` only if the diff shows the plan shifted during implementation. If implementation matched the plan, leave Design alone.
- Create or update pages under `wiki/operational/` if the task introduced operational concerns (new env var, deploy step, runtime dependency, health check).

If docsub returns `OPEN QUESTIONS` (e.g. "task marked `[x]` but diff doesn't touch the claimed surface"), resolve them before committing. If it returns `NO CHANGES`, that's fine — not every task touches documented surface.

**Docsub is NOT invoked during step 4 (Implement).** Only here, once gates are green. If you find yourself reaching for docsub mid-implementation, stop — the urge means the plan's intent was unclear, not that the docs need updating in-line.

### 9. Commit

One task, one commit, unless the project convention says otherwise (squash-on-merge workflows may prefer multiple). Commit message should reference the task:

```
<scope>: implement task N — <title>

<1-2 sentence why. Not what — the diff shows what.>
```

Follow project convention for trailers (`Co-Authored-By`, issue references, etc.). Check recent `git log` for the style.

If the project requires signed commits or has pre-commit hooks, let them run — do not use `--no-verify`.

### 10. Stop

**Do not start the next task.** The next task gets its own session: either `/work` again (clean context) or `/review` first if the task warranted it.

Return to the user with a ≤5-bullet summary:
- Task completed: task N, title
- Files changed: count + the most notable path
- Tests added: count, what they cover
- Gates: all green (or: "N iterations needed, here's why")
- Next: `/review` if the task is high-risk; otherwise `/work` for the next task, or `/release` if all tasks done

## When to invoke `/review` vs. going straight to the next `/work`

Not every task needs adversarial review. Review is expensive (fresh context, adversarial framing, sometimes multiple rounds). Use it when:
- The task touches security, auth, payments, or data persistence
- The task is the last one in the plan (always review before `/release`)
- The plan's "Risks" section flagged this area
- You have a nagging feeling the change is brittle

Skip `/review` for routine changes (new tests, pure refactors, docs, scaffolding). The harness will still run gates on the next `/work` session before it does anything, so routine changes are caught by the next iteration anyway.

## Failure modes to avoid

- **Starting the next task "while you're in there."** The single most common way `/work` breaks coherence. Stop after one.
- **Editing tests to make them pass.** Banned. If a test is wrong, say so and stop.
- **Skipping failed gates** ("I'll fix this next session"). Gates must be green before the task is marked `[x]`.
- **Silently expanding scope.** If the task is bigger than planned, surface it — don't quietly do more than the plan says.
- **Summarizing errors instead of passing them through** on gate failure. The full error is information; a summary is a guess at what matters.
- **Committing without running gates.** Gates first, commit second.
- **Forgetting to update PLAN.md and progress.md.** The next session is blind without them.
- **Implementing across multiple tasks in one commit.** Messy to review, messy to revert.

## Output to the user (at end of `/work`)

Example:

> Completed task 2: "Core line-counting with file inputs" in `~/lc/`
> - 3 files changed (`src/cli.js`, `src/count.js`, `test/count.test.js`)
> - 4 tests added (fixture-based line counts, error on unreadable file)
> - Gates: typecheck N/A, lint clean, all tests pass, no build step
> - Committed as `feat: implement file-mode line counting`
> - Next: `/work` for task 3 (stdin support), or `/review` if you want a critic pass first

Tight. No essay.
