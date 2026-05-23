# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.6.0] — 2026-05-23 — Evidence-tracking for /work (paired with toolkit v0.12.0)

Minor — second non-doc-only paired pair in the recent run (after v2.5.0). Harness ships the **`/work` §5b spec amendment** documenting the contract for the new `evidence-tracker` base hook in [`agent-toolkit v0.12.0`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.12.0). Default-FAIL evidence enforcement: every PLAN.md task starts with `evidence-met=false`; the agent must demonstrably READ relevant spec/test/evidence files before a `Write`/`Edit` that flips `[ ]` → `[x]` is allowed. Hook blocks otherwise.

**What changes for operators**:
- With `agent-toolkit` installed + the `evidence-tracker` hook in place: `/work` task closeouts gain a deterministic verification gate. Hook fires PreToolUse on `Read|Write|Edit`; records reads; blocks unmet-evidence flips with a helpful stderr message + 3 recovery paths.
- Without those prerequisites: **zero behavior change**. Hook absent → no enforcement → `/work` runs as it always has.

Triggered by [ROADMAP item #9](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md). Decision rationale + 3 locked design calls Q1-Q3 + 4 load-bearing assumptions in the toolkit-side [ADR 0009 — evidence-tracker hook](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0009-evidence-tracker-hook.md). Operator-facing how-to at [Use The Evidence-Tracker Hook](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/how-to/Use-The-Evidence-Tracker-Hook.md).

### Added

- **`harness/phases/03-work.md` §5b** "Evidence-tracking (graceful-skip if not installed)" inserted between §5 Run-deterministic-gates + §6 Iterate-on-failures (~75 lines). Documents: hook trigger table; 3 task-body conventions (default heuristic / `**Evidence:** <pattern>` override / `**Evidence:** none — <rationale>` opt-out); 3 recovery paths on block; 5 graceful-skip conditions.
- **`templates/PLAN.md`** task-block template gains optional `**Evidence:**` field hint cross-referencing §5b. Operators creating new plans see the hook surface in the template.
- **`wiki/reference/Completed-Features.md`** v2.6.0 row.

### Changed

- **4 wiki line-range anchors updated** in `wiki/explanation/GitHub-Projects-Integration.md` for the line-position shift from §5b insertion (`03-work.md#L194-L216 → #L241-L263`). Sub-letter pattern (§5b) preserves integer §-numbering — incoming wiki ref to §10 keeps citing "§10"; only line-range moves.

### Internal

- **1 commit on this side** (`2027bec`) + this v2.6.0 release commit. Toolkit-side ships substantive in 7 commits (`8c6419f` + `6e875d5` + `e6f4411` + `83fb3e7` + `a3100ab` + `4569c20` + `8793237` + `ecd8d6c` + `dfc802b`).
- **3 design calls locked at /plan time** (Q1-Q3 per ADR 0009 toolkit-side): hybrid evidence resolver / per-task PLAN.md flip gate only / explicit opt-out.
- **Sub-letter spec amendment pattern continues** — same §-numbering preservation as plan #8's §1b/§4c/§7b/§7c/§5b/§5c amendments. Wiki refs that cite "§N" stay valid across plan #9.
- **Self-hosting note**: this harness repo doesn't itself have the evidence-tracker hook installed; the spec describes the contract for projects that DO install it. First real-world dogfood happens in the next `/work` session in a project with both repos installed.
- **Paired-release ordering**: toolkit v0.12.0 tagged first; this release URL-links to it per `[[coordinated-release-order]]`.

## [v2.5.0] — 2026-05-22 — Auto-context into harness phases (paired with toolkit v0.11.1)

Minor — **first non-doc-only paired pair** in the recent run (after v2.4.0/v2.4.1/v2.4.2/v2.4.3 all doc-only on this side). Harness ships real new phase behavior: every phase command (`/setup`, `/plan`, `/work`, `/review`, `/release`, `/bugfix`) now auto-invokes MemoryVault at predictable boundaries without the agent or operator having to remember to call `/memory search` or `/memory save`. Paired with [`agent-toolkit v0.11.1`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.11.1) which ships the toolkit-side companion documentation (`Cross-Repo-Memory-Protocol.md`).

**What changes for operators**:
- With `MEMORY_VAULT_PATH` set + `agent-toolkit/skills/memory/` sibling-cloned: every phase auto-loads operator conventions + project-specific decisions + open-questions / known-issues (per phase) at its start; phases that surface durable items offer to save them at the end (self-modulating ask — high-confidence saves silently with stderr notice; low-confidence prompts).
- Without those prerequisites: **zero behavior change**. Every phase graceful-skips silently. Harness runs unchanged on systems where MemoryVault isn't adopted.

Triggered by [ROADMAP item #8](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md). Decision rationale + 5 locked design calls (Q1–Q5) + 4 load-bearing assumptions in new [ADR 0007 — Auto-context into harness phases](wiki/explanation/decisions/0007-auto-context-into-harness-phases.md). Operator-facing how-to at [Use Auto-Context In Harness Phases](wiki/how-to/Use-Auto-Context-In-Harness-Phases.md).

### Added

- **`scripts/harness_memory.py`** (~520 lines, stdlib-only) — dispatcher with 4 sub-commands:
  - `recall --phase <P> --project <S>` — phase-scoped recall (loads `_always-load/` conventions + per-phase `personal-projects/<slug>/` subdirs per `_PHASE_PROJECT_DIRS` mapping); per-phase token cap via `HARNESS_RECALL_BUDGET_<PHASE>` env.
  - `offer-save --phase --project --kind --slug --content-file [--confidence] [--confidence-reason]` — self-modulating ask: `confidence ≥ HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD` (default 0.8) silent-saves with `[auto-saved high-confidence]` stderr; below threshold prompts. `HARNESS_AUTO_SAVE_MODE` (ask/silent/off) outer envelope.
  - `plan-done-promotion --project-root . [--dry-run]` — cursor-tracked progress.md tail-scan via `.harness/.promoted-progress-cursor`. Shared between `/work` plan-done + `/release` triggers — single fire per plan-window.
  - `available` — exit 0/1 short-circuit for phase specs.
  - **3-tier toolkit discovery** (`HARNESS_MEMORY_TOOLKIT_PATH` env > sibling-clone > `~/Antigravity/agent-toolkit/`). Toolkit-absent path graceful-skips with stderr notice.
- **`scripts/vault_project.py`** (~200 lines, stdlib-only) — `read_vault_project()` with 3-tier fallback (explicit field > `github.repo` basename > git origin); `write_vault_project()` atomic merge-preserving. CLI wrapper.
- **33 unit tests** for `harness_memory.py` (`scripts/test_harness_memory.py`) across 7 classes — `TestAvailable` / `TestRecall` / `TestOfferSaveDecision` (pure logic) / `TestOfferSaveBehavior` (end-to-end with toolkit stub) / `TestOfferSaveToolkitAbsent` / `TestPlanDonePromotion` / `TestCLI`.
- **28 unit tests** for `vault_project.py` (`scripts/test_vault_project.py`) across 4 classes — read tier-1/2/3/none paths, atomic write merge-preserve + round-trip, URL-shape variety (https/ssh/file/no-.git), CLI exit codes.
- **CI step** added to all 3 OS workflows — `python3 scripts/test_vault_project.py` + `python3 scripts/test_harness_memory.py`. 61 total new test cases.
- **New [ADR 0007 — Auto-context into harness phases](wiki/explanation/decisions/0007-auto-context-into-harness-phases.md)** — full Status/Context/Decision-Q1-Q5/Consequences/Related shape matching toolkit ADRs 0007/0008. 5 design calls + 4 load-bearing assumptions with re-audit triggers.
- **New [how-to/Use-Auto-Context-In-Harness-Phases](wiki/how-to/Use-Auto-Context-In-Harness-Phases.md)** — per-phase boundary table + dispatcher CLI reference + 5-env-var matrix + 3 worked scenarios (offer-save fatigue tuning / recall budget tight / plan-done-promotion cursor confirmation) + 8 troubleshooting table rows. Length-justified inline.
- **`wiki/reference/Completed-Features.md`** v2.5.0 row.
- **Home.md + _Sidebar.md** references to new how-to + ADR.

### Changed

- **All 6 canonical phase/pipeline specs amended** via sub-letter convention (preserves integer §-numbers — incoming wiki refs that cite "§N" stay valid):
  - `harness/phases/01-setup.md` — new §1b (Auto-recall conventions + vault_project write) + §8b (Project index stub offer-save).
  - `harness/phases/02-plan.md` — new §1b (Auto-recall decisions + open-questions) + §4c (Open-questions offer-save).
  - `harness/phases/03-work.md` — new §1b (Auto-recall task-relevant decisions + known-issues) + §7b (Remember-this offer-save, 3-kind taxonomy decision/gotcha/workflow) + §7c (Plan-done-promotion on final task flip).
  - `harness/phases/04-review.md` — new §2b (Recall-only conventions — read-only by design; "a reviewer that writes biases toward confirming its own findings").
  - `harness/phases/05-release.md` — new §1c (Auto-recall decisions for changelog framing) + §5b (Decisions offer-save after CHANGELOG draft) + §5c (Progress.md tail-scan via plan-done-promotion — shared cursor with `/work` §7c).
  - `harness/pipelines/bugfix.md` — new §2b (Auto-recall known-issues at Analyze) + §4b (Gotcha offer-save at Verify; cap at 1 per /bugfix; ADR write operator-controlled not auto).
- **4 wiki line-range anchors updated** in `wiki/explanation/GitHub-Projects-Integration.md` + `wiki/explanation/decisions/0003-ProjectsV2-Ownership-And-Linking.md` for the line-position shifts (sub-letter pattern keeps §-numbers stable; only line ranges move).

### Internal

- **10 commits across plan #8** on this side, then this v2.5.0 release commit: `9ccc020` + `34d21e9` (vault_project helper + PII scrub) + `17b2061` (harness_memory dispatcher) + `aebf189` (01-setup) + `44d0ba1` (02-plan) + `b3f653b` (03-work) + `3a9fb32` (04-review) + `ddfb5c0` (05-release) + `d134c5a` (bugfix.md) + `132a42e` (docs pass: ADR 0007 + how-to + Completed-Features + 11 ADR-number fixes). Toolkit side: 1 commit (`9176bc2` Cross-Repo-Memory-Protocol.md + Home.md ref).
- **5 design calls locked at /plan time** (Q1–Q5 per ADR 0007): per-phase budget envs / vault_project 3-tier auto-detect / silent graceful-skip / self-modulating ask with confidence threshold / dual-trigger cursor-tracked promotion.
- **Operator clarifications mid-plan**: Q4 revised from flat "ask" default to **self-modulating ask** (agent confidence threshold gates silent-vs-prompt); Q5 revised from "release-only trigger" to **dual-trigger middle ground** (plan-done AND release; shared cursor). Both revisions baked into dispatcher implementation + spec amendments + ADR 0007.
- **ADR-number correction**: PLAN.md task 9 said ADR 0009 (conflated with toolkit numbering); harness ADRs go 0001–0006 so the new ADR landed as 0007. 11 inbound refs across the 6 amended spec files updated in task 9's docs commit.
- **PII scanner false-positive caught mid-plan** (task 1 commit `9ccc020`): canonical SSH-form URL + a personal-path-shaped file:// URL in test fixtures flagged as PII; scrubbed to `example.com` + neutral `/srv/git/` placeholders in `34d21e9`. (Same pattern hit again on this release commit `76d17a6` when I described the scrub in this entry — fixed forward via the next commit.)

## [v2.4.3] — 2026-05-22 — diataxis-author skill (paired with toolkit v0.11.0)

Patch — paired-doc-only release pairing with [`agent-toolkit v0.11.0`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.11.0). Substantive change ships entirely on the toolkit side: new `diataxis-author` skill with 5 sub-commands (`/diataxis author` + `check` + `repair` + `migrate` + `classify`) covering the full Diátaxis-wiki lifecycle. Subsumes harness's `migrate-to-diataxis` predecessor (deprecated 2026-05-22 in commit `d4d4adf`; predecessor file removal in a follow-up harness PATCH after dogfood). **4th consecutive paired-release-as-documentation pair** (after v2.4.0/v2.4.1/v2.4.2).

Harness-side changes for this release pair:

1. **`harness/skills/migrate-to-diataxis.md`** gains NOTE-WARNING deprecation block (shipped in commit `d4d4adf` 2026-05-22 alongside toolkit Part 4 push). Predecessor file stays through v1 dogfood; full removal lands in a follow-up harness PATCH release.
2. **No phase-spec or adapter changes** — the toolkit's `/diataxis` sub-commands are operator-invokable; harness `/release` documenter dispatch remains unchanged. Future harness PATCH could amend `/release` to call `/diataxis check` when the skill is installed (graceful-skip otherwise); deferred from v1 to keep change surface narrow.
3. **CHANGELOG + Completed-Features.md row** documenting the paired release.

Triggered by [ROADMAP item #13](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md). Implemented as plan #13 (5 parts: scaffold + author-classify + check-repair + migrate-subsume + AgentMemory-docs-release). Decision rationale + 4 locked design calls + 4 load-bearing assumptions in [toolkit-side ADR 0008](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0008-diataxis-author.md). Parent design at [diataxis-author](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/designs/diataxis-author.md) (Status: launched as of this release).

**Why this matters for harness users**: operators with the agent-toolkit installed gain five new `/diataxis` sub-commands on next install. Drift detection (`/diataxis check`) becomes a regular auditing tool alongside `check-wiki.py --strict`. The `migrate-to-diataxis` predecessor still works through v1 dogfood for operators with existing installs, but new migrations should use `/diataxis migrate` for the additional capabilities (per-repo `.diataxis-conventions.md` auto-seed + delegation to `/diataxis repair` for mode-mixed splits + AgentMemory convention sync).

### Added

- **CHANGELOG.md v2.4.3 entry** + **Completed-Features.md row** for the paired release.

### Changed

- **`harness/skills/migrate-to-diataxis.md`** — NOTE-WARNING deprecation block + redirect to `/diataxis migrate` (committed `d4d4adf` 2026-05-22).

### Internal

- **Plan #13 close-out**: 5/5 parts shipped across 8 toolkit commits + 1 harness commit. Plan archived to `.harness/PLAN.archive.20260522-diataxis-author-part-5.md` (sibling archives for parts 1-4). ROADMAP item #13 moves to Completed.
- **Second real dogfood of `/design` skill** (after MemoryVault parent design closed 2026-05-20 + 2026-05-22). Parent design transitions `final → launched` automatically per `/design` lifecycle.
- **3 Windows-specific CI failures caught + fixed mid-plan** per `[[wake-on-ci-pattern]]`: Start-Process multi-word arg split (Part 2 `caf3c5a`); `git mv` cwd dependence + cp1252 stdout encoding crash on `→` arrow (Part 4 `c5b32fd` + `79cf283`). Pattern locked: cross-platform Python scripts must defensively configure encoding + line endings + invocation patterns. Same family of bugs as Part 4 of plan #18 (CRLF line endings).

## [v2.4.2] — 2026-05-22 — MemoryVault Discovery + Mining (paired with toolkit v0.10.0)

Patch — second MemoryVault roadmap item closes. Paired with [`agent-toolkit v0.10.0`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.10.0) which ships the substantive feature set: five new `/memory` sub-commands (`/memory index-skills` + `/memory reflect corpus` + `/memory discover-skills` + `/memory adapt-skills` + `/memory watchlist`) that turn the vault from a static curated store into a living surface.

Harness-side changes for this release pair are **doc-only** per the paired-release-as-documentation pattern established in v2.4.0 + v2.4.1. The harness hasn't owned customizations since the v2.0.0 split; discovery + mining lives entirely on the toolkit side. The harness's role in plan #7b is closing out the active plan + moving ROADMAP item #7b to Completed.

Triggered by [ROADMAP item #7b](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md) which had been queued from plan #7a's design-skill output (plan #6 dogfood). Implemented as plan #7b (7 tasks across 8 toolkit commits). Decision rationale + 7 locked design calls live in [toolkit-side ADR 0007 — MemoryVault Discovery + Mining](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0007-memoryvault-discovery.md) — no new harness-side ADR (discovery + mining is a toolkit-side concern; harness inherits via its toolkit-customization dependency).

**Why this matters for harness users**: the harness itself is unchanged. Operators who installed the memory skill via the toolkit gain five new `/memory` sub-commands on next install. The personal-skills indexer auto-runs from `bash agent-toolkit/install.sh ~/their-project` (against the toolkit's own `skills/` + sibling `agentic-harness/.claude/skills/`); the cadence-checked skill-discovery scan auto-fires from the existing `memory-reflect-idle` hook (no operator action required); the adapt-don't-import workflow + watchlist review are operator-invoked when ready. **Adapt-don't-import is architecturally enforced** — the `adapt-evaluator` sub-agent's write allowlist physically prevents auto-fork into `agent-toolkit/skills/`; the operator's manual authoring step is the only path to a real skill.

After this release pair ships, ROADMAP item #7b moves to Completed; the next ROADMAP item per the locked execution order is **#13 diataxis-author skill** (with smaller items #19-#22 queued in parallel: Ideas.md format redesign, transfer-context × AgentMemory integration, harness self-audit skill, cross-surface AgentMemory protocol).

### Added

- **`.harness/PLAN.archive.20260522-memoryvault-discovery-mining.md`** — archived plan #7b PLAN.md with 7/7 tasks `[x]` + full per-task narrative.
- **Completed-Features.md row** for plan #7b — overview entry + dated section narrative.

### Changed

- **`.harness/ROADMAP.md`** — item #7b moved from active table to the Completed section with full narrative (releases shipped + what shipped end-to-end + notable patterns established + deferred items).
- **`.harness/PLAN.md`** — promoted from queued-plans/ to active for the next roadmap item per the established `/release` lifecycle hook pattern.

### Internal

- **Plan close-out pattern**: plan #7b followed the same shape as plan #7a (5 substantive code tasks + 2 batch tasks for docs + release); pre-authorized autonomous-batch execution mode for tasks 5+6+7 (decided during task 1 close-out) saved 2 approval cycles for ~30% of remaining scope.
- **Per-task narratives** captured in `.harness/progress.md` between 2026-05-21 and 2026-05-22, one entry per task close-out + one end-of-plan summary.

## [v2.4.1] — 2026-05-20 — Local-only embeddings (paired with toolkit v0.9.2)

Patch — embedding-mode collapse paired with [`agent-toolkit v0.9.2`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.9.2). **Drops the Voyage/Anthropic API embedding mode from the toolkit's memory skill; local `sentence-transformers` is now the only production mode.** Default model upgraded `all-MiniLM-L6-v2` → `BAAI/bge-large-en-v1.5` (1024-d native; ~1.3GB on disk + ~1.5GB RAM at runtime; PyTorch MPS on Apple Silicon for acceleration).

Harness-side changes for this release pair are **doc-only** per the paired-release-as-documentation pattern established in v2.4.0. The harness hasn't owned customizations since the v2.0.0 split (when `dependabot-fixer` + `ship-release` migrated to `agent-toolkit`); the embedding-mode refactor happens entirely on the toolkit side. The harness's role in plan #18 is acknowledging the v0.9.2 toolkit shape in its docs + framing the paired release.

Triggered by [ROADMAP item #18](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md) (added 2026-05-20 mid-flight of plan #7a part 5 / seed-pass; task 6 of seed-pass needed a worthwhile embedding model for sample-recall validation, which forced the embed-refactor work first). Implemented as plan #18 (7 tasks; this release pair is task 7). Decision rationale lives in [toolkit-side ADR 0001's 2026-05-20 amendment](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0001-agent-toolkit-purpose.md#amendment-2026-05-20) — no new harness-side ADR (the embedding-mode decision is a toolkit-side concern; harness inherits via its dependency on toolkit customizations).

**Why this matters for harness users**: the harness itself is unchanged. Operators who installed the memory skill via the toolkit see the embedding-mode change on next install (`bash agent-toolkit/install.sh ~/their-project` runs the new `install_python_deps()` step by default; `--no-python-deps` opts out). Existing 384-d vec-indexes invalidate due to the dim bump 384 → 1024 — the toolkit's new `vec_index.py rebuild` subcommand handles migration with a graceful-skip + clear stderr message on first invocation that detects the dim mismatch.

After this release pair ships, plan #7a part 5 (seed-pass) resumes at task 6 (validate via sample recalls) using the new BGE-large model. Plan-#18-driven detour is complete; the MemoryVault Core roadmap (#7a) resumes its sequential execution.

### Added

- **`wiki/reference/Completed-Features.md`** v2.4.1 overview row + full narrative section (What shipped / Why this shape / Doesn't do / Tracked as / Related — mirrors v2.4.0 format).

### Changed

- Adapter wrappers (`.claude/commands/*.md` + Antigravity adapter equivalents) untouched — canonical-reference inheritance: adapters point at `harness/phases/` specs which are themselves untouched in this release.
- No changes to harness phase specs (no embedding-related logic in the harness; embedding is wholly a toolkit-side concern via the memory skill).

### Internal

- **Paired-release-as-documentation pattern (continued from v2.4.0)**: this is the second consecutive paired release where the substantive change is toolkit-side and the harness ships doc-only. The pattern keeps version cadences readable for operators tracking changes across both repos — they don't have to wonder "why did toolkit ship a MINOR but harness didn't?"
- **First post-#18 install on harness side**: operators who run `bash agent-toolkit/install.sh ~/their-project` after this release pair will see the new `==> python deps` install step. Operators can opt out via `--no-python-deps` if they manage Python deps via virtualenv / conda / system packages, or accept the install (sentence-transformers + transitive deps total ~1.5GB+ on first pull; BGE-large model downloads lazily ~1.3GB on first `/memory save` or `embed.py --mode local`).
- **Plan #18 was inserted mid-flight of plan #7a part 5** (seed-pass) — first time a plan was inserted into the queue mid-execution rather than queued at the end. The mechanism: archive the active PLAN.md to `.harness/PLAN.paused.YYYYMMDD-<slug>.md`, write the new plan as the active PLAN.md, execute it, then restore the paused plan as the new active PLAN.md after the inserted plan completes. This pattern is captured in plan #18's "How to resume" section + this CHANGELOG entry as precedent for future mid-flight insertions.

## [v2.4.0] — 2026-05-17 — Gemini-CLI host removal (paired with toolkit v0.9.0)

Minor — host-scope reduction paired with [`agent-toolkit v0.9.0`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.9.0). **Drops standalone Gemini CLI as a supported host** across the personal-dev-env. Keeps Claude Code + Antigravity (Gemini-in-Antigravity is a different surface — IDE-level integration, not standalone CLI).

Harness-side changes for this release pair are **doc-only**. The harness hasn't owned customizations since the v2.0.0 split (when `dependabot-fixer` + `ship-release` migrated to `agent-toolkit`); the customization sweep happens entirely on the toolkit side. The harness's role in plan #15 is acknowledging the host-scope reduction in its docs + framing the paired release.

Triggered by [ROADMAP item #15](https://github.com/alexherrero/agentic-harness/blob/main/.harness/ROADMAP.md) (added 2026-05-16 during plan #7a part 1 task 1 ship). Implemented as plan #15 (7 tasks; this release pair is task 7). Decision rationale lives in [toolkit-side ADR 0006](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0006-gemini-cli-host-removal.md) — no new harness-side ADR (the host-scope decision is a toolkit-side concern; harness inherits via its dependency on toolkit customizations).

### Added

- **`wiki/reference/Completed-Features.md`** v2.4.0 overview row + full narrative section (What shipped / Why this shape / Doesn't do / Tracked as / Related — mirrors v2.3.x format).

### Changed

- Adapter wrappers (`.claude/commands/*.md` + Antigravity adapter equivalents) untouched — canonical-reference inheritance: adapters point at `harness/phases/` specs which are themselves untouched in this release.
- No changes to harness phase specs (no host-related conditionals to update — the harness phases don't reference specific hosts by name; host scope is decided by toolkit-side manifests).

### Internal

- **Paired-release-as-documentation pattern**: when the substantive change lives entirely on one side of the toolkit/harness split, the other side still ships a paired release with framing-only content. This keeps the two repos' version cadences readable for operators tracking changes — they don't have to wonder "why did toolkit ship a MINOR but harness didn't?". v2.4.0 is the documentation-acknowledgement counterpart to v0.9.0.
- **First post-#15 install on harness side**: operators who run `bash agent-toolkit/install.sh` against an agentic-harness install will see the legacy-cleanup prompt fire if `.agents/skills/` exists from a prior install. The harness's `--update` path (separate from toolkit's `--update`) is unaffected — harness doesn't manage `.agents/`.

## [v2.3.1] — 2026-05-16 — `/plan` external-review-handoff option (paired with toolkit v0.8.1)

Patch — additive only, no breaking changes. Adds an **external-review-handoff option** to the harness's `/plan` phase, mirroring the option added to `agent-toolkit`'s `/design` skill in [v0.8.1](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.1). Operators can now hand off a drafted `.harness/PLAN.md` to Antigravity IDE for inline-comment review + Gemini-applies-comments revision, then resume in Claude Code with a diff-on-resume pass against a pre-handoff snapshot.

Dogfood-driven amendment from plan #6's first real design exercise (MemoryVault): the inline block-by-block walk pattern works but tires fast on long content. Antigravity's native inline-comment UI + Gemini-applies-comments pattern is dramatically better for review-style work; the new option lets operators reach for that workflow on long plans without leaving the harness.

Paired with [`agent-toolkit v0.8.1`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.1), which adds the same option to `/design author` Step 5 + Step 6 + `/design translate` Step 4. Shared template (`agent-toolkit/skills/design/templates/transfer-context.md`), shared workflow shape, shared cleanup discipline across both repos.

### Mechanics

When the operator picks "Hand off for external review" after the agent drafts `.harness/PLAN.md`:

1. **Pre-handoff snapshot** at `.harness/PLAN.pre-handoff-<YYYYMMDDhhmmss>.md` — full copy used on resume to diff against revised version.
2. **Transfer-context file** at `.harness/transfer/plan-<YYYYMMDDhhmmss>.md` — uses the toolkit-side template; `DOC_TYPE: plan` triggers plan-specific guardrails (harness PLAN.md shape per `templates/PLAN.md`; Status lifecycle `draft → in-progress → done` don't-transition; paragraph-long Status:[x] narratives required; Locked design calls section at the bottom is load-bearing). Inlines dev-flow conventions (Antigravity won't see device-global `~/.claude/CLAUDE.md`) + operator intent extracted from brief + Goal sections + recent decisions extracted from the plan's `## Locked design calls` section + most recent `.harness/progress.md` entries.
3. **Handoff prompt** output with explicit Antigravity steps: open `.harness/PLAN.md` + transfer-context file, add inline comments via Antigravity's native UI, ask Gemini to apply per the transfer-context. Gemini revises + writes change-summary log at `.harness/PLAN.diff.md`.
4. **Resume flow** (`/plan --resume-external-review` or natural "plan review complete"): harness reads revised PLAN.md + change-summary log, diffs against pre-handoff snapshot, surfaces findings (task list changes / verification spec changes / `## Locked design calls` modifications / Gemini's adjacent-issue suggestions), asks Accept / Iterate / Discard. Accept archives snapshot + transfer-context to `.harness/transfer/_archive/`; Iterate regenerates for another round; Discard restores from snapshot.

### Added

- **`harness/phases/02-plan.md` §4b "External-review handoff (optional, alternative to inline iteration — v2.3.1+)"** — new section between §4 (Write PLAN.md) and §5 (Update features.json) documenting the when-to-offer trigger, the pre-handoff snapshot write, the transfer-context generation (with `DOC_TYPE: plan` guardrails), the handoff prompt output, and the resume flow with diff-on-resume + Accept/Iterate/Discard. Cross-references the toolkit-side ADR 0004 amendment as the design rationale (shared design across both repos).

### Changed

- Adapter wrappers (`.claude/commands/plan.md` + Antigravity adapter) untouched — canonical-reference inheritance: adapters point at `harness/phases/02-plan.md` and pick up the new §4b automatically.

### Internal

- First cross-repo dogfood-driven amendment shipped as a coordinated patch pair. Pattern: ship v1 of both repos, dogfood on a real exercise (MemoryVault `/design author`), surface gaps that apply to both surfaces (the `/design` skill on toolkit + the `/plan` phase on harness), ship paired patches with the amendment captured in the toolkit-side ADR (one ADR for both since the design is shared).
- Implementation lives entirely in phase spec documentation. No script changes, no adapter changes, no template changes (the harness reuses the toolkit-side `transfer-context.md` template). Harness-side install validates toolkit presence; graceful-skip warning if toolkit is not installed (operator gets a message: "External-review handoff requires `agent-toolkit` v0.8.1+ installed alongside; toolkit not detected — inline review only").
- Re-audit triggers in the toolkit-side ADR 0004 amendment fire after the next 3-5 real external-review handoffs on either skill point — surfaces apply to both repos.

## [v2.3.0] — 2026-05-15 — `/release` + `/setup` integration for agent-toolkit's `/design` skill (additive)

Additive minor — no breaking changes. Two harness extensions that integrate with the new [`agent-toolkit v0.8.0`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.0) `/design` skill: a `/release` lifecycle hook that auto-promotes queued plans + transitions design Status `final → launched` + surfaces launched designs in the wiki; and a `/setup` scaffolding extension for the `wiki/explanation/designs/` landing dir. Plus a small `/work` Step 11 summary template enhancement that applies to any harness install with a ROADMAP-driven multi-plan project.

Paired with [`agent-toolkit v0.8.0`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.8.0), which ships the `/design` skill itself with three sub-commands (`author` / `translate` / `sequence`). The harness extensions in this release light up the integration points the toolkit-side skill writes to:

- `/design sequence` writes a first PLAN.md to `<project>/.harness/PLAN.md` + queues subsequent parts at `<project>/.harness/designs/<doc-slug>/queued-plans/<part-slug>.PLAN.md`. **v2.3.0's `/release` §1b consumes that queue** — auto-promoting the next plan when the active completes, or transitioning the parent design Status when the last part ships.
- `/design author --visibility published` routes design docs to `wiki/explanation/designs/<slug>.md`. **v2.3.0's `/setup` §7 extension scaffolds the `wiki/explanation/designs/` landing dir** so target projects have the destination ready before first design.

Without `agent-toolkit` installed alongside, both extensions silent-skip — the harness still works standalone exactly as it did in v2.2.0.

### Added

- **`harness/phases/05-release.md` §1b "Design-doc lifecycle check (agent-toolkit)"** — new section between §1 (Verify plan completion) and §2 (Re-run gates). Three cases handled:
  - **Case A — not design-sourced**: silent no-op; existing `/release` flow continues unchanged.
  - **Case B — design-sourced, more queued plans exist**: archive completed plan to `.harness/PLAN.archive.YYYYMMDD-<part-slug>.md`; promote next queued plan (alphabetical order — same deterministic ordering `/design sequence` uses) to `.harness/PLAN.md`; append parent design's Document History with the promotion entry; **halt /release** with operator-facing next-step message. No release to prepare yet — just a plan promotion.
  - **Case C — design-sourced, LAST queued plan**: archive completed plan; transition parent design Status `final → launched`; append Document History with launched-state entry; **if `visibility: published`** update `wiki/Home.md` + `wiki/_Sidebar.md` to surface the design in a "Designs" section (idempotent — re-runs are no-op); continue with §2-§9 — this IS a real release.
  - **Graceful-skip**: silent no-op when no design-doc origin signal present (`agent-toolkit` not installed, or plan was hand-authored).
- **`harness/phases/01-setup.md` §7 (Populate the wiki scaffold) extended** with a new bullet for `wiki/explanation/designs/` landing dir. Cross-refs the `agent-toolkit` `/design` skill how-to + the §1b `/release` lifecycle that transitions designs to launched.
- **`templates/wiki/explanation/designs/`** — NEW scaffold dir installed by `install.sh`'s per-file walk into target projects. Contents: `.gitkeep` (keeps dir tracked in git) + `README.md` (one-paragraph explanation of visibility routing rules, the Status lifecycle, the wiki surfacing trigger, and the toolkit dependency).
- **`scripts/check-references.py` `EXTERNAL_CUSTOMIZATIONS` extended** with `design` entry. Inline comment captures the current state honestly: phase specs use slash-command phrasing "the `/design` skill" with leading slash, which keeps it from matching `INVOKE_SKILL_RE` (regex char class `[A-Za-z0-9_-]` excludes `/`), so this exclusion is forward-compatibility documentation rather than currently load-bearing. If phase spec phrasing ever shifts to bare "`design`", the exclusion becomes load-bearing.
- **`/work` Step 11 summary template enhanced for ROADMAP-driven projects** (`harness/phases/03-work.md`). Opt-in via the `.harness/ROADMAP.md` signal — single-plan installs keep the existing minimal `≤5-bullet summary`; multi-plan projects get the richer template (roadmap context lead-in, ✅/⬜ chart, link block to `.harness/` state files, explicit handoff phrase, optional commit SHA / CI status / design calls detail). Applies to any harness install with a roadmap; not specific to the design skill.

### Changed

- **Adapter wrappers untouched.** All six `/release` + `/setup` adapter wrappers (claude-code/commands, antigravity/workflows, gemini/commands) reference their canonical phase spec exactly once; the new §1b + extended §7 inherit via the existing canonical-reference pattern. Same pattern as plan #3 task 3 (evaluator integration in /review) and plan #4 task 3 (hooks integration in /work + /release).

### Internal

- **Task 5 of plan #6** (design skill v1) — the only task in plan #6 that touches the harness. Tasks 1-4 + 6 land in `agent-toolkit`; task 7 is this paired release.
- **Negative test on `EXTERNAL_CUSTOMIZATIONS`** during implementation confirmed the `design` entry is **not currently load-bearing** — phase spec phrasing uses `` `/design` `` (slash-command form) which doesn't match `INVOKE_SKILL_RE`'s char class. Updated the inline comment to reflect this honestly; entry stays as forward-compatibility documentation.
- **`/work` Step 11 enhancement source**: came out of the dev-flow codification work (commit `ce86977`, 2026-05-14), separate from plan #6 but shipping in the same release window. Universal applicability — any harness install with a `ROADMAP.md` benefits.
- **All 8 harness gates green** on every commit in the `v2.2.0..v2.3.0` range.

[v2.3.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v2.3.0

## [v2.2.0] — 2026-05-14 — `/work` + `/release` augmentable with agent-toolkit's base hooks (additive)

Additive minor — no breaking changes. Two new optional sections in the harness phase specs document how to dispatch the three new base operator-control hooks (`kill-switch`, `steer`, `commit-on-stop`) shipped in [`agent-toolkit v0.7.0`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.7.0) alongside the existing phase workflow.

The three hooks are lifted from the cwc-long-running-agents pattern and give the operator precise control over long-running Claude Code sessions:

| Hook | Trigger | Effect |
|---|---|---|
| `kill-switch` | `PreToolUse` | `touch .harness/STOP` halts the next tool call; `rm` to resume |
| `steer` | `PreToolUse` | Write `.harness/STEER.md` for mid-run redirect (contents → agent context; file → `STEER.consumed-<ts>.md`) |
| `commit-on-stop` | `Stop` event | Dirty tree → `auto-save/<ts>` safety branch with commit; never modifies current branch; never pushes |

`/work` is the primary beneficiary — long-running iteration loops, mid-task redirects, and crashed sessions all become recoverable motions. `/release` benefits less from kill-switch + steer (release flows are typically short) but the `commit-on-stop` backstop reduces the cost of an interrupted release prep. Both new sections graceful-skip when `agent-toolkit` is absent; the phase contracts don't require the hooks.

Paired with [`agent-toolkit v0.7.0`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.7.0). The decision rationale for the hooks' design (per-repo file location, audit-trail rename for STEER, safety-branch not current-branch, Stop-event-only for v0.7.0, alphabetical-install-order hook ordering, claude-code-only host scope, Python helper for settings.json merge) lives in [agent-toolkit ADR 0003](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0003-base-operator-hooks.md). No new harness-side ADR — this release is the integration surface, not the design decision.

### Added

- **`/work` phase spec — new section "Long-running `/work` — operator-control hooks (agent-toolkit)"** (`harness/phases/03-work.md`). 20-line section between "When to invoke /review" and "Failure modes to avoid". Reference table for all three hooks (event + trigger + effect); when-they-earn-their-keep framing (runaway loop / mid-task redirect / crashed session); the alphabetical-ordering invariant (kill-switch fires before steer in PreToolUse — a halt always takes precedence over a steer); graceful-skip framing.
- **`/release` phase spec — new section "Optional: `commit-on-stop` safety net (agent-toolkit)"** (`harness/phases/05-release.md`). Shorter 4-line section between progress.md closeout and "Failure modes to avoid". Documents commit-on-stop as the safety net for interrupted release flows (mid-CHANGELOG-edit, mid-tag-prep); cross-references the `/work` section for the full hook lineup; notes kill-switch + steer provide less marginal value for typically-short release flows.
- **`scripts/check-references.py`** — `EXTERNAL_CUSTOMIZATIONS` set extended with `kill-switch`, `steer`, `commit-on-stop`. Inline-commented as forward-compatibility documentation: the existing `DISPATCH_AGENT_RE` and `INVOKE_SKILL_RE` regexes don't currently match the hooks' phase-spec phrasing (hooks fire from the host, not via agent dispatch — phase specs use markdown links + "the X hook" prose rather than `<name>` hook dispatch patterns), so the set entries don't trigger today; they're listed for the possibility of a future hook-reference regex.

### Changed

- **Adapter wrappers** (`adapters/claude-code/commands/{work,release}.md`, `adapters/antigravity/workflows/{work,release}.md`, `adapters/gemini/commands/{work,release}.toml`) — untouched. All six reference their respective canonical phase spec (`harness/phases/0{3,5}-*.md`) exactly once, so the new sections inherit via the existing canonical-reference pattern without per-adapter edits.

### Internal

- **Task 3 of plan #4** in `.harness/PLAN.md` (base operator-control hooks). Plan #4 is a 5-task project spanning both repos: tasks 1, 2, 4 land in `agent-toolkit` (installer + body + docs); task 3 is the harness-side wiring (this release); task 5 is the coordinated release pair (this release + agent-toolkit v0.7.0).
- **Design call deviation from plan**: did NOT add a new `INVOKE_HOOK_RE` regex. Hooks fire from the host, not via agent dispatch — there's no "the agent invokes a hook" semantics like there is for sub-agents/skills. Phase-spec phrasing uses markdown links + "the X hook" prose, neither of which matches a `<name>` hook dispatch pattern. EXTERNAL_CUSTOMIZATIONS entries for the three hook names are forward-compatibility documentation; future plans may add a hook-reference regex if needed.
- **Negative test confirmed** the exclusion isn't currently load-bearing: removing `kill-switch` from `EXTERNAL_CUSTOMIZATIONS` doesn't break `check-references` because no existing regex matches the phrasing. Acceptable shape; documented inline.

[v2.2.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v2.2.0

## [v2.1.0] — 2026-05-13 — `/review` augmentable with agent-toolkit's `evaluator` (additive)

Additive minor — no breaking changes. The `/review` phase spec gains a new optional **§3b "Optional: evaluator augmentation (agent-toolkit)"** documenting how to dispatch the [`evaluator`](https://github.com/alexherrero/agent-toolkit/blob/main/agents/evaluator.md) sub-agent (shipped in [agent-toolkit v0.6.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.6.0)) alongside the existing `adversarial-reviewer` flow.

The two reviewers are **complementary, not competing**:

| | `adversarial-reviewer` (§3) | `evaluator` (§3b, agent-toolkit) |
|---|---|---|
| **Framing** | "the code contains bugs, find them" | "did this satisfy the rubric?" |
| **Output** | failing test / `file:line` defect / `NO ISSUES FOUND` | `PASS` / `NEEDS_WORK` + per-rubric-item PASS/FAIL |
| **Input** | the artifact + PLAN.md task | the artifact + an explicit rubric |
| **Best when** | rubric is loose; you want defect surfacing | rubric is precise; you want binary judgment |

Both can run in the same `/review` session — their outputs combine into a richer finding set. The harness still works standalone without the toolkit installed: §3b graceful-skips when `agent-toolkit` is absent (no `.claude/agents/evaluator.md` / `.agent/skills/evaluator/SKILL.md` / `.gemini/agents/evaluator.md` in the project), and the adversarial-reviewer-only flow continues to satisfy the phase contract.

Paired with [`agent-toolkit v0.6.0`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.6.0). The decision rationale for the evaluator's design (read-only allowlist, caller-supplied inline rubric, coexist with adversarial-reviewer not replace) is captured in [agent-toolkit ADR 0002](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0002-evaluator-design.md). No new harness-side ADR — this release is the integration surface, not the design decision.

### Added

- **`/review` phase spec §3b — Optional: evaluator augmentation (agent-toolkit)** (`harness/phases/04-review.md`). 54-line section between §3a Reconcile and §4 Validate format. Documents:
  - The complementary framing with a side-by-side comparison table vs. adversarial-reviewer.
  - When to add evaluator dispatch (PLAN.md Verification clause is a numbered list of falsifiable claims).
  - When to skip (vague rubric, or `agent-toolkit` not installed in the project — graceful-skip silently).
  - The dispatch prompt shape (`ARTIFACT:` + `RUBRIC:` labeled sections drawn from the PLAN.md Verification clause).
  - The output shape (PASS/NEEDS_WORK header + per-rubric-item PASS/FAIL line with citations + final Verdict line).
  - Treat-as-finding semantics: if NEEDS_WORK, the structured output is the `/review` exit artifact (counts as the executable artifact the phase requires).
  - Full-spec pointer to `agent-toolkit/agents/evaluator.md`.
- **Cross-repo agent references resolve** (`scripts/check-references.py`). Renamed `EXTERNAL_SKILLS` → `EXTERNAL_CUSTOMIZATIONS` to cover the new agent kind alongside the existing migrated skills. The exclusion now applies to both `DISPATCH_AGENT_RE` and `INVOKE_SKILL_RE` regexes — previously only the skill regex had the exclusion. Inline comments name each entry's `agent-toolkit` home (`skills/dependabot-fixer/`, `skills/ship-release/`, `agents/evaluator.md`).

### Changed

- **Adapter wrappers** (`adapters/claude-code/commands/review.md`, `adapters/antigravity/workflows/review.md`, `adapters/gemini/commands/review.toml`) — untouched. All three already reference `harness/phases/04-review.md` exactly once, so §3b inherits via the existing canonical-reference pattern without per-adapter edits.

### Internal

- **Task 3 of plan #3** in `.harness/PLAN.md` (fresh-context evaluator). Plan #3 is a 5-task project spanning both repos: tasks 1, 2, 4 land in `agent-toolkit` (installer + body + docs); task 3 is the harness-side wiring (this release); task 5 is the coordinated release pair (this release + agent-toolkit v0.6.0).
- **Negative test confirmed**: removing `evaluator` from `EXTERNAL_CUSTOMIZATIONS` immediately produces `FAIL: harness/phases/04-review.md: references` evaluator `sub-agent but harness/agents/evaluator.md is missing` — the exclusion is load-bearing.

[v2.1.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v2.1.0

## [v2.0.0] — 2026-05-12 — `agent-toolkit` repo split: `dependabot-fixer` + `ship-release` moved out

**BREAKING:** The `dependabot-fixer` and `ship-release` skills have moved out of this repo into the new sibling repo [`agent-toolkit`](https://github.com/alexherrero/agent-toolkit). Anyone who relied on them being installed by `agentic-harness/install.sh` must additionally clone `agent-toolkit` as a sibling directory and run `bash ../agent-toolkit/install.sh <project>` to get those skills back. The harness itself still works on its own for the phase-gated workflow (setup / plan / work / review / release / bugfix); only the two migrated skills are affected.

**Migration:**

```bash
# Clone agent-toolkit as a sibling of agentic-harness:
gh repo clone alexherrero/agent-toolkit ../agent-toolkit

# Refresh harness state (auto-cleans orphaned dependabot-fixer + ship-release paths
# from the v1.x install via the true-sync --update mechanism shipped in v1.0.0):
bash /path/to/agentic-harness/install.sh --update /path/to/your-project

# Install the migrated skills into the same target:
bash ../agent-toolkit/install.sh /path/to/your-project
```

`doctor` and `migrate-to-diataxis` remain in this repo — they are harness-setup-specific and harness-shaped, not personal customizations. The harness's `/release` and `/work` phase specs already reference `ship-release` with graceful-skip framing ("install agent-toolkit to enable; otherwise cut release manually with `gh release create`"), so a v2.0.0 install without the toolkit still functions — it just falls back to manual release cuts.

Released alongside [`agent-toolkit v0.5.0`](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.5.0). Decision rationale captured in two parallel ADRs: [agentic-harness ADR 0006 — agent-toolkit split](https://github.com/alexherrero/agentic-harness/blob/main/wiki/explanation/decisions/0006-agent-toolkit-split.md) (this repo, parity-tax + harness-identity framing) and [agent-toolkit ADR 0001 — agent-toolkit purpose](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0001-agent-toolkit-purpose.md) (toolkit side, sibling-repo purpose + scope).

### Removed

- **`dependabot-fixer` skill** — canonical spec (`harness/skills/dependabot-fixer.md`) + adapter copies (`adapters/claude-code/skills/dependabot-fixer/`, `adapters/antigravity/skills/dependabot-fixer/`). Now lives at [`agent-toolkit/skills/dependabot-fixer/`](https://github.com/alexherrero/agent-toolkit/tree/main/skills/dependabot-fixer).
- **`ship-release` skill** — canonical spec (`harness/skills/ship-release.md`) + adapter copies (`adapters/claude-code/skills/ship-release/`, `adapters/antigravity/skills/ship-release/`). Now lives at [`agent-toolkit/skills/ship-release/`](https://github.com/alexherrero/agent-toolkit/tree/main/skills/ship-release).
- Combined removal: 6 files. `scripts/check-parity.sh` `CANON_SKILLS`, `scripts/check-references.py` `SHARED_SKILLS`, and `scripts/validate-adapters.py` `SKILLS` all narrow from 4 entries (`dependabot-fixer`, `doctor`, `migrate-to-diataxis`, `ship-release`) to 2 (`doctor`, `migrate-to-diataxis`). `install.sh` + `install.ps1` shared-skills enumeration trims from 4 to 2. Cross-platform smoke-install + check-integrity scripts updated for the same narrowing.

### Added

- **[ADR 0006 — agent-toolkit split](https://github.com/alexherrero/agentic-harness/blob/main/wiki/explanation/decisions/0006-agent-toolkit-split.md)** — captures Context (parity-tax scales linearly with personal customizations + harness identity at risk + 11-primitive scope is broader than skills), Decision (sibling repo + byte-identical `lib/install/` + skill-ownership table + public-with-PII-guardrails), Consequences (5 positive + 4 negative + load-bearing assumptions). Cross-references the toolkit's [ADR 0001](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/explanation/decisions/0001-agent-toolkit-purpose.md).
- **`lib/install/` shared install plumbing.** Extracted ~80 lines of inline install primitives from `install.sh` + `install.ps1` into a new shared lib byte-identical with `agent-toolkit/lib/install/`. Files: `lib/install/bash/primitives.sh` (6 functions: `ensure_boundary_src`, `cp_user`, `cp_managed`, `cp_user_walk`, `cp_managed_dir`, `sync_managed_parents`), `lib/install/pwsh/primitives.ps1` (8 functions; pwsh equivalents + `Copy-AdapterFiles` / `Copy-AdapterDirs`), `lib/install/CONTRACT.md` (caller-contract docs + six behavior invariants), `lib/install/.checksums.txt` (SHA-256 manifest). Both repos consume the same code path; cross-repo edits flow through `scripts/sync-lib.sh` (canonical → sibling). `scripts/check-lib-parity.sh` asserts self-consistency in CI on every push.
- **PII guardrails in CI.** Added `scripts/check-no-pii.sh` (regex scanner, byte-copied from agent-toolkit) and `.gitleaks.toml` to this repo. New `pii-guardrails` job in all three per-OS test workflows runs both `check-no-pii.sh` and the official `gitleaks/gitleaks-action@v2`. Defense in depth for personal-path / API-key / email leaks even in the harness repo, which has grown reference examples touching ADR 0006 + the toolkit cross-references.
- **`lib-parity` CI gate.** New job in all three per-OS workflows runs `scripts/check-lib-parity.sh` to assert the committed SHA-256 manifest matches the actual `lib/install/` contents.
- **Graceful-skip framing for migrated skills.** `harness/phases/05-release.md` (ship-release suggestion) and `harness/phases/03-work.md` (feature-flip suggestion) now note "install agent-toolkit to enable; otherwise cut release manually with `gh release create`". `harness/skills/doctor.md` probes 3 + 5 (ship-release + dependabot-fixer) gain explicit "skip if not installed" framing — structural skill check now expects only `doctor` + `migrate-to-diataxis`. `harness/telemetry.md` notes the dependabot-fixer signal lives in agent-toolkit as of v2.0.0.
- **`check-references.py` `EXTERNAL_SKILLS` set** — `{"dependabot-fixer", "ship-release"}` exclusion lets phase specs reference the migrated skills as graceful-skip suggestions without asserting `harness/skills/<name>.md` exists.
- **Cross-repo docs.** `README.md` Skills section restructured to clearly delineate the two harness-shipped skills (`doctor`, `migrate-to-diataxis`) from the two migrated skills with links to their new toolkit homes. `AGENTS.md` gains a "Personal customizations" section pointing at agent-toolkit with sibling-clones layout guidance. `wiki/Home.md`, `wiki/_Sidebar.md`, `wiki/reference/Repo-Layout.md` all gain agent-toolkit cross-references; Repo-Layout's Quick Reference table gains rows for the sibling repo and `lib/install/`.

### Changed

- **`install.sh` + `install.ps1`** consume the shared `lib/install/` primitives. Behavior is preserved exactly — same outputs, same idempotence, same `--update` true-sync semantics. The cross-platform debugging journey to make `lib/install/` byte-identity work on Mac + Linux + Windows surfaced four real cross-platform bugs (locale-dependent `sort` collation on Mac, `$host` collision in PowerShell, missing `shasum` in Git Bash on Windows, autocrlf + binary-mode SHA-256 difference) — all fixed before this release tag. Fixes also landed in `.gitattributes` (forces LF on every platform regardless of `core.autocrlf`).
- **Shared-skill delivery narrows from 4 to 2.** `.agents/skills/` (read by Gemini per the Agent Skills standard) now ships only `doctor` and `migrate-to-diataxis` — the two skills that remain harness-owned. Anyone who needs `dependabot-fixer` or `ship-release` installs agent-toolkit on top.

### Internal

- **7-task plan (#1) completed.** Tracked in `.harness/PLAN.md`: task 1 (`agent-toolkit` repo scaffold + PII guardrails), task 2 (shared `lib/install/` extraction + byte-identity gate), task 3 (real toolkit installer + manifest validator + per-host paths), task 4 (toolkit CI matrix + PII gate in both repos), task 5 (migrate the two skills from harness to toolkit), task 6 (full Diátaxis wiki in toolkit + cross-repo ADRs), task 7 (this release pair). Each task closed with `PLAN.md` mark `[x]` + a `progress.md` append entry.
- **End-to-end byte-identity flow exercised.** Nine commits between the two repos during the plan included parallel commits cross-referencing each other's SHA; the `sync-lib.sh` helper was used for every `lib/install/` edit; `check-lib-parity.sh` ran in CI on every push and gated the parity invariant successfully across all three OSes.
- **CI green across all three per-OS workflows** on every commit in the v1.0.0..v2.0.0 range after the cross-platform fixes landed.

[v2.0.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v2.0.0

## [v1.0.0] — 2026-05-11 — Three-adapter scope; Codex dropped; 1.0.0 commitment

**BREAKING:** Codex adapter removed. Supported hosts narrow from four (Claude Code, Antigravity, Codex, Gemini CLI) to three (Claude Code, Antigravity, Gemini CLI). Anyone running agentic-harness through Codex must migrate to one of the three remaining adapters — the phase-gated workflow itself is host-agnostic, so migration is install + relearn the host-specific invocation surface.

The version bump from 0.9.x to 1.0.0 reflects the breaking change *plus* a commitment: the harness has had enough churn (v0.1.0 → v0.9.0) to feel stable, and semver becomes firm going forward — major = breaking, minor = additive, patch = fixes. Future host removals, fundamental shape changes, or invariant inversions become explicit major-version events. Additive changes (new skills, the planned `agent-toolkit` repo split, ContextVault, design skill) become clear minor bumps. See [ADR 0005](https://github.com/alexherrero/agentic-harness/blob/main/wiki/explanation/decisions/0005-drop-codex-support.md) for the full decision narrative.

### Removed

- **Codex adapter** (`adapters/codex/`, 15 files: README, 4 sub-agents in TOML, 10 skill dirs — 7 `harness-` prefixed phase-commands-as-skills + 4 shared skills).
- **Codex adapter research note** (`harness/agents/codex-adapter-research.md`, 294-line deep-dive on Codex-specific design — dead weight once the adapter is gone).
- **Codex-specific code in scripts**: `scripts/check-parity.sh`'s `== codex ==` block + divergence comments; `scripts/check-references.py`'s `CODEX_PHASE_PREFIX` constant + codex branch in `expected_canonical_for`; `scripts/validate-adapters.py`'s `validate_codex_agents()` function + codex skills-dir entry; codex expected-files lines in `scripts/smoke-install-{bash,pwsh}` and `scripts/check-integrity-{bash,pwsh}`.

### Added

- **[ADR 0005 — Drop Codex support; three-adapter scope](https://github.com/alexherrero/agentic-harness/blob/main/wiki/explanation/decisions/0005-drop-codex-support.md)**: documents Context (5 reasons codex was dropped), Decision (7 concrete actions including the v1.0.0 framing), Consequences (5 positive + 4 negative), and load-bearing re-audit assumptions.
- **True-sync `--update` semantics.** `install.sh` and `install.ps1` now wipe twelve fully-harness-authored subdirs before recreating from source on `--update`. Orphan paths from previous versions (e.g. `.codex/` for users upgrading from v0.9.0) are automatically removed and reported as `removed legacy <path>/`. User state files at `.harness/` root, merged `settings.json` files, `wiki/**`, and root `AGENTS.md`/`CLAUDE.md` are deliberately preserved. The generalized mechanism means future host or skill removals also clean up automatically — codex is the first user, not a special case. Documented in [Update-Installed-Harness](https://github.com/alexherrero/agentic-harness/blob/main/wiki/how-to/Update-Installed-Harness.md).
- **`no-Co-Authored-By` convention** added to `AGENTS.md` + `CLAUDE.md`. Host-agnostic rule: agents do not append `Co-Authored-By:` trailers naming the model or host. Sole-author-of-intent framing.

### Changed

- **Adapter parity narrows to three hosts.** `check-parity.sh` enforces the new canonical set; no more `harness-` prefix divergence (was only needed for Codex's collision with built-in `/plan` and `/review`). Removing the prefix requirement simplifies the parity invariant.
- **Shared-skill delivery becomes explicit.** `.agents/skills/` (read by Gemini per the Agent Skills standard) was previously delivered by the Codex install block as a side effect. Now `install.sh` explicitly enumerates the four shared skills (`dependabot-fixer`, `doctor`, `migrate-to-diataxis`, `ship-release`) and sources them from `adapters/claude-code/skills/` (parity-enforced identical content; cleanest source — `antigravity/skills/` would over-deliver because it mixes sub-agents-as-skills).
- **Repo-public surfaces scrubbed of Codex mentions.** README "works with" chip row drops Codex (now 3 chips); intro paragraph drops Codex from host list; Mermaid Host node collapses to single-line three-host listing; AGENTS.md intro tool-list and Co-Authored-By convention examples drop Codex; `harness/skills/doctor.md` and `ship-release.md` adapter tables drop Codex; `adapters/gemini/README.md` reframes 5 Codex-block references to actual `install.sh`/Agent Skills standard delivery; `harness/agents/gemini-adapter-research.md` 10 codex-comparison phrasings reframed to current state.
- **Wiki updated to three-adapter shape.** 9 wiki pages scrubbed across `explanation/` (Product-Intent, GitHub-Projects-Integration, How-The-Pieces-Fit including 2 ASCII diagrams), `how-to/` (Cut-A-Release, Install-Into-Project, Update-Installed-Harness with new v1.0.0 sync semantics section), `tutorials/01-First-Install.md`, and `reference/` (Repo-Layout with rewritten three-adapters table + Quick Reference + tree diagram, Installer-CLI with v1.0.0 sync details). Home.md and _Sidebar.md gain ADR 0005 row in Decisions section. Historical entries in CHANGELOG.md and `wiki/reference/Completed-Features.md` past-release sections deliberately preserved as history.
- **README polish (multi-commit run).** Mirrored install commands across MacOS/Linux and Windows sections; eliminated redundant license callout; QoL updates (badges, "works with" chip row, Mermaid architecture diagram); intro paragraph framing tightened around production-quality engineering posture.

### Internal

- **5-task Codex-removal sweep plan completed.** Tracked in `.harness/PLAN.md`: task 1 (adapter dir + canonical specs + scripts cleanup with the `codex-adapter-research.md` deletion decision); task 2 (installers + smoke tests — flagged Gemini's shared-skill delivery as the load-bearing risk and resolved it by sourcing from `claude-code/skills/`); task 2 amendment (true-sync `--update` generalized beyond codex cleanup per user direction "github is SoT for my personal setup"); task 3 (README + CONTRIBUTING + AGENTS + CLAUDE + adapter READMEs scrub); task 4 (wiki + ADR 0005); task 5 (release).
- **Stats**: 26 files changed task 1 (25 ins, 1204 del). 2 files changed task 2 (25 ins, 19 del). 2 files changed task 2 amendment (96 ins). 4 files changed task 3 (12 ins, 13 del). 12 files changed task 4 (99 ins, 27 del). Net: ~46 files touched, ~1300 lines removed.
- **CI green across all three per-OS workflows** on every commit in the range. Smoke tests intentionally not run as a gate during task 1 (install.sh was inconsistent post-adapter-removal); reintroduced as a gate in task 2 where the installer fix landed.

[v1.0.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v1.0.0

## [v0.9.0] — 2026-04-23 — Diátaxis documentation spec + `/doctor` skill

Two substantial threads landed together. First, the 7-task Diátaxis rollout (ADR 0004): wiki scaffold and dogfood wiki reshaped to the four-mode layout (tutorials/how-to/reference/explanation), `documenter` rewired to write to the new mode dirs, `scripts/check-wiki.py` shipped as a structural gate and flipped to `--strict` in CI, and a `migrate-to-diataxis` skill for one-shot migration of already-installed projects. Second, a new user-invocable `/doctor` skill — companion to `telemetry.sh` — that verifies the harness install is correctly wired up in the host, with an opt-in `--live` mode that actually dispatches each sub-agent and dry-runs each skill to prove end-to-end wiring.

### Added

- **`/doctor` skill** — verifies an installed harness is correctly wired up. Default mode runs structural discovery only: expected phase commands, sub-agents, skills, state files, and hooks present and parseable in the detected adapter (<5s, no tokens). `--live` adds six real probes: `explorer` dispatch on a trivial filesystem prompt, `adversarial-reviewer` dispatch requiring an executable artifact (not prose), `ship-release --dry-run`, `migrate-to-diataxis` preview on an already-migrated tree, `dependabot-fixer` no-match path, and a hook synthetic trigger. Probes stop at the first foundational failure and never mutate repo state. Canonical spec at [`harness/skills/doctor.md`](https://github.com/alexherrero/agentic-harness/blob/main/harness/skills/doctor.md); adapter wrappers for claude-code, antigravity, and codex (Gemini reuses the Codex delivery). `check-parity.sh` CANON_SKILLS and `check-references.py` SHARED_SKILLS extended. First dogfood run caught a spec bug (phase-command frontmatter doesn't carry a `name:` field) and shipped the fix in the same release.
- **`migrate-to-diataxis` skill** — one-shot preview-first migration of an already-installed project's `wiki/` to the Diátaxis four-mode layout. Classifies each page (ADR, Status, How-to, Tutorial, Reference, Explanation, Mode-mixed), proposes a tree of `git mv`s to preserve blame, surfaces mode-mixed pages for manual split, and writes `wiki/.diataxis` to enable strict lint. Non-destructive; preview is always first.
- **Diátaxis wiki scaffold in the template** — `templates/wiki/` reshaped to `tutorials/`, `how-to/`, `reference/`, `explanation/`, with `wiki/.diataxis` marker, updated `_Sidebar.md`, and Diátaxis-shaped starter content. New installs land directly in the four-mode layout.
- **Mode-aware `documenter` writes** — the `documenter` sub-agent now dispatches with mode-specific write targets per phase. `/plan` → `wiki/explanation/` (feature pages) and `wiki/reference/` (subsystem pages), `/work` → `wiki/how-to/` (recipes), `/release` → `wiki/explanation/decisions/` (ADRs) and `wiki/reference/Completed-Features.md`, `/bugfix` → `wiki/reference/` (Known-Issues) and `wiki/explanation/decisions/`.
- **`scripts/check-wiki.py`** — Diátaxis structural lint with 11 rules (a–k): mode purity, ADR append-only + `Status: accepted|superseded|rejected`, orphan-link detection, globally-unique filenames, no banned-headings-per-mode. Shipped as warn-only in the same release; flipped to `--strict` (blocks PRs) in `tests-linux.yml`. Negative-test fixtures at `scripts/fixtures/check-wiki/` exercise each rule.
- **[ADR 0004](https://github.com/alexherrero/agentic-harness/blob/main/wiki/explanation/decisions/0004-diataxis-documentation-spec.md)** — Diátaxis documentation spec. Supersedes ADR 0002's audience-based layout (`wiki/{development,operational,design,architecture}/`) with the four Diátaxis modes. Rationale, consequences, migration path all captured.
- **`CONTRIBUTING.md`** — newly extracted from the previous README's Contributing and Status sections. Documents the three-workflow per-OS CI matrix, the full "what CI verifies without an agent" bullet list, the installer-boundary invariant, and the local-gate command set (bash and pwsh).

### Changed

- **Harness phase specs retargeted to Diátaxis mode dirs.** `harness/phases/02-plan.md`, `03-work.md`, `05-release.md`, and `harness/pipelines/bugfix.md` previously dispatched `documenter` at the old audience dirs (`wiki/development/`, `wiki/operational/`, `wiki/design/`, `wiki/architecture/`); they now point at the correct Diátaxis equivalents. `harness/documentation.md` gains a new "Migrating an existing install" section pointing at the `migrate-to-diataxis` skill, and the Non-goals list acquires a "five-mode extensions" bullet.
- **Dogfood wiki reshaped to Diátaxis layout.** The agentic-harness repo's own `wiki/` migrated file-by-file with `git mv` to preserve blame. ADRs moved to `wiki/explanation/decisions/`, feature pages to `wiki/explanation/`, how-to recipes to `wiki/how-to/`, reference tables to `wiki/reference/`. `Completed-Features.md` consolidated to `wiki/reference/Completed-Features.md`.
- **README simplified** — trimmed from 126 → 64 lines. Install section kept concise with a pointer to `wiki/how-to/Install-Into-Project.md`; the six-point Principles list collapsed to a one-sentence lead with a link to `harness/principles.md`; CI and contributing details extracted to the new `CONTRIBUTING.md`; Skills table gained `migrate-to-diataxis` and (later in the release) `doctor`.
- **`check-parity.sh` and `check-references.py` extended** with the two new shared skills (`doctor`, `migrate-to-diataxis`). Each ships in claude-code, antigravity, and codex; Gemini reuses the `.agents/skills/` delivery.

### Fixed

- **ProjectsV2 `/setup` flow regression** (pre-v0.9.0 drift) caught during the v0.8.7 cut — the v0.8.7 release note already covered the linkage fix, but the CHANGELOG wording understated how subtle the `@me`-vs-literal-owner gh-CLI quirk was. Documented in the ADR 0003 update for v0.8.7 readers who hit it during migration.
- **`doctor` frontmatter rubric** — the doctor skill's initial spec required a `name:` field on every surface's frontmatter. The first dogfood run revealed that Claude Code phase commands, Antigravity workflows, and Gemini TOML commands intentionally have no `name:` field (name is implicit from the filename). The spec and all three adapter wrappers now require `name:` match only on surfaces that actually carry the field (sub-agents + skills), preventing false-positive FAIL rows on every valid install.

### Internal

- **7-task Diátaxis rollout plan completed.** The full sequence was tracked in `.harness/PLAN.md` and released as a single coherent thread: task 1 (lint script), task 2 (template scaffold), task 3 (dogfood wiki reshape), task 4 (documenter mode-aware writes), task 5 (migrate-to-diataxis skill), task 6 (flip lint to `--strict`), task 7 (harness spec retargeting). Each task closed out with `PLAN.md` mark `[x]` and a `progress.md` append entry. Plan Status flipped to `done`.
- **First end-to-end exercise of `/doctor`.** Ran `/doctor` structurally against a scratch install (fresh `install.sh --hooks`) and the two highest-leverage `--live` probes (`explorer` + `adversarial-reviewer`) against the harness repo itself. The structural run surfaced the `name:` rubric bug; the live probes confirmed both sub-agents return within spec (explorer: 7.3s returning two absolute paths; adversarial: 10.8s returning a `file:line` pointer plus a failing pytest body, not prose).
- **CI green across all three per-OS workflows** on every commit in the range. Linux validate job reports `check-wiki: 0 structural issue(s), 0 soft warning(s)` after the dogfood reshape. Installer-boundary invariant holds (scratch install under smoke test never receives test infra).

[v0.9.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.9.0


## [v0.8.7] — 2026-04-21 — GitHub Projects integration (the Issues-lifecycle's deferred-work half) + documenter end-to-end dogfood

Closes the symmetric gap opened by v0.8.2: where `/bugfix` maintains a public GitHub Issue as bug posterity, now `/plan`, `/work`, `/review`, and `/release` each offer to file deferred-work items to a user- or org-owned ProjectsV2 board linked to the repo. Opt-in at `/setup`, preview-and-ask at every `gh` call, graceful-skip when the project isn't configured. Parallel track: the first end-to-end exercise of the `documenter` sub-agent's `/release` contract, flipping two feature flags and adding three new wiki pages + an ADR.

### Added

- **`gh project item-create` offer wired into every phase.** `/plan` proposes from the plan's `## Out of scope` section; `/work` from out-of-task-scope findings noticed while implementing; `/review` from deferred-rather-than-blocked findings; `/release` from cross-session themes. Each phase batches its proposals into a single preview, preview-and-ask on every invocation, graceful-skip when `.harness/project.json` is absent or `gh` is unavailable. Canonical blocks in `harness/phases/{02-plan,03-work,04-review,05-release}.md` with adapter-parity across all four adapters (claude-code, antigravity, codex, gemini — 20 adapter files touched). See the new [`wiki/design/features/GitHub-Projects-Integration.md`](https://github.com/alexherrero/agentic-harness/blob/main/wiki/design/features/GitHub-Projects-Integration.md) for the feature page and [ADR 0003](https://github.com/alexherrero/agentic-harness/blob/main/wiki/architecture/decisions/0003-ProjectsV2-Ownership-And-Linking.md) for the ownership-and-linking decision.

### Fixed

- **ProjectsV2 `/setup` flow now links the project to the repo.** The initial implementation created a user-scoped project that didn't appear under `github.com/<owner>/<repo>/projects`. ProjectsV2 has no repo-owned form — the fix is a two-step `gh project create` + `gh project link --repo <owner>/<repo>` flow at `/setup` step 8. `.harness/project.json` schema gains a `repo` field recording the linkage. Includes the `@me`-vs-literal-owner gh-CLI quirk as an inline code comment (passing `@me` to `gh project link --owner` sometimes fails with *"'<repo>' has different owner from '@me'"* even when they match). Rationale and consequences are in [ADR 0003](https://github.com/alexherrero/agentic-harness/blob/main/wiki/architecture/decisions/0003-ProjectsV2-Ownership-And-Linking.md).
- **Dropped the "at most 1 per session" cap on Project-item proposals.** Early drafts capped at one item; in practice a single `/work` or `/review` session can legitimately surface multiple deferred findings, and silent misses are worse than a user seeing a three-item batched preview. Replaced with a quality-bar-plus-batching rule: propose one item per distinct finding, batch into a single preview at phase end, per-phase soft caps as reminders rather than hard limits. Applied uniformly across all 20 canonical + adapter files.

### Internal

- **First end-to-end exercise of the `documenter` sub-agent.** Invoked per its `/release` contract (`harness/agents/documenter.md §/release`) with plan-to-HEAD diff + the current `wiki/` tree. Returned the canonical structured report (FILES CREATED / EDITED / OPEN QUESTIONS / NO-OP CATEGORIES). Outputs: new Feature page for GitHub-Projects-Integration (Template 2, Status: implemented), new ADR 0003 (Template 3, Status: accepted), new `wiki/development/Completed-Features.md` (Template 1 with overview table), Home.md + _Sidebar.md updated for the new pages. All three OPEN QUESTIONS resolved without further docsub edits. Flipped `feat-documenter-subagent.passes` and `feat-gh-projects-integration.passes` to `true` in `features.json`.
- **README refreshed against v0.8.2 drift.** Stale "v0.1" Status block replaced with a CHANGELOG pointer; Skills table gained `ship-release` (which shipped in v0.8.0 but was never cross-linked); `/bugfix` Phases row expanded with the Issue-posterity lifecycle; `documenter` sub-agent named in the intro + Install "drops in" list; new bullet for the `wiki/` + `.github/workflows/wiki-sync.yml` pair. Install / Contributing / License untouched.
- **ADR 0002 updated** with the runtime installer-boundary guard shipped in v0.8.2. Section 4 split into Runtime-guard vs. Test-time-assertions subsections, Consequences bullet rewritten for copy-time enforcement. Matches the `ensure_boundary_src` / `Ensure-BoundarySrc` implementation in `install.sh` / `install.ps1`.
- **Windows boundary-guard test coverage.** Added `scripts/test-install.ps1` (PowerShell twin of `scripts/test-install.sh` with all 5 checks a–e). Wired into `.github/workflows/tests-windows.yml` install-smoke job. Ensures the installer-boundary regression class caught by Defect 2 of [#1](https://github.com/alexherrero/agentic-harness/issues/1) is guarded on both OSes.

[v0.8.7]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.8.7

## [v0.8.2] — 2026-04-20 — First bugfix cycle + installer-boundary runtime guard

Three changes shipped together, themed around closing the loop on v0.8.0's documentation convention. (1) The wiki-sync workflow shipped in v0.8.0 as a template was never activated in the harness repo itself — this release activates it and adds a CI gate so the class of omission can't recur. (2) `/bugfix` now maintains a GitHub Issue as the public posterity record across all four phases, turning every bug's trajectory into a searchable narrative. (3) The installer boundary gains a runtime guard, with a test that proves it catches the exact regression scenario flagged by the adversarial reviewer.

### Fixed

- **`wiki/` not syncing to the GitHub Wiki** ([#1](https://github.com/alexherrero/agentic-harness/issues/1)). Root cause: `.github/workflows/wiki-sync.yml` was missing from the harness repo — v0.8.0 shipped the template at `templates/.github/workflows/` but no one activated it in this repo's own `.github/workflows/`. Every push since v0.8.0 had skipped the sync. Fix: copied the template byte-identical to `.github/workflows/wiki-sync.yml`, added `workflow_dispatch:` for backfill + manual re-sync, and a new `dogfood-workflows` job in `tests-linux.yml` that loops every `templates/.github/workflows/*.yml` and asserts a byte-identical counterpart exists at the repo root — so the class of bug can't recur.

### Changed

- **`/bugfix` now maintains a GitHub Issue as the bug's posterity record.** Phase 1 (Report) opens the tracking issue with title + body preview; Phase 2 (Analyze) posts the Analysis; Phase 3 (Fix) posts the Fix summary with commit SHA; Phase 4 (Verify) posts the Verify summary and closes the issue with `gh issue close --reason completed`. Every `gh issue *` call is preview-and-ask per `harness/documentation.md` — no silent automation. Graceful-skip if `gh` is unavailable or the repo isn't on GitHub. Propagated to all four adapter `bugfix` specs (Claude Code / Antigravity / Codex / Gemini).

### Internal

- **Installer-boundary runtime guard.** `install.sh` and `install.ps1` now call `ensure_boundary_src` / `Ensure-BoundarySrc` inside every copy helper (`cp_user`, `cp_managed`, `cp_managed_dir` and their pwsh twins). The guard rejects source paths outside `$HARNESS_ROOT/templates/` or `$HARNESS_ROOT/adapters/` with a loud boundary-violation message. `scripts/test-install.sh` gains check (e) that mutates `install.sh` in place via `sed` — rewriting the wiki-sync `cp_managed` source to the source-repo mirror — runs the mutated installer, and asserts the guard fires with non-zero exit. Addresses Defect 2 from the [#1](https://github.com/alexherrero/agentic-harness/issues/1) adversarial review: after `.github/workflows/wiki-sync.yml` became byte-identical to its template by design, a silent `install.sh` regression copying from the source-repo path would have been undetectable — the new guard makes it impossible.
- **`.gitignore`** — exclude `.claude/scheduled_tasks.lock` and `.claude/worktrees/` (local Claude Code artifacts).

[v0.8.2]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.8.2

## [v0.8.1] — 2026-04-20 — CI hardening + dogfood wiki

Follow-up to v0.8.0. Tightens the cross-platform CI gate suite, ships the agentic-harness repo's own wiki as a worked example of the v0.8.0 documentation convention, and fixes a PowerShell parse regression in the verify.ps1 template.

### Fixed

- `templates/verify.ps1` — empty `switch` statement (all clauses commented out) failed to parse on pwsh hosts with "Missing condition in switch statement clause". Added a required `default { }` clause so the template parses as shipped. Caught by the cross-platform CI added in v0.8.0.

### Internal

- **Cross-platform harness-integrity CI** — beyond install-smoke, the three per-OS workflows now run `check-parity.sh`, `validate-adapters.py`, `check-references.py`, `check-syntax.{sh,ps1}`, and `check-integrity-{bash,pwsh}` against a scratch install on every push / PR. A POSIX path-separator bug in `check-references.py` surfaced as part of this work and was fixed.
- **Dogfood wiki** — `wiki/` at repo root now contains this project's own documentation under the v0.8.0 convention: Home, Sidebar, one page per subdir (Getting-Started / Runbook / Product-Intent / Overview), plus ADRs 0001 (phase-gated workflow) and 0002 (documentation convention). The installer boundary is preserved — `install.sh` still copies only from `templates/wiki/`, never from this repo's own `wiki/`.
- **Dedicated installer-boundary test** — `scripts/test-install.sh` runs `diff -r templates/wiki/ <scratch>/wiki/` byte-for-byte plus a SHA-256 hash-based leak detector for each file under `$HARNESS_ROOT/wiki/`, wired into Linux CI. Proves the boundary on every PR.

[v0.8.1]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.8.1

## [v0.8.0] — 2026-04-19 — Documentation convention, three new full-parity adapters, Windows support, release automation

This is the largest release in the project's history. Four themes: (1) a first-class
documentation convention with a dogfooded wiki scaffold and per-phase documenter
sub-agent; (2) two new full-parity adapters (Codex CLI, Gemini CLI) plus the
existing Antigravity adapter expanded from README-only to full parity with
Claude Code; (3) cross-platform support — `install.ps1` and PowerShell twins
of every Unix helper, with CI validating install + parity on Linux, macOS,
and Windows; (4) a new `ship-release` skill that automates this exact kind
of tag cut going forward.

### Added

- **Documentation convention** — `harness/documentation.md` specifies a
  four-section `wiki/` scaffold (architecture / development / features / operational)
  that `install.sh` drops into every project on `/setup`. A canonical
  `documenter` sub-agent (`harness/agents/documenter.md`) is dispatched post-gates
  during `/work` and does a full-pass sweep during `/release` — flipping
  `Status: pending → implemented` only when the diff proves it.
- **`wiki-sync` GitHub Action** — pushes to the default branch mirror `wiki/`
  content to the GitHub Wiki (collision-checked, graceful-skip when the wiki
  is disabled). Ships via `install.sh` per-file walk.
- **Antigravity adapter full parity** — expanded from README-only to 4 agents
  (adversarial-reviewer, adversarial-reviewer-cross, documenter, explorer),
  6 workflow commands, the `dependabot-fixer` skill, and an always-on rules
  file. Installed into `.agent/`.
- **Codex CLI adapter** — new full-parity adapter. 7 skills (6 phase commands
  prefixed `harness-` to avoid colliding with Codex built-ins, plus
  `dependabot-fixer`), 4 TOML sub-agents with `sandbox_mode` specs, README
  documenting divergences. Installed into `.agents/skills/` and `.codex/agents/`.
- **Gemini CLI adapter** — new full-parity adapter. 6 native TOML slash
  commands, 4 markdown sub-agents with YAML frontmatter + tool allowlists,
  `settings.json` wiring `AGENTS.md` via `context.fileName`. Shared skills
  (`dependabot-fixer`, `ship-release`) reused from the `.agents/skills/`
  delivery. Installed into `.gemini/`.
- **Windows cross-platform support** — `install.ps1` at repo root with
  semantic parity to `install.sh` (PowerShell 7+). PowerShell twins of
  `verify.sh`, `precompact.sh`, `session-start-compact.sh`, and
  `cross-review.sh` ship alongside the Unix versions. Hook JSON is factored
  into canonical `settings-fragment-{bash,pwsh}.json` fragments so each
  installer reads the correct shell invocation.
- **Harness-repo CI** — three per-OS workflows (`tests-linux.yml`,
  `tests-mac.yml`, `tests-windows.yml`) gate every PR. Linux runs
  `install-smoke` + `adapter-parity` + `validate`; macOS and Windows run
  install-smoke via bash and pwsh respectively. Each smoke test asserts
  the installer boundary — `tests-*.yml`, `scripts/*`, and repo-root `wiki/`
  never propagate to installed projects.
- **`ship-release` skill** — auto-sized semver releases from conventional
  commits (`feat!` / `BREAKING CHANGE` → major, `feat:` → minor,
  `fix:`/`perf:`/`refactor:` → patch, `docs:`/`chore:`/`ci:` → no-bump).
  Writes `CHANGELOG.md`, tags, pushes, creates the GitHub release.
  Aborts if the tree is dirty, the default branch isn't pushed, or the
  tag already exists. Canonical spec at `harness/skills/ship-release.md`
  with adapter SKILL.md in claude-code / antigravity / codex.
- **Parity + validation scripts** — `scripts/check-parity.sh` asserts each
  adapter ships the canonical set of phase-commands, sub-agents, and skills
  (with documented divergences). `scripts/validate-adapters.py` parses all
  TOML, YAML frontmatter, and JSON across every adapter.
- **Contributing section** in README documenting CI matrix + local
  invocation commands.

### Changed

- **Phase specs wired to `documenter`** — `/setup`, `/plan`, `/work`
  (post-gates), and `/release` now dispatch the documenter sub-agent;
  `/review` gets an explicit not-invoked note to prevent docs drift from
  biasing the critic. `/bugfix` dispatches documenter on resolution.
- **Phase specs suggest `ship-release`** — `/work` suggests it when a
  feature's `passes` flag flips true; `/release` recommends it as the
  post-merge follow-up to the pre-merge gate.
- **`install.sh` per-file walk semantics** for `wiki/` — user-edited pages
  never get clobbered; new scaffold pages merge in cleanly.
- **`install.sh` boundary comment** — clarifies which directories are
  harness-authored (refreshed on `--update`) vs. user-owned (`cp_user`).
- **Hook settings factored** — `templates/hooks/settings-fragment-bash.json`
  and `-pwsh.json` are now the canonical source both installers read,
  mitigating JSON drift between the two.

### Fixed

- **PowerShell `ConvertTo-Json` array unwrap bug** — `install.ps1` now
  uses `ConvertFrom-Json -AsHashtable` throughout and stores hook-event
  arrays as `List[object]`, preventing single-element array unwrap that
  would have broken Claude Code's hook loader schema on Windows.

### Internal

- Research notes for Codex CLI conventions (`harness/agents/codex-adapter-research.md`)
  and Gemini CLI conventions (`harness/agents/gemini-adapter-research.md`) —
  both answer the research questions + open questions that informed the
  final adapter layouts.
- Installer-boundary assertion is now load-bearing in CI — break-each-invariant
  reproducers verified: rogue file → parity fails, corrupt TOML → validate
  fails, renamed subagent → parity fails, broken `install.sh` → smoke fails.

[v0.8.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.8.0
[v0.5.1]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.5.1
[v0.5.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.5.0
[v0.4.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.4.0
[v0.3.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.3.0
[v0.2.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.2.0
[v0.1.0]: https://github.com/alexherrero/agentic-harness/releases/tag/v0.1.0
