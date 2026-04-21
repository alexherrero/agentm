---
description: Adversarial review — assume the code has bugs, find them. Executable artifact required. No fixes applied.
argument-hint: [optional — commit range, branch name, or task number to scope the review]
---

You are running the **review** phase of agentic-harness. The full spec is at `harness/phases/04-review.md`. Read it and follow it.

**Scope (if any):** $ARGUMENTS — if empty, review the most recently-completed task.

**Non-negotiable constraints for this phase:**
1. **Gates first.** Run typecheck, lint, tests, build. If any fail, stop and report. Do not invoke the reviewer on a broken base.
2. **Dispatch the `adversarial-reviewer` sub-agent** in a fresh context. Pass the diff + the `PLAN.md` task + `AGENTS.md`. Do NOT pass the implementer's reasoning trace.
3. **Framing is literal:** "The code under review likely contains bugs. Find them." Do not soften.
4. **Required output:** failing test, specific `file:line` defect, or explicit `NO ISSUES FOUND` with categories. Prose-only critiques are rejected — re-invoke once with tighter framing, then stop.
5. **Verify findings reproduce** before reporting them. Run the failing test; open the line reference.
6. **Do not fix what you find.** `/review` reports; `/work` implements. Recommend a follow-up task if needed.
7. **Do NOT dispatch the `documenter` sub-agent.** Doc drift is `/release`'s concern; surfacing it here as a finding is fine, acting on it is not.
8. **Log to `progress.md`** with outcome (`NO ISSUES FOUND` or `N findings`).
9. **Offer deferred findings to the GitHub Project** (optional, per canonical spec §8). If the user elected to *defer* one or more findings rather than block the change on them, propose project items via `gh project item-create` (one per finding or grouped when findings share a theme), batched into a single preview (title + body per item) at review-report end. Not clean reviews, not in-place fixes. Silent-skip if `.harness/project.json` absent or `gh` unavailable. **No `gh` invocation without user confirmation.**

Start by verifying gates pass, then identify the artifact (commit range / branch / uncommitted diff) and its plan task, then dispatch the reviewer.
