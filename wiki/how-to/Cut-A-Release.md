# How to cut a release

> [!NOTE]
> **Goal:** Publish a tagged GitHub release of the harness with `CHANGELOG.md` notes and an `install.sh`-discoverable version string.
> **Prereqs:** `/release` has passed on a branch merged to `main`; CI on `main` is green; `gh` is authenticated.

Releases are cut by the [`ship-release` skill](https://github.com/alexherrero/agentm/blob/main/harness/skills/ship-release.md), not by hand. The skill computes the next semver from conventional-commit prefixes in the range since the last tag, drafts notes in Keep-a-Changelog format, prepends to `CHANGELOG.md`, tags, pushes, and creates the GitHub release via `gh`. Every step confirms with the user before acting.

## Prerequisites

1. `/release` passed — clean tree, full test suite green, feature flags flipped truthfully.
2. The branch is merged to `main` and pushed (the tag must point at a commit on `origin/main`, otherwise collaborators can't resolve it).
3. CI on `main` is green:

   ```bash
   gh run list --branch main --status success --limit 1
   ```

4. Dogfood-freshness checked (see below).

## Steps

1. Invoke the skill — exact surface depends on the adapter:

   | Adapter | How to invoke |
   |---|---|
   | Claude Code | `/ship-release [size-or-version]` or phrase like "ship a release" |
   | Antigravity | "Run the ship-release skill" (optionally with a size) |

2. Size arguments: `patch` / `minor` / `major` override the auto-sized bump. A literal version like `0.8.0` pins the tag exactly (used when the commit range's auto-size undershoots — see the [v0.8.0 entry in `CHANGELOG.md`](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md) for a worked example).

3. Confirm each preview-and-ask prompt (tag, changelog, push, `gh release create`).

## Semver sizing

| Commit prefix(es) | Bump |
|---|---|
| `feat!:`, any body with `BREAKING CHANGE:` | major |
| `feat:` | minor |
| `fix:`, `perf:`, `refactor:` | patch |
| `docs:`, `chore:`, `ci:`, `test:` | no bump (skill aborts unless the user passes an explicit size) |

User-supplied size hints larger than the commit range suggests are honored. Smaller hints trigger a confirmation prompt.

## Dogfood-freshness check

Run before invoking the skill. This repo's own `wiki/` is hand-maintained dogfood — it references specific line ranges in `install.sh` and specific files in `scripts/`; as the harness evolves those can drift.

1. Search the wiki for `#L<line>` anchors:

   ```bash
   grep -rn '#L[0-9]' wiki/
   ```

2. For each hit, open the linked file at that line and confirm the referenced block is still there. If it moved, update the anchor or drop the line-precision.
3. Confirm every `(Page-Name)` cross-link resolves — visual scan of [`wiki/_Sidebar.md`](https://github.com/alexherrero/agentm/blob/main/wiki/_Sidebar.md) or run `python3 scripts/check-references.py`.
4. Confirm the Quick Reference tables still match the shipped commands (especially `install.sh --update` ownership and the CI job list in [CI-Gates](CI-Gates)).

If any of these drift, refresh the wiki page *before* the release, not the anchor. The installer-boundary test at [`scripts/test-install.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/test-install.sh) proves drift never leaks into target projects (it runs `diff -r templates/wiki/ <scratch>/wiki/` byte-for-byte on every CI run); this manual check keeps drift from misinforming contributors to the harness repo.

## Verify

```bash
gh release view v<tag>     # points at the new tag + has notes
git describe --tags         # returns the new version string
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "Unpushed commits on main" | Local `main` ahead of `origin/main` | `git push origin main`, retry |
| "Dirty working tree" | Uncommitted changes | Commit or stash, retry — the skill will not force-stash |
| "Existing tag collision" | The computed tag already exists | Bump the size hint, or cut a `-fix` patch on top |
| `gh release create` fails after tag push | Auth / network / protected branch | Tag is already pushed; run the `gh release create` invocation by hand, the skill prints it |

See [ship-release spec](https://github.com/alexherrero/agentm/blob/main/harness/skills/ship-release.md#L129-L135) for the full list. See [CI-Gates](CI-Gates) for what has to be green before invoking the skill.
