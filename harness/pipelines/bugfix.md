# Pipeline: bugfix

Triage-first pipeline for bug reports. Replaces `/plan` + `/work` when the work is driven by a defect, not a new feature. Adapted from [Pimzino/claude-code-spec-workflow](https://github.com/Pimzino/claude-code-spec-workflow)'s bug-fix pipeline — one of the few harness patterns in OSS that formalizes defect handling instead of treating bugs as a special case of features.

> [!NOTE]
> **State-file resolution (V4 #26 + #37).** **Invoke the dispatcher CLI for state-file reads + writes — don't bare-`Read .harness/<file>`:**
>
> ```bash
> python3 scripts/harness_memory.py read-state PLAN.md       # bugfix plan
> echo "$NEW" | python3 scripts/harness_memory.py write-state PLAN.md
> python3 scripts/harness_memory.py vault-state-path PLAN.md  # resolve path
> ```
>
> Dispatcher resolves vault path → legacy `<project>/.harness/<file>` fallback. Writes go to vault unless local state mode is configured **on-host** (a repo-local `.project-mode` marker, or `state_mode` in `.agentm-config.json`; DC-8).

## Why a separate pipeline

Bugs have different failure modes than features:
- **Root cause vs. symptom.** The first suspicious line is often not the bug. Jumping to the fix skips the part of the job that matters.
- **Reproduction is half the work.** A bug you can't reproduce is a bug you can't verify you fixed.
- **Regression tests are mandatory.** A fix without a test is a bug that will come back.

Running bugs through `/plan` + `/work` accidentally lets the discipline lapse. A dedicated pipeline keeps it tight.

## Four phases

Every phase ends with a posterity update to a GitHub Issue (see [Issue as posterity record](#issue-as-posterity-record)). The issue is opened in Phase 1, commented on in Phases 2–4, and closed at the end of Phase 4. This makes the bug's full trajectory — report, root cause, fix, verification — publicly auditable without the reader having to dig through commits or `PLAN.md` history.

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

**Open the tracking issue.** After writing the Report, propose a GitHub Issue with a one-sentence title + body derived from the report (verbatim quote + source/date/reporter + any reproduction steps). Preview title and body to the user, then run `gh issue create --label bug` on confirmation. Record the issue number in `.harness/PLAN.md` as `**Tracking:** #N` near the top. Reference `#N` in the fix commit. If `gh` is unavailable, the repo has no GitHub origin, or the user opts out, **skip** the issue entirely — fall back to `PLAN.md`-only and note the skip in `## Report`. Covered by [documentation.md §GitHub Projects + Issues](../documentation.md) ("always asks with title + body preview before `gh issue create`").

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

If root cause analysis reveals the bug is actually a symptom of a larger design problem, **stop**. Surface it to the user: this may need `/plan`, not `/bugfix`. Patching a symptom of a design flaw creates two bugs. (In this case, post the Analysis comment to the issue anyway and leave it open, pending the `/plan` outcome — then link the resulting PR back from the issue.)

**Post Analysis to the tracking issue.** Propose a comment mirroring the `## Analysis` block (Reproduction / Root cause / Why it happened / Scope / Fix strategy). Preview to the user, then run `gh issue comment $N -b <body>` on confirmation. Skip silently if no issue was opened in Phase 1.

### 2b. Auto-recall known-issues (graceful-skip if not installed)

**Execution note:** invoke this **at the start of §2 Analyze, before reproduction**, even though it documents in §2b. The recall informs the "why" analysis chain — if a vault entry shows you've hit this pattern before, the third "why" comes faster.

If MemoryVault is installed (`MEMORY_VAULT_PATH` env set + directory exists), load known-issues for this project. The "this is the third time we've hit this CRLF issue" pattern is the highest-signal use case — recurring root causes often share a single underlying fix.

```bash
SLUG=$(python3 scripts/vault_project.py read . 2>/dev/null || true)
python3 scripts/harness_memory.py recall --phase bugfix --project "${SLUG:-}"
```

What this loads (per `_PHASE_PROJECT_DIRS["bugfix"]` in `harness_memory.py`):
- `personal-private/_always-load/*.md` — operator-global conventions (debugging style, reproduction discipline).
- `projects/<slug>/known-issues/*.md` — prior gotchas + recurring root causes (resolver-aware: also reads from legacy `personal-projects/<slug>/known-issues/` if vault rename hasn't run yet).

Budget defaults to 6k tokens (override via `HARNESS_RECALL_BUDGET_BUGFIX` env); cap is 5 entries. If a known-issue entry matches the current bug's surface area, factor it into the §2 Analysis: the prior fix path may apply directly, or the recurrence may signal a deeper design issue that warrants a `/plan` rather than another patch.

**Graceful-skip conditions** (silent):
- `MEMORY_VAULT_PATH` env unset or directory missing.
- `scripts/harness_memory.py available` exits 1.

See [ADR 0007](../../wiki/explanation/decisions/0007-auto-context-into-harness-phases.md) for the recall-budget rationale + the bugfix-specific surface mapping.

### 3. Fix

Implement the fix under `/work` discipline (see [03-work.md](../phases/03-work.md)) with two bugfix-specific rules:

- **Regression test first.** Write a test that *fails against the current code* and will *pass after the fix*. If you can't write a failing test, you don't have a clear enough grip on the bug to fix it — go back to Analyze.
- **Minimal scope.** Fix the bug, not adjacent issues. "While I'm in here" is how a one-line fix becomes a regression-introducing rewrite. Adjacent issues go on the backlog.

When the fix is done:
- Regression test passes
- Pre-existing tests still pass
- Deterministic gates are green

**Post Fix summary to the tracking issue.** After the commit lands, propose a comment with: commit SHA, regression test path + one-line description, bullet list of files changed (no diff), gate results ("all green: typecheck / lint / test / build"). Preview, then `gh issue comment $N` on confirmation.

### 4. Verify

Run `/review` on the fix (this is one of the cases where review is non-negotiable — bugs flagged the code as fragile, fresh eyes matter).

Additionally confirm:
- The regression test *exists* and is committed
- The regression test *actually exercises the root cause*, not just the symptom
- The original reproduction steps from `## Report` now produce the expected behavior

Dispatch the `documenter` sub-agent (full spec: [`harness/agents/documenter.md`](../agents/documenter.md)) with the bug report and the fix diff. Bugfix is a **lightweight pass** — docsub does nothing for run-of-the-mill bugs (typo fix, null check, off-by-one). It updates only when the fix reveals a gotcha worth persisting:

- **`wiki/reference/Known-Issues.md`** — append only if the bug exposes a non-obvious reproduction condition, an environmental dependency, or a surprising interaction between features that a future reader would benefit from seeing listed.
- **`wiki/explanation/decisions/<NNNN>-<slug>.md`** — add an ADR only if the fix implies a design-decision change that wasn't previously recorded.

Over-documentation is drift too. If docsub returns `NO CHANGES` that's the expected outcome for most bugfixes.

**Post Verify summary to the tracking issue, then close it.** Propose a final comment with: `/review` outcome (clean | N findings addressed), evidence that the original reproduction no longer reproduces, docsub outcome (`NO CHANGES` | which wiki pages updated). Preview, then `gh issue comment $N` on confirmation. Immediately after, propose `gh issue close $N --reason completed` with a one-line closing note referencing the fix commit SHA.

Append to `.harness/progress.md`:

```
<YYYY-MM-DD HH:MM> /bugfix — fixed <one-line description> (tracking: #N, root cause: <summary>, regression test: <path>)
```

### 4b. Offer-save gotcha to known-issues (graceful-skip if not installed)

If `harness_memory.py available` exits 0 AND the bug had a **non-obvious root cause** (env-specific, platform-specific, surprising-interaction, recurring-pattern), offer to persist it to MemoryVault. This is the bug-equivalent of `/work`'s §7b — the difference is that bugfix gotchas tend to be higher-signal because the bug surfaced because the prior fix wasn't durable enough.

```bash
cat > /tmp/known-issue-<slug>.md <<EOF
# <one-line title>

**Surfaced:** <YYYY-MM-DD> as bug #<N>
**Root cause:** <file:line, one-sentence>
**Reproduction:** <steps that reliably trigger>
**Fix:** <commit SHA + one-line description>

**Why this is durable:** <paragraph — what makes this worth remembering, what
future bug would benefit from this entry being recalled>
EOF

python3 scripts/harness_memory.py offer-save \
    --phase bugfix --project "<slug>" \
    --kind gotcha --slug "<date>-<short-slug>" \
    --content-file /tmp/known-issue-<slug>.md \
    --confidence <0.0-1.0> \
    --confidence-reason "<one-line rationale>"
```

**Confidence rubric** (per ADR 0007):
- **High (≥0.85)** when the root cause involves an environment/platform-specific issue with deterministic reproduction steps (e.g. "Windows cp1252 stdout encoding", "macOS APFS case-insensitive collision") — these recur and the entry's value compounds.
- **Medium (0.7)** when the root cause is non-obvious but project-internal (e.g. "feature flag X interacts badly with cache layer Y") — useful in this project, less reusable elsewhere.
- **Low (0.5)** when the root cause is narrow + unlikely to recur (e.g. typo, off-by-one) — operator should confirm before persisting; most narrow bugs don't merit a known-issues entry.

Per the self-modulating ask contract (Q4), confidence ≥ `HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD` (default 0.8) saves silently; below threshold fires the preview-and-ask prompt. Non-TTY stdin defaults to skip.

**ADR write is operator-controlled, not auto.** If the bug exposes a design-decision change (§4 already covered this — docsub flags it), the operator writes the ADR via `/design author` or directly; the harness does not auto-write ADRs. The known-issues entry is the cheap, durable artifact; the ADR is the expensive, intentional one.

**Cap at 1 known-issue per `/bugfix`.** A single bug rarely produces more than one durable gotcha. If the analysis surfaced two distinct issues, that's a sign §2's "stop and ask if this needs `/plan` instead" rule should have fired.

**Graceful-skip conditions** (silent):
- `harness_memory.py available` exits 1.
- `HARNESS_AUTO_SAVE_MODE=off`.
- Bug had an obvious / narrow root cause — no durable gotcha to persist.

## Issue as posterity record

The tracking issue accumulates the full bug lifecycle as a chronological record:

| Phase | Issue action | Preview required |
|---|---|---|
| Report | `gh issue create --label bug` | title + body |
| Analyze | `gh issue comment` with Analysis | body |
| Fix | `gh issue comment` with Fix summary + commit SHA | body |
| Verify | `gh issue comment` with Verify summary, then `gh issue close --reason completed` | body + close reason |

Why:
- **Commits are a diff, issues are a narrative.** A future reader shouldn't have to reconstruct why a fix was shaped a certain way from the commit alone. The issue comments carry the Analysis, the adversarial-review findings, and the verification evidence.
- **Issues outlive `.harness/PLAN.md`.** PLAN.md gets overwritten by the next pipeline. The issue persists.
- **One canonical thread.** Slack threads / chat history disappear or require access. The GitHub Issue is searchable, linkable, and attached to the fix commit via `Closes #N`.

Graceful-skip conditions (checked once, at Phase 1):
- `gh auth status` exits non-zero, OR
- `git remote get-url origin` doesn't match `github.com`, OR
- User opts out when the Phase 1 issue preview is shown.

When skipped, `PLAN.md` alone is the record. Note the skip reason in `## Report`.

"Always ask with preview" is non-negotiable per [documentation.md §GitHub Projects + Issues](../documentation.md). The agent proposes title/body/close-reason text up front; the user confirms before `gh` runs. No silent automation.

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
