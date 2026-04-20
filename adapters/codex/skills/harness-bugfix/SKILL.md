---
name: harness-bugfix
description: Bug triage pipeline — Report → Analyze → Fix → Verify. Use instead of harness-plan+harness-work for bugs. Prefixed harness- for consistency with the other phase skills.
---

# harness-bugfix skill

Run the **bugfix** pipeline of agentic-harness. Full spec: [`harness/pipelines/bugfix.md`](../../../../harness/pipelines/bugfix.md). Read it and follow it.

**Bug report:** provide the bug report, issue link, or reproduction steps when invoking this skill.

## Four phases, in order

1. **Report** — capture the bug verbatim in `.harness/PLAN.md` under `## Report`. Do not paraphrase. Interview if the report is unclear. Then propose a GitHub Issue (`gh issue create --label bug`) with title + body preview; on confirmation, record `#N` in PLAN.md as `**Tracking:** #N`. Skip gracefully if `gh` unavailable or origin not on GitHub.
2. **Analyze** — reproduce locally, find the *root cause* (ask "why" at least three times), not just the first suspicious line. Write findings under `## Analysis`. If root cause is actually a design flaw, stop and escalate to `harness-plan`. Post the Analysis to the tracking issue as a comment (preview-and-ask).
3. **Fix** — write a regression test that FAILS against current code and WILL pass after the fix. Then fix it. Minimal scope — no "while I'm in here" changes. Post the Fix summary (commit SHA, regression test path, files changed, gate results) to the tracking issue as a comment.
4. **Verify** — run the `harness-review` skill (non-negotiable for bugs). Confirm the regression test actually exercises the root cause, not just the symptom. Confirm the original report's reproduction steps no longer reproduce. Then dispatch the `documenter` subagent with the bug report + fix diff — it updates `Known-Issues.md` or adds an ADR **only** if the fix reveals a gotcha worth persisting. Most bugfixes get `NO CHANGES` from docsub and that's correct. Post the Verify summary to the tracking issue, then close it with `gh issue close --reason completed`.

## Non-negotiables

- Regression test is mandatory. No test, no fix.
- Root cause before fix. Jumping to a patch is how bugs come back.
- `harness-review` on every bugfix. Bugs are evidence of code you got wrong once — fresh eyes matter more, not less.
- Docsub's bugfix pass is lightweight. Don't force a wiki update on a run-of-the-mill fix — over-documentation is drift too.
- Every `gh issue *` call is preview-and-ask per `harness/documentation.md`. No silent automation. The issue serves as the bug's posterity record — Report → Analysis → Fix → Verify, one comment per phase, closed on completion.

Start with the Report phase.
