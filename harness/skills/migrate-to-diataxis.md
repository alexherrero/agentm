# Skill: migrate-to-diataxis

> [!WARNING]
> **DEPRECATED 2026-05-22.** This skill has been subsumed by [`crickets`'s `diataxis-author` skill](https://github.com/alexherrero/crickets/blob/main/skills/diataxis-author/SKILL.md) — specifically the `/diataxis migrate` sub-command which ships the same one-shot legacy → Diátaxis migration contract (preview-first, deterministic classification, `git mv` for blame, mode-mixed flagged for human split) plus net-new capabilities: `.diataxis-conventions.md` auto-seed for per-repo overrides, delegation to `/diataxis repair` for mode-mixed splits, AgentMemory integration for convention sync across the operator's wikis (lands in plan #13 part 5).
>
> **Use this instead**: `python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/migrate.py --preview` (or via the skill body when Claude Code/Antigravity is dispatching).
>
> This skill body stays in the harness through the v1 dogfood window for operators with mid-flight installs who already know its contract. A follow-up harness PATCH release will remove the file entirely once `diataxis-author/migrate` proves out in real use. **Do not extend this skill** — bug fixes + new functionality land in the toolkit-side `diataxis-author` skill.
>
> Tracked in: [crickets ROADMAP #13](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) + [diataxis-author design doc Migrations §1](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/diataxis-author.md#migrations) + [ADR 0008](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0008-diataxis-author.md) (the latter ships in plan #13 part 5).

**Purpose:** one-shot migration of an already-installed project's `wiki/` from the old audience-based layout (`development/`, `operational/`, `design/`, `architecture/`) to the Diátaxis four-mode layout (`tutorials/`, `how-to/`, `reference/`, `explanation/`). Preview-first, user-confirmed, non-destructive — classifies every page by heading shape, produces a diff, asks before any filesystem change, uses `git mv` so blame is preserved.

**Not for:** fresh installs (those get the new scaffold straight from `templates/wiki/` at `/setup`). Not for partial migrations — the skill migrates the whole `wiki/` in one shot or aborts. Not for rewriting page *content* — it classifies, moves, and rewrites links only; mode-mixed pages are flagged for human split, not auto-split.

## Preconditions

1. Working tree is clean: `git status --porcelain` is empty. Refuse to run otherwise — the migration is big and readable diffs matter.
2. A `wiki/` directory exists at the repo root.
3. `wiki/.diataxis` does **not** exist. If it does, the migration has already run; exit with "already migrated" and show the current layout.
4. At least one of `wiki/development/`, `wiki/operational/`, `wiki/design/`, `wiki/architecture/` exists. If none do, the project is already on a non-audience layout; exit with guidance ("nothing to migrate — are you sure this project came from an older harness version?").
5. This skill is run from the target project's root, not from the agentm repo itself.

## Inputs

One of:
- No argument — classify, preview, confirm, execute.
- `--preview` — classify and preview only; exit 0 without touching disk.
- `--yes` — skip the confirmation prompt (for scripted use). The preview still prints so the run log is auditable.

## Workflow

### 1. Walk the wiki and classify every page

For every `*.md` under `wiki/` (excluding `Home.md`, `_Sidebar.md`, any `README.md`, and `.diataxis`), open the file and classify it by heading shape + content heuristics. Apply rules in order; stop at the first match.

| Rule | Pattern | Target mode |
|---|---|---|
| **ADR** | H1 matches `^# ADR \d{4}:` or path is `wiki/architecture/decisions/NNNN-*.md`. | `explanation/decisions/` (preserve `NNNN` filename verbatim). |
| **Status page (Template 2)** | Body contains a `> [!NOTE]` block with a `**Status:**` line AND `**Plan:**` (pending/implemented/deprecated). | `explanation/` (feature and subsystem Status pages are explanation of intent + implementation trace). |
| **How-to** | Body has a `## Steps` H2 with a numbered list, OR ≥3 numbered imperative steps in the first 40 lines (e.g. `1. Run \`...\`` style), AND no `## Rationale` / `## Why` / `## Background` / `## Context` H2. | `how-to/` |
| **Tutorial** | Heading shape matches `## Step N —` AND body has a `## What you learned` H2 AND a `## Next` H2. (Rare — usually a human-written artifact.) | `tutorials/` (preserve numeric prefix if present; otherwise prefix as `01-`). |
| **Reference** | First 20 lines contain a `## ⚡ Quick Reference` or `## Quick Reference` table header, OR the file is ≥60% tables-by-line-count. | `reference/` |
| **Explanation (default)** | Anything not matching the above — prose-heavy narrative, design docs, product intent, feature descriptions without Status blocks. | `explanation/` |
| **Mode-mixed (flag)** | Meets two or more of {how-to heuristic, reference heuristic, explanation heuristic} with competing strength (e.g. a how-to with a `## Rationale` H2, or a reference page with a `## Steps` section). | **Flag for human split.** Do not move; emit as `NEEDS HUMAN SPLIT` in the preview. |

Classification is deterministic — same inputs produce the same preview every run. Do not use heuristics that depend on wall-clock time, random sampling, or model inference.

### 2. Build the rewrite map

For every classified page, compute:

- **Old path** (e.g. `wiki/design/features/Access-Token-Refresh.md`).
- **New path** (e.g. `wiki/explanation/Access-Token-Refresh.md`). Preserve the basename unchanged except to lift ADRs' `NNNN-` prefix. Collisions across mode dirs are impossible because basenames must already be globally unique per the convention — if a collision is detected, abort with the colliding pair and let the human resolve.
- **Link rewrites required**: scan every `wiki/**/*.md` file's body for references to the old path or basename. Build a replacement table: `[text](Old-Basename)` and `wiki/<old-dir>/<Old-Basename>.md` URL refs become the equivalent at the new location. Wiki-internal links use basenames only (the old subdir in the URL is dropped).

### 3. Preview

Print a structured report to the user. Exact shape:

```
migrate-to-diataxis: preview

MOVES (N pages):
  wiki/architecture/Overview.md              → wiki/reference/Overview.md
  wiki/design/Product-Intent.md              → wiki/explanation/Product-Intent.md
  wiki/architecture/decisions/0001-*.md      → wiki/explanation/decisions/0001-*.md
  ...

LINK REWRITES (M files, K references):
  wiki/Home.md:              12 refs updated
  wiki/_Sidebar.md:           8 refs updated
  wiki/reference/Overview.md: 3 refs updated
  ...

NEEDS HUMAN SPLIT (P pages — not moved, flagged for you to split manually):
  wiki/development/Getting-Started.md
    — contains both ## Quick Reference (reference) and step-by-step install
      (how-to) and a ## Why this matters section (explanation).
    — Suggested split: tutorials/01-Getting-Started.md + how-to/Install.md +
      reference/CLI.md.

DELETIONS (empty old dirs after moves):
  wiki/development/
  wiki/operational/
  wiki/design/features/
  wiki/design/
  wiki/architecture/decisions/
  wiki/architecture/

POST-MIGRATION:
  - wiki/.diataxis marker will be created (enables strict-mode check-wiki lint).
  - wiki/Home.md and wiki/_Sidebar.md will be updated to reader-journey ordering
    (📚 Tutorials → 🔧 How-to → 📖 Reference → 💡 Explanation).
  - git log --follow on each moved page will show blame preserved.

Run with no flag to apply, --preview to re-emit this summary, or Ctrl-C to abort.
```

Under `--preview` the skill exits 0 here and never touches the filesystem.

### 4. Confirm

Default behavior: prompt the user with `Apply? [y/N]`. Only `y` or `yes` proceeds; anything else aborts with exit 0 and zero filesystem changes.

Under `--yes` the prompt is skipped but the preview still prints.

### 5. Execute

In order:

1. **`git mv` each page** from old to new path. Using `git mv` (not `mv` + `git add`) is what lets Git detect the rename so `git log --follow` preserves blame.
2. **Rewrite wiki-internal links** using the replacement table from step 2. Apply with `Edit` or explicit text replace — never `sed` in a way that could match accidentally. Each file's change is one atomic edit.
3. **Update `wiki/Home.md` and `wiki/_Sidebar.md`** to reader-journey ordering. If the page shapes already exist at the new paths, just rewrite the links; if sections need reorganizing (from `🛠 Development` / `📟 Operational` / `🎨 Design` / `🏗 Architecture` to `📚 Tutorials` / `🔧 How-to` / `📖 Reference` / `💡 Explanation`), reorganize.
4. **Remove now-empty old dirs** (`wiki/development/`, `wiki/operational/`, `wiki/design/features/`, `wiki/design/`, `wiki/architecture/decisions/`, `wiki/architecture/`). Use `rmdir` (fails if not empty — a safety check); if any is not empty, abort with the offending path and leave the rest in place.
5. **Create `wiki/.diataxis`** marker (empty file; presence enables strict-mode lint and advertises the convention to future tooling).
6. **Do not commit.** The human stages and commits — they read the `git status` diff, confirm blame was preserved on spot-checked pages, and write the commit message themselves.

### 6. Report

Print a final summary:

```
migrate-to-diataxis: applied
  moved:         N pages (git mv, blame preserved)
  rewrote:       K links across M files
  flagged:       P pages for human split (see preview above — now sitting at
                 old paths)
  removed:       <list of empty dirs>
  marker:        wiki/.diataxis created

NEXT:
  1. git status — review the diff.
  2. Spot-check git log --follow wiki/<new-path>/<Some-Page>.md to confirm
     blame preserved.
  3. Split the P flagged pages by hand into the suggested destinations.
  4. Run python3 scripts/check-wiki.py --strict on the wiki/ root.
  5. Commit. Suggested message:
     refactor(wiki): migrate to Diátaxis four-mode layout (ADR 0004)
```

## What the skill must never do

- **Rewrite page content.** It classifies, moves, and rewrites links. Never edits prose, headings, or table contents.
- **Commit or push.** Always leaves the migration staged-but-not-committed for the user to review and commit. Never uses `git commit` or `git push`.
- **Delete content.** Pages flagged for human split stay at their old paths; they are not moved, not rewritten, not pruned. The skill also does not delete files — only `rmdir` on empty directories.
- **Touch files outside `wiki/`.** No edits to `.harness/`, `.claude/`, `AGENTS.md`, `CLAUDE.md`, source code, CI configs, or any other repo surface.
- **Run on a dirty working tree.** The precondition check is non-negotiable; the diff is too large to read reliably against unrelated uncommitted changes.
- **Re-run on an already-migrated wiki.** The `wiki/.diataxis` precondition check is non-negotiable. If the user wants to re-classify, they remove the marker themselves — the skill does not do it for them.
- **Invent a classification.** If a page's shape is genuinely ambiguous, flag it for human split; do not guess.
- **Fail silently.** Every abort path prints a one-line reason and exits non-zero.

## Why this skill is scoped tight

The migration is structural, not editorial. Rewriting prose to fit the new modes (adding `## What you learned` sections, splitting cross-mode pages, promoting how-tos to tutorials) is the documenter sub-agent's job at `/release` time, and it needs human guidance — the skill can't infer intent. So the skill does the mechanical part (classify, move, link-rewrite, deposit the marker) and hands back control. One shot, preview-first, non-destructive, blame-preserving.

See [ADR 0004 §6](../../wiki/explanation/decisions/0004-diataxis-documentation-spec.md) for the migration-rollout rationale and [`harness/documentation.md`](../documentation.md) for the four-mode convention this skill migrates toward.
