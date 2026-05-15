# Phase: setup

First-time initialization of agentic-harness in a project. Run once per project (or after a major restructure). Produces the `.harness/` state artifacts populated with real, project-specific values — not templates.

## Purpose

`install.sh` copies template files. `/setup` makes them real. The gap matters: a template `init.sh` doesn't actually boot anything, and a template `features.json` doesn't know what this project does.

`/setup` is the phase where the harness learns enough about the project to be useful in later phases.

## Preconditions

- `install.sh` has been run (or the `.harness/` + `.claude/` + `AGENTS.md` files exist some other way).
- The project has enough code or README to answer basic questions about it — what it runs, how it's tested.

## Process

### 1. Inventory what's there

Read:
- `README.md` (or equivalent) — what is this project?
- `package.json` / `go.mod` / `pyproject.toml` / `Cargo.toml` / whatever the project uses — what are the run/test/build commands?
- `.github/workflows/` — what does CI do? Good signal for what "gates green" means here.
- `.harness/init.sh` — is it still a template?
- Any existing `CLAUDE.md` / `AGENTS.md` / `.cursorrules` / `GEMINI.md` — what conventions are already documented?

### 2. Interview, briefly

Confirm or fill in what the inventory didn't settle:

- **What does this project do?** One sentence, for `AGENTS.md`.
- **How do you boot the dev env?** (The commands that go in `init.sh`.)
- **How do you run tests?** Full suite + single-file, if they differ.
- **How do you typecheck / lint?** Skip if not applicable.
- **Any commands the harness should avoid?** (destructive migrations, deploys, anything that shouldn't run as part of a gate).
- **Commit convention?** (Conventional commits? Free-form? Specific trailer?)

Batch the questions. Default to not asking if the inventory already answered.

### 3. Populate `.harness/init.sh` with real commands

Replace the template placeholders with what this project actually runs. Example for a Node project:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "==> install deps"
npm install

echo "==> typecheck"
npm run typecheck

echo "==> start dev server (background)"
# npm run dev &
# echo $! > .harness/.dev-server.pid

echo "==> ready"
```

The script should be runnable top-to-bottom and leave the repo in a state where `/work` can proceed. If there are commands that shouldn't run every time (heavy builds, DB seeds), keep them commented with a note.

### 4. Seed `features.json` (optional)

If the project already has a known feature list — a PRD, a backlog, an existing README features section — seed `features.json` with those entries, all `passes: false`. Otherwise leave it empty — `/plan` will add features as they get specified.

Do not invent features. An empty features list is honest; a fabricated one is noise.

### 5. Ensure AGENTS.md and CLAUDE.md are right for this project

If they exist and mention the harness, good. If they exist but don't, merge a pointer section. If they're missing, copy from the harness repo.

Add one project-specific section to `AGENTS.md` under `## This project`:

```markdown
## This project

<One-sentence description.>

**Stack:** <languages, frameworks, DB — what a new contributor would want to know>
**Run:** `bash .harness/init.sh`
**Test:** <project's test command>
**Typecheck:** <command>
**Lint:** <command>
**Build:** <command, if applicable>

**Conventions:**
- <commit convention>
- <any project-specific style rules the harness should respect>
```

This is the block later phases read to know how to operate.

### 6. Verify the harness boots

Run `bash .harness/init.sh`. Confirm it exits 0. If it doesn't, fix it now — every later phase depends on this working.

### 7. Populate the wiki scaffold

`install.sh` dropped an empty `wiki/` scaffold (four subdirs + seed pages). Dispatch the `documenter` sub-agent (full spec: [`harness/agents/documenter.md`](../agents/documenter.md), convention: [`harness/documentation.md`](../documentation.md)) to fill the seed pages from the codebase scan:

- `development/Getting-Started.md` — from `init.sh` + manifests
- `operational/Runbook.md` — from CI configs + deploy hints
- `design/Product-Intent.md` — from `README.md` + the interview
- `architecture/Overview.md` — from top-level layout + entry points
- `Home.md` and `_Sidebar.md` — initialized with the project name and four section headers
- `wiki/explanation/designs/` — landing dir for design docs authored via [agent-toolkit's `/design` skill](https://github.com/alexherrero/agent-toolkit/blob/main/skills/design/SKILL.md). Initially empty (`.gitkeep` + a one-line README pointing at the how-to); fills in over time as the user authors published-visibility designs. Surfaces in `wiki/Home.md` + `_Sidebar.md` as the canonical "Why we built X" entry point per design Status lifecycle (the harness `/release` flow transitions `final → launched` per [`harness/phases/05-release.md` §1b](05-release.md)).

The documenter returns a structured report of what it created. If it surfaces `OPEN QUESTIONS`, answer them before moving on — a broken `Product-Intent.md` on day one is drift that compounds.

### 8. Offer GitHub Project creation (optional)

GitHub Projects v2 are owned by a user or org (never a repo directly), but can be **linked** to a repo so they appear under `github.com/<owner>/<repo>/projects`. Offer create + link as a single flow. Preview-and-ask at each step; default is skip.

**Interview (batched)**:
1. *"Create a GitHub Project for deferred-work tracking?"* — yes / no / later.
2. If yes: *"Owner — your personal account (`@me`) or an org?"* Default: derive from `gh repo view --json owner` if the repo has a GitHub origin. Ask for the org name if org-owned.
3. *"Project title?"* Default: `"<repo-name> backlog"` (e.g. `"agentic-harness backlog"`).

**Run the two `gh` calls with preview**:

```bash
# Step 1 — create (user-scoped example; substitute org name if selected):
gh project create --owner @me --title "<repo-name> backlog" --format json
# Parse the JSON output to capture the returned number + url.

# Step 2 — link to the repo so it appears under <owner>/<repo>/projects.
# NOTE: --owner must be the literal username/org, not @me, even when it
# matches — gh's ownership check sometimes rejects @me here with
# "'<repo>' has different owner from '@me'".
gh project link <number> --owner <literal-owner> --repo <owner>/<repo>
```

**Write `.harness/project.json`**:

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

The `repo` field records the link — later phase wiring (`gh project item-create` from `/plan`, `/work`, `/review`, `/release`) doesn't consult it directly, but it's the only on-disk record that this project is linked to this repo, which matters for dogfood-freshness checks and `--update` re-verification.

**Verification**:

```bash
# Should list project #N under the repo:
gh api graphql -f query='query{repository(owner:"<owner>",name:"<repo>"){projectsV2(first:5){nodes{number title url}}}}'
```

**Graceful-skip conditions** for step 8 itself (don't even ask):
- `gh auth status` fails.
- `git remote get-url origin` doesn't resolve to `github.com`.
- `gh` missing required scopes (`project` + `read:project`) — print the fix-up line (`gh auth refresh -s project,read:project`) and move on; user can rerun `/setup` after.

Default behavior: **skip entirely**. The file stays absent until the user opts in, which is valid. All per-phase Projects wiring silently no-ops when `.harness/project.json` is missing.

### 9. Log and stop

Append to `.harness/progress.md`:

```
<YYYY-MM-DD HH:MM> /setup — initialized harness for this project (stack: <X>, gates: <list>)
```

Return a ≤5-bullet summary:
> - Harness installed at `.harness/`
> - Stack: <detected>
> - Gates configured: typecheck, lint, test, build (or: "test only, no typecheck configured")
> - `init.sh` boots clean
> - Next: `/plan <first brief>`

## Failure modes to avoid

- **Filling `init.sh` with guesses.** If you don't know the real command, ask. A broken `init.sh` breaks every later phase silently.
- **Inventing features.** Empty `features.json` is fine. Fabricated entries are worse than no entries.
- **Overwriting existing AGENTS.md / CLAUDE.md.** Merge, don't replace. These may have project-specific content the user wrote.
- **Skipping the boot verification.** `init.sh` not being run in `/setup` means the first `/work` session is the one that discovers it doesn't work — at the worst possible time.
- **Starting to plan.** `/setup` is pure scaffolding. Planning is `/plan`.
