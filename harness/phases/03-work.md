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
- Create or update pages under `wiki/how-to/` if the task introduced operational concerns (new env var, deploy step, runtime dependency, health check) — recipe shape, `## Steps`, no rationale.

If docsub returns `OPEN QUESTIONS` (e.g. "task marked `[x]` but diff doesn't touch the claimed surface"), resolve them before committing. If it returns `NO CHANGES`, that's fine — not every task touches documented surface.

**Docsub is NOT invoked during step 4 (Implement).** Only here, once gates are green. If you find yourself reaching for docsub mid-implementation, stop — the urge means the plan's intent was unclear, not that the docs need updating in-line.

### 9. Commit

One task, one commit, unless the project convention says otherwise (squash-on-merge workflows may prefer multiple). Commit message should reference the task:

```
<scope>: implement task N — <title>

<1-2 sentence why. Not what — the diff shows what.>
```

Follow project convention for trailers (issue references, `Signed-off-by`, etc.) — check recent `git log` for the style. Do **not** add a `Co-Authored-By: Claude …` trailer; the user is the sole author of intent, the agent is the tool. Only add such a trailer if the user explicitly opts in for that commit.

If the project requires signed commits or has pre-commit hooks, let them run — do not use `--no-verify`.

### 10. Offer deferred items to the GitHub Project (optional)

If `.harness/project.json` exists and `gh` is available on PATH, consider whether this `/work` session surfaced anything **out of task scope** that the user might want on the backlog: an adjacent bug you noticed while implementing, a refactor opportunity, missing test coverage elsewhere, a stale doc. *Not* follow-ups to the current task — those belong in the next `/work` task, not a project item.

Propose one project item per distinct finding. **Batch the proposals into a single preview after the commit lands**, not interleaved during implementation — the user confirms (or declines) the whole set in one pass, or picks which to create. No count cap: if you noticed three adjacent bugs, propose three. But if you're proposing more than three in one `/work` session, reconsider — over-firing is a failure mode, and the session probably *is* scope-creeping.

Preview title + body per item. On confirmation, run for each accepted item:

```bash
gh project item-create <number> --owner <owner> \
  --title "<title>" \
  --body "<body — what you observed, where, and why it's deferred rather than in-task>"
```

reading `number` and `owner` from `.harness/project.json`.

**Graceful-skip conditions** (silent, no prompt):
- `.harness/project.json` is absent.
- `gh auth status` fails or `gh` is not on PATH.
- Nothing out-of-task-scope surfaced — the cleanest case. Over-firing is a failure mode.

Preview-and-ask is non-negotiable per [`documentation.md §GitHub Projects + Issues`](../documentation.md). If the user declines, record nothing. If accepted, reference the project item URL in the ≤5-bullet summary.

### 11. Stop

**Do not start the next task.** The next task gets its own session: either `/work` again (clean context) or `/review` first if the task warranted it.

If this task flipped a `features.json` entry's `passes` flag from `false` to `true` during `/review`, or the task finished the last feature in the plan, **suggest the `ship-release` skill** as the next step — do not auto-invoke it, the user may have more features queued. Phrase it: *"Feature `<id>` is now passing end-to-end. Consider invoking the `ship-release` skill (from agent-toolkit) to cut a tagged release."* If `agent-toolkit` isn't installed alongside, graceful-skip the suggestion — `ship-release` migrated to `agent-toolkit` in v2.0.0 (see ADR 0006).

Return to the user with a summary. Minimum (≤5 bullets — adequate for a routine task close):

- Task completed: task N, title
- Files changed: count + the most notable path
- Tests added: count, what they cover
- Gates: all green (or: "N iterations needed, here's why")
- Next: `/review` if the task is high-risk; otherwise `/work` for the next task, or `/release` if all tasks done, or `ship-release` if a feature just went green

**Enhancements when `.harness/ROADMAP.md` exists** (signals a multi-plan project — apply by default in that case):

- **Lead with roadmap context**: *"Currently building ROADMAP item #X — &lt;name&gt;. &lt;one-sentence framing&gt;."*
- **Plan-status chart** with `✅` / `⬜` symbols per task in the active plan, so the user sees where this task lands in the larger plan.
- **Link block** to `.harness/ROADMAP.md` / `.harness/PLAN.md` / `.harness/progress.md` (absolute paths; note `.harness/` is gitignored).
- **Explicit handoff phrase**: *"Say 'let's do task N' to continue"* (or *"Ending loop"* if no follow-on action).
- When relevant: commit SHA, CI status with per-OS times, key design calls or scope adjustments, manual verification scenarios, negative-test results.

These enhancements are opt-in via the ROADMAP.md signal so a harness install without a roadmap stays minimal; multi-plan projects get the navigation aids that match their scale.

## When to invoke `/review` vs. going straight to the next `/work`

Not every task needs adversarial review. Review is expensive (fresh context, adversarial framing, sometimes multiple rounds). Use it when:
- The task touches security, auth, payments, or data persistence
- The task is the last one in the plan (always review before `/release`)
- The plan's "Risks" section flagged this area
- You have a nagging feeling the change is brittle

Skip `/review` for routine changes (new tests, pure refactors, docs, scaffolding). The harness will still run gates on the next `/work` session before it does anything, so routine changes are caught by the next iteration anyway.

## Long-running `/work` — operator-control hooks (agent-toolkit)

If [`agent-toolkit`](https://github.com/alexherrero/agent-toolkit) is installed alongside the harness, three Claude-Code-only hooks land at `.claude/hooks/` and give the operator precise control over a long-running `/work` session without closing the session:

| Hook | Trigger | What it does |
|---|---|---|
| [`kill-switch`](https://github.com/alexherrero/agent-toolkit/blob/main/hooks/kill-switch/hook.md) | `PreToolUse` (every tool call) | Touch `.harness/STOP` to halt; `rm` to resume. Exits 2 + halt message on stderr; Claude Code blocks the tool call. |
| [`steer`](https://github.com/alexherrero/agent-toolkit/blob/main/hooks/steer/hook.md) | `PreToolUse` (every tool call) | Write `.harness/STEER.md` with a "do it this way instead" instruction; next tool call picks it up; file renamed to `STEER.consumed-<iso-ts>.md` for audit trail. |
| [`commit-on-stop`](https://github.com/alexherrero/agent-toolkit/blob/main/hooks/commit-on-stop/hook.md) | `Stop` event (end of each turn) | If the working tree is dirty, creates `auto-save/<iso-ts>` branch and commits the work there. Returns HEAD to the original branch with a clean tree. Recovery: `git checkout auto-save/<ts>`. |

**Why these earn their keep in long-running `/work`.** A `/work` session that hits an unexpected loop, drifts off-spec, or crashes mid-task today loses information — the only kill-switch is closing the session, the only redirect is restart, and crash recovery is hoping the working tree wasn't important. These three hooks make each precise:

- **Runaway loop**: `touch .harness/STOP` halts the next tool call without ending the session.
- **Mid-task redirect**: write `.harness/STEER.md` with the correction; the agent sees it on the next tool call without restart.
- **Crashed session / interrupted at task end**: commit-on-stop fires on the `Stop` event and saves the in-flight work to `auto-save/<ts>` — next session recovers via `git checkout`.

**Ordering invariant.** `kill-switch` and `steer` both fire on `PreToolUse`. Alphabetical install order means `kill-switch` runs first — if `.harness/STOP` is present, the tool call is blocked **before** `steer` reads `STEER.md`. Halt always takes precedence over a steer.

**Graceful-skip.** Install `agent-toolkit` to enable; otherwise `/work` runs without the hooks. The phase contract doesn't require them — they're an operator-precision layer on top of the existing workflow. See [agent-toolkit's how-to](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/how-to/Use-The-Base-Hooks.md) for installation + worked scenarios.

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
