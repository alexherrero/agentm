# Wiki convention

How this project documents itself. This `wiki/` folder is the source of truth for human-and-agent-readable documentation and is mirrored to the repo's GitHub Wiki on every push to the default branch.

This scaffold follows the Diátaxis convention — four modes, one intent per page, never mixed. See [ADR 0004 in agentic-harness](https://github.com/alexherrero/agentic-harness/blob/main/wiki/architecture/decisions/0004-diataxis-documentation-spec.md) for the rationale.

## Two readers, one surface

Every page is written for two audiences: a **human** who needs to understand the system without reading every file, and an **agent** who needs to resume work in a future session without the original context. Tables, diagrams, cross-links, and `file:line` references serve both.

## Four modes

| Mode | Purpose | Reader's question | Typical pages |
|---|---|---|---|
| 📚 `tutorials/` | Learning by doing | "I'm new — walk me through an outcome." | `01-Getting-Started.md`, `02-First-Feature.md` |
| 🔧 `how-to/` | Task-focused recipes | "How do I X?" | `Run-The-Tests.md`, `Deploy.md`, `Rollback.md` |
| 📖 `reference/` | Canonical lookup | "What are the flags / keys / codes?" | `CLI.md`, `Config.md`, `Exit-Codes.md` |
| 💡 `explanation/` | Intent and rationale | "Why is it this way?" | `Product-Intent.md`, `How-The-Pieces-Fit.md`, `decisions/<NNNN>-<slug>.md` |

Pages outside these four dirs are not part of the convention. File under an existing mode, or — if a genuinely new mode is needed — update ADR 0004 in agentic-harness first; don't invent a fifth here.

## The single-mode rule

Each page serves exactly one reader intent. A tutorial does not contain rationale; a how-to does not contain background narrative; a reference is not a walk-through. When content mixes modes, split the page — don't cram modes together under different headings.

The `.diataxis` marker file in this folder enables structural-lint enforcement of this rule (in agentic-harness, via `scripts/check-wiki.py`).

## Filename rules

- `CamelCase-With-Dashes.md` (matches GitHub Wiki URL convention).
- **Globally unique across mode dirs** — basename collisions fail the sync workflow loudly.
- Tutorials sort numerically: `01-`, `02-`, etc.
- ADRs sort numerically: `0001-`, `0002-`, etc.

## Templates

Four shapes — one per mode. Every page starts with `# H1 — <Title>` and a one-paragraph summary. **No YAML front-matter.**

### Template 1 — Tutorial

Used for `tutorials/<NN>-<slug>.md`. Goal-driven walk-through with numbered steps.

```markdown
# Tutorial N — <Title>

> [!NOTE]
> **Goal:** <what the reader will have achieved at the end.>
> **Time:** <rough duration.>
> **Prereqs:** <what the reader must have before step 1.>

<1-paragraph orientation.>

## Step 1 — <action>
## Step 2 — <action>
## Step 3 — <action>

## What you learned

- <one bullet per learning outcome.>

## Next

- <pointer to a how-to or reference for the reader to go deeper.>
```

### Template 2 — How-to

Used for `how-to/<Task>.md`. Task-focused recipe, no rationale, no background.

```markdown
# How to <task>

> [!NOTE]
> **Goal:** <one line describing the task.>
> **Prereqs:** <what the reader needs before step 1.>

## Steps

1. <action>
2. <action>
3. <verify>

## Variants

<sub-sections for meaningful variants; skip if none.>

## Verify

<how to confirm the task succeeded.>

## Troubleshooting

| Symptom | Fix |
|---|---|
| ... | ... |
```

### Template 3 — Reference

Used for `reference/<Surface>.md`. Tables-first, no narrative.

```markdown
# <Surface name> reference

<1-paragraph scope statement.>

## ⚡ Quick Reference

| <column> | <column> |
|---|---|
| ... | ... |

## <Section — flags / commands / config / etc.>

| ... | ... |

## Related

- <cross-links to tutorials, how-tos, or other references.>
```

### Template 4 — Explanation / ADR

Used for `explanation/<Topic>.md` and `explanation/decisions/<NNNN>-<slug>.md`.

Explanation pages are narrative and may use any section structure that serves the argument. ADRs use the canonical Nygard shape:

```markdown
# ADR <NNNN>: <Title>

> [!NOTE]
> **Status:** proposed | accepted | superseded-by-<NNNN>
> **Date:** YYYY-MM-DD

## Context
## Decision
## Consequences
```

ADRs are append-only once accepted. Amend with `## Amendment YYYY-MM-DD` subheadings; supersede by recording a new ADR.

## Stylistic conventions

- **Tables over bullet lists** for comparative information.
- **Diagrams** — ASCII in fenced code blocks or Mermaid. Use one whenever a relationship is clearer drawn than described.
- **GitHub alerts** for load-bearing callouts: `> [!NOTE]`, `> [!IMPORTANT]`, `> [!WARNING]`.
- **Emoji mode markers**, consistent: 📚 Tutorials · 🔧 How-to · 📖 Reference · 💡 Explanation · ⚡ Quick Reference · 📁 File Layout · 🤝 Integration.
- **Cross-links**: wiki pages by basename (`Home`, `01-Getting-Started`, etc.), full GitHub URLs with `#L<line>` for code references.

## Who maintains what

- **Humans** may edit any wiki file anytime.
- **The `documenter` sub-agent** updates pages at phase boundaries only — never during `/work`'s implement step:
  - `/setup` — populates seed tutorial + reference + explanation from the codebase.
  - `/plan` — creates pending how-to pages and reference rows for the plan's tasks.
  - `/work` (post-gates) — flips pending how-tos to implemented, fills reference tables.
  - `/release` — adversarial sweep across all four modes; may promote stable how-tos to tutorials and may add new ADRs under `explanation/decisions/`.
- `Home.md` and `_Sidebar.md` are maintained by the sub-agent — not generated by sync.

## GitHub Wiki sync

`.github/workflows/wiki-sync.yml` mirrors this folder to the repo's GitHub Wiki on push to the default branch. Mirror semantics (add / edit / rename / delete). Collisions fail loudly. Gracefully skips if the wiki isn't enabled on the repo.

## Full spec

[agentic-harness/harness/documentation.md](https://github.com/alexherrero/agentic-harness/blob/main/harness/documentation.md) is the canonical convention spec that shipped this scaffold, amended by [ADR 0004](https://github.com/alexherrero/agentic-harness/blob/main/wiki/architecture/decisions/0004-diataxis-documentation-spec.md).
