---
name: dependabot-fixer
description: Fix breakage on a Dependabot PR. Trigger when (a) the current branch matches `dependabot/*` and CI is red, (b) the user asks to "fix the dependabot PR" / "make this dependency update pass", or (c) the user invokes `/dependabot-fix [pr-number]`. Reads failing CI logs and upstream CHANGELOG, applies a bounded fix loop, pushes commits to the Dependabot branch, comments residual risks on the PR. Never merges. Aborts honestly when the fix needs human judgment.
---

You are running the `dependabot-fixer` skill. Full canonical spec: `harness/skills/dependabot-fixer.md` in the agentic-harness repo. The summary below is the operational version.

## Preconditions (check first, abort if not met)

1. `gh` CLI is authenticated: `gh auth status`.
2. Working tree is clean: `git status --porcelain` returns empty. If not, refuse.
3. `.harness/verify.sh` exists and is executable. If not, warn and fall back to language defaults (`go test ./...`, `npm test`, `pytest`, etc.).

## Identify the target PR

- If current branch matches `dependabot/*` → operate on the PR for that branch (`gh pr view --json number`).
- If user passed a PR number → use it.
- Otherwise → list open Dependabot PRs with red CI and ask which one:
  ```
  gh pr list --author "app/dependabot" --json number,title,headRefName,statusCheckRollup \
    | jq '[.[] | select(.statusCheckRollup[]?.conclusion == "FAILURE")]'
  ```

## Gather context

```
gh pr view <n> --json title,body,headRefName,files,statusCheckRollup
gh run view <latest-failed-run-id> --log-failed > /tmp/dependabot-fix-logs.txt
```

Extract: ecosystem, package, old version, new version, delta (patch/minor/major).

Try to fetch the upstream CHANGELOG for the bump range (GitHub releases of the package's source repo). If unavailable, proceed without it but lower your confidence accordingly.

Read `.harness/known-migrations.md` (if it exists). If the package matches a recipe, that recipe is your first fix attempt.

## Diagnose (kept in scratch — do not write to disk yet)

Produce: failure category, confidence (high/medium/low), proposed fix in 1–2 sentences with the files to edit. **If confidence is low → abort, do not attempt.**

## Bounded fix loop

Budget: 3 iterations (override with `DEPENDABOT_FIX_BUDGET` env).

```
for i in 1..budget:
  apply the proposed fix (Edit tool)
  bash .harness/verify.sh
  if exit 0: break
  re-read failing output; produce next diagnosis (if confidence drops to low → abort)
```

## Hard rules — never violate

- **Never merge the PR.** Human merges.
- **Never modify tests** to make them pass (AGENTS.md rule 5).
- **Never disable lint/type checks** to dodge errors.
- **Never push to the default branch.** Only to the Dependabot branch.
- **Never pin to an older version** to escape the bump. If it can't be fixed, abort.
- **Never claim success unless `verify.sh` exited 0 in the final iteration.**
- **Never touch more than 10 files** in one fix attempt (override with `DEPENDABOT_FIX_MAX_FILES`). Broader changes need a human.

## On success

- Commit each iteration separately: `fix: update call sites for <pkg> v<old>→v<new>`.
- `git push` to the Dependabot branch.
- Comment on the PR via `gh pr comment <n> --body-file -`:
  - Summary + linked CHANGELOG entry.
  - Files touched.
  - **Residual risks** — always include this section, never claim "fully verified".
- Append one line to `.harness/progress.md`: `dependabot-fixer: <pkg> v<old>→v<new> fixed in <N> iterations`.

## On abort

- Comment on the PR: diagnosis, what was tried, what's blocking, concrete next step for the human.
- Discard partial fixes that don't leave the tree in a passing state.
- Append to `.harness/progress.md`: `dependabot-fixer: <pkg> v<old>→v<new> ABORTED — <reason>`.
- Exit with a clear failure message.

## Scope note

This skill exists for the **major-version Dependabot PR where CI failed** case. Green-CI auto-merge is handled by GitHub's native Dependabot auto-merge action — do not try to replicate that here.
