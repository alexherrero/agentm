# Wiki convention

How this project documents itself. This `wiki/` folder is the source of truth for human-and-agent-readable documentation and is mirrored to the repo's GitHub Wiki on every push to the default branch.

This scaffold follows the six-section documentation taxonomy — one reader intent per page, never mixed. See the crickets [`documentation`](https://github.com/alexherrero/crickets/wiki/crickets-conventions) convention for the rationale (it converges the older four-mode Diátaxis layout onto crickets' frame).

> [!NOTE]
> **Authoring tooling lives in crickets.** Page authoring + the structural lint are owned by [`crickets`](https://github.com/alexherrero/crickets)' `wiki-maintenance` plugin (ADR 0006 single-source). If crickets is paired, use its `/wiki-init` to scaffold the two conditional sections (architecture/operational) and provision wiki-sync CI; if it isn't, the harness degrades gracefully (graceful-skip) and this native scaffold stands on its own. The lint gate is `scripts/check-wiki.py`.

## Two readers, one surface

Every page is written for two audiences: a **human** who needs to understand the system without reading every file, and an **agent** who needs to resume work in a future session without the original context. Tables, diagrams, cross-links, and `file:line` references serve both.

## Seven sections

Five sections are always present; two are conditional, added only when the repo earns them.

| Section | Purpose | Reader's question | Typical pages |
|---|---|---|---|
| 🔧 `how-to/` | Task-focused recipes | "How do I X?" | `Run-The-Tests.md`, `Deploy.md`, `01-Getting-Started.md` |
| 📖 `reference/` | Canonical lookup | "What are the flags / keys / codes?" | `CLI.md`, `Config.md`, `Exit-Codes.md` |
| 📐 `designs/` | "Why we built X" + decision records | "What was the plan, why this shape, what did we decide?" | `<slug>.md` (via crickets' `/design`); decisions live in each design's `## Amendment log` |
| 💡 `explanation/` | Intent and rationale | "Why is it this way?" | `Product-Intent.md`, `How-The-Pieces-Fit.md` |
| 🏛️ `architecture/` *(conditional)* | Component map | "How do the pieces fit at the system level?" | added when a `wiki/architecture.yml` manifest declares it |
| ⚙️ `operational/` *(conditional)* | Run-the-system | "How do I operate / recover this in prod?" | added when the repo's visibility is non-public |

Onboarding walkthroughs (the learning-by-doing pages the old layout called "tutorials") live under `how-to/`, numerically prefixed (`01-`, `02-`), carrying a `<!-- mode: tutorial -->` hint so the lint gate holds them to tutorial discipline. Pages outside these sections are not part of the convention. File under an existing section, or — if a genuinely new section is needed — update ADR 0004 in agentm first; don't invent one here.

## The single-section rule

Each page serves exactly one reader intent. An onboarding walkthrough does not contain rationale; a how-to does not contain background narrative; a reference is not a walk-through; an explanation is not a how-to. When content mixes intents, split the page — don't cram intents together under different headings.

The `.diataxis` marker file in this folder enables structural-lint enforcement of this rule (in agentm, via `scripts/check-wiki.py`).

## Filename rules

- `CamelCase-With-Dashes.md` (matches GitHub Wiki URL convention).
- **Globally unique across sections** — basename collisions fail the sync workflow loudly.
- Onboarding walkthroughs sort numerically: `01-`, `02-`, etc.
- ADRs sort numerically: `0001-`, `0002-`, etc.

## Templates

Every page starts with `# H1 — <Title>` and a one-paragraph summary. **No YAML front-matter.** crickets' `wiki-maintenance` plugin ships the canonical, evolving set; the shapes below are the load-bearing ones a fresh scaffold needs.

### Onboarding walkthrough (how-to/, tutorial-shaped)

Used for `how-to/<NN>-<slug>.md` with a `<!-- mode: tutorial -->` hint. Goal-driven walk-through with numbered steps.

```markdown
# Tutorial N — <Title>

<!-- mode: tutorial -->

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

### How-to

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

### Reference

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

- <cross-links to how-tos or other references.>
```

### Explanation / Design / Decision records

Explanation pages (`explanation/<Topic>.md`) are narrative and may use any section structure that serves the argument. Design docs (`designs/<slug>.md`) are authored via crickets' `/design` skill. **Decision records are not standalone files** — a load-bearing decision is recorded as an entry in the governing design's `## Amendment log` (under `designs/`), reconciling the design's body in the same atomic change:

```markdown
**YYYY-MM-DD — <summary of the change>.**
<decision prose>. *Why not the alternative:* <why-not>. *Re-audit trigger:* <condition that would make this wrong>.
```

This replaces the retired ADR model. The why-not + re-audit discipline carries over; only the artifact moved (into the design, not a separate `decisions/` file).

## Stylistic conventions

- **Tables over bullet lists** for comparative information.
- **Diagrams** — ASCII in fenced code blocks or Mermaid. Use one whenever a relationship is clearer drawn than described.
- **GitHub alerts** for load-bearing callouts: `> [!NOTE]`, `> [!IMPORTANT]`, `> [!WARNING]`.
- **Emoji section markers**, consistent: 🔧 How-to · 📖 Reference · 📐 Designs · 💡 Explanation · 🏛️ Architecture · ⚙️ Operational · ⚡ Quick Reference · 📁 File Layout · 🤝 Integration.
- **Cross-links**: wiki pages by basename (`Home`, `01-Getting-Started`, etc.), full GitHub URLs with `#L<line>` for code references.

## Who maintains what

- **Humans** may edit any wiki file anytime.
- **Crickets' `wiki-maintenance:documenter` sub-agent** (when crickets is installed; graceful-skip otherwise) updates pages at phase boundaries only — never during `/work`'s implement step:
  - `/setup` — populates a seed onboarding walkthrough + reference + explanation from the codebase.
  - `/plan` — creates pending how-to pages and reference rows for the plan's tasks.
  - `/work` (post-gates) — flips pending how-tos to implemented, fills reference tables.
  - `/release` — adversarial sweep across all sections; may record decisions in the governing design's amendment log.
- `Home.md` and `_Sidebar.md` are maintained by the sub-agent — not generated by sync.

## GitHub Wiki sync

`.github/workflows/wiki-sync.yml` mirrors this folder to the repo's GitHub Wiki on push to the default branch. Mirror semantics (add / edit / rename / delete). Collisions fail loudly. Gracefully skips if the wiki isn't enabled on the repo.

## Full spec

[agentm/harness/documentation.md](https://github.com/alexherrero/agentm/blob/main/harness/documentation.md) is the canonical convention spec that shipped this scaffold; the taxonomy is owned by the crickets [`documentation`](https://github.com/alexherrero/crickets/wiki/crickets-conventions) convention.
