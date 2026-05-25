---
name: migrate-to-diataxis
description: One-shot migration of an already-installed project's wiki/ from the old audience-based layout (development/operational/design/architecture) to the Diátaxis four-mode layout (tutorials/how-to/reference/explanation). Trigger when the user says "migrate the wiki to Diátaxis", "migrate to diataxis", or runs the skill explicitly. Preview-first, user-confirmed, non-destructive — classifies every page by heading shape, produces a diff, asks before any filesystem change, uses `git mv` so blame is preserved. Refuses if wiki/.diataxis already exists (already migrated) or if the working tree is dirty.
---

# migrate-to-diataxis skill

Canonical spec: [`harness/skills/migrate-to-diataxis.md`](../../../../harness/skills/migrate-to-diataxis.md). Convention: [`harness/documentation.md`](../../../../harness/documentation.md). Decision record: [ADR 0004](../../../../wiki/explanation/decisions/0004-diataxis-documentation-spec.md).

**What this skill does:** walks the target project's `wiki/`, classifies every page by heading shape, builds a rewrite map of path moves + wiki-internal link updates, prints a structured preview, asks for confirmation, then uses `git mv` to execute. One shot, non-destructive, blame-preserving. The human commits afterward.

**What this skill does not do:** rewrite page content, commit, push, delete files, touch anything outside `wiki/`, or auto-split mode-mixed pages (those are flagged for human review).

## Preconditions (check first, abort if not met)

1. Working tree clean: `git status --porcelain` empty.
2. `wiki/` exists at repo root.
3. `wiki/.diataxis` does **not** exist. If it does: exit with "already migrated" and show the current layout.
4. At least one of `wiki/development/`, `wiki/operational/`, `wiki/design/`, `wiki/architecture/` exists. If none: exit with guidance.
5. You are running from the target project's root, not the agentm repo.

## Input handling

- **No argument** → classify, preview, confirm, execute.
- **`--preview`** → classify and preview only; exit 0 without disk writes.
- **`--yes`** → skip the confirmation prompt; preview still prints.

## Workflow

### 1. Walk and classify every `wiki/**/*.md`

Skip `Home.md`, `_Sidebar.md`, any `README.md`, and `.diataxis`. Apply rules in order; stop at the first match:

| Rule | Pattern | Target |
|---|---|---|
| ADR | H1 matches `^# ADR \d{4}:` OR path is under `architecture/decisions/`. | `explanation/decisions/` (preserve `NNNN` filename). |
| Status page | Body has `> [!NOTE]` containing `**Status:**` + `**Plan:**`. | `explanation/` |
| How-to | `## Steps` H2 with numbered list, OR ≥3 numbered imperative steps in first 40 lines; AND no `## Rationale` / `## Why` / `## Background` / `## Context`. | `how-to/` |
| Tutorial | `## Step N —` headings AND `## What you learned` AND `## Next`. | `tutorials/` (preserve or add `NN-` prefix). |
| Reference | `## ⚡ Quick Reference` / `## Quick Reference` table in first 20 lines, OR ≥60% tables-by-line. | `reference/` |
| Explanation (default) | Prose narrative — anything not matching above. | `explanation/` |
| **Mode-mixed** | Meets two or more competing heuristics. | **Flag for human split.** Not moved. |

Classification is deterministic.

### 2. Build the rewrite map

Old path → new path (basename preserved; ADRs keep `NNNN-` prefix). Scan every `wiki/**/*.md` for links to old basename / old URL; build a replacement table. Wiki-internal links use basenames only (old subdir dropped). If cross-mode-dir basename collision detected: abort.

### 3. Preview (always print)

```
migrate-to-diataxis: preview

MOVES (N pages):
  wiki/architecture/Overview.md         → wiki/reference/Overview.md
  wiki/design/Product-Intent.md         → wiki/explanation/Product-Intent.md
  wiki/architecture/decisions/0001-*.md → wiki/explanation/decisions/0001-*.md
  ...

LINK REWRITES (M files, K references):
  wiki/Home.md:      12 refs updated
  wiki/_Sidebar.md:   8 refs updated
  ...

NEEDS HUMAN SPLIT (P pages — not moved):
  wiki/development/Getting-Started.md
    — contains ## Quick Reference (reference) AND step-by-step install (how-to)
      AND ## Why this matters (explanation).
    — Suggested split: tutorials/01-Getting-Started.md + how-to/Install.md
      + reference/CLI.md.

DELETIONS (empty old dirs after moves):
  wiki/development/ wiki/operational/ wiki/design/features/ wiki/design/
  wiki/architecture/decisions/ wiki/architecture/

POST-MIGRATION:
  - wiki/.diataxis marker created.
  - Home.md + _Sidebar.md updated to reader-journey ordering.
  - git log --follow preserves blame on each moved page.

Run with no flag to apply, --preview to re-emit, or Ctrl-C to abort.
```

Under `--preview`: exit 0 here.

### 4. Confirm

Prompt `Apply? [y/N]`. Only `y` / `yes` proceeds. Under `--yes` skip the prompt.

### 5. Execute (in order)

1. `git mv` each page old→new (use `git mv`, not `mv` + `git add`).
2. Rewrite wiki-internal links per the replacement table. One atomic Edit per file.
3. Update `wiki/Home.md` + `wiki/_Sidebar.md` to reader-journey ordering (📚 → 🔧 → 📖 → 💡).
4. `rmdir` the now-empty old dirs (fails if not empty — abort if so).
5. Create empty `wiki/.diataxis` marker.
6. **Do not commit.** Human stages, reviews, commits.

### 6. Report

```
migrate-to-diataxis: applied
  moved:   N pages (git mv, blame preserved)
  rewrote: K links across M files
  flagged: P pages for human split (still at old paths)
  removed: <list of empty dirs>
  marker:  wiki/.diataxis created

NEXT:
  1. git status — review the diff.
  2. Spot-check git log --follow wiki/<new-path>/<Some-Page>.md.
  3. Split the P flagged pages by hand.
  4. python3 scripts/check-wiki.py --strict on wiki/.
  5. Commit. Suggested:
     refactor(wiki): migrate to Diátaxis four-mode layout (ADR 0004)
```

## Must never do

- Rewrite page content. Classify + move + link-rewrite only.
- Commit or push. Leaves staged-but-uncommitted for human review.
- Delete content — `rmdir` on empty dirs only; flagged pages stay at old paths.
- Touch anything outside `wiki/`.
- Run on a dirty tree.
- Re-run on a wiki that already has `.diataxis`.
- Invent a classification — flag for human split when ambiguous.
- Fail silently — every abort prints a one-line reason and exits non-zero.
