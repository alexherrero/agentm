---
description: Implement exactly one task from .harness/PLAN.md. Stop after one.
argument-hint: [optional — "task N" to pick a specific task instead of the next unchecked one]
---

You are running the **work** phase of agentic-harness. The full spec is at `harness/phases/03-work.md` in the harness repo (also copied to this project if the harness was installed into it). Read that spec and follow it.

**Argument (if any):** $ARGUMENTS

**Non-negotiable constraints for this phase:**
1. **One task per session.** Do not start the next task, even if it looks easy.
2. **Gates must be green before the task is marked `[x]`.** No "I'll fix this next session" on failed gates.
3. **Never edit or delete a failing test to make it pass.** If a test is wrong, surface it and stop.
4. **Feed full error output back** on gate failures — do not summarize.
5. **Cap iterations at 5 per gate.** If not green after 5, stop and report.
6. **Do not silently expand task scope.** If it turns out bigger than planned, stop and ask.
7. **Do not touch `wiki/` during implementation.** Documentation updates are phase-boundary-only.
8. **After gates are green (before committing), dispatch the `documenter` sub-agent** with the task spec + the diff. It flips matching `pending → implemented` pages and adds operational pages if the task introduced one. Resolve `OPEN QUESTIONS` before committing.
9. **End by updating `PLAN.md` (mark `[x]`), `progress.md` (append line), and committing.**
10. **Offer deferred items to the GitHub Project** (optional, per canonical spec §10). If this session surfaced anything *out of task scope* (adjacent bug, refactor opportunity, stale doc elsewhere — not follow-ups to the current task), propose one item per distinct deferred finding via `gh project item-create`, batched into a single preview (title + body per item) at phase end. Silent-skip if `.harness/project.json` absent or `gh` unavailable. **No `gh` invocation without user confirmation.** Then stop.

Start by reading `.harness/PLAN.md`, `.harness/progress.md`, and the project's `AGENTS.md` / `CLAUDE.md`. Identify the next unchecked task (or the one the user specified). Confirm the task and its verification criterion with the user before writing code.
