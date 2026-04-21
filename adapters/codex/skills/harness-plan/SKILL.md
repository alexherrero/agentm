---
name: harness-plan
description: Turn a brief into .harness/PLAN.md with per-task verification criteria. No code written — implementation is the harness-work skill. Prefixed harness- to avoid collision with Codex's built-in /plan (which has different semantics).
---

# harness-plan skill

Run the **plan** phase of agentic-harness. Full spec: [`harness/phases/02-plan.md`](../../../../harness/phases/02-plan.md). Read it and follow it.

**Brief:** provide the brief when invoking this skill (free text, ticket link, or reference to a document).

## Non-negotiable constraints

1. Do not write any application code. Implementation is the `harness-work` skill.
2. Read `.harness/PLAN.md` and `.harness/progress.md` first. If a plan is in flight, ask before replacing it.
3. Interview the user (≤5 batched questions) only if the brief is ambiguous. Skip if the brief is clear.
4. Write the plan to `.harness/PLAN.md` using the structure from `templates/PLAN.md`.
5. Update `.harness/features.json` if this plan introduces user-visible features.
6. **Dispatch the `documenter` subagent** once `PLAN.md` is written to create `pending` Feature/Subsystem pages for tasks that affect user-visible behavior or architecture.
7. **Offer deferred items to the GitHub Project** (optional, per canonical spec §7). Scan `## Out of scope` for *intentionally-deferred* items (not hard non-goals), propose one item per distinct deferred finding via `gh project item-create`, batched into a single preview (title + body per item) at phase end. Silent-skip if `.harness/project.json` absent or `gh` unavailable. **No `gh` invocation without user confirmation.**
8. Append a single line to `.harness/progress.md`.
9. End with a ≤5-bullet summary to the user. Next skill to run is `harness-work`.

Start by reading the relevant state files and the full phase spec.
