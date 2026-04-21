# Phase: release

Pre-merge gate. The last checkpoint before work becomes shared, visible, or shipped. Enforces that the plan is actually done, gates are green on a clean base, and the human decides to pull the trigger — not the agent.

## Purpose

Release is where coherence meets blast radius. Everything up to this point has been local and reversible; a merge/tag/deploy is neither. The agent's job is to *verify* and *prepare*, not to push the button.

## Preconditions

1. `.harness/PLAN.md` has `Status: done` and all tasks are `[x]`.
2. `/review` ran on the last task (or earlier if the task was routine) and findings were resolved — no open defects.
3. Working tree is clean. No uncommitted changes, no untracked cruft.
4. The current branch is ahead of its base (there's actually something to release).

If any precondition fails, stop and report which one. Do not proceed to the gate suite.

## Process

### 1. Verify plan completion

Read `.harness/PLAN.md`. Confirm:
- `Status: done`
- All tasks are `[x]`
- No "Risks / open questions" marked unresolved

If a task is still `[ ]`, stop:
> "Task N ('<title>') is not complete. Run `/work` to finish it, or update PLAN.md if it's intentionally deferred."

### 2. Re-run deterministic gates, full suite

Not a subset. The full gate suite on the current branch:
- Typecheck
- Lint
- Unit tests (full suite)
- Integration tests (if they exist)
- Build (production build, not just dev-server)

This is the last local check. Any failure stops the release — go back to `/work` or `/bugfix`.

### 3. Update features.json

Walk the features list. For each feature this plan implemented, set `passes: true` — but *only if* both:
- The feature's behavior is exercised by the current tests (or was manually verified), AND
- `/review` did not flag an unresolved issue against it.

This is the one place `passes: true` gets set. Do not set it speculatively.

### 4. Wiki full-pass sweep

Dispatch the `documenter` sub-agent (full spec: [`harness/agents/documenter.md`](../agents/documenter.md)) with the complete diff since `/plan` started (`git diff <plan-start-sha>..HEAD`) and the entire `wiki/` tree. Docsub's release-time responsibilities:

1. Every completed task has reached `Status: implemented` on the right page. Fix any that got missed during `/work`.
2. Any new subsystem / feature / decision that surfaced during implementation but wasn't documented — create the page now.
3. Update `Home.md` and `_Sidebar.md` to reflect any pages added / renamed / removed during this plan.
4. If the plan introduced a non-obvious architectural choice, add an ADR at `wiki/architecture/decisions/<NNNN>-<slug>.md` (Template 3). Number one higher than the highest existing ADR; start at `0001` if none exist.
5. Append a reverse-chronological entry to `wiki/development/Completed-Features.md` — one line in the overview table + a section below with date, branch/PR ref, and a 2–3 sentence summary.

**Block the release** if docsub returns `OPEN QUESTIONS` it can't auto-answer. Surface them to the user; do not proceed until resolved. Shipping with stale docs is how the wiki becomes untrustworthy, and once it's untrustworthy the whole convention is worthless.

### 5. Update changelog / release notes

If the project has a `CHANGELOG.md` / `RELEASES.md`, add an entry for this release:
- Version bump (following project convention — semver, calver, date-based)
- User-visible changes grouped: Added / Changed / Fixed / Removed
- Credit where applicable (issue numbers, PRs, contributors)

If the project doesn't have a changelog, skip this step unless the user asks for one. Don't introduce new conventions in a release session.

### 6. Verify CI state (if applicable)

If there's a PR or remote branch:
- `gh pr checks` — all green
- Any required approvals satisfied

If CI is red or checks are pending, stop:
> "CI not green: <failing check>. Wait for it to complete, or fix and `/work` again."

### 7. Prepare, don't execute

At this point the release is *ready*. The agent does not:
- Push to main
- Tag the release
- Create the GitHub release
- Deploy
- Merge the PR

These actions are high blast-radius and explicitly require human confirmation. Instead, summarize:

> "Ready to release. Next steps (your call):
> - `git push` — to push the release-prep commit
> - `gh pr merge` — to merge the PR
> - Invoke the `ship-release` skill — to auto-size the semver bump, write CHANGELOG.md, tag, push, and create the GitHub release in one flow
>
> I'm stopping here. Say the word if you want me to run any of the above."

Wait for explicit confirmation on each action. "Looks good" is not confirmation; "push and merge" is.

Once the user confirms the merge (or push to default branch) is done, the `ship-release` skill is the recommended follow-up — it handles the tag + GitHub release cut with conventional-commit-driven version sizing. `/release` and `ship-release` are sequential: `/release` is the pre-merge gate, `ship-release` is the post-merge tag cut.

### 8. Offer next-release themes to the GitHub Project (optional)

If `.harness/project.json` exists and `gh` is available on PATH, scan this release's accumulated deferred items (from `/plan`'s Out of scope, `/work`'s out-of-task findings, `/review`'s deferred findings) for a **recurring theme** that suggests next-release planning — e.g. "several adapters missing feature X", "user-visible docs need a sweep", "test coverage lag on subsystem Y". A theme is a pattern, not a single item. If no pattern emerges, skip silently.

Propose **at most one** project item per release session. Preview title + body to the user. On confirmation, run:

```bash
gh project item-create <number> --owner <owner> \
  --title "<theme — e.g. 'next release: sweep docs for adapter parity'>" \
  --body "<body — the individual deferred items that surfaced this theme, with links>"
```

reading `number` and `owner` from `.harness/project.json`.

**Graceful-skip conditions** (silent, no prompt):
- `.harness/project.json` is absent.
- `gh auth status` fails or `gh` is not on PATH.
- No recurring theme emerged this cycle (the default — a single deferred item is not a theme).

Preview-and-ask is non-negotiable per [`documentation.md §GitHub Projects + Issues`](../documentation.md). The bar for a `/release`-time theme is higher than a per-phase item: release proposals should represent real cross-session patterns, not restatements of individual `/work` deferrals.

### 9. Log

Once the user has taken the release actions (or chosen not to), append to `.harness/progress.md`:

```
<YYYY-MM-DD HH:MM> /release — prepared vX.Y.Z (M tasks, N features); user <merged|pushed|held>
```

If the release was held, leave `PLAN.md` as-is. If it shipped, archive or clear `PLAN.md` to make room for the next plan — ask the user which.

## Failure modes to avoid

- **Auto-pushing or auto-merging.** The blast radius is too large to trust to the agent, and a user approving "release" once doesn't mean they approve every push/merge/tag forever.
- **Setting `passes: true` without verification.** Speculative success claims poison the telemetry. Only set it on features that are actually verified.
- **Ignoring a red CI.** A failing check that "doesn't apply" still needs to be marked as such. Don't release past it.
- **Shipping with open `/review` findings.** Resolve first, release second.
- **Running on a dirty working tree.** Uncommitted state means the release isn't the state you think it is.
- **Inventing changelogs for projects that don't have one.** Stay in the project's lane.

## Output to the user

```
Release v1.2.0 is ready.

- Plan complete: 4 of 4 tasks, all gates green
- Review: clean on last task
- CHANGELOG.md updated (Added: JSON output mode; Fixed: trailing-newline count)
- CI: all checks green on origin/main
- Features set to passes: true: feat-lc-file-mode, feat-lc-stdin-mode, feat-lc-json-output

Next (your call):
  git push
  gh release create v1.2.0 --notes-from-tag
  gh pr merge <num>  (if this is a PR workflow)

Say the word.
```
