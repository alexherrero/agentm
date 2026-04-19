# Phase: review

Adversarial critique of recent work. The framing is literal: **assume the code contains bugs, find them**. Neutral-prompted reviewers rubber-stamp; this phase is engineered to not do that.

## Purpose

From [principles.md §5](../principles.md): neutral-prompted reviewers rubber-stamp, and LLM judges degrade >50% when rebutted. The `/review` phase exists to engineer around those failure modes — adversarial priming, fresh context, required executable output.

Two things this phase does not do:
1. **It does not fix what it finds.** Findings flow back into `/work` (or `/plan` if they're large enough). The reviewer is the critic, not the implementer. Keeping the roles separate prevents the critic from softening its own findings to keep the implementation session moving.
2. **It does not run before deterministic gates are green.** Review is for things typecheckers and tests can't see. If tests are failing, fix those first — no LLM review will help.

## Preconditions

1. Deterministic gates pass on the current change:
   - Typecheck ✅
   - Lint ✅
   - Tests (at minimum the ones affected by the change) ✅
   - Build, if applicable ✅
2. There is a concrete artifact to review:
   - A recent commit or commits (preferred — stable, diffable)
   - An uncommitted diff on a clean base (acceptable)
   - A PR / branch (acceptable)
3. The relevant `.harness/PLAN.md` task is identifiable. Review without a spec is toothless — the reviewer needs to know what the code is *supposed* to do.

If any precondition fails, stop and report. Do not invoke the reviewer on a broken base.

## Inputs the reviewer sees

- The **diff** under review
- The **`.harness/PLAN.md` task** (the "What" and especially "Verification" clauses)
- **`AGENTS.md` / `CLAUDE.md`** — project conventions
- The **code around the change** — the reviewer may read neighboring files

## Inputs the reviewer does NOT see

- The **implementer's reasoning trace** from the `/work` session. This is non-negotiable — the whole point of fresh context is to prevent anchoring on the implementer's justifications.
- The **implementer's self-assessment** ("I think this is correct because…"). If it's in the commit message, strip it before passing to the reviewer.

Fresh context is enforced by dispatching the `adversarial-reviewer` sub-agent — it has no conversation history with the implementer.

## Process

### 1. Verify deterministic gates

Run the full gate suite. If anything fails, stop:
> "Gates not green. [typecheck|lint|tests|build] failed with: <output>. Review blocked until gates pass. Run `/work` to iterate."

Do not proceed to the LLM reviewer.

### 2. Identify the artifact and the spec

Determine:
- **What's being reviewed:** commit SHA range, branch, or uncommitted diff.
- **Which task it implements:** the most recently-completed task in `PLAN.md`, or the one the user names.

Show this to the user in one line before invoking the reviewer:
> "Reviewing: task N '<title>'. Artifact: <sha1>..<sha2> (<N files, ±lines>)."

### 3. Dispatch the adversarial reviewer

Invoke the `adversarial-reviewer` sub-agent (full spec at `harness/agents/adversarial-reviewer.md`). The invocation must include:

- The diff (as patch or file list + current contents)
- The `PLAN.md` task: What, Verification, any relevant Constraints
- A pointer to `AGENTS.md` / `CLAUDE.md` for conventions
- The framing (literal): "The code under review likely contains bugs. Find them."

The sub-agent runs in a fresh context — no parent conversation history.

### 4. Validate the reviewer's output format

The reviewer must return **one** of:

1. **A failing test** (preferred):
   ```
   // path/to/test.ts
   test("X should Y when Z", () => { ... })
   ```
   You will run this test and confirm it fails against the current code.

2. **A specific defect:**
   ```
   DEFECT: path/to/file.ts:42
   Spec says: <verification criterion from PLAN.md>
   Actual: <what the code does>
   Minimal reproducer: <input> → <actual> ≠ <expected>
   ```

3. **Explicit no-issues finding:**
   ```
   NO ISSUES FOUND
   Reviewed: <file list>
   Categories checked: <spec adherence, edge cases, API design, security, dead code, regressions>
   ```

If the reviewer returns **prose** ("consider adding error handling", "this could be cleaner") — reject it. Re-invoke once with the framing tightened:
> "Your previous output was prose, which is not acceptable. Return one of: failing test, specific defect with file:line, or NO ISSUES FOUND with categories. Try again."

If the second attempt is also prose, report to the user and stop. Two-round prose means the reviewer is stuck in rubber-stamp mode and the session should end.

### 5. Verify findings (if any)

Before handing findings back to the user, sanity-check:

- **If it's a failing test:** run it. Confirm it actually fails against the current code. A test that *passes* is not a finding — treat it as prose and reject.
- **If it's a defect reference:** open the file at the line. Does the code do what the reviewer claims? A misread defect wastes `/work` time; an unchecked one wastes even more.

If a finding turns out not to reproduce, note it in the report but do not propagate it as a real finding. This is a check on the reviewer, not censorship — log these in the rejection-rate telemetry too.

### 6. Log the outcome

Append to `.harness/progress.md` one of:

```
<YYYY-MM-DD HH:MM> /review — task N: NO ISSUES FOUND
<YYYY-MM-DD HH:MM> /review — task N: 1 defect found (path/file.ts:42)
<YYYY-MM-DD HH:MM> /review — task N: 1 failing test written (path/test.ts)
<YYYY-MM-DD HH:MM> /review — task N: 2 findings (see report)
```

Rejection-rate telemetry: over time, scan `progress.md` for `NO ISSUES FOUND` vs. findings. If a reviewer's "no issues" rate exceeds ~80% over a sample of 10+ reviews, the framing is broken — revisit `harness/agents/adversarial-reviewer.md`.

### 7. Return findings to the user

The user decides what to do with findings — they don't get auto-fixed. Present:

- **Clean review:** "NO ISSUES FOUND. Categories checked: <list>. Safe to `/release` or continue with next task."
- **Findings:** list them with the reviewer's artifacts. Recommend:
  - **Small fixes** (single-file, test easy to write): re-run `/work` with a one-line task appended to `PLAN.md`.
  - **Larger issues** (design problem, spec misunderstanding): go back to `/plan` to revise the plan before more code happens.
  - **False finding** (verified not to reproduce): note it, move on.

Do **not** fix the findings in this session. `/review` is not an implementation phase.

## Failure modes to avoid

- **Running review before gates pass.** Typecheck failures make the reviewer read broken code; you get noise findings. Gates first.
- **Passing the implementer's reasoning trace to the reviewer.** Defeats the fresh-context design. Pass artifact + spec only.
- **Accepting prose critiques.** "Consider adding error handling" is the canonical rubber-stamp output. Reject and re-invoke once; then stop.
- **Auto-applying findings.** Review reports; `/work` implements. Keep the roles separate.
- **Skipping the reproduce-check.** An unverified finding that doesn't actually reproduce wastes more time than the review saved.
- **Reviewing the whole codebase "while you're at it."** Scope is the current change. Pre-existing issues get their own tasks.
- **Running `/review` on every task.** Not every change needs it — see [03-work.md §"When to invoke /review"](03-work.md). Routine changes can go straight to the next `/work` or `/release`.

## Output to the user (at end of `/review`)

Two shapes, depending on outcome:

**No issues:**
> Review clean for task N: "<title>".
> - Reviewed: 3 files, +48/-12 lines
> - Categories checked: spec adherence, edge cases, API design, dead code
> - Rejection rate so far: 3 clean of 8 reviews (37%) — within the sane band
> - Next: `/work` for task N+1, or `/release` if plan is done

**Findings:**
> Review found 1 issue for task N: "<title>".
>
> **DEFECT** `src/count.js:23` — the loop skips the final line when the file lacks a trailing newline. Spec in PLAN.md says partial last line must count.
> Minimal repro: `printf 'a\nb' | lc` → prints `1`, expected `2`.
>
> - Recommend: re-run `/work` with a fix task. The change is one-file.
> - Next: `/work fix-trailing-newline` (I'll add the task to PLAN.md)
