---
parent_design: ../../memoryvault.md
part_slug: seed-pass
title: "Manual co-created seed pass + content migration"
status: pending
visibility: published
author: Alex Herrero
contributors: []
created: 2026-05-15
updated: 2026-05-15
last_major_revision: 2026-05-15
dependencies: [write-primitives, recall-loop, reflection-and-recovery]
estimated_scope: L
prd:
project:
---

# Manual co-created seed pass + content migration

**Parent design:** [MemoryVault](../../memoryvault.md) — see Detailed Design §7 (Manual seed pass — task 1 of #7a) for full architectural context. This part is deliberately the **last #7a part to ship** so the seed runs against a fully-functional vault and validates the loop end-to-end.

## Scope

The vault is empty when the prior #7a parts ship. This part populates it with curated content — explicitly co-created with the user, not agent-driven alone — so the recall loop has substance to work with from Day 1 of dogfooding.

**Seven sub-tasks**:

1. **Seed `MemoryVault/personal-private/_always-load/`** (~10-20 entries) by distilling existing user state:
   - `~/.claude/CLAUDE.md` dev-flow conventions (status reports with ✅/⬜ charts, link blocks, handoff phrases, paragraph-long task narratives, wake-on-CI pattern, locked design calls section, CHANGELOG + ADR shapes, coordinated cross-repo releases).
   - `~/Antigravity/agentm/AGENTS.md` sibling-repo conventions.
   - `~/Antigravity/crickets/AGENTS.md` toolkit conventions (PII guardrails, manifest schema, commit-no-coauthor rule).
   - Locked design calls from plans #3-#6 (evaluator allowlist; base hooks alphabetical-install-order invariant; design skill 10-section template lock; etc.). Each entry hand-written with the `always_load: true` frontmatter flag.

2. **Seed `MemoryVault/personal-projects/<slug>/`** for in-flight projects:
   - `agentm` — repo purpose, conventions, current state, key ADRs, ROADMAP execution order.
   - `crickets` — repo purpose, customization shape, key ADRs (0001-0004), shipping cadence.
   - operator-private siblings — device-config-symlinks, `~/.claude/CLAUDE.md` symlink convention, sibling-repo bootstrap (optional).
   - Each project gets a `_index.md` load-cheap entry plus `decisions/` + `conventions.md` files lifted from the repo's existing state.

3. **Seed `~/Obsidian/Ideas.md` + `MemoryVault/personal-private/_idea-incubator/`** from two sources:
   - User-provided fresh ideas during the seed-pass session (the user has a backlog of "things I want to try someday" — co-created).
   - Agent-extracted ideas from recent Claude Code transcripts at `~/.claude/projects/*/` (one-time extraction pass — though the full ongoing transcript-mining is plan #7b's scope).

4. **Migrate `~/ContextVault/` contents**:
   - `~/ContextVault/domains/*.md` → `MemoryVault/personal-private/domains/*.md` (reorganized into `personal-private` group with `kind: domain-reference` frontmatter).
   - `~/ContextVault/projects/ai-context-system/conversations/*.md` → `MemoryVault/personal-projects/memoryvault/conversations/*.md` (the prior design conversation that resolved 70% of the v1 architecture lands inside the MemoryVault project itself).
   - After migration: `~/ContextVault/` can be deleted; ROADMAP references to `~/ContextVault/` paths get updated in a follow-up doc pass.

5. **Inventory + decide on 3 additional source paths** (flagged in the parent design's Migrations §1 for follow-up at this task):
   - The user's own Obsidian vault — existing notes that may overlap with what MemoryVault would otherwise auto-capture. **Decision per source-note**: pull into MemoryVault (duplicate) / link to from MemoryVault but leave in place (reference) / leave entirely in user vault (not relevant to agent).
   - A GitHub experimental repo with a README + supporting files describing prior context-system / memory-related explorations. **Decision per file**: distill key ideas into `_always-load/` or `personal-projects/memoryvault/decisions/` / archive whole-cloth into `personal-projects/memoryvault/historical/` / leave in place.
   - Scattered synced-GitHub-repo content: CLAUDE.md fragments, AGENTS.md sections, PLAN.archive narratives, ADRs, ROADMAP locked design calls. **Decision per source**: most CLAUDE.md / AGENTS.md content already gets distilled in sub-task 1; PLAN.archive narratives are mineable for "what I've learned from past plans" → write to `personal-projects/agentm/decisions/` or similar; ADRs are reference material → link from `personal-projects/<repo>/decisions/`; locked design calls are gold-tier capture material.

6. **Validate by running sample recalls**:
   - Pose 5-10 sample queries that should match seeded content.
   - Confirm SessionStart + UserPromptSubmit hooks return sensible matches.
   - Tune the rank-merge weights (`sim × 0.7 + keyword × 0.3` per Tech Debt #7) if recall quality is off.

7. **Document the seed manifest** at `MemoryVault/_meta/seed-manifest-20260515.md` — what got seeded from where, when, by what process. Audit trail for future re-seeds + a reference for the discovery-mining part (#7b) so the transcript-reflection pass doesn't re-capture what was already seeded.

**This task is genuinely large** (probably a full session's worth of work) and is **deliberately co-created** — the agent can't seed the vault alone, and the quality of the seed determines whether the loop pays off in the first weeks of use. Expect the implementing session to be heavily interactive: the user provides additional context + ideas + decisions throughout.

## Dependencies

- **`write-primitives`** — all seeding goes through `/memory save` (the entries need correct frontmatter + vec-indexing on save).
- **`recall-loop`** — sub-task 6 (validation) requires the recall hooks + engine to exist; without them, seeding is theater (no way to verify the seed actually surfaces under recall).
- **`reflection-and-recovery`** — sub-task 3 (extracting ideas from past transcripts) reuses the reflection-mining logic in a one-time mode rather than per-session. Not strictly required (could be a manual extraction pass) but cleaner if it exists.

## Verification criteria

1. **`~/ContextVault/` fully migrated** — every `.md` file in the source path has a corresponding entry in MemoryVault at the expected path; no source files left un-migrated; `~/ContextVault/` can be deleted (verify by `ls -la` showing only `.git` or empty after).
2. **`_always-load/` has 10-20 entries** — count + spot-check entries are substantive (not stub placeholders); each has correct frontmatter (`always_load: true`, `kind`, `tags`, `status: active`).
3. **`personal-projects/` has at least 3 project trees** — agentm, crickets, plus operator-private siblings; each has `_index.md` + `decisions/` + `conventions.md` populated.
4. **`Ideas.md` populated** — at least 5 ideas (user-provided + agent-extracted); each has a 2-sentence summary + wikilink to `_idea-incubator/<slug>/_index.md`.
5. **`_idea-incubator/` has matching dirs** — one per `Ideas.md` entry; each has `_index.md` + deep-research files.
6. **3 additional source paths inventoried** — co-decision-doc captured at `MemoryVault/_meta/seed-additional-sources-decisions.md` recording the per-source decision (pull / link / leave).
7. **Sample recalls return sensible matches** — 5-10 sample queries; each returns relevant seeded content; rank-merge weights documented + tuned if needed.
8. **Seed manifest documented** — `_meta/seed-manifest-20260515.md` exists + lists what got seeded from where, when, by what process.
9. **All 3 OS CI workflows green** on the commit that lands this part (the seed manifest is the only thing committed — vault contents themselves are NOT in the toolkit repo, they're in the user's private Obsidian vault).

## Notes for the implementing /work session

- This part touches the user's actual personal Obsidian vault on disk — be careful with destructive operations. The migration from `~/ContextVault/` should `git mv` (or `mv`-then-`git-add` for non-git paths) rather than rewrite. Confirm with the user before deleting `~/ContextVault/` even after migration.
- The seed is co-created — the implementing session is much more interactive than a typical /work session. Expect the user to provide content + make decisions throughout. Don't try to seed autonomously.
- The 3 additional source paths are a meaningful decision surface — don't blow through them. Each source likely warrants a 5-10 minute discussion with the user about what's worth pulling in vs. leaving in place.
- The seed manifest at `_meta/seed-manifest-20260515.md` is the most important artifact for plan #7b's transcript-reflection pass — that pass needs to know what was already seeded so it doesn't duplicate.
- Consider seeding `_inbox/` with a handful of low-confidence sample entries so the user has something to triage during early dogfooding (validates the inbox-review workflow + builds the habit).
