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
7. **End by updating `PLAN.md` (mark `[x]`), `progress.md` (append line), and committing.** Then stop.

Start by reading `.harness/PLAN.md`, `.harness/progress.md`, and the project's `AGENTS.md` / `CLAUDE.md`. Identify the next unchecked task (or the one the user specified). Confirm the task and its verification criterion with the user before writing code.
