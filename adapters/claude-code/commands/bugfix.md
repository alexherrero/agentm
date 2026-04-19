---
description: Bug triage pipeline — Report → Analyze → Fix → Verify. Use instead of /plan+/work for bugs.
argument-hint: <bug report, issue link, or reproduction steps>
---

You are running the **bugfix** pipeline of agentic-harness. The full spec is at `harness/pipelines/bugfix.md`. Read it and follow it.

**Bug report from user:** $ARGUMENTS

**Four phases, in order:**

1. **Report** — capture the bug verbatim in `.harness/PLAN.md` under `## Report`. Do not paraphrase. Interview if the report is unclear.
2. **Analyze** — reproduce locally, find the *root cause* (ask "why" at least three times), not just the first suspicious line. Write findings under `## Analysis`. If root cause is actually a design flaw, stop and escalate to `/plan`.
3. **Fix** — write a regression test that FAILS against current code and WILL pass after the fix. Then fix it. Minimal scope — no "while I'm in here" changes.
4. **Verify** — run `/review` (non-negotiable for bugs). Confirm the regression test actually exercises the root cause, not just the symptom. Confirm the original report's reproduction steps no longer reproduce.

**Non-negotiables:**
- Regression test is mandatory. No test, no fix.
- Root cause before fix. Jumping to a patch is how bugs come back.
- `/review` on every bugfix. Bugs are evidence of code you got wrong once — fresh eyes matter more, not less.

Start with the Report phase.
