# Completed Features

Reverse-chronological log of feature deliveries in this repo. One line in the overview table per plan; a dated section below with branch/commit ref and a short summary. Maintained by the `documenter` sub-agent at `/release` time.

This page is **narrative**, not a changelog — the authoritative version log is [`CHANGELOG.md`](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md). Use this page when you want to understand *what shipped and why* without reading every commit; use `CHANGELOG.md` when you want the semver history.

## ⚡ Overview

| Date | Plan / release | Features flipped | Notes |
|---|---|---|---|
| 2026-05-23 | [v2.6.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.6.0) — Evidence-tracking for /work (paired with toolkit v0.12.0) | **Default-FAIL evidence enforcement on `/work`**: every PLAN.md task starts with `evidence-met=false`; the agent must demonstrably READ relevant spec/test/evidence files (via the Read tool, observed by a new PreToolUse hook) before a Write/Edit that flips `[ ]` → `[x]` is allowed. Hook blocks (exit 2) with helpful stderr + 3 recovery paths. Harness-side ships the `/work` §5b spec amendment + templates/PLAN.md task-body convention update + Completed-Features row. Toolkit-side ships the substantive 4th base hook (~720-line stdlib-only Python helper + 61 unit tests + .sh/.ps1 entries + installer .py-sidecar extension). | 2 commits (`v2.5.0..v2.6.0`); paired with [agent-toolkit v0.12.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.12.0) which does the 9-commit substantive sweep across plan #9 tasks 1-6 + drift-cleanup; decision rationale + 3 locked design calls Q1-Q3 in toolkit-side [ADR 0009](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0009-evidence-tracker-hook.md); closes ROADMAP item #9; 6th consecutive paired-release pair + second non-doc-only on the harness side (after v2.5.0); 2 in-flight scope expansions caught + fixed mid-plan (installer .py-sidecar pattern; toolkit baseline drift 21→0 structural errors) |
| 2026-05-22 | [v2.5.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.5.0) — Auto-context into harness phases (paired with toolkit v0.11.1) | **First non-doc-only paired pair in the run** — harness ships real new phase behavior across all 5 phases + bugfix pipeline. New `scripts/harness_memory.py` dispatcher (4 sub-commands: `recall` / `offer-save` / `plan-done-promotion` / `available`) called from amended phase specs at natural boundaries. Self-modulating offer-save (agent-supplied `--confidence ≥ HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD` → silent save with stderr notice; below threshold → preview-and-ask prompt). Shared `.harness/.promoted-progress-cursor` between `/work` plan-done + `/release` tail-scan triggers means single-fire per plan-window. Graceful-skip when MemoryVault not installed (harness still runs unchanged). | 9 commits (`v2.4.3..v2.5.0`); paired with [agent-toolkit v0.11.1](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.11.1) which adds the cross-repo memory protocol doc (wiki-only PATCH); new [ADR 0007](0007-auto-context-into-harness-phases) with 5 locked design calls (Q1–Q5: per-phase budgets / vault_project 3-tier auto-detect / graceful-skip / self-modulating ask / dual-trigger cursor-tracked promotion); 5 phase specs + 1 pipeline amended via sub-letter convention (§1b / §4c / §7b / §7c / §2b / §5b / §5c) preserving integer §-numbers + 4 wiki line-anchor updates; closes ROADMAP item #8; 5th consecutive paired-release pair but **first non-doc-only** in the run (harness MINOR bump justified by real new phase behavior) |
| 2026-05-22 | [v2.4.3](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.3) — diataxis-author skill (paired with toolkit v0.11.0) | **New second-major-skill `diataxis-author` shipped in toolkit** — one skill, five sub-commands (`/diataxis author` + `check` + `repair` + `migrate` + `classify`) covering the full Diátaxis-wiki lifecycle. Subsumes `migrate-to-diataxis` harness predecessor (deprecated). Harness-side is doc-only (Completed-Features + CHANGELOG + deprecation NOTE on predecessor); skill scripts + sub-agent + templates + how-to + ADR ship on toolkit side. | 1 harness commit (deprecation NOTE `d4d4adf`) + 1 release commit; paired with [agent-toolkit v0.11.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.11.0) which does the substantive 8-commit sweep across plan #13 parts 1-5 (scaffold + author-classify + check-repair + migrate-subsume + agentmemory-docs-release); decision rationale in toolkit-side [ADR 0008](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0008-diataxis-author.md); second real dogfood of plan #6's `/design` skill (after MemoryVault); 4th consecutive paired-release-as-documentation pair (v2.4.0/v2.4.1/v2.4.2/v2.4.3) |
| 2026-05-22 | [v2.4.2](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.2) — MemoryVault Discovery + Mining (paired with toolkit v0.10.0) | **Discovery + mining additive layer**: five new `/memory` sub-commands (`index-skills` / `reflect corpus` / `discover-skills` / `adapt-skills` / `watchlist`) turn the vault from a static curated store into a living surface. Personal-skills auto-indexer; historical-transcript-backlog mining with dry-run-default; cadence-checked internet skill-discovery scan (operator's 4-source whitelist); adapt-don't-import workflow with 6-rule Python rubric + GitHub metadata + trustworthiness signals + LLM sub-agent judgment; promote/dismiss/defer review surface. **Adapt-don't-import is architecturally enforced** via sub-agent write allowlist. Harness-side is doc-only (Completed-Features + CHANGELOG + ROADMAP move + PLAN archive). | 1 commit (`v2.4.1..v2.4.2`); paired with [agent-toolkit v0.10.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.10.0) which does the 8-commit substantive sweep across plan #7b tasks 1-7; decision rationale + 7 locked design calls in toolkit-side [ADR 0007](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0007-memoryvault-discovery.md); closes ROADMAP item #7b (MemoryVault parent design fully shipped — both #7a + #7b roadmap items now Completed); paired-release-as-documentation pattern (continues from v2.4.0 + v2.4.1) |
| 2026-05-20 | [v2.4.1](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.1) — Local-only embeddings (paired with toolkit v0.9.2) | **Embedding-mode collapse**: Voyage/Anthropic API mode dropped from agent-toolkit's memory skill; local sentence-transformers is now the only production mode. Default model `BAAI/bge-large-en-v1.5` (1024-d native; 384-d → 1024-d dim bump). Harness-side is doc-only (Completed-Features + CHANGELOG); embed.py + vec_index.py + install scripts refactor happens on toolkit side. | 1 commit (`v2.4.0..v2.4.1`); paired with [agent-toolkit v0.9.2](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.9.2) which does the 8-commit substantive sweep across plan #18 tasks 1-7 (embed.py refactor + vec_index.py rebuild + smoke install split + install scripts pip-install + ADR amendment + docs rewrite + this paired release); decision rationale in toolkit-side [ADR 0001's 2026-05-20 amendment](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0001-agent-toolkit-purpose.md#amendment-2026-05-20); plan #18 is the **first mid-flight plan insertion** in the personal-dev-env (inserted into the active queue during plan #7a part 5 execution; seed-pass resumes at task 6 after this release pair ships); paired-release-as-documentation pattern (continues from v2.4.0) |
| 2026-05-17 | [v2.4.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.0) — Gemini-CLI host removal (paired with toolkit v0.9.0) | **Host-scope reduction**: standalone Gemini CLI dropped from supported hosts across the personal-dev-env. Keeps Claude Code + Antigravity. Harness-side is doc-only (Completed-Features + CHANGELOG); customization sweep happens on toolkit side. | 1 commit (`v2.3.1..v2.4.0`); paired with [agent-toolkit v0.9.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.9.0) which does the substantive customization + installer + wiki + ADR sweep across 5 commits (e1b477e + 5af1a59 + b216043 + 13109fa + 7a4162f); decision rationale in toolkit-side [ADR 0006](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0006-gemini-cli-host-removal.md); first host-scope reduction in personal-dev-env history; paired-release-as-documentation pattern (substantive change lives toolkit-side; harness still ships a paired MINOR for version-cadence readability) |
| 2026-05-16 | [v2.3.1](https://github.com/alexherrero/agentic-harness/releases/tag/v2.3.1) — `/plan` external-review-handoff option (dogfood-driven patch) | Additive: `/plan` phase spec §4b documents the external-review-handoff option (pre-handoff snapshot + transfer-context generation + handoff prompt + diff-on-resume + Accept/Iterate/Discard) as an alternative to inline iteration on long plans | 1 commit (`v2.3.0..v2.3.1`); paired with [agent-toolkit v0.8.1](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.1) which adds the same option to `/design author` + `/design translate`; shared transfer-context template lives toolkit-side; first cross-repo dogfood-driven amendment shipped as coordinated patch pair (surfaced during MemoryVault design-doc walk on 2026-05-15); design rationale in toolkit-side [ADR 0004 amendment (2026-05-16)](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0004-design-skill.md#amendment--2026-05-16-v081-external-review-handoff-option) |
| 2026-05-15 | [v2.3.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.3.0) — `/release` + `/setup` integration for agent-toolkit's `/design` skill | Additive: `/release` §1b lifecycle hook auto-promotes queued plans + transitions design `final → launched` + surfaces launched published designs in `wiki/Home.md`; `/setup` §7 scaffolds `wiki/explanation/designs/` landing dir; `templates/wiki/explanation/designs/.gitkeep` + README; `EXTERNAL_CUSTOMIZATIONS` extends with `design`; `/work` Step 11 ROADMAP-driven enhancement (universal) | 2 commits (`v2.2.0..v2.3.0`); paired with [agent-toolkit v0.8.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.0) which ships the `/design` skill with 3 sub-commands; no new harness ADR (the design decision lives in [agent-toolkit ADR 0004](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0004-design-skill.md)); harness still functions standalone — §1b silent-skips when no design-doc origin signal |
| 2026-05-14 | [v2.2.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.2.0) — `/work` + `/release` augmentable with agent-toolkit's base hooks | Additive: `/work` + `/release` phase specs gain optional sections documenting kill-switch + steer + commit-on-stop dispatch from agent-toolkit (operator-precision hooks for long-running sessions); `check-references.py` `EXTERNAL_CUSTOMIZATIONS` extended with 3 new hook names | 1 commit (`v2.1.0..v2.2.0`); paired with [agent-toolkit v0.7.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.7.0) which ships the three hook customizations + first-class `kind: hook` installer support; no new harness ADR (the design decision lives in [agent-toolkit ADR 0003](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0003-base-operator-hooks.md)); harness still functions standalone — both sections graceful-skip when toolkit absent |
| 2026-05-13 | [v2.1.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.1.0) — `/review` augmentable with agent-toolkit's `evaluator` | Additive: `/review` phase spec gains §3b documenting evaluator dispatch alongside `adversarial-reviewer` (complementary, not competing); `check-references.py` `EXTERNAL_SKILLS` → `EXTERNAL_CUSTOMIZATIONS` rename to cover cross-repo agent references | 1 commit (`v2.0.0..v2.1.0`); paired with [agent-toolkit v0.6.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.6.0) which ships the evaluator; no new harness ADR (the decision lives in [agent-toolkit ADR 0002](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0002-evaluator-design.md)); harness still functions standalone — §3b graceful-skips when toolkit absent |
| 2026-05-12 | [v2.0.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.0.0) — `agent-toolkit` repo split: `dependabot-fixer` + `ship-release` moved out | **BREAKING**: two skills migrated to [`agent-toolkit`](https://github.com/alexherrero/agent-toolkit); new shared `lib/install/` byte-identical across repos; PII + lib-parity CI gates added | 9 commits (`v1.0.0..v2.0.0`); new [ADR 0006](0006-agent-toolkit-split); paired with [agent-toolkit v0.5.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.5.0); harness shared-skills narrow 4→2; cross-platform byte-identity work surfaced + fixed four real Mac/Windows bugs |
| 2026-05-11 | [v1.0.0](https://github.com/alexherrero/agentic-harness/releases/tag/v1.0.0) — Three-adapter scope; Codex dropped; 1.0.0 commitment | **BREAKING**: Codex adapter removed; true-sync `--update` semantics; firm-semver 1.0.0 floor | 13 commits (`v0.9.0..v1.0.0`); new [ADR 0005](0005-drop-codex-support); ~1300 lines net removed; first major version; parity invariant simplified to three adapters |
| 2026-04-23 | [v0.9.0](https://github.com/alexherrero/agentic-harness/releases/tag/v0.9.0) — Diátaxis documentation spec + `/doctor` skill | Diátaxis rollout (ADR 0004, 7-task plan); `migrate-to-diataxis` skill; mode-aware `documenter` writes; `/doctor` skill for post-install verification | 10 commits (`v0.8.7..v0.9.0`); new [ADR 0004](0004-diataxis-documentation-spec); two new shared skills; `scripts/check-wiki.py` shipped + flipped to `--strict`; wiki dogfood reshaped with `git mv` for blame |
| 2026-04-21 | [v0.8.7](https://github.com/alexherrero/agentic-harness/releases/tag/v0.8.7) — GitHub Projects wiring + documenter end-to-end dogfood | `feat-gh-projects-integration` (pending — gated on offer-cycle observation); `feat-documenter-subagent` (this sweep is the dogfood) | 4 commits (`801dbd7..HEAD`), 23 files; new [ADR 0003](0003-ProjectsV2-Ownership-And-Linking), new [Feature page](GitHub-Projects-Integration) |

## 2026-05-22 — v2.4.3: diataxis-author skill (paired with toolkit v0.11.0)

**Commit range:** `v2.4.2..v2.4.3` (2 commits on `main`: predecessor deprecation `d4d4adf` + this release commit). Release notes: [v2.4.3](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.3). Paired with [agent-toolkit v0.11.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.11.0) (8 toolkit commits across plan #13 parts 1-5).

### What shipped

agent-toolkit's second major skill after `memory`: **`diataxis-author`** — author + maintain a Diátaxis-style wiki for any repo. Five sub-commands:

1. **`/diataxis author <slug>`** — live authoring guidance: mode classification + template selection + filename style; pre-fills `wiki/<mode-dir>/<filename>.md` skeleton.
2. **`/diataxis classify <file>`** — single-page mode classification (Tier-1 deterministic heuristic + Tier-2 `diataxis-evaluator` sub-agent for ambiguous cases).
3. **`/diataxis check [--strict]`** — drift detection (wraps `check-wiki.py` + adds 4 skill-side heuristics: mode-mixed, stale-cross-ref, template-shape-drift, convention-drift).
4. **`/diataxis repair`** — interactive fix-application; preview-first per finding; dispatches `documenter` sub-agent for mode-mixed splits.
5. **`/diataxis migrate`** — one-shot legacy → Diátaxis migration (subsumes harness's `migrate-to-diataxis` predecessor; same contract + `.diataxis-conventions.md` auto-seed + delegation to `/diataxis repair` for splits).

Plus: `diataxis-evaluator` read-only sub-agent (zero filesystem write scope); 4 Diátaxis templates; AgentMemory integration with 3-tier convention fallback (per-repo `.diataxis-conventions.md` > vault `_always-load/diataxis-*.md` > ADR 0004 defaults).

Substantive sweep happens entirely on toolkit side (8 toolkit commits + 1 harness commit). Harness side is doc-only this release pair: this Completed-Features row + narrative + paired CHANGELOG entry + the deprecation NOTE on `harness/skills/migrate-to-diataxis.md` (`d4d4adf`, shipped during plan part 4).

### Why this shape

The operator maintains three Diátaxis-shaped wikis (agentic-harness + agent-toolkit + dev-setup) plus the just-shipped MemoryVault parent design. Diátaxis discipline was previously supported only by:
- `scripts/check-wiki.py` (strict validator; catches post-write)
- `documenter` sub-agent (sweep at `/release` only)
- `migrate-to-diataxis` predecessor (one-shot; never fires after migration)

The gap was live authoring guidance + ongoing drift detection + repair. The new skill ships that gap + subsumes the predecessor + repurposes `documenter` as the skill's mechanical-write worker (same orchestration + sub-agent pattern as `/memory adapt-skills` + `adapt-evaluator` from #7b task 4).

This is the **2nd real dogfood of plan #6's `/design` skill** (first was MemoryVault parent design closed 2026-05-20). Parent design lived through `/design author` → walk-sections → operator approval as final → `/design translate` (5 parts) → `/design sequence` (1 active + 4 queued PLAN.md files) → 5 parts executed in sequence → parent design transitions `final → launched` on this release.

The **paired-release-as-documentation pattern** continues from v2.4.0 + v2.4.1 + v2.4.2: substantive change lives on one side (toolkit); the other side (harness) ships a paired PATCH for version-cadence readability. v2.4.3 is the documentation-acknowledgement counterpart to v0.11.0; **4th consecutive paired-doc-only pair** — the pattern is now firmly established convention.

### Doesn't do

- Doesn't change harness phase specs. Future PATCH could amend `/release` to call `/diataxis check` when skill installed (graceful-skip otherwise) — deferred to keep v1 surface narrow.
- Doesn't remove `harness/skills/migrate-to-diataxis.md` — predecessor stays through v1 dogfood window. Removal in a follow-up harness PATCH after `/diataxis migrate` proves out.
- Doesn't auto-fork into `agent-toolkit/skills/` from sub-agent. `diataxis-evaluator` sub-agent has **zero filesystem write scope** (stricter than `adapt-evaluator`'s scoped-write-to-`_skill-watchlist/`).
- Doesn't ship hooks integration (SessionStart surface; UserPromptSubmit auto-dispatch). Deferred to a v2 task per parent design's §8 Hooks Integration.
- Doesn't ship cross-skill integration with ROADMAP items #19-#23 — sibling work; ships independently.

### Tracked as

2 harness commits in this release cycle (deprecation NOTE + this release commit). No new feature entries in `features.json` — the new skill is wholly a toolkit-internal capability + the harness-side change is doc-only.

### Related

- [agent-toolkit v0.11.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.11.0) — paired release; 8 commits across plan #13 parts 1-5
- [agent-toolkit ADR 0008 — diataxis-author skill](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0008-diataxis-author.md) — 4 locked design calls + 4 load-bearing assumptions with re-audit triggers
- [agent-toolkit parent design — diataxis-author](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/designs/diataxis-author.md) — full architectural context (Status: launched)
- [agent-toolkit how-to — Use Diataxis Author](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/how-to/Use-Diataxis-Author.md) — 5 worked scenarios + AgentMemory walkthrough + troubleshooting
- [agentic-harness ADR 0004 — Diátaxis Documentation Spec](https://github.com/alexherrero/agentic-harness/blob/main/wiki/explanation/decisions/0004-diataxis-documentation-spec.md) — canonical Diátaxis convention this skill enforces (upstream)
- [predecessor `migrate-to-diataxis` (deprecated)](https://github.com/alexherrero/agentic-harness/blob/main/harness/skills/migrate-to-diataxis.md) — subsumed by `/diataxis migrate`
- [v2.4.3](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.3) — release notes; [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)

## 2026-05-22 — v2.4.2: MemoryVault Discovery + Mining (paired with toolkit v0.10.0)

**Commit range:** `v2.4.1..v2.4.2` (1 commit on `main`). Release notes: [v2.4.2](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.2). Paired with [agent-toolkit v0.10.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.10.0) (8 toolkit commits across plan #7b tasks 1-7).

### What shipped

agent-toolkit's memory skill gained **five new sub-commands** that turn the vault from a static curated store into a living surface:

1. **`/memory index-skills`** — auto-indexer walks `SKILL.md` files across configured skill paths; writes one `kind: skill-pointer` entry per skill to `<vault>/personal-skills/<repo>/<skill-name>.md`. Auto-fires from `bash agent-toolkit/install.sh` against toolkit's own `skills/` + sibling `agentic-harness/.claude/skills/`. Idempotent: unchanged entries skip.
2. **`/memory reflect corpus`** — batched paced walk over `~/.claude/projects/*/<session>.jsonl` with skip-resume state file at `<vault>/_meta/transcript-reflection-state.json`. **Dry-run by default** — first call counts + estimates without writing; operator re-runs with `--execute`. Single-session resume granularity.
3. **`/memory discover-skills`** — periodic fetcher over operator-editable source whitelist at `<vault>/personal-private/skill-discovery-sources.md`. Auto-seeds 4 sources in operator's confirmed priority order: Anthropic Cookbook → awesome-claude-code → awesome-mcp-servers → awesome-llm-apps. Per-source dated snapshot + diff cache. Cadence default 7d; `--cadence-check` self-throttles for idle-hook integration (extended `memory-reflect-idle` to call it at end of each pass).
4. **`/memory adapt-skills`** — Pass 1 of the adapt-don't-import workflow. Parses candidates from cached diffs; applies 6-rule rubric (R1 new-tool / R2 complements-convention / R3 agent-building-context / R4 names-primitive / R5 experimental-flag -1 / R6 cross-vendor-proprietary -2); thresholds 3+ HIGH / 1-2 MEDIUM / ≤0 LOW. Enriches with GitHub metadata (owner / repo / stars / archived / last_commit / license SPDX / html_url via unauth API) + trustworthiness signals (operator-editable trusted-orgs whitelist + cross-citation count + activity-recent / permissive-license / high-stars / low-stars / archived-warning). Writes enriched candidate JSONs for the `adapt-evaluator` sub-agent (Pass 2 — read-only with **write allowlist physically scoped to `_skill-watchlist/`** for adapt-don't-import architectural enforcement).
5. **`/memory watchlist`** — review surface. `list` / `review` (interactive) / `promote <slugs>` / `dismiss <slugs>` / `defer <slugs> --until YYYY-MM-DD [--reason]`. Promote = annotation-only; dismiss = archive (never rm); defer = snooze. Non-TTY stdin defaults all prompts to skip (never-silent-action contract).

Substantive sweep happens entirely on the toolkit side (8 toolkit commits across plan #7b tasks 1-7: index_skills.py + reflect.py corpus + discover_skills.py + adapt_skills.py + adapt-evaluator agent + watchlist_review.py + docs + ADR 0007 + paired release; plus one Windows CRLF fix commit per `[[wake-on-ci-pattern]]` scope expansion). Harness side is doc-only: this Completed-Features row + narrative + paired CHANGELOG v2.4.2 entry. No harness phase spec changes, no adapter changes, no new harness ADR.

### Why this shape

Plan #7a (MemoryVault Core, closed 2026-05-20) shipped the static curation surface — auto-recall via hooks + reflection sidecar + tri-modal routing + idea ledger + manual seed-pass. #7b's mandate was the *living* surface:

- **Indexing installed skills** so the agent automatically knows what tools the operator has built (no re-mentioning per session)
- **Mining the historical transcript backlog** for durable patterns the manual seed pass missed (~140 sessions in operator's `~/.claude/projects/`)
- **Scanning curated internet sources** for skill-shaped patterns to potentially adopt
- **Gating adoption** through a deterministic Python rubric + LLM sub-agent judgment with adapt-don't-import architecturally enforced — agents physically cannot fork into `agent-toolkit/skills/`; only the operator's manual authoring step graduates a pattern to a real skill

The **two-pass adapt-don't-import architecture** (deterministic Python Pass 1 + LLM sub-agent Pass 2) is the load-bearing design call. Pure rubric is testable + fast but semantically blind; pure LLM is sharp but expensive + non-deterministic. Pass 1 narrows the surface (drops LOW outright; gates enrichment + sub-agent dispatch); Pass 2 makes the final call. Operators can inspect Pass 1's JSON output to verify the rubric is scoring sensibly before paying the LLM cost.

**Stdlib-only Python pipelines** (no new third-party deps) align with plan #18's local-first design. GitHub API access via unauthenticated `urllib.request` with graceful-skip on 60/hr rate limit.

The **paired-release-as-documentation pattern** continues from v2.4.0 + v2.4.1: substantive change lives entirely on toolkit side; harness still ships a paired PATCH for version-cadence readability.

### Doesn't do

- **Doesn't auto-fork into `agent-toolkit/skills/`** — architectural rule enforced by the `adapt-evaluator` sub-agent's tool allowlist (Write scoped to `_skill-watchlist/<source-slug>/<pattern-slug>.md` only). The operator's manual authoring is the only path to a real skill.
- **Doesn't auto-dispatch the Pass 2 sub-agent** — operator invokes manually via the `/memory adapt-skills` skill body. Pass 1 (JSON enrichment) is automatic; Pass 2 (judgment + watchlist write) is operator-gated for v1. Future task may add idle-hook auto-dispatch with `--limit N`.
- **Doesn't change harness phase specs** (no discovery-mining logic on harness side — wholly a toolkit-side concern via the memory skill).
- **Doesn't change adapter wrappers** (canonical-reference inheritance — adapters point at phase specs which are untouched).
- **Doesn't ship a new harness ADR** (the discovery + mining decision lives in toolkit-side ADR 0007).
- **Doesn't process HTML sources** — `discover_skills.py` is markdown-only; HTML pages get cached as raw HTML, diff still works but downstream candidate parsing assumes markdown structure.
- **Doesn't ship the `/memory search` body** — that stub remains for a future task to wire `recall.py query` into the skill UX.
- **Doesn't ship cross-surface AgentMemory protocol** — that's [ROADMAP item #22](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md) (queued post-#7b).

### Tracked as

Single-commit harness release. No new feature entries in `features.json` (discovery + mining isn't a net-new user-visible feature on harness side — it's a toolkit-internal architectural extension).

### Related

- [agent-toolkit v0.10.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.10.0) — paired release; 8 commits across plan #7b tasks 1-7
- [agent-toolkit ADR 0007 — MemoryVault Discovery + Mining](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0007-memoryvault-discovery.md) — 7 locked design calls + 4 load-bearing assumptions with re-audit triggers
- [agent-toolkit MemoryVault design doc](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/designs/memoryvault.md) — Document History row 11 captures the discovery + mining additive layer
- [agent-toolkit `adapt-evaluator` sub-agent](https://github.com/alexherrero/agent-toolkit/blob/main/agents/adapt-evaluator.md) — read-only Pass 2 worker with write allowlist scoped to `_skill-watchlist/`
- [Use-The-Memory-Skill how-to](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/how-to/Use-The-Memory-Skill.md) — `## Discovery + mining (plan #7b)` section with worked invocations for all 5 new sub-commands
- [ROADMAP item #7b](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md) — the roadmap entry closed by this release (MemoryVault parent design now fully shipped — both #7a + #7b are Completed)
- [v2.4.2](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.2) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)

## 2026-05-20 — v2.4.1: Local-only embeddings (paired with toolkit v0.9.2)

**Commit range:** `v2.4.0..v2.4.1` (1 commit on `main`). Release notes: [v2.4.1](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.1). Paired with [agent-toolkit v0.9.2](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.9.2) (8 toolkit commits across plan #18 tasks 1-7).

### What shipped

agent-toolkit's memory skill collapsed from dual-mode (Voyage/Anthropic API + local sentence-transformers fallback) to **local-only sentence-transformers**. Default model upgraded `all-MiniLM-L6-v2` (384-d, MTEB English 56.3) → `BAAI/bge-large-en-v1.5` (1024-d, MTEB English 64.2). `EMBEDDING_DIM` bumped 384 → 1024. New `AGENT_TOOLKIT_EMBEDDING_MODEL` env var as escape hatch for low-spec hosts (still local — no API option). All `VOYAGE_API_KEY` / `ANTHROPIC_API_KEY` env var reads removed; `MEMORY_USE_API_EMBEDDINGS` env var no longer consulted. `sentence-transformers` becomes a hard install dep (was optional fallback) — `install.sh` + `install.ps1` pip-install it by default from new `requirements.txt`; opt-out via `--no-python-deps`. `vec_index.py` gained a `rebuild` subcommand + dim-mismatch detection for migrating existing 384-d indexes. Substantive sweep happens entirely on the toolkit side (8 toolkit commits across plan #18 tasks 1-7: embed.py refactor + smoke install fixups + vec_index.py rebuild + local-mode integration test + install scripts + ADR amendment + docs rewrite + paired release). Harness side is doc-only: this Completed-Features row + narrative + the paired CHANGELOG v2.4.1 entry. No harness phase spec changes, no adapter changes, no new harness ADR.

### Why this shape

The primary operator is a Claude Ultra subscriber without a separate Anthropic / Voyage API key — the API path was unreachable for the toolkit's actual user. Dual-mode added surface area (mode resolution, env-var contract, dim-truncation, two test paths) without value for the personal-dev-env use case. Modern small-to-mid local models (BGE-large family, mxbai, nomic-embed) deliver near-SOTA MTEB results on desktop-class hardware (M-series + 64GB RAM) — the quality gap that motivated dual-mode is no longer load-bearing.

Plan #18 was **inserted mid-flight** of plan #7a part 5 (seed-pass) because task 6 (validate via sample recalls) needs a worthwhile embedding model for validation signal to be meaningful. This is the **first mid-flight plan insertion** in the personal-dev-env: the active PLAN.md was archived to `.harness/PLAN.paused.20260520-memoryvault-seed-pass.md`, the new plan was written as the active PLAN.md, executed, and after this release pair ships the paused plan is restored as active. The mechanism is captured in plan #18's "How to resume" section + the harness v2.4.1 CHANGELOG entry as a documented precedent for future mid-flight insertions.

The **paired-release-as-documentation pattern** continues from v2.4.0: substantive change lives entirely on one side (toolkit); the other side (harness) still ships a paired MINOR for version-cadence readability. v2.4.1 is the documentation-acknowledgement counterpart to v0.9.2.

### Doesn't do

- Doesn't change harness phase specs (no embedding-related logic on harness side — embedding is wholly a toolkit-side concern via the memory skill).
- Doesn't change adapter wrappers (canonical-reference inheritance — adapters point at phase specs which are themselves untouched).
- Doesn't change scripts, validators, or tests on harness side.
- Doesn't ship a new harness ADR (the embedding-mode decision lives in toolkit-side ADR 0001's 2026-05-20 amendment).
- Doesn't auto-migrate existing 384-d vec-indexes — operators see a graceful-skip warning on first invocation with the new toolkit + a clear `python3 vec_index.py rebuild --vault-path <path>` command (toolkit-side handling).
- Doesn't ship Antigravity 2.0 / Antigravity CLI host support — that's [ROADMAP item #17](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md) (queued post-#7a).

### Tracked as

Single-commit harness release. No new feature entries in `features.json` (embedding-mode collapse isn't a net-new user-visible feature on harness side — it's a toolkit-internal architectural narrowing).

### Related

- [agent-toolkit v0.9.2](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.9.2) — paired release; 8 commits across plan #18 tasks 1-7
- [agent-toolkit ADR 0001's 2026-05-20 amendment](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0001-agent-toolkit-purpose.md#amendment-2026-05-20) — full decision rationale + 4 load-bearing assumptions with re-audit triggers
- [agent-toolkit MemoryVault design doc](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/designs/memoryvault.md) — design doc body rewritten in-place across 12 substantive references; Document History row 10 captures the rewrite
- [ROADMAP item #18](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md) — the roadmap entry inserted mid-flight 2026-05-20 that triggered this plan
- [v2.4.1](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.1) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)

## 2026-05-17 — v2.4.0: Gemini-CLI host removal (paired with toolkit v0.9.0)

**Commit range:** `v2.3.1..v2.4.0` (1 commit on `main`). Release notes: [v2.4.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.0). Paired with [agent-toolkit v0.9.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.9.0) (5 toolkit commits across plan #15 tasks 1-5).

### What shipped

Standalone Gemini CLI dropped from supported hosts across the personal-dev-env. Antigravity (Gemini-in-IDE) stays — different surface than standalone CLI. Substantive customization sweep happens entirely on the toolkit side (5 toolkit commits across plan #15 tasks 1-5: installer dispatch + manifests + validator/tests + wiki/ADRs + MemoryVault design docs). Harness side is doc-only: this Completed-Features row + narrative + the paired CHANGELOG v2.4.0 entry. No harness phase spec changes, no adapter changes, no new harness ADR.

### Why this shape

In practice the operator (one person) runs Claude Code + Antigravity. Standalone Gemini CLI was added defensively in v0.1.0 of the toolkit (then `agentic-harness` v1.0.0 era — the original three-adapter scope) but never grew into the workflow; the Gemini usage that does happen lives inside Antigravity's IDE-level integration. Maintaining a third host destination, dispatch arms in `install.{sh,ps1}`, a third column in `Per-Host-Paths.md`, three case-arms in every dispatch function, and three branches in tests was carrying maintenance cost without observed payoff. Plan #7a part 1 (memory skill scaffold, shipped 2026-05-16) was the first new skill that opted out of the three-host scope from day 1; plan #15 (executed 2026-05-16/17) sweeps the rest of the toolkit to match.

The split between toolkit (substantive customization sweep) and harness (doc-only paired release) follows the customization-vs-phase pattern from agentic-harness ADR 0006: customizations live in toolkit; phase-gated workflow lives in harness; the two integrate via shared template + shared workflow contract. The host-scope decision is entirely a toolkit-side concern; harness inherits via its dependency on toolkit customizations.

The **paired-release-as-documentation pattern** (new in v2.4.0): when the substantive change lives entirely on one side of the toolkit/harness split, the other side still ships a paired release with framing-only content. This keeps the two repos' version cadences readable for operators tracking changes — they don't have to wonder "why did toolkit ship a MINOR but harness didn't?". v2.4.0 is the documentation-acknowledgement counterpart to v0.9.0.

### Doesn't do

- Doesn't change harness phase specs (no host-related conditionals to update — phases don't reference specific hosts by name).
- Doesn't change adapter wrappers (canonical-reference inheritance — adapters point at phase specs which are themselves untouched).
- Doesn't change scripts, validators, or tests on harness side (no harness-side customizations to validate since the v2.0.0 split).
- Doesn't ship a new harness ADR (the host-scope decision lives in toolkit-side ADR 0006).
- Doesn't auto-clean up legacy `.agents/skills/` on harness installs — that's toolkit-side `install.sh` behavior, gated by `--no-legacy-cleanup` flag.

### Tracked as

Single-commit harness release. No new feature entries in `features.json` (host-scope reduction isn't a net-new user-visible feature — it's a removal). The legacy-cleanup-prompt operator UX is documented in [toolkit-side Installer-CLI reference](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/reference/Installer-CLI.md) + ADR 0006 + CHANGELOG v0.9.0.

### Related

- [agent-toolkit v0.9.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.9.0) — paired release; 5 commits doing the substantive sweep
- [agent-toolkit ADR 0006 — Gemini-CLI host removal](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0006-gemini-cli-host-removal.md) — full decision rationale + 4 load-bearing assumptions
- [agent-toolkit ADR 0001 + 0002 amendments (2026-05-17)](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0001-agent-toolkit-purpose.md#amendment-2026-05-17) — preserve original text + audit trail; same pattern as ADR 0004's 2026-05-16 amendment
- [ROADMAP item #15](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md) — the roadmap entry that triggered this plan (added 2026-05-16 during plan #7a part 1 task 1 ship)
- [v2.4.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.4.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)

## 2026-05-16 — v2.3.1: `/plan` external-review-handoff option (paired with toolkit v0.8.1)

**Commit range:** `v2.3.0..v2.3.1` (1 commit on `main`). Release notes: [v2.3.1](https://github.com/alexherrero/agentic-harness/releases/tag/v2.3.1). Paired with [agent-toolkit v0.8.1](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.1).

### What shipped

Adds the external-review-handoff option to the harness's `/plan` phase. After the agent drafts `.harness/PLAN.md` (per the existing flow), the operator now has an alternative to inline iteration: hand off the drafted PLAN.md to Antigravity IDE for inline-comment review + Gemini-applies-comments revision, then resume in Claude Code with a diff-on-resume pass against a pre-handoff snapshot.

The harness writes a pre-handoff snapshot at `.harness/PLAN.pre-handoff-<ts>.md`, generates a transfer-context file at `.harness/transfer/plan-<ts>.md` (using the toolkit-side template at `agent-toolkit/skills/design/templates/transfer-context.md` — `DOC_TYPE: plan` triggers harness-PLAN.md-specific guardrails), outputs a handoff prompt with explicit Antigravity steps, and pauses the phase. On resume (`/plan --resume-external-review` or natural "plan review complete"), the harness diffs the revised PLAN.md against the snapshot, reads Gemini's change-summary log at `.harness/PLAN.diff.md`, surfaces findings, asks Accept / Iterate / Discard.

### Why this shape

Dogfood-driven amendment from plan #6's first real design exercise (MemoryVault, 2026-05-15). The 6-chunk inline walk of a ~7200-word design surfaced a real UX gap: Claude Code's block-by-block review pattern works but tires fast on long content. Antigravity IDE has a native inline-comment UI + Gemini AI integration that handles bulk-apply of comments dramatically better.

The harness `/plan` phase faces the same pattern when drafting plans for substantial scope (the MemoryVault-style plans with many tasks + thick locked-design-calls sections). Mirroring the option from toolkit `/design` skill v0.8.1 means operators have the same workflow available in both surfaces with the same mechanics — shared transfer-context template, shared workflow shape, shared cleanup discipline. One mental model, two skill surfaces.

The split between toolkit (template + design-skill option) and harness (`/plan` option) follows the customization-vs-phase pattern from agentic-harness ADR 0006: customizations live in toolkit; phase-gated workflow lives in harness; the two integrate via shared template + shared workflow contract.

### Doesn't do

- Doesn't change adapter wrappers (canonical-reference inheritance — adapters point at phase specs).
- Doesn't require any script changes, manifest changes, or installer changes. Implementation lives entirely in phase spec documentation; the agent executes the documented flow.
- Doesn't change the inline-review path. The inline iteration path stays default; external-review is opt-in per session.
- Doesn't ship cleanup automation for `.harness/transfer/` — that's manual until MemoryVault's idle-time hook (plan #7a) lands, at which point the same 30-day GC pass handles transfer artifacts alongside crash-recovery markers.

### Tracked as

This patch ships a single new option in the `/plan` phase; not flagged in `features.json` (features.json tracks net-new user-visible features; this is an alternative path for an existing feature).

### Related

- [agent-toolkit v0.8.1](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.1) — paired release; ships the same option in `/design author` + `/design translate` + the transfer-context template
- [agent-toolkit ADR 0004 amendment (2026-05-16)](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0004-design-skill.md#amendment--2026-05-16-v081-external-review-handoff-option) — shared design rationale + 4 load-bearing assumptions with re-audit triggers
- [v2.3.1](https://github.com/alexherrero/agentic-harness/releases/tag/v2.3.1) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)

## 2026-05-15 — v2.3.0: `/release` + `/setup` integration for agent-toolkit's `/design` skill

**Commit range:** `v2.2.0..v2.3.0` (2 commits on `main`). Release notes: [v2.3.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.3.0). Paired with [agent-toolkit v0.8.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.0).

**What shipped:**

- **`/release` §1b "Design-doc lifecycle check (agent-toolkit)"** — new section between §1 (Verify plan completion) and §2 (Re-run gates). Three cases: (A) silent no-op when plan isn't design-sourced; (B) archive completed plan + promote next queued plan + halt /release when more parts remain in the design; (C) archive + transition parent design Status `final → launched` + update `wiki/Home.md` + `_Sidebar.md` to surface launched published designs + continue release flow when this was the LAST queued part. Graceful-skip silent no-op when no design-doc origin signal present.
- **`/setup` §7 (Populate the wiki scaffold)** extended with `wiki/explanation/designs/` landing dir bullet — cross-refs the agent-toolkit `/design` skill how-to + the §1b /release lifecycle.
- **`templates/wiki/explanation/designs/`** — NEW scaffold dir installed by `install.sh`'s per-file walk. Contents: `.gitkeep` + `README.md` (one-paragraph explanation of visibility routing rules, Status lifecycle, wiki surfacing trigger, toolkit dependency).
- **`scripts/check-references.py`** — `EXTERNAL_CUSTOMIZATIONS` extended with `design` entry (currently forward-compatibility documentation because phase specs use slash-command phrasing `` `/design` `` which doesn't match `INVOKE_SKILL_RE` — inline comment captures this honestly).
- **`/work` Step 11 summary template enhancement** for ROADMAP-driven multi-plan projects — opt-in via the `.harness/ROADMAP.md` signal. Adds roadmap context lead-in + ✅/⬜ chart + link block to `.harness/` state files + explicit handoff phrase. Universal applicability — any harness install with a roadmap benefits.

**Why it shipped this shape:**

The harness needed integration hooks for the agent-toolkit `/design` skill's per-part PLAN.md workflow. The toolkit-side skill writes PLAN.md files to `.harness/` (active + queued); without the harness lifecycle hook, operators would have to manually promote queued plans after each part's `Status: done` completion. v2.3.0's §1b automates that promotion + handles the design `final → launched` transition + wiki surfacing for published designs.

The split between toolkit (skill body) and harness (lifecycle integration) follows the customization-vs-phase pattern from agentic-harness ADR 0006: customizations live in toolkit; phase-gated workflow lives in harness; the two integrate via well-defined hand-off points. v2.3.0 ships the harness-side hand-off; v0.8.0 of the toolkit ships the skill that writes to it.

The `/work` Step 11 enhancement came out of the dev-flow codification work (separate from plan #6 but shipping in the same release window). It's the universal good — opt-in via `.harness/ROADMAP.md` signal so single-plan installs stay minimal; multi-plan projects get the navigation aids that match their scale.

**What it doesn't do:**

- Doesn't ship the `/design` skill itself. That lives in [agent-toolkit v0.8.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.0).
- Doesn't auto-detect orphan queued plans without `parent_design_doc:` frontmatter. The §1b detection signal is the frontmatter field; hand-authored plans without it skip §1b entirely.
- Doesn't surface confidential designs in the wiki. The visibility check is hard — `confidential` designs at `.harness/designs/` never appear in `wiki/Home.md` even at `launched` Status.
- Doesn't add a new harness ADR. The design decision lives in [agent-toolkit ADR 0004](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0004-design-skill.md) since the customization itself lives there.

**Tracked as:**

- Task 5 of plan #6 (design skill v1) in `.harness/PLAN.md`. Plan #6 is a 7-task project spanning both repos: tasks 1-4 + 6 in `agent-toolkit` (template + 3 sub-command bodies + docs); task 5 in harness (this release); task 7 is the coordinated release pair.
- [v2.3.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.3.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)
- Paired release: [agent-toolkit v0.8.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.0)

**Related pages:**

- [agent-toolkit ADR 0004 — Design skill design](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0004-design-skill.md)
- [agent-toolkit how-to: Use-The-Design-Skill](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/how-to/Use-The-Design-Skill.md) — three worked scenarios end-to-end
- [agent-toolkit /design skill spec](https://github.com/alexherrero/agent-toolkit/blob/main/skills/design/SKILL.md)
- [agent-toolkit 10-section design-doc template](https://github.com/alexherrero/agent-toolkit/blob/main/skills/design/templates/design-doc.md)

## 2026-05-14 — v2.2.0: `/work` + `/release` augmentable with agent-toolkit's base hooks

**Commit range:** `v2.1.0..v2.2.0` (1 commit on `main`). Release notes: [v2.2.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.2.0). Paired with [agent-toolkit v0.7.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.7.0).

**What shipped:**

- **New section in `/work` phase spec** (`harness/phases/03-work.md`): "Long-running `/work` — operator-control hooks (agent-toolkit)". 20-line section between "When to invoke /review" and "Failure modes to avoid". Reference table for all three hooks ([kill-switch](https://github.com/alexherrero/agent-toolkit/blob/main/hooks/kill-switch/hook.md) / [steer](https://github.com/alexherrero/agent-toolkit/blob/main/hooks/steer/hook.md) / [commit-on-stop](https://github.com/alexherrero/agent-toolkit/blob/main/hooks/commit-on-stop/hook.md)); when-they-earn-their-keep framing (runaway loop / mid-task redirect / crashed session); alphabetical-ordering invariant (kill-switch fires before steer in PreToolUse); graceful-skip framing.
- **New section in `/release` phase spec** (`harness/phases/05-release.md`): "Optional: `commit-on-stop` safety net (agent-toolkit)". Shorter 4-line section focused on commit-on-stop as the backstop for interrupted release flows (mid-CHANGELOG-edit, mid-tag-prep); cross-refs the `/work` section for the full hook lineup.
- **`scripts/check-references.py` `EXTERNAL_CUSTOMIZATIONS` extended** with `kill-switch`, `steer`, `commit-on-stop`. Inline-commented as forward-compatibility documentation — the existing regexes don't currently match hook phrasings.
- **No adapter edits.** All six `/work` + `/release` wrappers (3 hosts × 2 phases) reference the canonical specs at `harness/phases/0{3,5}-*.md` exactly once; the new sections inherit via the existing canonical-reference pattern.

**Why it shipped this shape:**

The three base hooks lifted from cwc-long-running-agents give the operator precision that didn't exist before:

- **Runaway loop**: today, the only way to halt is closing the session. `touch .harness/STOP` is precise — the next `PreToolUse` blocks the tool call without ending the session.
- **Mid-run redirect**: today, the only way to redirect is interrupt-and-restart. Writing `.harness/STEER.md` injects the redirect into the next tool call's context; file is renamed to `.harness/STEER.consumed-<ts>.md` for audit trail.
- **Crash recovery**: today, a crashed session loses uncommitted work. `commit-on-stop` fires on Claude Code's `Stop` event (turn-end) and saves dirty trees to `auto-save/<ts>` branches automatically. Recovery via `git checkout`.

`/work` is the primary consumer because long-running iteration loops, mid-task redirects, and crashed sessions all happen there. `/release` benefits less (the release flow is typically short) but the commit-on-stop backstop reduces the cost of an interrupted CHANGELOG edit or tag prep.

Splitting the hooks into agent-toolkit (not bolting them onto the harness) keeps the harness phase-shaped: the harness owns the phase workflow + canonical sub-agents + setup-specific skills; agent-toolkit owns customizations that ride on top. Anyone (harness user or not) can install the toolkit on top to get the precision layer. Graceful-skip framing on both new sections means a harness without the toolkit installed still satisfies the phase contracts.

**What it doesn't do:**

- Doesn't auto-dispatch the hooks from any phase. The toolkit's installer registers them in Claude Code's settings.json; once installed, they fire on every relevant event. Phase specs document the convention; the firing is host-level, not phase-level.
- Doesn't add a new harness ADR. The design decisions (per-repo file location, audit-trail rename, safety-branch not current-branch, Stop-event-only for v0.7.0, alphabetical ordering, claude-code-only scope, Python helper for settings merge) live in [agent-toolkit ADR 0003](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0003-base-operator-hooks.md) since the customizations live there.
- Doesn't require the toolkit. Without `agent-toolkit` installed, both new sections graceful-skip silently and the existing flows continue to satisfy the phase contracts.
- Doesn't ship hooks for Antigravity or Gemini CLI. Both lack first-class hook surfaces today. Manual equivalents (always-on rules / operator prompts that check the trigger files between steps) are documented in agent-toolkit's how-to but no scripts ship for them.

**Tracked as:**

- Task 3 of plan #4 (base operator-control hooks) in `.harness/PLAN.md`. Plan #4 is a 5-task project spanning both repos: tasks 1, 2, 4 in `agent-toolkit` (installer + bodies + docs); task 3 in harness (this release); task 5 is the paired release.
- [v2.2.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.2.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)
- Paired release: [agent-toolkit v0.7.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.7.0).

**Related pages:**

- [agent-toolkit ADR 0003 — base operator-control hooks](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0003-base-operator-hooks.md)
- [agent-toolkit how-to: Use-The-Base-Hooks](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/how-to/Use-The-Base-Hooks.md)
- [kill-switch hook spec](https://github.com/alexherrero/agent-toolkit/blob/main/hooks/kill-switch/hook.md)
- [steer hook spec](https://github.com/alexherrero/agent-toolkit/blob/main/hooks/steer/hook.md)
- [commit-on-stop hook spec](https://github.com/alexherrero/agent-toolkit/blob/main/hooks/commit-on-stop/hook.md)

## 2026-05-13 — v2.1.0: `/review` augmentable with agent-toolkit's `evaluator`

**Commit range:** `v2.0.0..v2.1.0` (1 commit on `main`). Release notes: [v2.1.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.1.0). Paired with [agent-toolkit v0.6.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.6.0).

**What shipped:**

- **New §3b in `/review` phase spec** (`harness/phases/04-review.md`) — "Optional: evaluator augmentation (agent-toolkit)". Documents how to dispatch the [`evaluator`](https://github.com/alexherrero/agent-toolkit/blob/main/agents/evaluator.md) sub-agent alongside the existing `adversarial-reviewer` flow. Covers when to add evaluator dispatch (PLAN.md Verification clause is a numbered list of falsifiable claims), when to skip (vague rubric or toolkit not installed — graceful-skip), the `ARTIFACT:` + `RUBRIC:` dispatch prompt shape, the `PASS` / `NEEDS_WORK` output shape with per-rubric-item PASS/FAIL + final Verdict, treat-as-finding semantics (NEEDS_WORK counts as the executable exit artifact the phase requires), and a comparison table laying out the complementary framings.
- **Cross-repo agent references resolve** (`scripts/check-references.py`). Renamed `EXTERNAL_SKILLS` → `EXTERNAL_CUSTOMIZATIONS`; added `evaluator` to the set; the exclusion now applies to both `DISPATCH_AGENT_RE` and `INVOKE_SKILL_RE` regexes. Inline comments name each entry's `agent-toolkit` home (`skills/dependabot-fixer/`, `skills/ship-release/`, `agents/evaluator.md`).
- **No adapter edits.** All three review adapter wrappers (claude-code/commands, antigravity/workflows, gemini/commands) already reference `harness/phases/04-review.md` exactly once, so §3b inherits via the existing canonical-reference pattern without per-adapter changes.

**Why it shipped this shape:**

The adversarial-reviewer has framed `/review` since v0.8.0: "the code under review likely contains bugs, find them." That framing works well for defect surfacing but doesn't give a binary verdict against an explicit rubric. When the `PLAN.md` task's Verification clause is precise — a numbered list of falsifiable claims, which is the typical shape — the natural verification is "did the diff satisfy claims 1–5?" not "are there bugs?" The evaluator adds that binary-judgment surface without disturbing the existing flow: it coexists, not replaces. Consumers needing precise grading dispatch the evaluator; consumers needing defect surfacing dispatch the adversarial-reviewer; both useful at the same time.

Splitting the evaluator into agent-toolkit (rather than adding `harness/agents/evaluator.md`) keeps the harness phase-shaped: the harness owns the phase workflow + canonical sub-agents needed by every adapter; agent-toolkit owns customizations that ride on top. Anyone (harness user or not) can dispatch the evaluator from any consumer context. The future design skill (#6), quality-gates bundle (#10), and ContextVault (#7) all consume the evaluator for per-step grading.

**What it doesn't do:**

- Doesn't replace `adversarial-reviewer`. Both shipped; `/review` documents the choice.
- Doesn't auto-dispatch the evaluator from `/review`. Explicit dispatch stays the caller's call; the §3b section is a documented option, not a default flow change.
- Doesn't require the toolkit. Without `agent-toolkit` installed, §3b graceful-skips silently and the adversarial-reviewer-only flow continues to satisfy the phase contract.
- Doesn't add a new harness ADR. The design decision (read-only allowlist, caller-supplied inline rubric, coexist not replace, structured output) lives in [agent-toolkit ADR 0002](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0002-evaluator-design.md) since the customization itself lives there.

**Tracked as:**

- Task 3 of plan #3 (fresh-context evaluator) in `.harness/PLAN.md`. Plan #3 is a 5-task project spanning both repos: tasks 1, 2, 4 land in `agent-toolkit` (installer + body + docs); task 3 is the harness-side wiring (this release); task 5 is the coordinated release pair (this release + agent-toolkit v0.6.0).
- [v2.1.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.1.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)
- Paired release: [agent-toolkit v0.6.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.6.0).

**Related pages:**

- [agent-toolkit ADR 0002 — evaluator design](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0002-evaluator-design.md)
- [agent-toolkit how-to: Use-The-Evaluator](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/how-to/Use-The-Evaluator.md)
- [agent-toolkit agent spec: evaluator.md](https://github.com/alexherrero/agent-toolkit/blob/main/agents/evaluator.md)

## 2026-05-12 — v2.0.0: `agent-toolkit` repo split; `dependabot-fixer` + `ship-release` migrated

**Commit range:** `v1.0.0..v2.0.0` (9 commits on `main`). Release notes: [v2.0.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.0.0). ADR: [0006 — agent-toolkit split](0006-agent-toolkit-split). Paired with [agent-toolkit v0.5.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.5.0).

**What shipped:**

- **Sibling repo split.** The new [`agent-toolkit`](https://github.com/alexherrero/agent-toolkit) repo holds personal agent customizations (skills, sub-agents, hooks, MCP servers, slash commands, bundles, etc. — 11 primitive types) that ride on top of the harness's phase-gated workflow. `dependabot-fixer` and `ship-release` migrated from this repo to the toolkit (both were host-cross-cutting with no harness shape). `doctor` and `migrate-to-diataxis` stay in this repo — `doctor` is harness-setup-specific and `migrate-to-diataxis` is harness-shaped (encodes [ADR 0004](0004-diataxis-documentation-spec)).
- **Shared `lib/install/` byte-identical across repos.** Extracted ~80 lines of inline install primitives from `install.sh` + `install.ps1` into a new shared lib with 6 bash + 8 pwsh functions plus a CONTRACT.md documenting the caller contract (`UPDATE_MODE`/`$Update` flag + `BOUNDARY_ROOTS`/`$BoundaryRoots` array + six behavior invariants). SHA-256 manifest committed in both repos. Cross-repo updates flow through `scripts/sync-lib.sh` (canonical → sibling); `scripts/check-lib-parity.sh` asserts self-consistency in CI on every push. Cross-platform byte-identity work surfaced four real Mac + Windows bugs (locale-dependent `sort` collation, `$host` collision in PowerShell, missing `shasum` in Git Bash, autocrlf + binary-mode SHA-256 differences) — all fixed before v2.0.0 tag.
- **PII + lib-parity CI gates in this repo.** New `pii-guardrails` job in all three per-OS workflows runs `scripts/check-no-pii.sh` (regex scanner, byte-copied from the toolkit) and the official `gitleaks/gitleaks-action@v2`. New `lib-parity` job runs `scripts/check-lib-parity.sh`. Both repos now share the same PII detection surface, with the toolkit's pre-push hook layer + agent-facing `pii-scrubber` skill providing the additional remediation layers on that side.
- **Graceful-skip framing for migrated skills.** `harness/phases/05-release.md`'s ship-release suggestion and `harness/phases/03-work.md`'s feature-flip suggestion both note "install agent-toolkit to enable; otherwise cut release manually with `gh release create`". `harness/skills/doctor.md` probes 3 + 5 skip cleanly if the skills aren't installed. The harness still works on its own for the full phase-gated workflow; only the two migrated skills require the sibling install.

**Why it shipped this shape:**

The triggering observation, captured in [ADR 0006](0006-agent-toolkit-split), was that the **parity tax scales linearly with personal customizations**: every skill we'd want to add (kill-switch, fresh-context evaluator, ContextVault, a design skill, `pii-scrubber`) would need a canonical spec under `harness/skills/` + adapter copies under each of three adapter trees + parity script updates + reference checks. Past two or three skills, the tax becomes the dominant work. The harness also has a clear identity (phase-gated workflow + on-disk state + adversarial review primitive + Diátaxis convention) — mixing personal-customization scope into that identity blurs both. Splitting the repo lets the harness stay tight (six phases + canonical sub-agents + setup-specific skills) while the toolkit takes the open-ended customization scope with its own conventions (manifest schema + per-host paths dispatch + 11-primitive scope).

The byte-identical `lib/install/` is the load-bearing piece. Both repos need essentially the same install plumbing (boundary guard + per-file copy modes + true-sync wipe-and-recreate for `--update`); copying the code with drift would create silent divergence the moment one repo fixes a Windows bug the other doesn't. Byte-identity + CI gate makes drift impossible.

The cross-platform debugging work was a nice forcing function. Three of the four bugs surfaced in the Mac + Windows CI workflows on the toolkit side before any user hit them — exactly the kind of friction the three-OS matrix is meant to catch early. Each fix landed in both repos with parallel commits cross-referencing the sibling's SHA.

**What it doesn't do:**

- Doesn't ship a CLI sugar layer to invoke both tools as `harness` and `agent-toolkit` on PATH. That requires `dev-machine-setup` changes and lands in a future plan.
- Doesn't pre-install the toolkit alongside the harness. Users opt in by cloning `agent-toolkit` as a sibling directory and running its installer separately. Graceful-skip framing keeps the harness functional without it.
- Doesn't migrate `doctor` or `migrate-to-diataxis`. Both are harness-shaped; moving them would be a category error.
- Doesn't redesign `migrate-to-diataxis` into a general-purpose Diátaxis authoring skill. That's tracked as a separate roadmap item.

**Tracked as:**

- 7-task plan in `.harness/PLAN.md` (agent-toolkit-split) — all tasks `[x]`, Status `done`.
- [v2.0.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.0.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)
- Paired release: [agent-toolkit v0.5.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.5.0).

**Related pages:**

- [ADR 0006 — agent-toolkit split](0006-agent-toolkit-split)
- [agent-toolkit ADR 0001 — agent-toolkit purpose](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0001-agent-toolkit-purpose.md)
- [Repo-Layout](Repo-Layout) — sibling repo + `lib/install/` rows in Quick Reference

## 2026-05-11 — v1.0.0: Three-adapter scope; Codex dropped; 1.0.0 commitment

**Commit range:** `v0.9.0..v1.0.0` (13 commits on `main`). Release notes: [v1.0.0](https://github.com/alexherrero/agentic-harness/releases/tag/v1.0.0). ADR: [0005 — Drop Codex support](0005-drop-codex-support).

**What shipped:**

- **Codex adapter removed.** Supported hosts narrow from four to three: Claude Code, Antigravity, Gemini CLI. `adapters/codex/` (15 files), `harness/agents/codex-adapter-research.md` (294 lines), and codex-specific code across five scripts all gone. Repo-public surfaces (README, AGENTS.md, adapter READMEs, 9 wiki pages) scrubbed of Codex mentions. Historical entries in CHANGELOG.md and past-release sections of this page deliberately preserved as history.
- **True-sync `--update` semantics.** Beyond removing the codex install code, `install.sh` and `install.ps1` now wipe twelve fully-harness-authored subdirs before recreating from source on `--update`. Orphan paths from prior versions (e.g. `.codex/` from v0.9.0 installs) are automatically removed and reported as `removed legacy <path>/`. User state files at `.harness/` root, merged `settings.json` files, `wiki/**`, and root `AGENTS.md`/`CLAUDE.md` are deliberately preserved. The mechanism generalizes: any future host or skill removal also auto-cleans without per-removal patches.
- **v1.0.0 commitment.** Semver becomes firm going forward: major = breaking, minor = additive, patch = fixes. The harness's pre-1.0 churn period closes; future breaking changes (e.g. dropping Antigravity, restructuring adapters) become explicit major-version events, and the planned roadmap (agent-toolkit repo split, ContextVault, design skill, base-skill primitives, evidence-tracking) becomes clear minor bumps.

**Why it shipped this shape:**

The Codex adapter had been paying real ongoing costs without offsetting workflow value: Codex's built-in `/plan` and `/review` collisions forced a `harness-` prefix on every Codex phase-command (the only adapter with that divergence); the codex install block was a side-channel for delivering Gemini's shared skills (`.agents/skills/`), which made the codex removal load-bearing for two hosts; and the personal-dev-env scope had narrowed to Claude Code + Antigravity + Gemini CLI. Combined with the harness's maturity, this was the right moment to graduate to v1.0.0 — the host-scope reduction is a breaking change anyway, and a firm-semver 1.0 floor better communicates the stability commitment than another 0.x point release.

The `--update` true-sync amendment is worth noting separately. The user surfaced it during task 2 closeout: "removing the codex install code stops new installs from creating codex paths, but `--update` on an existing v0.9.0 install leaves orphan files." Rather than ship a codex-specific cleanup patch, the fix was generalized: declare `MANAGED_PARENTS` (12 subdirs that are fully harness-authored), wipe them on `--update`, then run the normal install flow which recreates from source. Codex becomes the first user of a mechanism that handles all future removals.

**What it doesn't do:**

- Anyone running agentic-harness through Codex has no harness adapter post-v1.0.0. The phase-gated workflow is host-agnostic, but they must migrate to one of the three remaining adapters.
- Codex's built-in `/plan` and `/review` semantics will not be harness-aware in a Codex session against a harness-installed project.
- `cross-review.sh` previously listed `codex` as an option for true cross-vendor review; that fallback drops to `claude` only.

**Tracked as:**

- 5-task plan in `.harness/PLAN.md` (Codex-removal sweep) — all tasks `[x]`, plus a task-2 amendment for the true-sync `--update` semantics.
- [v1.0.0](https://github.com/alexherrero/agentic-harness/releases/tag/v1.0.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)

**Related pages:**

- [ADR 0005 — Drop Codex support; three-adapter scope](0005-drop-codex-support)
- [Update-Installed-Harness](Update-Installed-Harness) — v1.0.0+ sync semantics section
- [Installer-CLI](Installer-CLI) — `--update` flag description updated
- [Repo-Layout](Repo-Layout) — three-adapter table

## 2026-04-23 — v0.9.0: Diátaxis documentation spec + `/doctor` skill

**Commit range:** `v0.8.7..v0.9.0` (10 commits on `main`). Release notes: [v0.9.0](https://github.com/alexherrero/agentic-harness/releases/tag/v0.9.0).

**What shipped:**

- **Diátaxis four-mode wiki convention end-to-end.** [ADR 0004](0004-diataxis-documentation-spec) supersedes ADR 0002's audience-based layout (`wiki/{development,operational,design,architecture}/`) with the four Diátaxis modes — `tutorials/`, `how-to/`, `reference/`, `explanation/` (with `explanation/decisions/` for ADRs). The rollout landed as a 7-task plan: (1) `scripts/check-wiki.py` shipped as a structural lint with 11 rules (a–k); (2) `templates/wiki/` reshaped so new installs land directly in the four-mode layout; (3) this repo's own `wiki/` migrated file-by-file with `git mv` to preserve blame; (4) the `documenter` sub-agent rewired to write to mode-specific targets per phase; (5) a new `migrate-to-diataxis` skill for one-shot conversion of already-installed projects; (6) [`check-wiki.py`](CI-Gates) flipped from warn-only to `--strict` in CI; (7) harness phase specs retargeted to the new mode dirs.
- **`/doctor` skill** — companion to `telemetry.sh` for post-install correctness. Default mode runs structural discovery only: verifies expected phase commands, sub-agents, skills, state files, and hooks are present and parseable in the detected adapter (<5s, no tokens). `--live` adds six real probes — `explorer` dispatch on a trivial prompt, `adversarial-reviewer` dispatch requiring an executable artifact (not prose), `ship-release --dry-run`, `migrate-to-diataxis` preview on an already-migrated tree, `dependabot-fixer` no-match path, and a hook synthetic trigger. Never mutates repo state. Canonical spec at [`harness/skills/doctor.md`](https://github.com/alexherrero/agentic-harness/blob/main/harness/skills/doctor.md); adapter wrappers for claude-code, antigravity, and codex (Gemini reuses the Codex delivery).
- **`CONTRIBUTING.md` extracted** from the README (which dropped 126 → 64 lines). CI matrix, invariant list, and local-gate command set now live at their natural home.

**Why it shipped this shape:**

Diátaxis gives readers a clear mental model before they open a page — "learn / do / look up / understand" is more durable than "dev / ops / design / architecture" (which conflates audience with intent, and blurs when a page serves multiple audiences). The migration skill exists because ADR changes that break installed projects are a tax the harness should pay, not the user. `/doctor` exists because file-presence smoke tests answer "is it there" but not "does it work", and several install regressions over the past quarter could have been caught pre-use by a cheap structural + live-probe check.

**First dogfood of `/doctor`** caught a real spec bug: the initial skill required a `name:` frontmatter field on every surface, but Claude Code phase commands, Antigravity workflows, and Gemini TOML commands intentionally have no `name:` field (name is implicit from filename). Spec and all three adapter wrappers were corrected inside the same release ([d078485](https://github.com/alexherrero/agentic-harness/commit/d078485)). Live probes (`explorer` and `adversarial-reviewer`) both matched their pass criteria — adversarial returned a `file:line` pointer plus a failing pytest body in 10.8s rather than prose.

**What it doesn't do:**

- No auto-install on top of a half-installed tree — `/doctor` reports gaps and points at `install.sh`; auto-repair would mask misconfiguration.
- No CI coverage for `/doctor` itself — the skill dispatches sub-agents, which requires an LLM session and can't run in headless CI. `scripts/check-parity.sh` and `check-references.py` catch the structural facets that don't need an agent.
- No additional Diátaxis modes. Five-mode extensions (glossary, changelog) were explicitly rejected in `harness/documentation.md` §Non-goals — glossaries live under `reference/`, changelogs under `reference/Completed-Features.md`.

**Tracked as:**

- v0.9.0 — [release notes](https://github.com/alexherrero/agentic-harness/releases/tag/v0.9.0), [CHANGELOG.md](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md)
- 7-task rollout plan in `.harness/PLAN.md` — Status: done

**Related pages:**

- [ADR 0004 — Diátaxis documentation spec](0004-diataxis-documentation-spec)
- [CI Gates reference](CI-Gates) — now includes the `check-wiki` row
- `harness/skills/doctor.md`, `harness/skills/migrate-to-diataxis.md` (canonical specs; not mirrored to the wiki because they target harness contributors, not installed-project users)

## 2026-04-21 — v0.8.7: GitHub Projects wiring + documenter end-to-end dogfood

**Commit range:** `801dbd7^..HEAD` (4 commits, all on `main`).

**What shipped:**

- **GitHub Projects surface across all four phases.** `/plan`, `/work`, `/review`, and `/release` each now offer to file deferred-work items to a user- or org-owned Project linked to the repo, preview-and-ask at every `gh` call, graceful-skip when `.harness/project.json` is absent. Propagated to all four adapters (claude-code, antigravity, codex, gemini). See [GitHub-Projects-Integration](GitHub-Projects-Integration).
- **ProjectsV2 ownership-and-linking decision.** The dogfood run surfaced that ProjectsV2 has no repo-owned form; `/setup` now runs a two-step `gh project create` + `gh project link --repo` flow to make the project visible under the repo. Rationale in [ADR 0003](0003-ProjectsV2-Ownership-And-Linking).
- **Dropped the "1 proposal per session" cap.** Replaced with a quality-bar-plus-batching rule (single preview at phase end, per-phase soft caps as reminders not hard limits). Avoids silent misses when a session genuinely surfaces multiple deferrals.
- **README refresh to v0.8.2.** `/setup` scope; not documenter's edit — main agent's. Removed stale "v0.1" string, added `ship-release` skill, added `documenter` sub-agent, added wiki-sync pointer.

**Why it shipped this shape:**

The `/bugfix` Issues lifecycle shipped in v0.8.2; this plan closed the symmetric half — the "defer this for later" flow that parallels "fix this now". The heuristic for detecting deferrals is intentionally soft (LLMs will miss or over-propose); mitigation is the mandatory preview-and-ask plus the per-phase batching rule. A noisy proposal the user declines is cheap; a silent miss is recoverable.

**What it doesn't do:**

- Task 3 part B ("observe an offer-accept or offer-decline cycle in a real phase session") is still gated. The `feat-gh-projects-integration.passes` flag stays `false` until a future phase session exercises the wiring on a real deferral.
- No auto-detection of org vs user ownership; the user picks interactively at `/setup`.
- Classic (pre-ProjectsV2) projects aren't supported.

**Tracked as:**

- [`feat-gh-projects-integration`](https://github.com/alexherrero/agentic-harness/blob/main/.harness/features.json) (currently `passes: false` — gated on dogfood observation)
- [`feat-documenter-subagent`](https://github.com/alexherrero/agentic-harness/blob/main/.harness/features.json) (this sweep is the dogfood of the spec)

**Related pages:**

- [GitHub-Projects-Integration](GitHub-Projects-Integration) — feature design + implementation.
- [ADR 0003: ProjectsV2 ownership and linking](0003-ProjectsV2-Ownership-And-Linking) — the two-call create-plus-link decision.
- [Cut-A-Release](Cut-A-Release) — the procedure that invokes `ship-release`.
