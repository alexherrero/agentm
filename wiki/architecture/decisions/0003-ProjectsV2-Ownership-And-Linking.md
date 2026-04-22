# ADR 0003: ProjectsV2 ownership and linking

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-04-21

## Context

The plan "GitHub Projects wiring + documenter end-to-end dogfood" wired a preview-and-ask `gh project item-create` offer into every phase. The first iteration assumed the obvious shape: a repo-scoped project, created at `/setup`, referenced by number from `.harness/project.json`, and visible under `github.com/<owner>/<repo>/projects` by default.

The dogfood run (task 3) falsified the assumption:

- `gh project create --owner @me` creates a **user-scoped** project (or `--owner <org>` for org-scoped). There is no `--owner <owner>/<repo>` form.
- The project created this way does *not* appear under `github.com/<owner>/<repo>/projects`. It lives only at `github.com/users/<user>/projects/<N>` (or the org equivalent).
- Users browsing the repo can't discover the project. Cross-referencing an item from a commit or issue requires the full user-scoped URL.

This is a property of GitHub ProjectsV2 — the generation that replaced the repo-scoped "classic projects" in 2023. ProjectsV2 has no repo-owned form. The only way to make a ProjectsV2 project appear under a repo is `gh project link --repo <owner>/<repo>`, which creates a **linkage** between a user-or-org-owned project and one or more repos.

There was an additional surface snag: `gh project link` requires the literal owner, not `@me`, even when they resolve to the same account. Passing `@me` sometimes fails with `"'<repo>' has different owner from '@me'"`. This is a `gh` CLI quirk, not a GitHub API one, but the harness has to document it because `/setup` runs both calls in sequence and the first can legitimately use `@me`.

The mid-dogfood surprise (project #1 created, invisible under the repo, deleted; project #2 created + linked, visible) argued for documenting the shape as an ADR rather than burying it as a code comment — future maintainers hitting the same surprise should find the rationale fast.

## Decision

At `/setup`, the Projects opt-in runs **two** `gh` calls in sequence, not one:

```bash
# Step 1 — create. User- or org-scoped.
gh project create --owner <@me-or-org> --title "<repo-name> backlog" --format json

# Step 2 — link to the repo. --owner must be the LITERAL username/org, never @me.
gh project link <number> --owner <literal-owner> --repo <owner>/<repo>
```

The literal spec lives in [`harness/phases/01-setup.md` §8](https://github.com/alexherrero/agentic-harness/blob/main/harness/phases/01-setup.md#L110-L160). The `@me`-vs-literal gotcha is documented inline as a code comment in that spec.

`.harness/project.json` gains a `repo` field alongside the existing `{owner, number, url}`:

```json
{
  "github": {
    "owner": "<username-or-org>",
    "number": <N>,
    "url": "https://github.com/users/<username>/projects/<N>",
    "repo": "<owner>/<repo>"
  }
}
```

The per-phase Projects wiring in `/plan`, `/work`, `/review`, `/release` reads only `owner` and `number`. The `repo` field is load-bearing for the harness itself — it records the linkage on disk so `/setup --update` and dogfood-freshness checks can re-verify the project is still linked to this repo.

Preview-and-ask is mandatory for **both** `gh` calls. The user must see and confirm the create command before it runs, and the link command separately. Declining either leaves the system in a clean state (no project created, or a project that exists but isn't linked; both are recoverable).

## Consequences

**Positive**

- **Projects appear under the repo.** Users browsing `github.com/<owner>/<repo>/projects` see the backlog, can cross-link from issues and commits, and can onboard collaborators to the project from the repo page rather than needing the user-scoped URL.
- **The `repo` field makes the linkage auditable.** A `--update` run can re-check `gh project view <N> --owner <owner>` and confirm the project is still linked to `.harness/project.json#.github.repo`. Without this, a silent unlink would only surface when a phase tried to create an item and hit a stale reference.
- **User-or-org flexibility falls out for free.** Teams with an org-owned backlog use the same two-call flow; only the `--owner` argument differs. No additional code path.
- **The `@me`-vs-literal footgun is documented at the point of use.** Future maintainers hitting the gotcha find the fix in the same file as the `gh` call rather than having to rediscover it.

**Negative**

- **Two `gh` calls, two preview-and-ask prompts.** A user opting in sees two confirmation dialogs at `/setup` rather than one. Mitigation: `/setup` batches the interview questions up front (owner + title) so the two calls run back-to-back after a single pass of confirmation.
- **Failure between step 1 and step 2 leaves an orphan project.** If `gh project create` succeeds but `gh project link` fails (network, scope, typo), the user has a live project that isn't linked to the repo. Mitigation: `/setup` prints the exact `gh project link` command to rerun; the user can invoke it manually, or delete the orphan and rerun `/setup`. Not a clean rollback, but a rare and recoverable failure mode.
- **ProjectsV2-only.** Classic repo-scoped projects aren't supported. Given GitHub is sunsetting classic projects, this is not worth solving.
- **No org-auto-detection.** The user picks `@me` vs `<org>` interactively. The harness could default to the repo's owner via `gh repo view --json owner`, but even with a default the user still confirms — so the saving is one keystroke. Left for a future refinement if the pattern gets enough use.

**Load-bearing assumptions**

- `gh` CLI continues to require a two-call flow for ProjectsV2 create-plus-repo-visibility. If GitHub adds a `--link-repo` flag to `gh project create`, collapse the two calls into one and keep the behavior.
- The `@me`-vs-literal quirk on `gh project link` persists. Re-check on every `gh` CLI major bump; remove the inline comment if the quirk goes away.
- ProjectsV2 remains the only project system GitHub ships. Classic projects are effectively archival.
