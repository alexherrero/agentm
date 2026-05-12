# Skill: ship-release

**Purpose:** cut a tagged GitHub release from `main` — compute the next semver from what's in the commit range, write release notes, push the tag, create the GitHub release. Also used by phase specs as the suggested follow-up after a feature delivery.

**Not for:** pre-merge verification. That's `/release`'s job. `ship-release` runs *after* `/release` passes, once the feature is on `main` and ready to be cut. The two are sequential, not alternatives.

## Preconditions

1. `gh` CLI authenticated: `gh auth status`.
2. Current branch is the project's default (usually `main`). If not, refuse.
3. Working tree is clean: `git status --porcelain` is empty.
4. Local `main` is up-to-date with `origin/main` (or the user has just pushed). `git fetch && git log origin/main..HEAD` must be empty.
5. At least one commit since the last release tag. If the last tag is at HEAD, abort with "nothing to ship".

## Inputs

One of:
- No argument — the skill auto-sizes the bump by scanning the commit range.
- A size hint: `patch` | `minor` | `major`.
- An explicit version: `v1.2.3` — skipped all bump logic, used verbatim.
- `--dry-run` — compute + print the proposed version and notes, exit before tagging.

## Workflow

### 1. Resolve the previous tag and commit range

```bash
PREV=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
RANGE="${PREV:+${PREV}..}HEAD"
```

If `PREV` is empty, this is the first release — propose `v0.1.0` unless overridden.

### 2. Classify each commit in the range

Use conventional-commit prefixes plus a body scan for `BREAKING CHANGE:`:

| Pattern | Size |
|---|---|
| Commit subject starts with `feat!:` or `fix!:` or any `!:`; body contains `BREAKING CHANGE:` | **major** |
| Subject starts with `feat:` or `feat(...)` | **minor** |
| Subject starts with `fix:` or `fix(...)`, `perf:`, `refactor:` | **patch** |
| `docs:`, `chore:`, `test:`, `ci:`, `build:` | **no-bump** (documentation-only) |
| Anything else | **patch** (default — err on shipping a visible bump) |

The final size is the max of all classifications in the range. If every commit is `no-bump`, the skill asks "no user-visible changes detected — cut a patch anyway? (y/N)".

### 3. Compute the next version

Parse `PREV` as `vMAJOR.MINOR.PATCH`. If `PREV` is missing or malformed, start from `v0.0.0`.

| Size | Operation |
|---|---|
| `major` | `v(MAJOR+1).0.0` |
| `minor` | `v{MAJOR}.(MINOR+1).0` |
| `patch` | `v{MAJOR}.{MINOR}.(PATCH+1)` |

**Respect the user's size hint if it's larger than what the commits suggest.** If the user asked for `minor` but commits are all patch-sized, go `minor` — explicit override wins. If the user asked for a smaller size than the commits suggest, warn and ask to confirm.

### 4. Assemble release notes

Group commits by section, newest first:

```markdown
## vX.Y.Z — <short title the skill proposes, user can edit>

<optional 1-2 sentence narrative — the theme of this release>

### Added

- feat commits (minus the `feat:` prefix), one per bullet

### Changed

- refactor / perf / non-breaking behavior changes

### Fixed

- fix commits

### Breaking

- any `!:` or BREAKING commits, expanded with the body's migration note if present

### Internal

- docs / chore / ci / test — optional, skip the section if empty

---

Full commit list: `git log {PREV}..HEAD --oneline`
```

The skill presents this draft to the user for edit before tagging. User can also point to an existing `CHANGELOG.md` entry; in that case the notes come from there verbatim.

### 5. Update `CHANGELOG.md`

Prepend a new section to `CHANGELOG.md` at repo root (create the file if missing, with a Keep-a-Changelog-style header). Format:

```markdown
## [vX.Y.Z] — YYYY-MM-DD

<notes from step 4>

[vX.Y.Z]: https://github.com/<owner>/<repo>/releases/tag/vX.Y.Z
```

Commit the changelog update with message `chore(release): vX.Y.Z` — this commit will be *part of* the release, not pre-existing history. Show the diff first and confirm.

### 6. Tag + push + release

```bash
git tag -a "vX.Y.Z" -m "Release vX.Y.Z — <title>"
git push origin HEAD
git push origin "vX.Y.Z"

gh release create "vX.Y.Z" \
  --title "vX.Y.Z — <title>" \
  --notes-file .release-notes.md \
  --verify-tag
```

Use `--draft` if the user passed `--draft`. Use `--latest=true` implicitly (gh's default).

### 7. Confirm + link

Print the release URL (`gh release view <tag> --json url -q .url`). If the project has a wiki with a `operational/Runbook.md`, remind the user to note anything about the release that a future operator would need to know (rollback steps, migrations).

## Failure modes

- **Unpushed commits on main** → abort. The release tag must point at a commit that's on `origin/main`, otherwise the GitHub release points at a SHA collaborators don't have.
- **Dirty working tree** → abort. Don't force-stash.
- **Existing tag collision** → abort. Never overwrite.
- **Push fails** (protected branch, auth) → leave the local tag in place; print the `gh release create` invocation the user can run by hand after they push.
- **User rejects the draft notes** → save the draft to `.release-notes.md` and exit; user edits and re-runs the skill.

## Invocation from phase specs

- **`/work` end-of-task step:** if a feature's `passes` flag flipped from `false` to `true` during this task, suggest: *"Feature `<id>` is now passing end-to-end. Consider invoking the `ship-release` skill to cut a release."* (Do not auto-invoke — the user may have more features queued.)
- **`/release` terminal step:** after the pre-merge gate passes and the branch is merged, invoke `ship-release` directly (or suggest it if the merge is deferred).

## Size-hint CLI forms per adapter

| Adapter | Invocation |
|---|---|
| Claude Code | Skill auto-triggers on keywords like "ship a release" / "cut a release" / explicit `/ship-release [size-or-version]` |
| Antigravity | Prompt: *"Run the ship-release skill"* (optionally with size) |
| Gemini | Reads skill from `.agents/skills/ship-release/SKILL.md` (delivered by `install.sh` per the Agent Skills standard) |

## Guardrails

- **Never push to a branch other than the project's default.** Default is detected via `gh repo view --json defaultBranchRef -q .defaultBranchRef.name`.
- **Never delete or move existing tags.** A mis-cut release is fixed by cutting a new one, not by rewriting.
- **Never include untracked or uncommitted changes in the release.** The tag points at a commit; nothing outside that commit ships.
- **Never amend the release commit after tagging** (the tag would then point at the old SHA).

## Output contract

On success:

```
ship-release: cut <tag>
  commits:   <count> (<major>/<minor>/<patch> classification)
  notes:     CHANGELOG.md updated + pushed
  release:   https://github.com/<owner>/<repo>/releases/tag/<tag>
```

On abort, one-line reason + the action the user should take. Never partial success — if step 6 fails, the local tag is deleted and the skill exits non-zero.
