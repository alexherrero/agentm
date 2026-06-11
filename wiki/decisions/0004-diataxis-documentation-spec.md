# ADR 0004: Diátaxis-shaped documentation spec

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-04-21
> **Amends:** [ADR 0002](0002-documentation-convention) (documentation convention) — keeps the installer boundary, phase-boundary sub-agent, and GitHub Wiki sync; replaces the audience-based four-subdir split with a mode-based four-subdir split.
>
> **Prototype validation (2026-04-21):** Reshaped `wiki/development/Getting-Started.md` into three mode-pure pages — [tutorials/01-First-Install](01-First-Install), [how-to/Install-Into-Project](Install-Into-Project), [reference/Installer-CLI](Installer-CLI). Read side-by-side against the original; the mode split is a clear readability win. Promoted from `proposed` to `accepted`; rollout tracked in the PLAN.md dated 2026-04-21.

## Context

[ADR 0002](0002-documentation-convention) shipped a four-subdir wiki (`development/`, `operational/`, `design/`, `architecture/`) organized by **audience**. In practice, each page ends up simultaneously a reference (tables of flags, file layouts), a how-to (install steps, commands to run), and an explanation (rationale, tradeoffs). A first-time reader can't tell which they're reading, and the page optimizes for none of them.

The industry-converged answer for this is the [Diátaxis framework](https://diataxis.fr/) — four documentation modes organized by **user intent**:

| Mode | User is… | Shape |
|---|---|---|
| **Tutorial** | Learning | Step-by-step, guaranteed success, no forward references |
| **How-to** | Solving a specific problem | Goal-oriented recipe, assumes competence, no rationale |
| **Reference** | Looking something up | Information-dense, tables/code, no narrative |
| **Explanation** | Understanding *why* | Discursive prose, tradeoffs, decisions |

Adopters include Python, Cloudflare, Canonical/Ubuntu, Django, Gatsby, and [many others on beautiful-docs](https://github.com/matheusfelipeog/beautiful-docs). The consistent report from teams that have migrated (e.g. [Sequin](https://blog.sequinstream.com/we-fixed-our-documentation-with-the-diataxis-framework/)) is that the biggest readability win comes from the **hard rule against mixing modes on one page**, not from the mode names themselves.

Concrete symptoms in this repo's current wiki:

- `development/Getting-Started.md` opens with a Quick Reference table (reference mode), transitions to install commands (how-to), then covers running tests and making changes (how-to), then links to an ADR for "why" (explanation elsewhere). Three modes, one page, no tutorial.
- There is **no tutorial** anywhere in the wiki. A new user has no "walk me through my first `/plan` → `/work` → `/release`" path.
- `architecture/Overview.md` is primarily reference (file layout, adapter table) but is filed under "architecture" as if it were explanation.
- `design/Product-Intent.md` is explanation, `design/features/*` are a mix of status-tracking and how-to. "Design" is an audience grouping, not a shape.

The audience model also doesn't match how humans actually look for docs. A contributor fixing a bug reads from *all four* current subdirs. A new user and an agent resuming work also read across all four. What they want differs by intent (learn / do / look up / understand), not by role.

Separately, [ADR 0002](0002-documentation-convention)'s non-enforcement remains: the only structural check is basename collision. There is no lint that a tutorial stays a tutorial, that an ADR stays immutable after `accepted`, or that every implemented behavior has a reference entry. `/release`'s `documenter` pass is adversarial but LLM-judgment — [principle 4](https://github.com/alexherrero/agentm/blob/main/harness/principles.md) calls for deterministic structural checks first.

## Decision

Reshape the wiki convention around Diátaxis while preserving every load-bearing property of [ADR 0002](0002-documentation-convention) (installer boundary, phase-boundary sub-agent, dumb-mirror GitHub Wiki sync, globally-unique filenames, three templates maximum).

### 1. Four top-level subdirs = four modes

```
wiki/
├── Home.md
├── _Sidebar.md
├── tutorials/              # learning. Numbered. Each ends in success.
├── how-to/                 # task-oriented. "How do I …?" filenames.
├── reference/              # information. Specs, layouts, flags.
└── explanation/            # understanding. Rationale, concepts.
    └── decisions/          # ADRs. Immutable after `accepted`.
```

The four audience labels (development / operational / design / architecture) survive as **tags** in a page's front-line summary and as cross-link groupings on `Home.md`, not as directories.

### 2. Four templates, one per mode

Three were in scope under [ADR 0002](0002-documentation-convention); one more is added for Tutorial because it has the most authoring discipline. All templates live in [`templates/wiki/`](https://github.com/alexherrero/agentm/tree/main/templates/wiki) and are rendered as starter content at `/setup`.

| Mode | Template opens with | Body shape | What is BANNED |
|---|---|---|---|
| **Tutorial** | `> [!NOTE]` **Goal** + **Time** + **Prereqs** | Numbered H2 steps; each step has a "you should see" checkpoint; ends with **What you learned** + **Next** | Tables of options, forward references, "alternatively", rationale |
| **How-to** | `> [!NOTE]` **Goal** + **Prereqs** | Numbered imperative steps | Background/concepts, rationale, "why" prose — link to explanation instead |
| **Reference** | One sentence + `⚡ Quick Reference` table | Tables, fenced code, definition lists; sorted alphabetically or by lookup order | Storytelling, tutorials, rationale |
| **Explanation** (incl. ADR) | One-paragraph summary | Prose narrative; diagrams; `Context / Decision / Consequences` for ADRs | Step-by-step instructions, complete reference tables (link out) |

### 3. Machine-enforceable authoring rules

A new [`scripts/check-wiki.py`](https://github.com/alexherrero/agentm/blob/main/scripts/check-wiki.py) (sibling of the existing `check-references.py`) runs in CI and enforces:

| Rule | Check |
|---|---|
| Every page is filed under a valid mode dir | Path walk |
| Every page front-line declares its mode via a `> [!NOTE]` block with the required fields | Parse check |
| Tutorials and how-tos have numbered H2 steps; tutorials additionally have a `## What you learned` section | Structural parse |
| How-tos have no `## Rationale` / `## Why` / `## Background` / `## Context` headings (push to explanation) | Heading blocklist |
| Reference pages open with a table or `⚡ Quick Reference` within first 20 lines | Structural parse |
| ADRs in `explanation/decisions/` are append-only once `Status: accepted` | `git diff` check in CI: modifications to accepted ADRs must add lines under a new dated `## Amendment <date>` heading, never rewrite prior content |
| Filenames `CamelCase-With-Dashes.md`, globally unique across the wiki | Existing check, extended |
| All wiki-internal cross-links resolve; all `github.com/...#L<line>` links point at existing lines | Extend `check-references.py` |
| Every tutorial links to at least one reference or how-to; every how-to links to at least one reference; every reference is linked from at least one other page | Graph walk |
| Home + Sidebar reference every non-index page exactly once | Graph walk |
| Soft word-count ceilings (warning only): tutorial ≤ 1200, how-to ≤ 600, explanation ≤ 2000, reference unbounded | Line-count warning |

Structural checks only — no LLM-as-judge. Consistent with [principle 4](https://github.com/alexherrero/agentm/blob/main/harness/principles.md).

### 4. `documenter` sub-agent gets mode-aware write rules

Update [`harness/agents/documenter.md`](https://github.com/alexherrero/agentm/blob/main/harness/agents/documenter.md) so that at each phase boundary the agent writes to the correct mode dir and refuses to cross modes on a single page:

| Phase | Writes to |
|---|---|
| `/setup` | Scaffolds all four mode dirs, writes a starter **tutorial** (first-install walkthrough), starter **reference** (CLI + file layout), starter **explanation** (product intent, already-shipped principles). No how-tos yet — they accrete per-feature. |
| `/plan` | Creates/updates a **how-to** page per user-visible feature (status: pending). May add reference rows for new flags/state-files. Never writes tutorials or explanation at `/plan`. |
| `/work` (commit step) | Flips pending → implemented on the matching how-to. Adds reference rows for any new surface. Never writes tutorials or explanation. |
| `/release` | Full-pass adversarial sweep: find implemented behavior with no how-to or reference page; add ADR under `explanation/decisions/` for any decision the commit log reveals; update `Home.md` and `_Sidebar.md` as a reader-journey ordering. May promote stable how-tos to tutorials if they read like onboarding. |
| `/bugfix` | Update `reference/Known-Issues.md` if a gotcha emerged. Add ADR if the fix implies a decision change. Never touches tutorials. |

The hard rule "documenter may not add explanation content to a tutorial/how-to page, and vice versa" is in the agent spec and is also caught by the lint.

### 5. `Home.md` and `_Sidebar.md` as reader-journey

Both are hand-curated by `documenter`, not auto-generated. Ordering is by user intent:

```
Home.md:
  New here?          → tutorials/01-...
  Trying to do X?    → how-to/...
  Looking up Y?      → reference/...
  Want to know why?  → explanation/...
```

Sidebar mirrors the journey: Tutorials (numbered) → How-to (task-grouped) → Reference (alphabetical) → Explanation (topic-grouped, ADRs last).

### 6. Migration for already-installed projects

`install.sh --update` does not touch user-owned `wiki/` content ([ADR 0002](0002-documentation-convention)). A one-shot `documenter:migrate-to-diataxis` skill:

1. Reads every page under `wiki/{development,operational,design,architecture}/`.
2. Classifies it by heading shape + content heuristics (tables-first → reference, numbered-steps → how-to, ADR → explanation/decisions, prose-only → explanation).
3. Proposes a move + link-rewrite diff; asks before applying.
4. Flags pages that mix modes for a human split.

Opt-in, per-project. The convention does not force a migration — existing projects keep working; the lint is gated on a `wiki/.diataxis` marker file that `--migrate` drops in.

### 7. Installer scaffold changes

[`templates/wiki/`](https://github.com/alexherrero/agentm/tree/main/templates/wiki) is reshaped to the new four-dir layout with starter files: `tutorials/01-First-Run.md`, `how-to/README.md`, `reference/README.md`, `explanation/README.md`, `explanation/decisions/README.md`. The scaffold ships empty-body starters with the right mode block so a `/setup` run immediately produces lint-passing pages.

## Amendment 2026-05-27

**Wiki I/O contract — V4 #30 plan 2 of 3.** Codifies three wiki I/O conventions on top of this ADR. These extend the original spec; do not contradict it.

1. **Preview-before-write is mandatory for ALL writes** — per-repo or cross-repo. The agent (documenter sub-agent + `wiki-author` skill dispatcher) emits a unified diff of every proposed change + waits for explicit operator approval before executing. Per-write gate (not per-batch).

2. **Per-repo `.diataxis-conventions.md` override** is honored when present in the target repo's root. Operator-locked deviations from this ADR's defaults (e.g. a per-repo soft-ceiling override, a project-specific Tutorial naming convention, repo-specific ADR numbering) take precedence over the operator-global conventions in `_always-load/diataxis-*.md`. The convention from the `diataxis-author` skill (V4 #28 / plan #13) extends to the wiki I/O contract: convention drift between operator's Diátaxis wikis is mitigated per-repo, not globally.

3. **Cross-repo write target resolved via `repo_registry.list_repos()`** (vault-backed registry shipped V4 #30 plan 1; lives at `<vault>/_meta/repos.json`). The documenter sub-agent + `wiki-author` skill (added V4 #30 plan 2) accept cross-repo write targets only when the named slug appears in the registry. Unregistered targets refuse with an actionable error: `python3 scripts/repo_registry.py register <slug> --root <path>`.

The `wiki-author` skill (operator-facing dispatcher; added V4 #30 plan 2) wraps cwd-vs-cross-repo resolution + dispatches to the documenter for the actual write. Documenter's hard-boundary write scope (extended in the same plan) explicitly covers cross-repo wiki/** under these three constraints.

Cross-references: [`documenter` sub-agent spec](https://github.com/alexherrero/agentm/blob/main/harness/agents/documenter.md) (write-scope section) + [`wiki-author` skill](https://github.com/alexherrero/agentm/blob/main/harness/skills/wiki-author/SKILL.md) (ergonomics + trigger phrases) + V4 #30 plan 2 PLAN narrative.

## Consequences

**Positive**

- **One page, one job.** A reader always knows whether they're learning, doing, looking up, or understanding. This is the single biggest readability lift — confirmed by [Sequin's migration post](https://blog.sequinstream.com/we-fixed-our-documentation-with-the-diataxis-framework/) and Diátaxis-adopter retrospectives across Python, Canonical, and Cloudflare.
- **Tutorials become a first-class artifact.** The harness's "first five minutes" experience goes from absent to a numbered, checkpoint-driven walkthrough scaffolded at `/setup`.
- **Enforcement moves from human vigilance to CI.** Mode confusion, drifted cross-links, rewritten ADRs, orphan pages — all caught deterministically, no LLM judgment.
- **The `documenter` sub-agent gets narrower, sharper write rules.** "Write to the correct mode dir" is easier to follow (and review) than "write to the right audience subdir." Reduces the per-phase surface the agent must reason about.
- **ADR immutability is enforced by CI, not social convention.** A `git diff` check on `explanation/decisions/**` blocks rewrites, requires dated `## Amendment` blocks instead. Matches [joelparkerhenderson/architecture-decision-record](https://github.com/joelparkerhenderson/architecture-decision-record) guidance.
- **The installer boundary and phase-boundary discipline from [ADR 0002](0002-documentation-convention) carry over unchanged.** This ADR does not weaken any existing invariant.

**Negative**

- **Migration cost for already-installed projects.** The opt-in `documenter:migrate-to-diataxis` skill does most of the work, but per-project review is still needed — especially for pages that genuinely mix modes today. Mitigated by leaving the old layout valid until the `.diataxis` marker exists.
- **Four directories instead of four roles is less immediately obvious to humans scanning the repo.** "Why is 'how to install' under `how-to/` instead of `development/`?" Mitigated by `Home.md` — the landing page, not the directory listing, is where readers start. Directory names are secondary.
- **More templates the sub-agent must not confuse.** Four vs. three. Mitigated by the lint: a wrong-template page fails CI immediately, the feedback loop is short.
- **Temporarily, this repo's own wiki will be the only Diátaxis-shaped one.** The migration skill must be validated against a real Diátaxis-shaped repo — this one — before being offered to target projects. That bootstrap order is fine; it's the same order `/setup` and `install.sh` followed.
- **Lint rules grow.** `check-wiki.py` adds ~200 lines of CI surface. Offset by deleting ad-hoc "is Home.md up to date" reasoning from the `documenter` spec — those become graph-walk assertions.

**Load-bearing assumptions**

- **Diátaxis is the right frame for both human readers and agents resuming work.** Agents benefit from mode separation at least as much as humans — a tutorial and a reference make different demands on context, and a mode-mixed page forces the agent to pick which mode to operate in mid-page.
- **Structural lints catch ≥80% of real authoring mistakes.** The remaining 20% (prose quality, accuracy, completeness) stays a human call at `/release`. No LLM-as-judge.
- **The three-template cap from [ADR 0002](0002-documentation-convention) was load-bearing but not sacred.** Going to four templates is worth it *because* each template gets stricter — the total decision surface for the sub-agent decreases, not increases, once templates are mode-matched.
- **The prototype-first rollout path is feasible.** Before merging this ADR into `accepted`, validate by reshaping a single page in this repo (`Getting-Started`) into three Diátaxis pages and reading the result. If the prototype does not noticeably improve readability, this ADR stays `proposed` and we learn something.

## Next steps

This ADR stays `proposed` until:

1. **Prototype** — a single PLAN.md task reshapes `wiki/development/Getting-Started.md` into `tutorials/01-First-Install.md` + `how-to/Install-Into-Project.md` + `reference/Installer-CLI.md`. Read the result in rendered GitHub Wiki.
2. **Decision checkpoint** — if the prototype reads better, promote this ADR to `accepted` and write a PLAN.md for the full rollout (lint, sub-agent rewrite, scaffold reshape, migration skill). If it does not, amend or reject this ADR with the lived-experience evidence.
3. **Rollout** — land in this order: (a) `check-wiki.py` on the *current* wiki as warnings only, (b) reshape `templates/wiki/` scaffold, (c) reshape this repo's own wiki, (d) ship the `documenter:migrate-to-diataxis` skill, (e) flip lint from warning to blocking.
