# Documentation convention

How every project installed with this harness documents itself. Shipped as a scaffold by `install.sh`, maintained by the `documenter` sub-agent at phase boundaries, synced to the GitHub Wiki on push.

## Purpose

A project's documentation is for two readers: a human who needs to understand the system without reading every file, and an agent who needs to resume work in a future session without the original context. This convention exists so that:

1. Both readers find docs in the same place with the same shape across every project.
2. Docs are narrative and rich (tables, diagrams, cross-links) — not a spec dump.
3. Updates happen at phase boundaries, not dynamically alongside code, so they reflect what shipped — not what was planned.
4. Drift is caught by an adversarial full-pass at `/release`, not left to human vigilance.

## Where docs live

Four surfaces, each with a defined job:

| Surface | Purpose | Source of truth |
|---|---|---|
| `wiki/` folder | Narrative, evergreen knowledge | Repo |
| GitHub Wiki | Mirror of `wiki/` for browsability | Synced, read-only |
| GitHub Projects | Future ideas, deferred feature work | GitHub |
| GitHub Issues | Bugs + their fixes | GitHub |

`.harness/PLAN.md`, `features.json`, and `progress.md` continue to own *current-work state*. Wiki owns *durable knowledge*.

## The `wiki/` folder

Four subdirs at repo root, each with multiple files:

```
wiki/
├── README.md                   # this convention, installed copy
├── Home.md                     # landing page (docsub maintains)
├── _Sidebar.md                 # nav (docsub maintains)
├── development/                # how to build, run, test, contribute locally
├── operational/                # how to run / observe / debug in production
├── design/                     # product/UX intent, features, rationale
└── architecture/               # subsystems, data flow, decisions (ADRs)
```

### What belongs where

- **development/** — Getting started, environment setup, testing, conventions, troubleshooting, completed-features log.
- **operational/** — Deployment, runbook, observability, configuration, rollback.
- **design/** — Product intent, user flows, features (one page per user-visible feature), open design questions.
- **architecture/** — System overview, subsystems (one page per subsystem), data model, integrations, decisions (ADRs under `architecture/decisions/<NNNN>-<slug>.md`).

Pages outside these four sections are not part of the convention — either file them under an existing section or add a subdir with a rationale in that section's `README.md`.

### Filename rules

- `CamelCase-With-Dashes.md` (matches GitHub Wiki URL convention).
- Globally unique across subdirs — basename collisions fail the sync workflow loudly.
- Subdirs are allowed (e.g. `design/features/access-token-refresh.md`), kept shallow.

## Templates

Every wiki file starts with `#` H1 + a one-paragraph summary. No YAML front-matter. Three shapes:

### Template 1 — "Page" (the default)

```markdown
# <Title>

<1-paragraph summary: what this page covers and who it's for.>

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| <common lookup> | <answer with cross-links> |
| Where's the code? | [`path/to/file.ts`](github-url) |
| Related pages | [Page One](Page-One), [Page Two](Page-Two) |

## <Semantic section>
<prose, tables, diagrams, alerts, code blocks>

## <Semantic section>
...
```

`⚡ Quick Reference` is encouraged, optional for tiny pages. Section headers are chosen for the page — not a fixed list.

### Template 2 — "Status" extension

Layered on Template 1. Used for pages `documenter` tracks through `pending → implemented → deprecated`: `design/features/<slug>.md` and `architecture/subsystems/<name>.md`.

```markdown
# Feature: <Title>

> [!NOTE]
> **Status:** pending
> **Plan:** `.harness/PLAN.md#task-N`
> **Last updated:** YYYY-MM-DD

<1-paragraph summary.>

## ⚡ Quick Reference
| ... | ... |

## Intent
<user-facing why. docsub leaves this alone after /plan writes it.>

## Design
<how it works. Tables, diagrams, `file:line` links. docsub updates if plan shifted.>

## Implementation
<filled in by docsub post-/work. Real `file:line` references, actual behavior.>

## Notes
<footguns, follow-ups, deferred items.>
```

### Template 3 — "ADR"

Only for `architecture/decisions/<NNNN>-<slug>.md`.

```markdown
# ADR <NNNN>: <Title>

> [!NOTE]
> **Status:** proposed | accepted | superseded-by-<NNNN>
> **Date:** YYYY-MM-DD

## Context
## Decision
## Consequences
```

## Stylistic conventions

- **Tables over bullet lists** for comparative information.
- **Diagrams** — ASCII in fenced code blocks or Mermaid. Use one whenever a relationship is clearer drawn than described.
- **GitHub alerts** for load-bearing callouts: `> [!NOTE]`, `> [!IMPORTANT]`, `> [!WARNING]`.
- **Emoji section markers**, consistent across pages: 🛠 Development · 📟 Operational · 🎨 Design · 🏗 Architecture · ⚡ Quick Reference · 📁 File Layout · 🤝 Integration.
- **Cross-links**: `[text](Page-Name)` for wiki pages, full GitHub URLs (with `#L<line>`) for `file:line` references into code.

## `Home.md` and `_Sidebar.md`

Maintained by the `documenter` sub-agent (not generated by sync). Sync is a dumb mirror. During `/release`, docsub updates both to reflect any added / renamed / removed pages.

## The `documenter` sub-agent

Canonical spec: [`harness/agents/documenter.md`](agents/documenter.md). Adapter copies under `adapters/*/agents/documenter.md`. Scope, tools, and guardrails are defined there — this section is the integration surface.

**Write scope:** `wiki/**` and `.harness/project.json`. Nothing else in the repo.

**Invoked at phase boundaries only. Never during `/work`'s implement step.**

| Phase | When | Goal |
|---|---|---|
| `/setup` | After scaffold drops | Populate `Getting-Started.md`, `Runbook.md`, `Product-Intent.md`, `Overview.md` from codebase scan. Initialize `Home.md`, `_Sidebar.md`. Offer to create GitHub Project. |
| `/plan` | After `PLAN.md` written | Create/update pending Feature/Subsystem pages for each affected task. |
| `/work` | After gates green, before commit | Flip pending → implemented on matching pages. Fill `## Implementation`. Create operational pages if task touched them. |
| `/review` | — | Not invoked. Reviewer may note stale docs secondarily. |
| `/release` | After gates green | Full-pass sweep: verify implementation reaches docs, create missing pages, update Home/Sidebar, append to `Completed-Features.md`, add ADRs for non-obvious architectural decisions. Block release on unresolved questions. |
| `/bugfix` | Post-fix | Update `Known-Issues.md` if gotcha emerged. Add ADR if fix implies decision change. |

Humans may edit any wiki file anytime. Docsub respects existing content and asks before deprecating, moving, or deleting pages.

## GitHub Wiki sync

Harness-ships `.github/workflows/wiki-sync.yml`:

- `name:` `[W] Update Wiki`
- Trigger: `push` on the repo's default branch, `paths: ['wiki/**']`.
- Job: `update-wiki`
- Step: shell-based, no third-party action. Clones `${REPO}.wiki.git`, fails loudly on basename collisions, `rsync -a --delete` mirror, commit + push to the wiki's `master` branch.
- Uses default `GITHUB_TOKEN` with `permissions: contents: write`.
- **Graceful skip** if wiki is disabled or repo isn't on GitHub. The `wiki/` folder remains authoritative locally.

## GitHub Projects + Issues

Dynamic surfaces, gated on confirmation. Triggered from any phase.

**Projects** — "remember for later", "idea for later", "future work", "we should also…". Agent proposes a project item with title + body. **Always asks before `gh project item-create`.** Project ID stored in `.harness/project.json`, created at `/setup` if the user opts in. GitHub Projects v2 are owned by a user or org (ProjectsV2 has no repo-owned form); `/setup` creates the project user/org-scoped and then **links it to the repo** via `gh project link --repo <owner>/<repo>` so it appears under `github.com/<owner>/<repo>/projects`. The two-step dance (`gh project create` → `gh project link`) is the canonical shape — see [`phases/01-setup.md` §8](phases/01-setup.md) for the exact sequence and the `--owner` literal-vs-`@me` gotcha.

**Issues** — user instructs directly, or agent encounters an out-of-scope bug to defer, or reviewer flags a bug the user chooses to defer. **Always asks with title + body preview before `gh issue create`.**

## `.harness/project.json`

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

- `owner` — the project owner (user or org). `@me` at create time resolves to the authenticated user.
- `number` — the Project V2 number returned by `gh project create --format json`.
- `url` — canonical project URL.
- `repo` — the repo the project is **linked to** via `gh project link` (as `<owner>/<repo>`). Records the linkage on disk so later phases and `--update` runs can re-verify it.

Only populated if the user opts into project creation at `/setup`. Absent otherwise — all per-phase Projects wiring silently no-ops when the file is missing.

## Installer behavior

`install.sh` copies:

- `templates/wiki/` → target's `wiki/` (user-owned, walked per-file so partial human-created wikis get missing scaffold files filled in without overwriting).
- `templates/.github/workflows/wiki-sync.yml` → target's `.github/workflows/wiki-sync.yml` (managed; refreshed on `--update`).

**Installer boundary:** `install.sh` copies from `$HARNESS_ROOT/templates/` only. This repo's own `wiki/` folder (documentation for agentic-harness itself) is never propagated to target projects.

## Non-goals

- **Single `DOCS.md` file.** Doesn't scale; hard to browse.
- **Generating the wiki from code annotations.** Humans need to edit these freely.
- **YAML front-matter.** Adds overhead; status is carried in GitHub alert blocks.
- **Auto-generated sidebar.** Docsub owns `_Sidebar.md` — a deliberate, curated nav beats alphabetical autogen.
- **Docs alongside code in `/work`'s implement step.** Biases the implementer toward confirming the plan. Phase-bounded only.
- **LLM-as-judge for doc quality.** `/release`'s docsub pass is adversarial-framed ("find what wasn't documented") but not a quality score.
- **More than three templates.** Every extra template is a decision the sub-agent gets wrong.
