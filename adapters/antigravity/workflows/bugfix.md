---
description: Bug triage pipeline — Report → Analyze → Fix → Verify. Use instead of /plan+/work for bugs.
---

# Bugfix workflow

Run the **bugfix** pipeline of agentic-harness. Full spec: [`harness/pipelines/bugfix.md`](../../../harness/pipelines/bugfix.md). Read it and follow it.

**Bug report:** provide the bug report, issue link, or reproduction steps when invoking this workflow.

## Four phases, in order

1. **Report** — capture the bug verbatim in `.harness/PLAN.md` under `## Report`. Do not paraphrase. Interview if the report is unclear.
2. **Analyze** — reproduce locally, find the *root cause* (ask "why" at least three times), not just the first suspicious line. Write findings under `## Analysis`. If root cause is actually a design flaw, stop and escalate to `/plan`.
3. **Fix** — write a regression test that FAILS against current code and WILL pass after the fix. Then fix it. Minimal scope — no "while I'm in here" changes.
4. **Verify** — run the `/review` workflow (non-negotiable for bugs). Confirm the regression test actually exercises the root cause, not just the symptom. Confirm the original report's reproduction steps no longer reproduce. Then dispatch the `documenter` skill with the bug report + fix diff — it updates `Known-Issues.md` or adds an ADR **only** if the fix reveals a gotcha worth persisting. Most bugfixes get `NO CHANGES` from docsub and that's correct.

## Non-negotiables

- Regression test is mandatory. No test, no fix.
- Root cause before fix. Jumping to a patch is how bugs come back.
- `/review` on every bugfix. Bugs are evidence of code you got wrong once — fresh eyes matter more, not less.
- Docsub's bugfix pass is lightweight. Don't force a wiki update on a run-of-the-mill fix — over-documentation is drift too.

Start with the Report phase.
