# Pipeline: bugfix

Triage-first pipeline for bug reports. Replaces `/plan` + `/work` when the work is driven by a defect, not a new feature. Adapted from [Pimzino/claude-code-spec-workflow](https://github.com/Pimzino/claude-code-spec-workflow)'s bug-fix pipeline — one of the few harness patterns in OSS that formalizes defect handling instead of treating bugs as a special case of features.

## Why a separate pipeline

Bugs have different failure modes than features:
- **Root cause vs. symptom.** The first suspicious line is often not the bug. Jumping to the fix skips the part of the job that matters.
- **Reproduction is half the work.** A bug you can't reproduce is a bug you can't verify you fixed.
- **Regression tests are mandatory.** A fix without a test is a bug that will come back.

Running bugs through `/plan` + `/work` accidentally lets the discipline lapse. A dedicated pipeline keeps it tight.

## Four phases

### 1. Report

Capture the bug report *verbatim* in `.harness/PLAN.md` under a `## Report` section. Do not paraphrase, do not "clean up" the user's words — the specifics matter for reproducing.

Include:
- Original report text
- Source (Slack message, GitHub issue, email, in-person quote)
- Reporter, date
- Any reproduction steps they gave
- What they expected vs. what happened
- Environment (OS, browser, version) if relevant

If the report is unclear ("the login is broken"), interview before moving on. One-word bug reports are not reports — they're the start of an interview.

### 2. Analyze

Find the root cause. Not the first plausible cause — the real one.

- **Reproduce locally** if possible. If you can't reproduce, the bug is either environment-specific (note that), flaky (harder — investigate timing/state), or not real (rare but happens).
- **Read the relevant code paths.** Dispatch the `explorer` sub-agent if the bug spans unfamiliar areas.
- **Ask "why" at least three times.** The function threw an error → why? → the input was malformed → why? → the upstream validator skipped this case → why? → the validator's regex was wrong. The first "why" gets you the symptom; the third gets you the cause.
- **Note load-bearing assumptions.** What else depends on the broken behavior working the way it currently does? A fix that breaks three other things is not a fix.

Write findings to `.harness/PLAN.md` under `## Analysis`:

```markdown
## Analysis

**Reproduction:** <steps that reliably trigger the bug>
**Root cause:** <file:line, one-sentence explanation>
**Why it happened:** <how did this ship? missing test? unclear spec? refactor artifact?>
**Scope:** <what else touches the broken code path — who/what might regress>
**Fix strategy:** <one-paragraph approach — what code changes, what test proves it>
```

If root cause analysis reveals the bug is actually a symptom of a larger design problem, **stop**. Surface it to the user: this may need `/plan`, not `/bugfix`. Patching a symptom of a design flaw creates two bugs.

### 3. Fix

Implement the fix under `/work` discipline (see [03-work.md](../phases/03-work.md)) with two bugfix-specific rules:

- **Regression test first.** Write a test that *fails against the current code* and will *pass after the fix*. If you can't write a failing test, you don't have a clear enough grip on the bug to fix it — go back to Analyze.
- **Minimal scope.** Fix the bug, not adjacent issues. "While I'm in here" is how a one-line fix becomes a regression-introducing rewrite. Adjacent issues go on the backlog.

When the fix is done:
- Regression test passes
- Pre-existing tests still pass
- Deterministic gates are green

### 4. Verify

Run `/review` on the fix (this is one of the cases where review is non-negotiable — bugs flagged the code as fragile, fresh eyes matter).

Additionally confirm:
- The regression test *exists* and is committed
- The regression test *actually exercises the root cause*, not just the symptom
- The original reproduction steps from `## Report` now produce the expected behavior

Append to `.harness/progress.md`:

```
<YYYY-MM-DD HH:MM> /bugfix — fixed <one-line description> (root cause: <summary>, regression test: <path>)
```

## Failure modes to avoid

- **Paraphrasing the report.** Specifics get lost. Copy verbatim.
- **Jumping to the fix without Analysis.** The first suspicious line is often not it. Three "whys" minimum.
- **Fixing the symptom, leaving the cause.** A design flaw dressed as a bug comes back. If you see it, stop and escalate to `/plan`.
- **No regression test.** Non-negotiable. If you can't write one, you don't understand the bug yet.
- **Expanding scope.** Adjacent issues go on the backlog, not in this fix.
- **Skipping `/review` on bugfixes.** Bugs are evidence of code you already got wrong once — the area deserves fresh skeptical eyes, not less scrutiny.

## Output to the user (at end of `/bugfix`)

```
Bug fixed: "<one-line summary>"

- Root cause: <file:line> — <one-sentence why>
- Fix: <one sentence, not the diff>
- Regression test: <path/test.ts>, fails without fix, passes with
- /review outcome: <clean | N findings addressed>
- Original reproduction (from Report) no longer reproduces

Next: `/release` if this was the only thing in flight, otherwise `/work` or `/plan` for what's next.
```
