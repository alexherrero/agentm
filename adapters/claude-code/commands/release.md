---
description: Pre-merge gate — verify plan done, gates green, CI passing. Does NOT push/merge/tag without explicit user approval.
---

You are running the **release** phase of agentic-harness. The full spec is at `harness/phases/05-release.md`. Read it and follow it.

**Non-negotiable constraints:**
1. **Preconditions:** PLAN.md `Status: done`, all tasks `[x]`, `/review` resolved, working tree clean, branch ahead of base. If any fails, stop and report.
2. **Re-run the full deterministic gate suite.** Full test suite, not a subset. Production build, not just dev-server.
3. **Set `passes: true` only on verified features.** One feature, one verified test exercise, one clean review — then true. Never speculative.
4. **Do NOT push, merge, tag, or deploy.** These are high-blast-radius actions requiring explicit human confirmation per action. Prepare and summarize; wait for the word.
5. **If CI is red, stop.** Do not release past failing checks.

End with a summary listing what's ready and what commands the user can run (`git push`, `gh release create`, `gh pr merge`). Wait for explicit confirmation before running any of them.
