---
description: Pre-merge gate — verify plan done, gates green, CI passing, docs swept. Does NOT push/merge/tag without explicit user approval.
---

# Release workflow

Run the **release** phase of agentic-harness. Full spec: [`harness/phases/05-release.md`](../../../harness/phases/05-release.md). Read it and follow it.

## Non-negotiable constraints

1. **Preconditions:** `PLAN.md` `Status: done`, all tasks `[x]`, `/review` resolved, working tree clean, branch ahead of base. If any fails, stop and report.
2. **Re-run the full deterministic gate suite.** Full test suite, not a subset. Production build, not just dev-server.
3. **Set `passes: true` only on verified features.** One feature, one verified test exercise, one clean review — then true. Never speculative.
4. **Dispatch the `documenter` skill** with the full plan-to-HEAD diff for a wiki sweep: flip any missed `pending → implemented`, add ADRs for non-obvious decisions, update `Home.md` / `_Sidebar.md`, append to `Completed-Features.md`. **Block the release** if docsub returns unresolved `OPEN QUESTIONS` — shipping with stale docs poisons the wiki.
5. **Do NOT push, merge, tag, or deploy.** These are high-blast-radius actions requiring explicit human confirmation per action. Prepare and summarize; wait for the word.
6. **If CI is red, stop.** Do not release past failing checks.
7. **Offer next-release themes to the GitHub Project** (optional, per canonical spec §8). If this release's accumulated deferred items show a *recurring theme* (pattern across multiple sessions, not a single item), propose one project item per distinct theme via `gh project item-create`, batched into a single preview (title + body per theme) at release-prep end. Bar is higher than per-phase items. Silent-skip if `.harness/project.json` absent, `gh` unavailable, or no theme emerged. **No `gh` invocation without user confirmation.**

End with a summary listing what's ready and what commands the user can run (`git push`, `gh release create`, `gh pr merge`). Wait for explicit confirmation before running any of them.
