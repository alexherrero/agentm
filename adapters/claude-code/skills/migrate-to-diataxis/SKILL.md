---
name: migrate-to-diataxis
description: One-shot migration of an already-installed project's wiki/ from the old audience-based layout (development/operational/design/architecture) to the Diátaxis four-mode layout (tutorials/how-to/reference/explanation). Trigger when the user says "migrate the wiki to Diátaxis", "migrate to diataxis", or invokes /migrate-to-diataxis. Preview-first, user-confirmed, non-destructive — classifies every page by heading shape, produces a diff, asks before any filesystem change, uses `git mv` so blame is preserved. Refuses if wiki/.diataxis already exists (already migrated) or if the working tree is dirty.
---

You are running the `migrate-to-diataxis` skill. Full canonical spec: `harness/skills/migrate-to-diataxis.md` in the agentm repo. The summary below is the operational version.

## Preconditions (check first, abort if not met)

1. Working tree is clean: `git status --porcelain` empty.
2. `wiki/` exists at the repo root.
3. `wiki/.diataxis` does **not** exist — exit with "already migrated" otherwise.
4. At least one of `wiki/development/`, `wiki/operational/`, `wiki/design/`, `wiki/architecture/` exists — exit with guidance otherwise.
5. You are in the target project's root, not the agentm repo itself.

## Input handling

- **No argument** → classify, preview, confirm, execute.
- **`--preview`** → classify and preview only; exit 0 without touching disk.
- **`--yes`** → skip the confirmation prompt; preview still prints.

## Workflow

### 1. Walk and classify every `wiki/**/*.md`

Skip `Home.md`, `_Sidebar.md`, any `README.md`, and `.diataxis`. For every other page, apply rules in order; stop at the first match:

| Rule | Pattern | Target |
|---|---|---|
| ADR | H1 matches `^# ADR \d{4}:` OR path is under `architecture/decisions/`. | `explanation/decisions/` (preserve `NNNN` filename). |
| Status page | Body has `> [!NOTE]` block containing both `**Status:**` and `**Plan:**` lines. | `explanation/` |
| How-to | `## Steps` H2 with numbered list, OR ≥3 numbered imperative steps in first 40 lines; AND no `## Rationale` / `## Why` / `## Background` / `## Context`. | `how-to/` |
| Tutorial | `## Step N —` headings AND `## What you learned` AND `## Next`. | `tutorials/` (preserve or add `NN-` prefix). |
| Reference | `## ⚡ Quick Reference` or `## Quick Reference` table in first 20 lines, OR ≥60% tables-by-line. | `reference/` |
| Explanation (default) | Prose-heavy narrative — anything not matching above. | `explanation/` |
| **Mode-mixed** | Meets two or more competing heuristics (e.g. how-to with `## Rationale`). | **Flag for human split.** Not moved. |

Classification is deterministic — same inputs every run.

### 2. Build the rewrite map

For each classified page: old path → new path (basename preserved; ADRs keep `NNNN-` prefix). Scan every `wiki/**/*.md` for links to the old basename / old URL and build a replacement table — wiki-internal links use basenames only (old subdir dropped).

Collisions across mode dirs cannot happen per the naming convention; if detected, abort with the colliding pair.

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
  - wiki/.diataxis marker created (enables strict-mode check-wiki lint).
  - Home.md + _Sidebar.md updated to reader-journey ordering
    (📚 Tutorials → 🔧 How-to → 📖 Reference → 💡 Explanation).
  - git log --follow preserves blame on each moved page.

Run with no flag to apply, --preview to re-emit, or Ctrl-C to abort.
```

Under `--preview`: exit 0 here. No disk writes.

### 4. Confirm

Prompt `Apply? [y/N]`. Only `y` / `yes` proceeds. Under `--yes` skip the prompt.

### 5. Execute (in order)

1. `git mv` each page old→new (blame preserved — use `git mv`, not `mv` + `git add`).
2. Rewrite wiki-internal links per the replacement table. One atomic Edit per file.
3. Update `wiki/Home.md` and `wiki/_Sidebar.md` to reader-journey ordering.
4. `rmdir` the now-empty old dirs (fails if not empty — a safety check; abort if so).
5. Create empty `wiki/.diataxis` marker.
6. **Do not commit.** The human stages, reviews, commits.

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

- Rewrite page content (prose, headings, tables). Classify + move + link-rewrite only.
- Commit or push. Always leaves staged-but-uncommitted for human review.
- Delete content — only `rmdir` on empty dirs. Flagged pages stay at old paths.
- Touch anything outside `wiki/`.
- Run on a dirty tree.
- Re-run on a wiki that already has `.diataxis`.
- Invent a classification. When genuinely ambiguous, flag for human split.
- Fail silently. Every abort prints one line and exits non-zero.

## Output contract

On apply: the final report shown in §6.
On preview-only: the preview shown in §3, exit 0.
On abort: one line naming the failed precondition, exit non-zero.
