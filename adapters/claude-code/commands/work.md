---
description: Work .harness/PLAN.md's task list autonomously; safety-gate each task, stop to ask only when one fails the check or needs a clarification.
argument-hint: [optional — "task N" to pick a specific task instead of the next unchecked one]
---

You are running the **work** phase of agentm. The full spec is at `harness/phases/03-work.md` in the harness repo (also copied to this project if the harness was installed into it). Read that spec and follow it.

**Argument (if any):** $ARGUMENTS

**Non-negotiable constraints for this phase:**
1. **Assume the full task list; safety-gate each task.** Work the plan's tasks autonomously, in sequence — no per-task approval. Before each task, run a safety pre-check; **stop and ask only when a task fails it (hard-to-reverse / ambiguous / scope-drifting / unverifiable) or needs a clarification** — otherwise continue to the end of the plan. Single-threaded always; never fan out parallel implementers.
2. **Gates must be green before the task is marked `[x]`.** No "I'll fix this next session" on failed gates.
3. **Never edit or delete a failing test to make it pass.** If a test is wrong, surface it and stop.
4. **Feed full error output back** on gate failures — do not summarize.
5. **Cap iterations at 5 per gate.** If not green after 5, stop and report.
6. **Do not silently expand task scope.** If it turns out bigger than planned, stop and ask.
7. **Do not touch `wiki/` during implementation.** Documentation updates are phase-boundary-only.
8. **After gates are green (before committing), dispatch crickets' `wiki-maintenance:documenter` sub-agent** (graceful-skip if crickets' `wiki-maintenance` plugin is absent) with the task spec + the diff. It flips matching `pending → implemented` pages and adds operational pages if the task introduced one. Resolve `OPEN QUESTIONS` before committing.
9. **End by updating `PLAN.md` (mark `[x]`), `progress.md` (append line), and committing.**
10. **Offer deferred items to the GitHub Project** (optional, per canonical spec §10). If this session surfaced anything *out of task scope* (adjacent bug, refactor opportunity, stale doc elsewhere — not follow-ups to the current task), propose one item per distinct deferred finding via `gh project item-create`, batched into a single preview (title + body per item) at phase end. Silent-skip if `.harness/project.json` absent or `gh` unavailable. **No `gh` invocation without user confirmation.** Then stop.

Start by reading `.harness/PLAN.md`, `.harness/progress.md`, and the project's `AGENTS.md` / `CLAUDE.md`. Then work the unchecked tasks in sequence (or the one the user specified). Don't confirm each task upfront — run the safety pre-check before each task and only stop to ask when it fails or a clarification is needed.
