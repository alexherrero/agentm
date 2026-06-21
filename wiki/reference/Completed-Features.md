# Completed Features

Reverse-chronological log of feature deliveries in this repo. One line in the overview table per plan; a dated section below with branch/commit ref and a short summary. Maintained by the `documenter` sub-agent at `/release` time.

This page is **narrative**, not a changelog — the authoritative version log is [`CHANGELOG.md`](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md). Use this page when you want to understand *what shipped and why* without reading every commit; use `CHANGELOG.md` when you want the semver history.

## ⚡ Overview

| Date | Plan / release | Features flipped | Notes |
|---|---|---|---|
| 2026-06-19 | [v5.9.1](https://github.com/alexherrero/agentm/releases/tag/v5.9.1) — vault-path hygiene: `check-no-hardcoded-vault-path` gate | **hygiene:** `scripts/check-no-hardcoded-vault-path.py` — new CI gate banning absolute vault-path literals (`…/Library/CloudStorage/…`) and retired pre-V5-3 root name (`…/Obsidian/AgentMemory`) from non-test tracked files; 14 unit tests; wired as 22nd gate in `check-all.sh`. `AGENTS.md` new `§ Vault-path convention — resolve, don't recall` subsection locks the runtime-resolve contract in prose. | 2 commits (`034a846`, `306cbf0`); 22/22 check-all.sh; solo harness PATCH |
| 2026-06-19 | [v5.9.0](https://github.com/alexherrero/agentm/releases/tag/v5.9.0) — V5-5: `auto_orchestration` trigger split | **V5-5:** `_BRIDGE_PHASES` frozenset + `phase_dispatch()` formalized (non-blocking, graceful-skip, kernel-single-writer, write-capable sibling) + 21 contract tests (task 1, `a65f901`); single-writer invariant declared + static `verify-v4.sh` assertion (task 2, `15da187`); `list_plan_files()` + `list-plans` CLI; session-start hooks delegate plan discovery through bridge (task 3, `a7e3bee`); `verify-v4.sh` fractured 162→85 lines (kernel A+E+G); `verify-orchestration-briefing.sh` new (PM-half B+C+D); session-marker + discover-skills scenarios → `verify-phases.sh` (Developer-half); `check-all.sh` 20→21 gates (task 4, `8e4b170`); bridge back-edge gate + 3 tests in `check-process-seam-import-direction.sh` (task 5, `0fcdfb0`); `Auto-Orchestration.md` trigger-ownership section (task 6, `9dffa4d`); `Orchestration-Bridge.md` reference + ADR 0011 DC-1 re-audit trigger fired (task 7, `4d3622c`). | 8 tasks (incl. re-audit task 0), 7 commits; 21/21 check-all.sh; solo harness MINOR |
| 2026-06-19 | [v5.8.0](https://github.com/alexherrero/agentm/releases/tag/v5.8.0) — V5-7: config-plane migration | **V5-7:** `harness_memory._read_config_vault_path` reads `plugins.obsidian-vault.vault_path` first + first-read migration to plugin key + `storage.backend=vault` (task 1, `6b6dd17`); `agentm_config --vault-path` writes plugin-namespaced key + `storage.backend=vault`; `--get vault_path` reads plugin key with legacy fallback; `--unset vault_path` removes both keys (task 2, `07e3293`); `choose_protocol` loses `vault_root` parameter; implicit vault inference removed; resolution chain is now 2-step (task 3, `31f1ba9`); ADR 0013 amendment + `Storage-Seam.md` 2-step chain + `memory-os-architecture.md` V5-7 noted (task 4, `4615f45`, `a864465`). | 4 tasks, 4 code commits + 2 docs commits; solo harness MINOR |
| 2026-06-18 | [v5.7.0](https://github.com/alexherrero/agentm/releases/tag/v5.7.0) — V5-6: routing-plane de-vaulting | **V5-6:** `resolve_project` / `_vault_projects_dir` → `Locator` (task 1); `repo_registry` fully de-vaulted to seam (task 2); `state_mode: vault` aliases to `backend` at read time (task 3); `check-storage-seam-no-path-leak` Pass 2 + `check-process-seam-import-direction` LC-8 block + `storage_conformance` routing checks (task 4); ADR 0019 + docs (task 5). ([ADR 0019](0019-v5-6-routing-plane-devaulting)) | 5 tasks, 26 files changed; 20/20 check-all.sh; solo harness MINOR |
| 2026-06-18 | [v5.6.0](https://github.com/alexherrero/agentm/releases/tag/v5.6.0) — V5-7: capability-request matching | **V5-7:** `CapabilityMismatchError(StorageSelectionError)` + `select_backend(required=)` — subset-only capability matching, fail-loud. `_doctor_main` gains `--requires <CAPS>` pre-flight flag. 4 new injection params on `_doctor_main` for test isolation. (ADR 0013 amendment) | 2 commits (`v5.5.0..v5.6.0`); 13 new unit tests (`TestCapabilityMatching` 8 + `TestDoctorRequires` 5); check-all.sh green; solo harness MINOR |
| 2026-06-18 | [v5.5.0](https://github.com/alexherrero/agentm/releases/tag/v5.5.0) — V5-3: storage cutover: kernel → device-local | **V5-3:** vault backend deleted from `harness_memory.py` kernel; `harness_state_dir` / `read_state_file` / `write_state_file` always device-local; `phase_recall` always `""`; `resolve_documenter_context` always `None`; `vault_path()` raises `StorageBackendNotInstalledError` when `storage.backend=vault` + no vault (`$MEMORY_VAULT_PATH` stays graceful-skip). `personal-private/` → `personal/` group rename shipped in lockstep. ([ADR 0018](0018-v5-3-storage-cutover)) | 7 commits (`v5.4.0..v5.5.0`); 1170 unit tests 0 failures; 20/20 check-all.sh; solo harness MINOR |
| 2026-06-17 | [v5.4.0](https://github.com/alexherrero/agentm/releases/tag/v5.4.0) — V5-9 memory MCP server | **V5-9:** `scripts/memory_mcp_server.py` (singleton streamable-HTTP daemon at `127.0.0.1:7821`) + four MCP tools (`memory_search` / `memory_recall` / `memory_append` / `memory_forget`) + bearer-token + Origin-validation security + `scripts/memory_mcp_stdio_shim.py` (Claude Desktop stdio shim) + `scripts/memory_mcp_doctor.py` (4 health checks) + `install.sh --mcp-server` + launchd plist + host configs for Claude Code / Cursor / Goose / Claude Desktop. ([ADR 0017](0017-mcp-server-design)) | 11 commits (`v5.3.0..v5.4.0`); contract tests + 13 doctor unit tests; solo harness MINOR |
| 2026-06-16 | [v5.3.0](https://github.com/alexherrero/agentm/releases/tag/v5.3.0) — V5-11: team-coordinator persona | **V5-11:** `personas/team-coordinator.md` (first real composed persona) + `scripts/plan_graph.py` + `scripts/standup.py` + `scripts/readiness.py` + `scripts/merge_order.py` — the coordinator capability set. `requires: [queue_status_lite]`, `enhances: [developer-workflows, github-projects]`. | 1 commit (`7966ac3`); 62 fixture-backed tests; `check-personas` exits 0; solo harness MINOR |
| 2026-06-16 | [v5.2.0](https://github.com/alexherrero/agentm/releases/tag/v5.2.0) — V5-8 capability-discovery resolver + V5-12 persona tier | **V5-8:** `scripts/capability_resolver.py` + `scripts/capability_version_match.py` + `agentm capability` CLI shim — the `enhances:` runtime ([ADR 0015](0015-capability-discovery)). **V5-12:** `personas/rememberer.md` + `scripts/check-personas.py` + `check-personas` CI gate — the `kind: persona` third tier ([ADR 0016](0016-persona-tier)). | 6 commits (`v5.1.0..v5.2.0`); 72 unit tests (32 resolver + 40 version-match); `check-capability-resolver-one-way` + `check-personas` gates green; solo harness MINOR, no crickets pairing |
| 2026-06-13 | [v5.0.1](https://github.com/alexherrero/agentm/releases/tag/v5.0.1) — V5-1 follow-on: non-UTF-8 config readers honor their contract (harness-only PATCH) | **Correctness follow-on to the V5-1 storage seam.** The four config readers (`backend_selection.py::_configured_backend`, `harness_memory.py::_read_config_vault_path` + `_read_config_state_mode`, `agentm_config.py::_read_config`) caught `(json.JSONDecodeError, OSError)` but not `UnicodeDecodeError` — a `ValueError` subclass — so a non-UTF-8 config (realistic trigger: a Windows UTF-16/BOM "Save As") leaked an uncaught decode error past each reader's documented contract: the fail-loud resolver crashed instead of raising `StorageSelectionError`, and the three graceful readers crashed instead of returning `None`. All four now also catch `UnicodeDecodeError`; five regression tests pin the contract (incl. the `doctor` preview reporting `[FAIL]` not crashing). Found by an adversarial review of the V5-1 part-5 selection code. | 2 commits (`v5.0.0..v5.0.1`: the fix `50cde80` + this release commit); solo harness PATCH, no crickets pairing; no new ADR — correctness fix within the [ADR 0013](0013-storage-seam-fail-loud-selection) fail-loud contract |
| 2026-06-13 | [v5.0.0](https://github.com/alexherrero/agentm/releases/tag/v5.0.0) — Agent M V5: the memory↔storage seam + the dev-loop unbundling (MAJOR) | **The storage-agnostic memory OS arrives, and the vendored dev loop leaves.** Five roadmap arcs land together. **V5-1 — the memory↔storage seam** (the headline): a verb interface (`resolve`/`read`/`write`/`list`/`exists`/`info`/`mkdir`) handing back an opaque `Locator` (never a `Path`, AST-gate-enforced); a named-backend registry (resolve-as-absent); a `device-local` plain-markdown backend (`~/.agentm/memory/`); a backend-agnostic conformance suite; the existing GDrive vault wrapped behind the seam (whole-file conflict-strategy *name*, never-orphan invariant); and **selection + fail-loud** — a `storage.backend` config key + resolver (fresh→device-local; `vault_path`→vault), a `StorageSelectionError` that refuses an unregistered/misconfigured backend rather than silently demoting (incl. corrupt-config hardening), and a `doctor` storage preview byte-identical to the guard. **V5-0 — vault-write protocol**: `vault_lock` mutex + content-hash CAS + `atomic_write` routing + a four-marker-family conflict-janitor (incl. DriveFS `lost_and_found`). **V5-4 — process seam**: a read-only memory↔process client seam + a one-way import-direction gate. **V5-10 — named plans**: a named-plan resolver + binding (loud-error guard), `queue_status_lite` read-only multi-plan dashboard, named-plan-aware session-start hooks + `doctor`, a naming gate, and a `resolve-active-plan` CLI bridge. **The 2 BREAKING changes**: docs taxonomy converged to seven sections; `migrate-to-diataxis` + the dev-loop primitives (phase commands + review sub-agents) + the vendored `documenter`/`diataxis-author` copies were **retired** from agentm — now provided by crickets plugins with graceful-skip. Two new seam gates (`check-storage-seam-no-path-leak`, `check-process-seam-import-direction`). | 76 commits (`v4.15.0..v5.0.0`); new [ADR 0013](0013-storage-seam-fail-loud-selection) (storage-seam fail-loud selection) joins [ADR 0011](0011-v5-unbundling-dev-loop) (V5 unbundling) + [ADR 0012](0012-vault-write-protocol) (vault-write protocol) shipped earlier in the wave; the two BREAKING `refactor(harness)!:` commits (`5e85b6b` retire migrate-to-diataxis; `3d2b328` slim the dev-loop) drove the MAJOR bump; per-`/work` documenter passes flipped each arc's pages `pending → implemented` during the wave — this release sweep adds the reconciliation entry + sidebar nav + the storage-seam ADR. **`Named-Plans` stays `pending`** on a single remaining dependency (the `.harness/active-plan` marker *writer*, a worktree-spawn helper not in this range) — see its page |
| 2026-05-25 | [v3.1.0](https://github.com/alexherrero/agentm/releases/tag/v3.1.0) — Repo rename `agentic-harness` → `agentm` (paired with crickets v1.1.0) | **Repo rename**: GitHub repo for this project is now `alexherrero/agentm` (was `alexherrero/agentic-harness`); local sibling-clone path convention changes to `~/Antigravity/agentm/`. Brand-name (`Agent M`) was always the operator-facing label; rename brings URL slug + clone path in line with the brand. Sibling repo also renamed: `alexherrero/agent-toolkit` → `alexherrero/crickets`. All cross-references across both repos swept (~595 occurrences across ~112 files in agentm; ~642 occurrences across ~142 files in crickets; vault entries also swept ~43 files/157 occurrences). GitHub installs HTTP redirects from old URLs to new automatically; existing clones/links keep working. **No behavior changes** — pure rename + cross-reference sweep; no API surface or installer behavior changes. | 1 commit (`v3.0.1..v3.1.0`); paired with [crickets v1.1.0](https://github.com/alexherrero/crickets/releases/tag/v1.1.0) which does the toolkit-side equivalent sweep; **9th consecutive paired-release pair**; closes plan #15 task 11 (the final closing task of the README refresh plan); operator-local `~/.claude/CLAUDE.md` imports + sibling `dev-setup` repo + `alexherrero/alexherrero` profile README all updated for cross-repo consistency |
| 2026-05-24 | [v3.0.1](https://github.com/alexherrero/agentm/releases/tag/v3.0.1) — Agent M logo hero + brand asset set (harness-only PATCH) | **First visual brand iteration** — adds the Agent M logo asset set (~41 files across `assets/agent-m/` + `assets/m-monogram/` + `assets/index.html` brand-preview page; designed in Claude.ai Artifacts with brand palette `--ink: #0a0a0a` + `--paper: #f4efe6` and typography Inter Tight + JetBrains Mono) and refreshes `README.md` with a centered logo hero (`agent-m-clean-transparent-512.png` at displayed 256px) + italic tagline ("Persistent agentic memory + phase-gated engineering harness.") + reorganized badge layout into two centered blocks per the new [[personal-comms-style]] public-surface conventions. No behavior changes. **Solo harness PATCH** — not paired with Crickets; Crickets assets land separately when designed. Plan #15 task 1 close-out (Wave 1 of README refresh). | 2 commits (`v3.0.0..v3.0.1`); first visual asset commit since repo inception; establishes `assets/` as the brand-asset convention going forward |
| 2026-05-24 | [v3.0.0](https://github.com/alexherrero/agentm/releases/tag/v3.0.0) — Agent M V3 close-out (paired with toolkit v1.0.0 — Crickets 1.0) | **Agent M V3 ships** — harness version now matches the memory implementation V-versioning (V3 = merged-Obsidian-and-GDrive vault with auto-recall in every harness phase + controlled write under the permeable A3 boundary). System renamed in operator-facing prose: **Agent M** (this repo + Crickets sibling toolkit + AgentMemory vault folder, together) + **Crickets** (the toolkit — noisy-cricket MiB ref; small primitives that punch above their weight). README rewrites (both repos) lead with brand framing; harness wiki Home rebranded; new HLD at `crickets/wiki/explanation/designs/agent-memory-evolution.md` walks V1→V4 evolution; new V3 retrospective at `crickets/wiki/explanation/v3-retrospective.md` covers what shipped / what we learned / what's deferred. Roadmap split into slim `ROADMAP.md` (non-V4 backlog) + new `ROADMAP-AgentMemoryV4.md` (9 V4-line items) + full pre-split snapshot at `ROADMAP.archive.20260523-v3-complete.md` (all operator-local; `.harness/` is gitignored). Folds in ROADMAP items #12 (retrospective) + #27 (Agent M evolution design doc) + #31 (META split-the-roadmap). | 2 commits (`v2.6.1..v3.0.0`); paired with [crickets v1.0.0](https://github.com/alexherrero/crickets/releases/tag/v1.0.0) which ships Crickets 1.0 (stable public API surface commitment: manifest schema + installer flags + bundles namespace + 11 customization kinds + per-host paths) + 4 commits on toolkit side (V3 retrospective + Agent M HLD + cross-ref fix + Crickets README/Home rewrite); closes ROADMAP items #12/#27/#31; **8th consecutive paired-release pair + first MAJOR-MAJOR pair**; harness V-versioning now matches memory implementation V-versioning explicitly |
| 2026-05-23 | [v2.6.1](https://github.com/alexherrero/agentm/releases/tag/v2.6.1) — quality-gates bundle (paired with toolkit v0.13.0) | **One-command install for the 4 base operator-control + verification primitives** most `/work` sessions want: evaluator + kill-switch + steer + commit-on-stop + evidence-tracker. Paired-doc-only on harness side; substance ships in toolkit. Closes the "I forgot to install commit-on-stop and lost an hour" failure mode by offering `--bundle quality-gates` as a single adoption command. Bundle uses sibling-reference dispatch (bundle dir is just `bundle.md`; installer resolves to standalone primitives at install time) — net -1992 lines vs. the originally-planned COPY pattern after operator pushback mid-plan. | 1 commit (`v2.6.0..v2.6.1`); paired with [crickets v0.13.0](https://github.com/alexherrero/crickets/releases/tag/v0.13.0) which does the 6-commit sweep across plan #10 tasks 1-4 (bundle scaffold + sibling-reference pivot + smoke tests + Windows fixes + how-to + ADR 0010 + release); decision rationale + 2 locked design calls Q1-Q2 + 4 load-bearing assumptions in toolkit-side [ADR 0010](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0010-quality-gates-bundle.md); closes ROADMAP item #10; 7th consecutive paired-release pair; 2 cross-platform Python gotchas caught + fixed mid-plan (pwsh mkdir; inline-Python UTF-8) |
| 2026-05-23 | [v2.6.0](https://github.com/alexherrero/agentm/releases/tag/v2.6.0) — Evidence-tracking for /work (paired with toolkit v0.12.0) | **Default-FAIL evidence enforcement on `/work`**: every PLAN.md task starts with `evidence-met=false`; the agent must demonstrably READ relevant spec/test/evidence files (via the Read tool, observed by a new PreToolUse hook) before a Write/Edit that flips `[ ]` → `[x]` is allowed. Hook blocks (exit 2) with helpful stderr + 3 recovery paths. Harness-side ships the `/work` §5b spec amendment + templates/PLAN.md task-body convention update + Completed-Features row. Toolkit-side ships the substantive 4th base hook (~720-line stdlib-only Python helper + 61 unit tests + .sh/.ps1 entries + installer .py-sidecar extension). | 2 commits (`v2.5.0..v2.6.0`); paired with [crickets v0.12.0](https://github.com/alexherrero/crickets/releases/tag/v0.12.0) which does the 9-commit substantive sweep across plan #9 tasks 1-6 + drift-cleanup; decision rationale + 3 locked design calls Q1-Q3 in toolkit-side [ADR 0009](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0009-evidence-tracker-hook.md); closes ROADMAP item #9; 6th consecutive paired-release pair + second non-doc-only on the harness side (after v2.5.0); 2 in-flight scope expansions caught + fixed mid-plan (installer .py-sidecar pattern; toolkit baseline drift 21→0 structural errors) |
| 2026-05-22 | [v2.5.0](https://github.com/alexherrero/agentm/releases/tag/v2.5.0) — Auto-context into harness phases (paired with toolkit v0.11.1) | **First non-doc-only paired pair in the run** — harness ships real new phase behavior across all 5 phases + bugfix pipeline. New `scripts/harness_memory.py` dispatcher (4 sub-commands: `recall` / `offer-save` / `plan-done-promotion` / `available`) called from amended phase specs at natural boundaries. Self-modulating offer-save (agent-supplied `--confidence ≥ HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD` → silent save with stderr notice; below threshold → preview-and-ask prompt). Shared `.harness/.promoted-progress-cursor` between `/work` plan-done + `/release` tail-scan triggers means single-fire per plan-window. Graceful-skip when MemoryVault not installed (harness still runs unchanged). | 9 commits (`v2.4.3..v2.5.0`); paired with [crickets v0.11.1](https://github.com/alexherrero/crickets/releases/tag/v0.11.1) which adds the cross-repo memory protocol doc (wiki-only PATCH); new [ADR 0007](0007-auto-context-into-harness-phases) with 5 locked design calls (Q1–Q5: per-phase budgets / vault_project 3-tier auto-detect / graceful-skip / self-modulating ask / dual-trigger cursor-tracked promotion); 5 phase specs + 1 pipeline amended via sub-letter convention (§1b / §4c / §7b / §7c / §2b / §5b / §5c) preserving integer §-numbers + 4 wiki line-anchor updates; closes ROADMAP item #8; 5th consecutive paired-release pair but **first non-doc-only** in the run (harness MINOR bump justified by real new phase behavior) |
| 2026-05-22 | [v2.4.3](https://github.com/alexherrero/agentm/releases/tag/v2.4.3) — diataxis-author skill (paired with toolkit v0.11.0) | **New second-major-skill `diataxis-author` shipped in toolkit** — one skill, five sub-commands (`/diataxis author` + `check` + `repair` + `migrate` + `classify`) covering the full Diátaxis-wiki lifecycle. Subsumes `migrate-to-diataxis` harness predecessor (deprecated). Harness-side is doc-only (Completed-Features + CHANGELOG + deprecation NOTE on predecessor); skill scripts + sub-agent + templates + how-to + ADR ship on toolkit side. | 1 harness commit (deprecation NOTE `d4d4adf`) + 1 release commit; paired with [crickets v0.11.0](https://github.com/alexherrero/crickets/releases/tag/v0.11.0) which does the substantive 8-commit sweep across plan #13 parts 1-5 (scaffold + author-classify + check-repair + migrate-subsume + agentmemory-docs-release); decision rationale in toolkit-side [ADR 0008](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0008-diataxis-author.md); second real dogfood of plan #6's `/design` skill (after MemoryVault); 4th consecutive paired-release-as-documentation pair (v2.4.0/v2.4.1/v2.4.2/v2.4.3) |
| 2026-05-22 | [v2.4.2](https://github.com/alexherrero/agentm/releases/tag/v2.4.2) — MemoryVault Discovery + Mining (paired with toolkit v0.10.0) | **Discovery + mining additive layer**: five new `/memory` sub-commands (`index-skills` / `reflect corpus` / `discover-skills` / `adapt-skills` / `watchlist`) turn the vault from a static curated store into a living surface. Personal-skills auto-indexer; historical-transcript-backlog mining with dry-run-default; cadence-checked internet skill-discovery scan (operator's 4-source whitelist); adapt-don't-import workflow with 6-rule Python rubric + GitHub metadata + trustworthiness signals + LLM sub-agent judgment; promote/dismiss/defer review surface. **Adapt-don't-import is architecturally enforced** via sub-agent write allowlist. Harness-side is doc-only (Completed-Features + CHANGELOG + ROADMAP move + PLAN archive). | 1 commit (`v2.4.1..v2.4.2`); paired with [crickets v0.10.0](https://github.com/alexherrero/crickets/releases/tag/v0.10.0) which does the 8-commit substantive sweep across plan #7b tasks 1-7; decision rationale + 7 locked design calls in toolkit-side [ADR 0007](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0007-memoryvault-discovery.md); closes ROADMAP item #7b (MemoryVault parent design fully shipped — both #7a + #7b roadmap items now Completed); paired-release-as-documentation pattern (continues from v2.4.0 + v2.4.1) |
| 2026-05-20 | [v2.4.1](https://github.com/alexherrero/agentm/releases/tag/v2.4.1) — Local-only embeddings (paired with toolkit v0.9.2) | **Embedding-mode collapse**: Voyage/Anthropic API mode dropped from crickets's memory skill; local sentence-transformers is now the only production mode. Default model `BAAI/bge-large-en-v1.5` (1024-d native; 384-d → 1024-d dim bump). Harness-side is doc-only (Completed-Features + CHANGELOG); embed.py + vec_index.py + install scripts refactor happens on toolkit side. | 1 commit (`v2.4.0..v2.4.1`); paired with [crickets v0.9.2](https://github.com/alexherrero/crickets/releases/tag/v0.9.2) which does the 8-commit substantive sweep across plan #18 tasks 1-7 (embed.py refactor + vec_index.py rebuild + smoke install split + install scripts pip-install + ADR amendment + docs rewrite + this paired release); decision rationale in toolkit-side [ADR 0001's 2026-05-20 amendment](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0001-crickets-purpose.md#amendment-2026-05-20); plan #18 is the **first mid-flight plan insertion** in the personal-dev-env (inserted into the active queue during plan #7a part 5 execution; seed-pass resumes at task 6 after this release pair ships); paired-release-as-documentation pattern (continues from v2.4.0) |
| 2026-05-17 | [v2.4.0](https://github.com/alexherrero/agentm/releases/tag/v2.4.0) — Gemini-CLI host removal (paired with toolkit v0.9.0) | **Host-scope reduction**: standalone Gemini CLI dropped from supported hosts across the personal-dev-env. Keeps Claude Code + Antigravity. Harness-side is doc-only (Completed-Features + CHANGELOG); customization sweep happens on toolkit side. | 1 commit (`v2.3.1..v2.4.0`); paired with [crickets v0.9.0](https://github.com/alexherrero/crickets/releases/tag/v0.9.0) which does the substantive customization + installer + wiki + ADR sweep across 5 commits (e1b477e + 5af1a59 + b216043 + 13109fa + 7a4162f); decision rationale in toolkit-side [ADR 0006](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0006-gemini-cli-host-removal.md); first host-scope reduction in personal-dev-env history; paired-release-as-documentation pattern (substantive change lives toolkit-side; harness still ships a paired MINOR for version-cadence readability) |
| 2026-05-16 | [v2.3.1](https://github.com/alexherrero/agentm/releases/tag/v2.3.1) — `/plan` external-review-handoff option (dogfood-driven patch) | Additive: `/plan` phase spec §4b documents the external-review-handoff option (pre-handoff snapshot + transfer-context generation + handoff prompt + diff-on-resume + Accept/Iterate/Discard) as an alternative to inline iteration on long plans | 1 commit (`v2.3.0..v2.3.1`); paired with [crickets v0.8.1](https://github.com/alexherrero/crickets/releases/tag/v0.8.1) which adds the same option to `/design author` + `/design translate`; shared transfer-context template lives toolkit-side; first cross-repo dogfood-driven amendment shipped as coordinated patch pair (surfaced during MemoryVault design-doc walk on 2026-05-15); design rationale in toolkit-side [ADR 0004 amendment (2026-05-16)](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0004-design-skill.md#amendment--2026-05-16-v081-external-review-handoff-option) |
| 2026-05-15 | [v2.3.0](https://github.com/alexherrero/agentm/releases/tag/v2.3.0) — `/release` + `/setup` integration for crickets's `/design` skill | Additive: `/release` §1b lifecycle hook auto-promotes queued plans + transitions design `final → launched` + surfaces launched published designs in `wiki/Home.md`; `/setup` §7 scaffolds `wiki/explanation/designs/` landing dir; `templates/wiki/explanation/designs/.gitkeep` + README; `EXTERNAL_CUSTOMIZATIONS` extends with `design`; `/work` Step 11 ROADMAP-driven enhancement (universal) | 2 commits (`v2.2.0..v2.3.0`); paired with [crickets v0.8.0](https://github.com/alexherrero/crickets/releases/tag/v0.8.0) which ships the `/design` skill with 3 sub-commands; no new harness ADR (the design decision lives in [crickets ADR 0004](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0004-design-skill.md)); harness still functions standalone — §1b silent-skips when no design-doc origin signal |
| 2026-05-14 | [v2.2.0](https://github.com/alexherrero/agentm/releases/tag/v2.2.0) — `/work` + `/release` augmentable with crickets's base hooks | Additive: `/work` + `/release` phase specs gain optional sections documenting kill-switch + steer + commit-on-stop dispatch from crickets (operator-precision hooks for long-running sessions); `check-references.py` `EXTERNAL_CUSTOMIZATIONS` extended with 3 new hook names | 1 commit (`v2.1.0..v2.2.0`); paired with [crickets v0.7.0](https://github.com/alexherrero/crickets/releases/tag/v0.7.0) which ships the three hook customizations + first-class `kind: hook` installer support; no new harness ADR (the design decision lives in [crickets ADR 0003](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0003-base-operator-hooks.md)); harness still functions standalone — both sections graceful-skip when toolkit absent |
| 2026-05-13 | [v2.1.0](https://github.com/alexherrero/agentm/releases/tag/v2.1.0) — `/review` augmentable with crickets's `evaluator` | Additive: `/review` phase spec gains §3b documenting evaluator dispatch alongside `adversarial-reviewer` (complementary, not competing); `check-references.py` `EXTERNAL_SKILLS` → `EXTERNAL_CUSTOMIZATIONS` rename to cover cross-repo agent references | 1 commit (`v2.0.0..v2.1.0`); paired with [crickets v0.6.0](https://github.com/alexherrero/crickets/releases/tag/v0.6.0) which ships the evaluator; no new harness ADR (the decision lives in [crickets ADR 0002](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0002-evaluator-design.md)); harness still functions standalone — §3b graceful-skips when toolkit absent |
| 2026-05-12 | [v2.0.0](https://github.com/alexherrero/agentm/releases/tag/v2.0.0) — `crickets` repo split: `dependabot-fixer` + `ship-release` moved out | **BREAKING**: two skills migrated to [`crickets`](https://github.com/alexherrero/crickets); new shared `lib/install/` byte-identical across repos; PII + lib-parity CI gates added | 9 commits (`v1.0.0..v2.0.0`); new [ADR 0006](0006-crickets-split); paired with [crickets v0.5.0](https://github.com/alexherrero/crickets/releases/tag/v0.5.0); harness shared-skills narrow 4→2; cross-platform byte-identity work surfaced + fixed four real Mac/Windows bugs |
| 2026-05-11 | [v1.0.0](https://github.com/alexherrero/agentm/releases/tag/v1.0.0) — Three-adapter scope; Codex dropped; 1.0.0 commitment | **BREAKING**: Codex adapter removed; true-sync `--update` semantics; firm-semver 1.0.0 floor | 13 commits (`v0.9.0..v1.0.0`); new [ADR 0005](0005-drop-codex-support); ~1300 lines net removed; first major version; parity invariant simplified to three adapters |
| 2026-04-23 | [v0.9.0](https://github.com/alexherrero/agentm/releases/tag/v0.9.0) — Diátaxis documentation spec + `/doctor` skill | Diátaxis rollout (ADR 0004, 7-task plan); `migrate-to-diataxis` skill; mode-aware `documenter` writes; `/doctor` skill for post-install verification | 10 commits (`v0.8.7..v0.9.0`); new [ADR 0004](seven-section-convergence); two new shared skills; `scripts/check-wiki.py` shipped + flipped to `--strict`; wiki dogfood reshaped with `git mv` for blame |
| 2026-04-21 | [v0.8.7](https://github.com/alexherrero/agentm/releases/tag/v0.8.7) — GitHub Projects wiring + documenter end-to-end dogfood | `feat-gh-projects-integration` (pending — gated on offer-cycle observation); `feat-documenter-subagent` (this sweep is the dogfood) | 4 commits (`801dbd7..HEAD`), 23 files; new [ADR 0003](0003-ProjectsV2-Ownership-And-Linking), new [Feature page](GitHub-Projects-Integration) |

## 2026-06-19 — v5.9.1: vault-path hygiene gate + convention doc

**Commit range:** `v5.9.0..v5.9.1` (2 commits on `main`: `034a846`, `306cbf0`). Release notes: [v5.9.1](https://github.com/alexherrero/agentm/releases/tag/v5.9.1). Solo harness PATCH — no crickets pairing.

### What shipped

A CI gate that prevents absolute vault-path literals from re-entering the codebase, paired with a prose lock of the runtime-resolve convention in `AGENTS.md`.

**`scripts/check-no-hardcoded-vault-path.py`** (`034a846`). New gate that fails (exit 1) when any non-test tracked file contains either (A) an absolute `…/Library/CloudStorage/…` path literal — as opposed to shell tilde/variable expansions or placeholder notation — or (B) the retired pre-V5-3 vault root name `…/Obsidian/AgentMemory` as a path component. Exit 0 = clean; exit 1 = violations printed with file, line, and column; exit 2 = setup error. Fourteen unit tests in `scripts/test_check_no_hardcoded_vault_path.py`, using the importlib pattern. Wired as the 22nd gate in `scripts/check-all.sh`. Row added to `wiki/reference/CI-Gates.md`.

**`AGENTS.md` convention lock** (`306cbf0`). New subsection `§ Vault-path convention — resolve, don't recall` under the Conventions heading. Captures in prose: the canonical resolver (`harness_memory.vault_path()`, which reads `plugins.obsidian-vault.vault_path` from kernel config set by `agentm_config --vault-path`); the `$MEMORY_VAULT_PATH` env-override escape hatch; and the "why" — machine-specific path segments (username, drive ID, mount point) encoded as literals become silently wrong across installs and over time. Also updates the Running-the-checks paragraph to name the 22nd gate.

### Why this shape

The V5-7 config-plane migration (v5.8.0) removed the last production site that read a vault path from a durable config key written by pre-V5-7 code. This gate closes the loop: it mechanically prevents any future refactor from re-introducing the pattern. The gate is intentionally narrow — shell variable expansions (`$MEMORY_VAULT_PATH`, `~/.agentm/`) and placeholder notation pass — so it flags only the cases that are unambiguously wrong (baked absolute machine-specific paths) without blocking legitimate relative or env-driven usage.

No ADR is needed: the gate enforces the existing AGENTS.md convention, not a new architectural decision.

### By the numbers

2 commits (`v5.9.0..v5.9.1`). 22/22 `check-all.sh` PASS (was 21/21; grew by one gate). Solo harness PATCH.

### Related

- [CI gates — `check-no-hardcoded-vault-path`](CI-Gates#what-each-gate-proves) — gate row; invariant; script link.
- [AGENTS.md § Vault-path convention](https://github.com/alexherrero/agentm/blob/main/AGENTS.md#vault-path-convention--resolve-dont-recall) — the prose lock of the resolve-don't-recall rule.
- [v5.9.1](https://github.com/alexherrero/agentm/releases/tag/v5.9.1) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md).

---

## 2026-06-19 — v5.9.0: V5-5 — `auto_orchestration` three-way trigger split

**Commit range:** `v5.8.0..v5.9.0` (7 commits on `main`). Release notes: [v5.9.0](https://github.com/alexherrero/agentm/releases/tag/v5.9.0). Solo harness MINOR — no crickets pairing.

### What shipped

V5-5 splits the fused `auto_orchestration` push surface into its three natural trigger owners without changing behavior or raising autonomy. The memory logic (reflect/discover/adapt/index) and cadence state stay kernel-resident; only the *knowledge of which boundary fires a loop* relocates to the owner of that boundary.

**Task 0 — Re-audit trigger topology (finding, no code commit).** Confirmed `phase_dispatch()` in `harness_memory.py` is the **sole caller** of `orchestration_phase.py` — the bridge embryo already exists as a production path. The dev-workflows plugin does NOT call `phase-dispatch` at v0.25.0 (premise from the design-doc drifted). State-writer audit: 4 sites call `ao.save_state()` — all routing through the same kernel function in `auto_orchestration.py`. Task 1 sized as formalization/hardening, not net-new construction.

**Task 1 — Formalize the orchestration bridge** (`a65f901`). Added `_BRIDGE_PHASES = frozenset({"post-work", "post-release"})` constant. `phase_dispatch()` refactored with four explicit contract properties in its docstring: non-blocking (subprocess, session-agnostic), graceful-skip (missing session-marker → `{"status": "no-session"}`), kernel-single-writer (only `auto_orchestration.py` writes the state file), write-capable sibling (distinct from the read-only process seam). `ValueError` on unknown phase. `project_root=None` and `dry_run=False` defaults added for direct Python callability. The CLI `phase-dispatch` verb and `choices=` arg mirror `_BRIDGE_PHASES`. 21 contract tests in `scripts/test_orchestration_bridge.py`.

**Task 2 — Declare `auto_orchestration.py` the sole state writer** (`15da187`). Updated module docstring and `save_state()` docstring to explicitly state the single-writer invariant (LC-2). Added static assertion to `verify-v4.sh` segment G: greps `orchestration_*.py` for `^def save_state` and asserts 0 matches — making the accidental invariant a stated, gate-checked one.

**Task 3 — Fold `harness-context-session-start` plan-resolution into the bridge** (`a7e3bee`). Added `list_plan_files(harness_dir: Path) -> list` to `harness_memory.py` — canonical enumeration of active `PLAN*.md` files (singleton first, named sorted, excluding conflict copies and archives). Added `list-plans` CLI verb that prints each plan path and `active-binding=<slug>` when `.harness/active-plan` is set; routes through `harness_state_dir()` to respect the V5-6 state_mode/Locator chain. Both `harness-context-session-start.sh` and `.ps1` now delegate plan discovery to `list-plans` instead of carrying their own PLAN-*.md glob + active-plan marker read. Removed the broken `vault-state-path` call silently dead in the PS1 hook since V5-3; fixed singleton output message. 13 new tests (TestListPlanFiles × 6, TestListPlansCLI × 7).

**Task 4 — Fracture `verify-v4.sh`** (`8e4b170`). Rewrote `verify-v4.sh` from 162 to 85 lines — kernel-only (config-seed A + idle-chain E + emit-gating/atomic-state G + single-writer assertion). Added G-seed (minimal inbox files) so the emit-gating assertion has signals without depending on briefing-check vault state. Created new `verify-orchestration-briefing.sh` (PM-half: segments B+C+D — briefing signals, staged-adapt, nudges; travels to crickets PM-trigger plan when that ships). Relocated phase-dispatch session-marker scenarios (no-session / single-marker / ambiguous) + discover-skills chain check to `verify-phases.sh` (Developer-half). `check-all.sh` grows from 20 to 21 gates.

**Task 5 — Extend `check-process-seam-import-direction.sh` to the bridge** (`0fcdfb0`). Added LC-8 bridge back-edge assertion: scans `harness/skills/memory/scripts/` for any `import harness_memory` or `from harness_memory import`; fails on a hit; excludes `test_*.py` by design. Three new tests in `ImportDirectionGate`.

**Task 6 — Document the PM-half hand-off** (`9dffa4d`). Added "Trigger ownership (V5-5)" section to `wiki/explanation/Auto-Orchestration.md`: three owners (kernel / Developer plugin / PM plugin), bridge entry point, PM-half forward-reference status (gate lifted — `github-projects` exists; actual relocation = separate crickets plan; failure mode = non-relocation, never broken briefing).

**Task 7 — Docs refresh** (`4d3622c`). Created `wiki/reference/Orchestration-Bridge.md` (new reference sibling to Process-Seam / Storage-Seam): chains, API, CLI, single-writer guarantee, one-way direction. Added to `wiki/_Sidebar.md`. Fixed `AGENTS.md` "three verify-*" → "four verify-*". Updated `CI-Gates.md` `check-process-seam-import-direction` row (V5-5 LC-8 bridge extension noted); fixed `verify-orchestration-briefing` description. Updated ADR 0011 DC-1 re-audit trigger as fired: baked-in orchestration call in the Developer plugin can now be retired via a separate crickets plan.

### Why this shape

The trigger-relocation (not a file-move) is the key framing: the memory logic and cadence core stay kernel-resident; what relocates is *which boundary fires a loop*. The two alternatives in the design-doc fork — multiple writers vs. kernel-single-owner + write-capable sibling bridge — were resolved before task 1 (LC-2 is the load-bearing safety call: shared state with multiple writers is torn state).

The fracture of `verify-v4.sh` is "relocate + de-dupe, don't rewrite": the phase-half coverage already existed in `verify-phases.sh`; the session-marker scenarios that didn't were added there. The three-file result maps directly to the three trigger owners.

`list-plans` resolves a latent coupling: the session-start hooks were carrying their own PLAN*.md glob and `active-plan` marker read, duplicating logic the bridge should own. Delegating through `harness_state_dir()` also makes the hooks respect the V5-6 routing-plane Locator chain at zero cost.

The PM-half is a forward-reference, not deferred scope: `github-projects` exists (gate lifted since the design), the bridge is ready, but the actual trigger relocation is a separate crickets plan. The failure mode is non-relocation (briefing keeps firing from kernel), never a broken briefing.

### By the numbers

7 commits (`v5.8.0..v5.9.0`): `a65f901`, `15da187`, `a7e3bee`, `8e4b170`, `0fcdfb0`, `9dffa4d`, `4d3622c`. 21/21 `check-all.sh` PASS (was 20/20; grew by one gate — `verify-orchestration-briefing`). New test file: `test_orchestration_bridge.py` (21 tests); 3 new `ImportDirectionGate` tests; 13 new tests in `test_harness_memory.py`. Solo harness MINOR.

### Related

- [Orchestration bridge](Orchestration-Bridge) — new reference page: `phase_dispatch()` contract, valid phases, session-marker states, CLI.
- [Auto-orchestration](Auto-Orchestration) — "Trigger ownership (V5-5)" section; three-owner topology.
- [ADR 0011 — V5 unbundling](0011-v5-unbundling-dev-loop) — DC-1 re-audit trigger fired; baked-in orchestration call in Developer plugin can now be retired.
- [CI gates](CI-Gates) — `verify-orchestration-briefing` added (21st gate); `check-process-seam-import-direction` extended (LC-8 bridge back-edge).
- [v5.9.0](https://github.com/alexherrero/agentm/releases/tag/v5.9.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md).

---

## 2026-06-19 — v5.8.0: V5-7 — config-plane migration

**Commit range:** `v5.7.0..v5.8.0` (4 tasks, 4 code commits + 2 docs commits on `main`). Release notes: [v5.8.0](https://github.com/alexherrero/agentm/releases/tag/v5.8.0). Solo harness MINOR — no crickets pairing.

### What shipped

V5-7 completes the config-plane leg of the V5 de-vaulting arc: the `vault_path` config key migrates from the flat top-level to the plugin-namespaced location (`plugins.obsidian-vault.vault_path`), vault selection becomes always explicit, and `choose_protocol` loses its last implicit vault-inference path.

**Task 1 — `harness_memory._read_config_vault_path`: plugin-namespaced read + first-read migration** (`6b6dd17`). `_read_config_vault_path()` now reads `plugins.obsidian-vault.vault_path` first; if absent, falls back to the legacy flat `"vault_path"` key. On the *first* successful legacy-key read that finds an accessible vault, the function atomically writes both `plugins.obsidian-vault.vault_path` and `storage.backend=vault` into the install config, migrating in place. The public `vault_path()` API is unchanged — callers see no difference.

**Task 2 — `agentm_config`: plugin-namespaced write + `--unset` cleanup** (`07e3293`). `--vault-path <dir>` now writes `plugins.obsidian-vault.vault_path` + `storage.backend=vault` (previously wrote the flat `vault_path` key only). `--get vault_path` reads the plugin key first with legacy fallback. `--unset vault_path` removes both the plugin-namespaced key and the legacy flat key, leaving no orphaned config state.

**Task 3 — `backend_selection`: remove implicit vault inference** (`31f1ba9`). `choose_protocol()` loses its `vault_root` parameter. The former implicit rule — "if `vault_path` resolves to an accessible directory, infer `vault`" — is removed. The resolution chain is now exactly 2 steps: (1) explicit `storage.backend` config wins; (2) `device-local`. An operator migrating from a pre-V5-7 implicit setup must run `agentm config --vault-path <dir>` or `agentm config --storage-backend vault` once. Both proof paths (`TestChooseProtocol`) updated in the same commit.

**Task 4 — Docs sweep** (`4615f45`, `a864465`). ADR 0013 amended: DC-4 comment block updated to record all 3 V5-7 tasks shipped, including the removal of the implicit vault-inference rule. `wiki/reference/Storage-Seam.md` 2-step resolution chain table updated (the former implicit-vault precedence 2 row removed; note added). `wiki/designs/memory-os-architecture.md` obsidian-vault row updated to note V5-7 config-plane all 3 tasks shipped. `wiki/reference/Installer-CLI.md` `--vault-path` row updated for the plugin-namespaced write.

### Why this shape

The implicit vault inference — "if `vault_path` is present and the directory exists, pick `vault`" — was the last place in the kernel that made vault selection *automatic* rather than *explicit*. This was a correctness hazard: on a machine where the `obsidian-vault` plugin is not installed but an old `vault_path` key persists, `choose_protocol` would silently resolve to `vault` and the fail-loud guard would then stop memory operations with a confusing "vault backend not registered" error rather than falling through to `device-local`. Making selection always explicit means the resolution chain is stateless and predictable: the only reason to reach `vault` is a deliberate write of `storage.backend=vault`.

The first-read migration in task 1 is the operator-transparent migration path: the first time a legacy-key vault is used after upgrading, the read succeeds (because the legacy key still works), and the atomic write promotes the config silently. Subsequent reads use the plugin-namespaced key. No operator intervention required for existing setups.

### By the numbers

4 code commits + 2 docs commits (`v5.7.0..v5.8.0`); 20/20 `check-all.sh` PASS (pre-release verification); solo harness MINOR.

### Related

- [ADR 0013 amendment — V5-7 config-plane](0013-storage-seam-fail-loud-selection#amendment--2026-06-18-v5-7-capability-request-matching-extends-dc-4-with-the-same-fail-loud-principle) — DC-4 updated to record all 3 V5-7 tasks and the removal of implicit vault inference.
- [Storage seam § The selection resolver](Storage-Seam#the-selection-resolver) — `choose_protocol` signature change; 2-step chain table.
- [Installer CLI](Installer-CLI) — `--vault-path` updated to show plugin-namespaced write.
- [Choose a storage backend](Choose-A-Storage-Backend) — operator-facing how-to; migration note for pre-V5-7 setups.
- [v5.8.0](https://github.com/alexherrero/agentm/releases/tag/v5.8.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md).

---

## 2026-06-18 — v5.7.0: V5-6 — routing-plane de-vaulting (third leg)

**Commit range:** `v5.6.0..v5.7.0` (5 tasks, 26 files changed on `main`). Release notes: [v5.7.0](https://github.com/alexherrero/agentm/releases/tag/v5.7.0). Solo harness MINOR — no crickets pairing.

### What shipped

V5-6 closes the third and final leg of the V5 de-vaulting arc (data plane V5-3 ✅, config plane V5-7 partial ✅, routing plane V5-6 ✅). After this release a fresh install with only the `device-local` backend can host a project, its harness state, and the repo registry without needing an Obsidian/GDrive vault. On the `obsidian-vault` backend every mechanism resolves to identical bytes at identical locations — behavior-preserving by parallel-run proof (LC-1 / LC-7).

**Task 1 — `resolve_project` / `_vault_projects_dir` onto the seam.** `harness_memory._vault_projects_dir` signature changed from `(vault: Path) -> Path` to `(backend: StorageBackend) -> Locator`. `harness_memory.resolve_project` returns `{slug, project_locator, backend, project_root, layout}` — `project_locator` is a `Locator`, never a `Path`. Callers updated: `process_seam.py:161` uses `project_locator.key`; `memory_mcp_tools.py:263` inlines segment detection. LC-1 parallel-run assertion proved byte-identical resolution before removing vault-shaped path construction.

**Task 2 — `repo_registry` fully de-vaulted.** `registry_path(vault_path) -> Path` replaced by `registry_locator(backend: StorageBackend) -> Locator`. All five public functions (`read_registry`, `write_registry`, `register_repo`, `unregister_repo`, `list_repos`) take `backend: StorageBackend`. `_mutate_registry` no longer holds `vault_mutex` directly — `VaultBackend.write()` handles it; CAS retry loop handles cross-device races. On `obsidian-vault`, resolves to identical on-disk path as before (LC-7 parallel-run test at `test_harness_memory.py#L2265`). `project_config.is_registered` takes `backend=None` instead of `vault_path=None`.

**Task 3 — `state_mode: vault` → `backend` alias.** `_read_config_state_mode` maps `"vault"` → `"backend"` at read time via a one-line guard. `_read_project_mode` applies the same alias for `.harness/.project-mode` markers. `agentm_config._STATE_MODES` adds `"backend"` as canonical; `"vault"` is retained as a deprecated CLI alias. `cmd_set_state_mode` normalizes `"vault"` → `"backend"` at write time. No operator migration required (LC-5): existing config entries continue to resolve correctly.

**Task 4 — Gate extensions + conformance suite.** `check-storage-seam-no-path-leak.py` gains Pass 2 targeting the two routing files and seven named routing functions for `pathlib.Path` return annotations. `check-process-seam-import-direction.sh` gains an LC-8 block: scans `harness_memory.py` + `repo_registry.py` for any `import storage_vault` and fails loud. `storage_conformance.py` extended with `check_routing_repo_registry(make_backend)` proving the register/list/unregister cycle on any conforming backend; gated by `run_conformance(include_routing=False)` default with explicit opt-in.

**Task 5 — ADR 0019 + docs.** [ADR 0019](0019-v5-6-routing-plane-devaulting) created, recording the three-leg arc completion, locked design calls (LC-1/4/5/6/7/8), and load-bearing assumptions with re-audit triggers. `Decisions.md` and `decisions/_Sidebar.md` updated. `device-wide-architecture.md` v1.0 lifecycle entry updated (pending → 2026-06-18 complete). `Storage-Seam.md` routing layer NOTE updated to complete. `Single-Repo-State-Mode.md` `state_mode` value updated (`"vault"` → `"backend"` with backward-compat note).

### By the numbers

26 files changed (1005 insertions, 361 deletions); 20/20 `check-all.sh` PASS; 9 new/rewritten `TestRepoRegistry` tests + 3 `TestReadConfigStateMode` tests + 3 routing gate tests + 3 conformance routing tests.

### Related

- [ADR 0019 — V5-6 routing-plane de-vaulting](0019-v5-6-routing-plane-devaulting) — the three-leg arc, locked design calls, load-bearing assumptions.
- [Seam-De-Vaulting-V5-6](../explanation/Seam-De-Vaulting-V5-6) — intent, design, and full implementation trace.
- [Storage seam § Routing layer](Storage-Seam#routing-layer-v5-6) — quick-reference tables for the three de-vaulted mechanisms.
- [Single-repo state mode](../explanation/Single-Repo-State-Mode) — `state_mode: vault` alias callout.
- [v5.7.0](https://github.com/alexherrero/agentm/releases/tag/v5.7.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md).

---

## 2026-06-18 — v5.6.0: V5-7 — capability-request matching

**Commit range:** `v5.5.0..v5.6.0` (2 commits on `main`). Release notes: [v5.6.0](https://github.com/alexherrero/agentm/releases/tag/v5.6.0). Solo harness MINOR — no crickets pairing.

### What shipped

V5-7 closes the capability-read/match split introduced when V5-1 part 5 (task 2) shipped the capabilities-read. That task added the ability to *read* a selected backend's capabilities; V5-7 adds the ability to *declare requirements* and have selection enforce them.

**`CapabilityMismatchError`** — a new public error class in `backend_selection.py`, subclassing `StorageSelectionError`. Constructor `(protocol: str, unsatisfied: list[str])`; `str(e)` → `"backend '<protocol>' does not satisfy required capabilities: <field1>, <field2>"`. Added to `backend_selection.__all__` alongside `StorageSelectionError`.

**`select_backend(required=)`** — the existing resolver gains an optional `required: Capabilities | None = None` keyword parameter. After resolving the backend instance, `select_backend` iterates `dataclasses.fields(required)` and raises `CapabilityMismatchError` listing every `True` requirement the backend cannot satisfy. `required=None` (default) preserves all prior behavior — zero changes needed at existing call sites.

**`_doctor_main --requires <CAPS>`** — the operator-facing pre-flight surface for `required=`. Accepts a comma-separated list of `Capabilities` field names (e.g. `--requires concurrent_writers,encryption`). Unknown names print an error to stderr and exit 1 before any backend is constructed. Valid names construct `Capabilities(**{name: True for name in requested})`, call `select_backend(required=...)`, and print exactly one line: `PASS: backend '<protocol>' satisfies required capabilities: <caps>` (exit 0) or `FAIL: <CapabilityMismatchError message>` (exit 1). `StorageSelectionError` (unregistered backend, missing vault path, corrupt config) also prints `FAIL` and exits 1.

**Three injection params on `_doctor_main`** — `device_local_root`, `vault_lock_root`, and `vault_plugin_scripts` are forwarded to `select_backend` for test isolation, following the same pattern as `select_backend` itself.

### Why this shape

The subset-only matching decision — a mismatch is always an error, never a silent downgrade or reroute — is the same load-bearing principle as DC-4 in ADR 0013: a wrong-but-running engine (satisfying fewer capabilities than a caller needs) is worse than a stopped one. Alternatives — pick the next registered backend that satisfies the requirement, return a warning, allow partial satisfaction — were rejected because each is a form of silent demotion. The `--requires` flag extends DC-5's principle: the pre-flight surface and the runtime guard share the same code path and produce identical output. See [ADR 0013 amendment (2026-06-18)](0013-storage-seam-fail-loud-selection#amendment--2026-06-18-v5-7-capability-request-matching-extends-dc-4-with-the-same-fail-loud-principle) for the full rationale.

### By the numbers

13 new unit tests: `TestCapabilityMatching` (8 cases covering mismatch, single-cap satisfaction, multi-cap mismatch, multi-cap all-satisfied, partial mismatch, required=None backward compat, `CapabilityMismatchError` str format, superclass chain) + `TestDoctorRequires` (5 cases: unknown field, mismatch, satisfied cap with vault fixture, multiple unknown fields, no-flag backward compat). `check-all.sh` green.

### Related

- [ADR 0013 amendment — capability-request matching](0013-storage-seam-fail-loud-selection) — the design rationale for subset-only matching and why alternatives were rejected.
- [Storage seam](Storage-Seam) — `CapabilityMismatchError` reference entry; `select_backend` `required=` parameter; `_doctor_main --requires` flag; `TestCapabilityMatching` + `TestDoctorRequires`.
- [Choose a storage backend](Choose-A-Storage-Backend) — operator how-to: step 4 `--requires` pre-flight.
- [v5.6.0](https://github.com/alexherrero/agentm/releases/tag/v5.6.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md).

---

## 2026-06-18 — v5.5.0: V5-3: storage cutover — kernel → device-local

**Commit range:** `v5.4.0..v5.5.0` (7 commits). Release notes: [v5.5.0](https://github.com/alexherrero/agentm/releases/tag/v5.5.0). Solo harness MINOR — no crickets pairing.

V5-3 closes the engine-cutover deferral that [ADR 0013](0013-storage-seam-fail-loud-selection) DC-6 and [ADR 0011](0011-v5-unbundling-dev-loop) both named as "a separate, later step beyond V5-1." Until V5-3, `harness_memory.py` still read and wrote phase state the old way — `harness_state_dir` might return a vault path, `read_state_file` / `write_state_file` might route to `<vault>/_harness/`, `phase_recall` assembled recall bundles from vault markdown, and `resolve_documenter_context` read `projects/_global/wiki-style/` from the vault. V5-3 collapses all of that to device-local.

**Three coordinated changes:**

**Delete the built-in vault backend from the kernel** (`8cb2b48`). `harness_state_dir(resolution)` now always returns `<project_root>/.harness/`. `read_state_file` / `write_state_file` read and write only device-local files — the vault-routing tier and the one-warn-per-session-per-file deprecation path are deleted. `phase_recall` always returns `""` (V5-9 MCP now provides context at the process boundary). `resolve_documenter_context` always returns `None` (callers already handled `rc=1` gracefully). All five `test_harness_memory.py` tests that expected vault-routing behavior were updated in the same commit, honoring the V5-6 principle of relaxing by-name test expectations in lockstep with the kernel delete.

**Make `vault_path()` fail-loud guard load-bearing** (`05e63d3`). New `StorageBackendNotInstalledError` class in `harness_memory.py`. `vault_path()` raises it when `storage.backend=vault` is set in the install config but no vault path resolves — the guard fires at the choke point, on every call path, not only through `select_backend()`. The `$MEMORY_VAULT_PATH` env override remains a graceful-skip per-session escape hatch (DC-5 of ADR 0018): the guard fires on the durable config-file commitment only, not on a transient env override. Six `TestVaultPathGuard` tests pin the contract.

**`personal-private/` → `personal/` group rename** (`053217b`). The planned vault-area rename (ADR 0010 re-audit trigger #1) ships in lockstep: vault directory on-disk, `_ALWAYS_LOAD_REL` kernel constant, and all functional path references in the kernel + scripts tree — making the cutover atomic from the operator's perspective.

**By the numbers:** 1170 unit tests, 0 failures; 20/20 `check-all.sh`; +158 insertions / +908 deletions net (bulk of the deletions from removing vault-routing code and stale tests).

**Related:** [ADR 0018](0018-v5-3-storage-cutover) — the five-decision record for this cutover; ADR 0013 amendment — the two V5-3 negative-consequence bullets now marked resolved; ADR 0010 amendment — `personal-private` → `personal` rename noted.

---

## 2026-06-17 — v5.4.0: V5-9 — memory MCP server

**Commit range:** `v5.3.0..v5.4.0` (11 commits on `main`). Release notes: [v5.4.0](https://github.com/alexherrero/agentm/releases/tag/v5.4.0). Solo harness MINOR — no crickets pairing.

### What shipped

V5-9 wraps the memory engine as a local MCP server so every MCP-capable host on the machine can reach the vault through a single protocol surface.

**Server skeleton and transport.** `scripts/memory_mcp_server.py` — a FastMCP (`>=3,<4`) singleton daemon bound to `127.0.0.1:7821`. Streamable-HTTP transport; one daemon, N `Mcp-Session-Id` sessions. All writes from all MCP hosts funnel through one process — the Phase-1 concurrent-write broker the V5-0 write protocol was staged to receive. A liveness probe at `/health` returns `{"status": "ok"}`.

**Four MCP tools.** `memory_search` (semantic similarity, paginated), `memory_recall` (budgeted phase-bundle), `memory_append` (idempotent write), `memory_forget` (soft-delete — never unlinks the backing file; status flip propagates safely under Drive sync). Tool names are snake_case, no dots — OpenAI-family host compatibility. An in-memory test client covers contract tests offline.

**Writer routing.** `memory_recall` fails loud on an unconfigured source; source resolution goes through V5-1 `backend_selection` (vault-primary) — never a hardcoded repo allowlist. The daemon is writer #2 alongside the CLI as writer #1.

**Security.** `TokenVerifier` ASGI middleware (bearer token via `AGENTM_MCP_TOKEN` env var); `_OriginValidator` ASGI middleware (unexpected `Origin:` header → 403, DNS-rebinding defense); path-traversal validation engine-side.

**stdio shim.** `scripts/memory_mcp_stdio_shim.py` — proxies stdio transport to the HTTP daemon for hosts that require it (Claude Desktop). The daemon stays the sole write-broker; the shim is a thin compatibility adaptor.

**Operations.** `scripts/memory_mcp_doctor.py` — four health checks (`liveness`, `token_env`, `origin_guard`, `index_root_safe`), 13 unit tests. `scripts/com.agentm.memory-mcp-server.plist` — launchd template (`RunAtLoad` + `KeepAlive`; binds the daemon at login). `install.sh --mcp-server` flag registers the plist. Host config snippets ship for Claude Code, Cursor, Goose, and Claude Desktop.

**Two bug-fix commits in range.** `fix: exclude __pycache__ from lib-parity checksum` and `fix(V5-9): normalize lock-root path to POSIX before marker check` (Windows backslash compatibility).

### Why this shape

V5-0 built the write protocol as "Phase 1: single-writer CLI"; V5-9 is Phase 2: any MCP host is a second writer, and the singleton HTTP daemon is the broker that collapses that fan-out from N writers to one. DC-1 through DC-5 in [ADR 0017](0017-mcp-server-design) record the five load-bearing design calls: singleton streamable-HTTP over stdio (single-writer property), four snake_case tools (host compat), soft-delete as a hard acceptance criterion (synced-vault safety), loopback-first deferring the remote tier (homelab posture + scope), and FastMCP `>=3,<4` with the official SDK named as fallback (OOtB bearer auth + in-memory test client; cheap to swap).

### Related

- [ADR 0017 — MCP server design](0017-mcp-server-design) — five design decisions + load-bearing assumptions.
- [Memory MCP tools reference](Memory-MCP-Tools) — full parameter tables, pagination, soft-delete contract, error codes.
- [Stand up the memory MCP server](Stand-Up-Memory-MCP-Server) — operator how-to: install deps → set token → launchd → configure each host → verify.
- [ADR 0012 — Vault-write protocol](0012-vault-write-protocol) — V5-0; the write stack the daemon composes as writer #2.
- [v5.4.0](https://github.com/alexherrero/agentm/releases/tag/v5.4.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md).

## 2026-06-16 — v5.3.0: V5-11: team-coordinator persona (first real composed persona)

**Commit:** [`7966ac3`](https://github.com/alexherrero/agentm/commit/7966ac3) on `main`. Release notes: [v5.3.0](https://github.com/alexherrero/agentm/releases/tag/v5.3.0). Solo harness MINOR — no crickets pairing.

### What shipped

`personas/team-coordinator.md` — the first persona with real composition. `kind: persona`, `requires: [queue_status_lite]`, `enhances: [developer-workflows, github-projects]`. Passes `check-personas` (requires ⊆ substrate-native; no always-load).

Four stdlib-only scripts form the capability set — no model, no live vault:

- **`scripts/plan_graph.py`** — shared map engine. Reads `_harness/` (active plans + `queued-plans/` subdir) and returns a `list[PlanInfo]`. Each `PlanInfo` carries slug, filename, status, tasks done/total, last_touched (from progress log), `depends_on` (YAML frontmatter), `touches` (YAML frontmatter), active (bool).
- **`scripts/standup.py`** — derives `worker_state` per active plan: `building` (tasks remain, recent touch), `mergeable` (all tasks done), `idle` (no touch in >2h, tasks remain). Returns a `list[WorkerRow]`.
- **`scripts/readiness.py`** — two-stage: (1) dep readiness (queued plan ready when all `depends_on` done); (2) safe-to-run-together overlap check via `touches:` glob lists. Plans without `touches:` are loudly degraded — never silently included. Returns `{ready, safe_together, held_back, degrade_warnings}`.
- **`scripts/merge_order.py`** — topo-sort by `depends_on` edges + smallest-git-diff tie-break (`git diff --stat`) + alphabetical fallback when git unavailable. Cycle detection raises `ValueError`.

Four test files, 62 fixture-backed tests (no live vault):
`scripts/test_plan_graph.py`, `test_standup.py`, `test_readiness.py`, `test_merge_order.py`.

Fixture tree at `scripts/fixtures/plan_graph/_harness/` — three active plans + one queued, covering all frontmatter forms and plan states.

### Why this shape

V5-12 built the substrate (the persona primitive + gate); V5-11 is the first persona that actually composes. The team-coordinator is stateless — it owns no engine of its own, just four read-only scripts that map the `_harness/` state and answer three coordinator questions: who is building what (standup), what is safe to start next (readiness), and in what order to merge (merge-order). Hard-requiring `queue_status_lite` ties it to the existing agentm-native read-model; soft-enhancing `developer-workflows` and `github-projects` means it degrades cleanly on a bare install with no errors.

The `touches:` field — YAML frontmatter on plan files — is new optional metadata introduced by this commit. It is a glob list of file patterns the plan modifies, used by `readiness.py` to detect file-overlap conflicts between concurrently running plans. Plans without `touches:` are loudly flagged in `degrade_warnings` rather than silently assumed safe — the loud-degrade contract is the key safety property.

### Related

- [Persona tier schema](persona-tier-schema) — `kind: persona` manifest fields; `team-coordinator` example added (V5-11).
- [The persona tier design doc](persona-tier) — Part 4 completion recorded in Document History.
- [ADR 0016 — Persona tier](0016-persona-tier) — the design decisions; DC-1 defines the inverted-dependency rule `team-coordinator` satisfies.
- [Queue status lite](Queue-Status-Lite) — the `queue_status_lite` substrate dependency `team-coordinator` hard-requires.
- [Named plans](Named-Plans) — `depends_on` / `touches:` are plan-file frontmatter; named-plan substrate their reader relies on.

## 2026-06-16 — v5.2.0: capability-discovery resolver + persona tier

**Commit range:** `v5.1.0..v5.2.0` (6 commits on `main`). Release notes: [v5.2.0](https://github.com/alexherrero/agentm/releases/tag/v5.2.0). Solo harness MINOR — no crickets pairing.

### What shipped

Two roadmap arcs landed together:

**V5-8 — the `enhances:` runtime** ([ADR 0015](0015-capability-discovery)). The capability-discovery resolver is the runtime half of crickets' `enhances:` soft-composition — it answers "is this capability available on this host?" without importing plugin code.

- `scripts/capability_resolver.py` — plugin-capability registry + resolver. Aggregates installed plugins' declared `capabilities:` keys from two host state directories (Claude Code's `installed_plugins.json` / marketplace manifests; Antigravity's `import_manifest.json` / per-plugin `capabilities.json` sidecars). Public API: `capability_available(name, *, version=None) → bool` and `capability_resolve(name) → {available, provider, version, reason}`. Claude Code wins the merge when both hosts declare the same capability. 32 unit tests; all four reason codes (`available` / `no-provider` / `provider-not-installed` / `version-mismatch`).
- `scripts/capability_version_match.py` — stdlib-only single-range semver checker. `satisfies(installed_version, range_str) → bool`. Supports `>=`, `>`, `<=`, `<`, `==`, `!=`, `~=` (PEP 440 compatible-release). Gracefully returns `False` — never raises — for `None` versions, malformed strings, or compound specifiers (LC-3: single-check, not a solver). 40 unit tests.
- `scripts/agentm-capability` CLI shim (Task 3) — wires the resolver for shell callers; exit 0 (available) / 1 (unavailable) / 2 (usage error).
- CI gate: `check-capability-resolver-one-way` — enforces the one-way read contract (resolver never imports plugin code).

**V5-12 — the persona tier** ([ADR 0016](0016-persona-tier)). A third classification above the substrate/plugin binary: personas are curated `kind: persona` markdown manifests that describe a sustained mode of engagement with the agent. They live in `personas/` in the repo root, have `requires:` substrate-native deps, and may optionally list `enhances:` capabilities for soft-composition.

- `personas/rememberer.md` — the first persona manifest; demonstrates the schema and `requires:` / `enhances:` fields.
- `scripts/check-personas.py` — the `check-personas` CI gate; asserts `requires ⊆ substrate-native` (no hard-dep on crickets capabilities) and no `always_load: true` (personas are opt-in, never injected automatically).
- `scripts/test_check_personas.py` — unit tests for the gate.
- CI wired on Linux, macOS, and Windows.

### Why this shape

The two arcs are complementary: V5-8 delivers the runtime that `enhances:` promises require; V5-12 is the first consumer of the persona-primitive the roadmap reserved. Both are stdlib-only, both are gate-enforced, and both ship without crickets pairing because the contracts they fulfill are harness-substrate concerns — the crickets `enhances:` orchestration consumes V5-8 at the toolkit surface.

### Related

- [Capability resolver](Capability-Resolver) — full reference page for the V5-8 public API, reason codes, host read paths, merge semantics, and version-matching operator table.
- [Persona tier schema](persona-tier-schema) — the `kind: persona` manifest fields + `check-personas` gate reference (V5-12).
- [Soft-Composition](Soft-Composition) — explanation of the `enhances:` mechanism and how V5-12 personas compose with it.
- [ADR 0015 — Capability discovery](0015-capability-discovery) · [ADR 0016 — Persona tier](0016-persona-tier)
- [CI gates](CI-Gates) — `check-capability-resolver-one-way` + `check-personas`.
- [v5.2.0](https://github.com/alexherrero/agentm/releases/tag/v5.2.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md).

## 2026-06-13 — v5.0.1: V5-1 follow-on — non-UTF-8 config readers honor their contract (PATCH)

**Commit range:** `v5.0.0..v5.0.1` (2 commits on `main`: the fix `50cde80` + this release commit). Release notes: [v5.0.1](https://github.com/alexherrero/agentm/releases/tag/v5.0.1). Solo harness PATCH — no crickets pairing.

### What shipped

A correctness follow-on to the V5-1 storage seam (shipped in v5.0.0). The four config readers each document a contract: the selection resolver `backend_selection.py::_configured_backend` is **fail-loud** (raise `StorageSelectionError` when the config is present but unparseable), and the three best-effort readers (`harness_memory.py::_read_config_vault_path`, `harness_memory.py::_read_config_state_mode`, `agentm_config.py::_read_config`) are **graceful-skip** (return `None`). But each guarded only `(json.JSONDecodeError, OSError)`. A non-UTF-8 config file makes `Path.read_text(encoding="utf-8")` raise `UnicodeDecodeError` — a `ValueError`, which is neither an `OSError` nor a `json.JSONDecodeError` — so it leaked past every guard and crashed the caller instead of honoring the documented contract. All four readers now also catch `UnicodeDecodeError`; five regression tests pin the behavior (the fail-loud resolver raises, the three graceful readers return `None`, and the `doctor` storage preview reports `[FAIL]` rather than crashing).

### Why this shape

The realistic trigger is mundane: a Windows editor's UTF-16/BOM "Save As" (`\xff\xfe…`) on an otherwise-valid config. The fix is the minimal contract-honoring widening — catch the decode error at the same site and route it to the behavior the docstring already promises — not a broad `except Exception`. The fail-loud path stays loud (a corrupt config still refuses startup with `StorageSelectionError`, never a silent demotion to `device-local`, per [ADR 0013](0013-storage-seam-fail-loud-selection)); the graceful paths stay graceful. Surfaced by an adversarial review of the V5-1 part-5 selection code immediately after the v5.0.0 tag.

### Related

- [ADR 0013 — Storage-seam fail-loud selection](0013-storage-seam-fail-loud-selection) — the fail-loud contract this fix keeps intact.
- [Memory↔storage seam](Memory-Storage-Seam) · [Choose a storage backend](Choose-A-Storage-Backend) — the V5-1 surfaces the readers back.
- [v5.0.1](https://github.com/alexherrero/agentm/releases/tag/v5.0.1) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md).

## 2026-06-13 — v5.0.0: Agent M V5 — the memory↔storage seam + the dev-loop unbundling (MAJOR)

**Commit range:** `v4.15.0..v5.0.0` (76 commits on `main`). Release notes: [v5.0.0](https://github.com/alexherrero/agentm/releases/tag/v5.0.0). This is the first MAJOR since v4.0.0 — two BREAKING `refactor(harness)!:` commits drove the bump.

> [!NOTE]
> Each roadmap arc below had its own per-`/work` documenter passes during the wave, flipping its pages `pending → implemented` and filling `## Implementation` sections as it shipped. This entry is the **release-level reconciliation** — the cross-arc summary, the new storage-seam ADR, and the sidebar nav — not a re-document of every commit.

### What shipped

Five roadmap arcs converged into one MAJOR release. They group into the V5 substrate (V5-0, V5-1, V5-4, V5-10) and the unbundling-and-convergence breaking changes.

**V5-1 — the memory↔storage seam** (the headline; a five-part arc). The memory engine now reads and writes its markdown state through a small interface of verbs (`resolve` / `read` / `write` / `list` / `exists` / `info` / `mkdir`) that hand back an opaque `Locator` — **never** a `pathlib.Path` — so the engine carries no filesystem assumption and a backend can be swapped behind it. The no-`Path`-leak rule is enforced by an AST-based gate (`check-storage-seam-no-path-leak`). A named-backend registry resolves a backend by protocol name and reports a miss as **absence** (`get → None`), not an error. Two concrete backends register into it: a **device-local** plain-markdown backend (`~/.agentm/memory/`, the new zero-setup default) and the **vault wrap** (today's GDrive-synced Obsidian vault re-presented through the verbs, composing the V5-0 write stack, proven byte-conservative by a never-orphan invariant). A backend-agnostic conformance suite holds every backend to one objective contract on the cross-OS test step. Part 5 shipped **selection + fail-loud**: a `storage.backend` config key + a resolver (`scripts/backend_selection.py`) that picks device-local on a fresh install, vault when a `vault_path` exists, and a named plugin when configured — refusing loudly with `StorageSelectionError` (and **never** demoting to device-local) when the named backend's plugin is uninstalled, the `vault` is selected with no `vault_path`, or the config is corrupt. A `doctor` storage preview shares the resolver's code path so the pre-flight check can never drift from the runtime guard. The seam does **not** yet route the live engine — the engine cutover is a step beyond V5-1, and the derived index lands in V6.

**V5-0 — the vault-write protocol** (the concurrency floor underneath the vault backend). A per-vault advisory `vault_mutex` on a local non-synced path, a content-hash CAS (replacing the Drive-mangled mtime CAS), one canonical fsync'd `atomic_write`, and a conflict-janitor broadened to four Drive marker families plus the DriveFS `lost_and_found/` dump. The protocol is a focused `scripts/vault_lock.py`, vendored byte-identically into the `/memory` skill and guarded by a parity gate.

**V5-4 — the memory↔process seam** (the *other* seam, facing up). A read-only client (`scripts/process_seam.py`) through which a process talks to the engine, plus a one-way `check-process-seam-import-direction` gate enforcing that the edge points one way only.

**V5-10 — named plans** (the multi-plan substrate slice). A named-plan resolver + session→plan binding (`resolve_active_plan`, loud-error on a dangling marker), a `queue_status_lite` read-only multi-plan dashboard, named-plan-aware session-start hooks + `doctor`, a `check-multi-plan-naming` gate, and a `resolve-active-plan` CLI bridge so the crickets phase specs can target named plans without reimplementing precedence.

**Seven-section convergence + dev-loop slim** (the two BREAKING changes). The documentation taxonomy converged to seven sections (amending [ADR 0004](seven-section-convergence)). And the vendored dev loop left agentm: `migrate-to-diataxis`, the phase commands, the review sub-agents, and the vendored `documenter` / `diataxis-author` copies were **retired** — all now provided by crickets plugins (`developer-workflows`, `code-review`, `wiki-maintenance`) with graceful-skip when the plugins are absent. This is the unbundling that repositions agentm as "storage-agnostic memory OS + plugin host" rather than "vendored dev loop."

### Why this shape

V5's identity, recorded in [ADR 0011](0011-v5-unbundling-dev-loop), is that the memory engine is the thing only agentm provides; the dev loop is a *consumer* any host with the crickets plugins can supply. Retiring the byte-duplicated dev-loop copies removes a standing parity tax and drift risk. The seam work (V5-1) is what makes the engine *storage-agnostic*: a fresh install works with zero setup on device-local markdown, and the vault becomes one backing among several reached through an interface. The fail-loud-never-demote selection guard ([ADR 0013](0013-storage-seam-fail-loud-selection)) is the load-bearing safety call of that arc — a silent demotion to device-local is exactly what would mis-write or orphan an operator's live vault, so a misconfigured backend stops startup loudly rather than degrading quietly. The vault-write protocol ([ADR 0012](0012-vault-write-protocol)) is the concurrency floor the seam's vault backend composes; the two process/storage seams (V5-1 + V5-4) are the symmetric down-facing and up-facing boundaries of the engine.

### Doesn't do

- **Didn't cut the engine over to the seam** *(at v5.0.0)*. `recall` / `reflect` / `save` / `evolve` and the five memory hooks were byte-unchanged; they still accessed state the old way. Selection resolved *which* backend a config would use and refused a misconfiguration, but the live read/write path did not route through the seam. The engine cutover shipped in **V5-3 (v5.5.0)**: `harness_state_dir` / `read_state_file` / `write_state_file` / `phase_recall` / `resolve_documenter_context` now route exclusively to device-local. See [v5.5.0 entry above](#2026-06-18--v550-v5-3-storage-cutover--kernel--device-local).
- **Doesn't ship the V6 derived index.** The three-tier `source` / `shared-abstracts` / `local-index` taxonomy is *reserved* in the contract (the `Tier` / `TierLayout` / `DerivedMaintenance` types), but no index, no `reindex`/`changed_since` implementation, ships here.
- **Doesn't auto-merge vault conflicts.** The vault's `conflict_strategy = "whole-file"` is a *name* the selection layer reads, not a merger; the janitor surfaces conflict/base pairs to the operator and resolution stays human judgment.
- **Doesn't fully ship named plans.** `Named-Plans` stays `pending` (correctly): the readers, the `resolve_active_plan` binder, the naming gate, the named-plan-aware hooks + `doctor`, and the CLI bridge are all in `main`, and the crickets behavioral half (the `--name <slug>` flag across `/work`·`/plan`·`/review`) shipped 2026-06-12 — but the **`.harness/active-plan` marker *writer*** (the worktree-spawn helper) is not in this range, and the page documents that single remaining blocker as the reason it is not yet `implemented`.

### Tracked as

The five V5 roadmap arcs (V5-0 / V5-1 / V5-4 / V5-10 + the seven-section-convergence + dev-loop-slim breaking work). Three ADRs cover the wave: [ADR 0011](0011-v5-unbundling-dev-loop) (unbundling, 2026-06-11), [ADR 0012](0012-vault-write-protocol) (vault-write protocol, 2026-06-12), and [ADR 0013](0013-storage-seam-fail-loud-selection) (storage-seam fail-loud selection, added in this release sweep, 2026-06-13).

### Related

- [ADR 0013 — Storage-seam fail-loud selection](0013-storage-seam-fail-loud-selection) · [ADR 0012 — Vault-write protocol](0012-vault-write-protocol) · [ADR 0011 — V5 unbundling](0011-v5-unbundling-dev-loop)
- [Memory↔storage seam](Memory-Storage-Seam) · [Memory↔process seam](Memory-Process-Seam) · [Named plans](Named-Plans) — the explanation pages for the three seam/plan arcs.
- [Storage seam](Storage-Seam) · [Vault write protocol](Vault-Write-Protocol) · [Process seam](Process-Seam) · [Queue status lite](Queue-Status-Lite) — the reference pages.
- [Choose a storage backend](Choose-A-Storage-Backend) — the operator-facing how-to for `storage.backend` + the `doctor` preview.
- [CI gates](CI-Gates) — the two new seam gates (`check-storage-seam-no-path-leak`, `check-process-seam-import-direction`).
- [v5.0.0](https://github.com/alexherrero/agentm/releases/tag/v5.0.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md).

## 2026-05-22 — v2.4.3: diataxis-author skill (paired with toolkit v0.11.0)

**Commit range:** `v2.4.2..v2.4.3` (2 commits on `main`: predecessor deprecation `d4d4adf` + this release commit). Release notes: [v2.4.3](https://github.com/alexherrero/agentm/releases/tag/v2.4.3). Paired with [crickets v0.11.0](https://github.com/alexherrero/crickets/releases/tag/v0.11.0) (8 toolkit commits across plan #13 parts 1-5).

### What shipped

crickets's second major skill after `memory`: **`diataxis-author`** — author + maintain a Diátaxis-style wiki for any repo. Five sub-commands:

1. **`/diataxis author <slug>`** — live authoring guidance: mode classification + template selection + filename style; pre-fills `wiki/<mode-dir>/<filename>.md` skeleton.
2. **`/diataxis classify <file>`** — single-page mode classification (Tier-1 deterministic heuristic + Tier-2 `diataxis-evaluator` sub-agent for ambiguous cases).
3. **`/diataxis check [--strict]`** — drift detection (wraps `check-wiki.py` + adds 4 skill-side heuristics: mode-mixed, stale-cross-ref, template-shape-drift, convention-drift).
4. **`/diataxis repair`** — interactive fix-application; preview-first per finding; dispatches `documenter` sub-agent for mode-mixed splits.
5. **`/diataxis migrate`** — one-shot legacy → Diátaxis migration (subsumes harness's `migrate-to-diataxis` predecessor; same contract + `.diataxis-conventions.md` auto-seed + delegation to `/diataxis repair` for splits).

Plus: `diataxis-evaluator` read-only sub-agent (zero filesystem write scope); 4 Diátaxis templates; AgentMemory integration with 3-tier convention fallback (per-repo `.diataxis-conventions.md` > vault `_always-load/diataxis-*.md` > ADR 0004 defaults).

Substantive sweep happens entirely on toolkit side (8 toolkit commits + 1 harness commit). Harness side is doc-only this release pair: this Completed-Features row + narrative + paired CHANGELOG entry + the deprecation NOTE on `harness/skills/migrate-to-diataxis.md` (`d4d4adf`, shipped during plan part 4).

### Why this shape

The operator maintains three Diátaxis-shaped wikis (agentm + crickets + dev-setup) plus the just-shipped MemoryVault parent design. Diátaxis discipline was previously supported only by:
- `scripts/check-wiki.py` (strict validator; catches post-write)
- `documenter` sub-agent (sweep at `/release` only)
- `migrate-to-diataxis` predecessor (one-shot; never fires after migration)

The gap was live authoring guidance + ongoing drift detection + repair. The new skill ships that gap + subsumes the predecessor + repurposes `documenter` as the skill's mechanical-write worker (same orchestration + sub-agent pattern as `/memory adapt-skills` + `adapt-evaluator` from #7b task 4).

This is the **2nd real dogfood of plan #6's `/design` skill** (first was MemoryVault parent design closed 2026-05-20). Parent design lived through `/design author` → walk-sections → operator approval as final → `/design translate` (5 parts) → `/design sequence` (1 active + 4 queued PLAN.md files) → 5 parts executed in sequence → parent design transitions `final → launched` on this release.

The **paired-release-as-documentation pattern** continues from v2.4.0 + v2.4.1 + v2.4.2: substantive change lives on one side (toolkit); the other side (harness) ships a paired PATCH for version-cadence readability. v2.4.3 is the documentation-acknowledgement counterpart to v0.11.0; **4th consecutive paired-doc-only pair** — the pattern is now firmly established convention.

### Doesn't do

- Doesn't change harness phase specs. Future PATCH could amend `/release` to call `/diataxis check` when skill installed (graceful-skip otherwise) — deferred to keep v1 surface narrow.
- Doesn't remove `harness/skills/migrate-to-diataxis.md` — predecessor stays through v1 dogfood window. Removal in a follow-up harness PATCH after `/diataxis migrate` proves out.
- Doesn't auto-fork into `crickets/skills/` from sub-agent. `diataxis-evaluator` sub-agent has **zero filesystem write scope** (stricter than `adapt-evaluator`'s scoped-write-to-`_skill-watchlist/`).
- Doesn't ship hooks integration (SessionStart surface; UserPromptSubmit auto-dispatch). Deferred to a v2 task per parent design's §8 Hooks Integration.
- Doesn't ship cross-skill integration with ROADMAP items #19-#23 — sibling work; ships independently.

### Tracked as

2 harness commits in this release cycle (deprecation NOTE + this release commit). No new feature entries in `features.json` — the new skill is wholly a toolkit-internal capability + the harness-side change is doc-only.

### Related

- [crickets v0.11.0](https://github.com/alexherrero/crickets/releases/tag/v0.11.0) — paired release; 8 commits across plan #13 parts 1-5
- [crickets ADR 0008 — diataxis-author skill](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0008-diataxis-author.md) — 4 locked design calls + 4 load-bearing assumptions with re-audit triggers
- [crickets parent design — diataxis-author](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/diataxis-author.md) — full architectural context (Status: launched)
- [crickets how-to — Use Diataxis Author](https://github.com/alexherrero/crickets/blob/main/wiki/how-to/Use-Diataxis-Author.md) — 5 worked scenarios + AgentMemory walkthrough + troubleshooting
- [agentm ADR 0004 — Diátaxis Documentation Spec](seven-section-convergence) — canonical Diátaxis convention this skill enforces (upstream)
- [predecessor `migrate-to-diataxis` (deprecated)](https://github.com/alexherrero/agentm/blob/main/harness/skills/migrate-to-diataxis.md) — subsumed by `/diataxis migrate`
- [v2.4.3](https://github.com/alexherrero/agentm/releases/tag/v2.4.3) — release notes; [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)

## 2026-05-22 — v2.4.2: MemoryVault Discovery + Mining (paired with toolkit v0.10.0)

**Commit range:** `v2.4.1..v2.4.2` (1 commit on `main`). Release notes: [v2.4.2](https://github.com/alexherrero/agentm/releases/tag/v2.4.2). Paired with [crickets v0.10.0](https://github.com/alexherrero/crickets/releases/tag/v0.10.0) (8 toolkit commits across plan #7b tasks 1-7).

### What shipped

crickets's memory skill gained **five new sub-commands** that turn the vault from a static curated store into a living surface:

1. **`/memory index-skills`** — auto-indexer walks `SKILL.md` files across configured skill paths; writes one `kind: skill-pointer` entry per skill to `<vault>/personal-skills/<repo>/<skill-name>.md`. Auto-fires from `bash crickets/install.sh` against toolkit's own `skills/` + sibling `agentm/.claude/skills/`. Idempotent: unchanged entries skip.
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
- **Gating adoption** through a deterministic Python rubric + LLM sub-agent judgment with adapt-don't-import architecturally enforced — agents physically cannot fork into `crickets/skills/`; only the operator's manual authoring step graduates a pattern to a real skill

The **two-pass adapt-don't-import architecture** (deterministic Python Pass 1 + LLM sub-agent Pass 2) is the load-bearing design call. Pure rubric is testable + fast but semantically blind; pure LLM is sharp but expensive + non-deterministic. Pass 1 narrows the surface (drops LOW outright; gates enrichment + sub-agent dispatch); Pass 2 makes the final call. Operators can inspect Pass 1's JSON output to verify the rubric is scoring sensibly before paying the LLM cost.

**Stdlib-only Python pipelines** (no new third-party deps) align with plan #18's local-first design. GitHub API access via unauthenticated `urllib.request` with graceful-skip on 60/hr rate limit.

The **paired-release-as-documentation pattern** continues from v2.4.0 + v2.4.1: substantive change lives entirely on toolkit side; harness still ships a paired PATCH for version-cadence readability.

### Doesn't do

- **Doesn't auto-fork into `crickets/skills/`** — architectural rule enforced by the `adapt-evaluator` sub-agent's tool allowlist (Write scoped to `_skill-watchlist/<source-slug>/<pattern-slug>.md` only). The operator's manual authoring is the only path to a real skill.
- **Doesn't auto-dispatch the Pass 2 sub-agent** — operator invokes manually via the `/memory adapt-skills` skill body. Pass 1 (JSON enrichment) is automatic; Pass 2 (judgment + watchlist write) is operator-gated for v1. Future task may add idle-hook auto-dispatch with `--limit N`.
- **Doesn't change harness phase specs** (no discovery-mining logic on harness side — wholly a toolkit-side concern via the memory skill).
- **Doesn't change adapter wrappers** (canonical-reference inheritance — adapters point at phase specs which are untouched).
- **Doesn't ship a new harness ADR** (the discovery + mining decision lives in toolkit-side ADR 0007).
- **Doesn't process HTML sources** — `discover_skills.py` is markdown-only; HTML pages get cached as raw HTML, diff still works but downstream candidate parsing assumes markdown structure.
- **Doesn't ship the `/memory search` body** — that stub remains for a future task to wire `recall.py query` into the skill UX.
- **Doesn't ship cross-surface AgentMemory protocol** — that's [ROADMAP item #22](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) (queued post-#7b).

### Tracked as

Single-commit harness release. No new feature entries in `features.json` (discovery + mining isn't a net-new user-visible feature on harness side — it's a toolkit-internal architectural extension).

### Related

- [crickets v0.10.0](https://github.com/alexherrero/crickets/releases/tag/v0.10.0) — paired release; 8 commits across plan #7b tasks 1-7
- [crickets ADR 0007 — MemoryVault Discovery + Mining](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0007-memoryvault-discovery.md) — 7 locked design calls + 4 load-bearing assumptions with re-audit triggers
- [crickets MemoryVault design doc](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/memoryvault.md) — Document History row 11 captures the discovery + mining additive layer
- [`adapt-evaluator` sub-agent](https://github.com/alexherrero/agentm/blob/main/harness/agents/adapt-evaluator.md) — read-only Pass 2 worker with write allowlist scoped to `_skill-watchlist/` (shipped in crickets; moved to agentm in V4 #23)
- [Use-The-Memory-Skill how-to](https://github.com/alexherrero/crickets/blob/main/wiki/how-to/Use-The-Memory-Skill.md) — `## Discovery + mining (plan #7b)` section with worked invocations for all 5 new sub-commands
- [ROADMAP item #7b](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) — the roadmap entry closed by this release (MemoryVault parent design now fully shipped — both #7a + #7b are Completed)
- [v2.4.2](https://github.com/alexherrero/agentm/releases/tag/v2.4.2) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)

## 2026-05-20 — v2.4.1: Local-only embeddings (paired with toolkit v0.9.2)

**Commit range:** `v2.4.0..v2.4.1` (1 commit on `main`). Release notes: [v2.4.1](https://github.com/alexherrero/agentm/releases/tag/v2.4.1). Paired with [crickets v0.9.2](https://github.com/alexherrero/crickets/releases/tag/v0.9.2) (8 toolkit commits across plan #18 tasks 1-7).

### What shipped

crickets's memory skill collapsed from dual-mode (Voyage/Anthropic API + local sentence-transformers fallback) to **local-only sentence-transformers**. Default model upgraded `all-MiniLM-L6-v2` (384-d, MTEB English 56.3) → `BAAI/bge-large-en-v1.5` (1024-d, MTEB English 64.2). `EMBEDDING_DIM` bumped 384 → 1024. New `AGENT_TOOLKIT_EMBEDDING_MODEL` env var as escape hatch for low-spec hosts (still local — no API option). All `VOYAGE_API_KEY` / `ANTHROPIC_API_KEY` env var reads removed; `MEMORY_USE_API_EMBEDDINGS` env var no longer consulted. `sentence-transformers` becomes a hard install dep (was optional fallback) — `install.sh` + `install.ps1` pip-install it by default from new `requirements.txt`; opt-out via `--no-python-deps`. `vec_index.py` gained a `rebuild` subcommand + dim-mismatch detection for migrating existing 384-d indexes. Substantive sweep happens entirely on the toolkit side (8 toolkit commits across plan #18 tasks 1-7: embed.py refactor + smoke install fixups + vec_index.py rebuild + local-mode integration test + install scripts + ADR amendment + docs rewrite + paired release). Harness side is doc-only: this Completed-Features row + narrative + the paired CHANGELOG v2.4.1 entry. No harness phase spec changes, no adapter changes, no new harness ADR.

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
- Doesn't ship Antigravity 2.0 / Antigravity CLI host support — that's [ROADMAP item #17](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) (queued post-#7a).

### Tracked as

Single-commit harness release. No new feature entries in `features.json` (embedding-mode collapse isn't a net-new user-visible feature on harness side — it's a toolkit-internal architectural narrowing).

### Related

- [crickets v0.9.2](https://github.com/alexherrero/crickets/releases/tag/v0.9.2) — paired release; 8 commits across plan #18 tasks 1-7
- [crickets ADR 0001's 2026-05-20 amendment](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0001-crickets-purpose.md#amendment-2026-05-20) — full decision rationale + 4 load-bearing assumptions with re-audit triggers
- [crickets MemoryVault design doc](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/memoryvault.md) — design doc body rewritten in-place across 12 substantive references; Document History row 10 captures the rewrite
- [ROADMAP item #18](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) — the roadmap entry inserted mid-flight 2026-05-20 that triggered this plan
- [v2.4.1](https://github.com/alexherrero/agentm/releases/tag/v2.4.1) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)

## 2026-05-17 — v2.4.0: Gemini-CLI host removal (paired with toolkit v0.9.0)

**Commit range:** `v2.3.1..v2.4.0` (1 commit on `main`). Release notes: [v2.4.0](https://github.com/alexherrero/agentm/releases/tag/v2.4.0). Paired with [crickets v0.9.0](https://github.com/alexherrero/crickets/releases/tag/v0.9.0) (5 toolkit commits across plan #15 tasks 1-5).

### What shipped

Standalone Gemini CLI dropped from supported hosts across the personal-dev-env. Antigravity (Gemini-in-IDE) stays — different surface than standalone CLI. Substantive customization sweep happens entirely on the toolkit side (5 toolkit commits across plan #15 tasks 1-5: installer dispatch + manifests + validator/tests + wiki/ADRs + MemoryVault design docs). Harness side is doc-only: this Completed-Features row + narrative + the paired CHANGELOG v2.4.0 entry. No harness phase spec changes, no adapter changes, no new harness ADR.

### Why this shape

In practice the operator (one person) runs Claude Code + Antigravity. Standalone Gemini CLI was added defensively in v0.1.0 of the toolkit (then `agentm` v1.0.0 era — the original three-adapter scope) but never grew into the workflow; the Gemini usage that does happen lives inside Antigravity's IDE-level integration. Maintaining a third host destination, dispatch arms in `install.{sh,ps1}`, a third column in `Per-Host-Paths.md`, three case-arms in every dispatch function, and three branches in tests was carrying maintenance cost without observed payoff. Plan #7a part 1 (memory skill scaffold, shipped 2026-05-16) was the first new skill that opted out of the three-host scope from day 1; plan #15 (executed 2026-05-16/17) sweeps the rest of the toolkit to match.

The split between toolkit (substantive customization sweep) and harness (doc-only paired release) follows the customization-vs-phase pattern from agentm ADR 0006: customizations live in toolkit; phase-gated workflow lives in harness; the two integrate via shared template + shared workflow contract. The host-scope decision is entirely a toolkit-side concern; harness inherits via its dependency on toolkit customizations.

The **paired-release-as-documentation pattern** (new in v2.4.0): when the substantive change lives entirely on one side of the toolkit/harness split, the other side still ships a paired release with framing-only content. This keeps the two repos' version cadences readable for operators tracking changes — they don't have to wonder "why did toolkit ship a MINOR but harness didn't?". v2.4.0 is the documentation-acknowledgement counterpart to v0.9.0.

### Doesn't do

- Doesn't change harness phase specs (no host-related conditionals to update — phases don't reference specific hosts by name).
- Doesn't change adapter wrappers (canonical-reference inheritance — adapters point at phase specs which are themselves untouched).
- Doesn't change scripts, validators, or tests on harness side (no harness-side customizations to validate since the v2.0.0 split).
- Doesn't ship a new harness ADR (the host-scope decision lives in toolkit-side ADR 0006).
- Doesn't auto-clean up legacy `.agents/skills/` on harness installs — that's toolkit-side `install.sh` behavior, gated by `--no-legacy-cleanup` flag.

### Tracked as

Single-commit harness release. No new feature entries in `features.json` (host-scope reduction isn't a net-new user-visible feature — it's a removal). The legacy-cleanup-prompt operator UX is documented in [toolkit-side Installer-CLI reference](https://github.com/alexherrero/crickets/blob/main/wiki/reference/Installer-CLI.md) + ADR 0006 + CHANGELOG v0.9.0.

### Related

- [crickets v0.9.0](https://github.com/alexherrero/crickets/releases/tag/v0.9.0) — paired release; 5 commits doing the substantive sweep
- [crickets ADR 0006 — Gemini-CLI host removal](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0006-gemini-cli-host-removal.md) — full decision rationale + 4 load-bearing assumptions
- [crickets ADR 0001 + 0002 amendments (2026-05-17)](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0001-crickets-purpose.md#amendment-2026-05-17) — preserve original text + audit trail; same pattern as ADR 0004's 2026-05-16 amendment
- [ROADMAP item #15](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) — the roadmap entry that triggered this plan (added 2026-05-16 during plan #7a part 1 task 1 ship)
- [v2.4.0](https://github.com/alexherrero/agentm/releases/tag/v2.4.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)

## 2026-05-16 — v2.3.1: `/plan` external-review-handoff option (paired with toolkit v0.8.1)

**Commit range:** `v2.3.0..v2.3.1` (1 commit on `main`). Release notes: [v2.3.1](https://github.com/alexherrero/agentm/releases/tag/v2.3.1). Paired with [crickets v0.8.1](https://github.com/alexherrero/crickets/releases/tag/v0.8.1).

### What shipped

Adds the external-review-handoff option to the harness's `/plan` phase. After the agent drafts `.harness/PLAN.md` (per the existing flow), the operator now has an alternative to inline iteration: hand off the drafted PLAN.md to Antigravity IDE for inline-comment review + Gemini-applies-comments revision, then resume in Claude Code with a diff-on-resume pass against a pre-handoff snapshot.

The harness writes a pre-handoff snapshot at `.harness/PLAN.pre-handoff-<ts>.md`, generates a transfer-context file at `.harness/transfer/plan-<ts>.md` (using the design skill's template at `harness/skills/design/templates/transfer-context.md` post-v4.0.0 — pre-v4.0.0 the template lived at `crickets/skills/design/templates/transfer-context.md` per the V4 #36 reorg; `DOC_TYPE: plan` triggers harness-PLAN.md-specific guardrails), outputs a handoff prompt with explicit Antigravity steps, and pauses the phase. On resume (`/plan --resume-external-review` or natural "plan review complete"), the harness diffs the revised PLAN.md against the snapshot, reads Gemini's change-summary log at `.harness/PLAN.diff.md`, surfaces findings, asks Accept / Iterate / Discard.

### Why this shape

Dogfood-driven amendment from plan #6's first real design exercise (MemoryVault, 2026-05-15). The 6-chunk inline walk of a ~7200-word design surfaced a real UX gap: Claude Code's block-by-block review pattern works but tires fast on long content. Antigravity IDE has a native inline-comment UI + Gemini AI integration that handles bulk-apply of comments dramatically better.

The harness `/plan` phase faces the same pattern when drafting plans for substantial scope (the MemoryVault-style plans with many tasks + thick locked-design-calls sections). Mirroring the option from toolkit `/design` skill v0.8.1 means operators have the same workflow available in both surfaces with the same mechanics — shared transfer-context template, shared workflow shape, shared cleanup discipline. One mental model, two skill surfaces.

The split between toolkit (template + design-skill option) and harness (`/plan` option) follows the customization-vs-phase pattern from agentm ADR 0006: customizations live in toolkit; phase-gated workflow lives in harness; the two integrate via shared template + shared workflow contract.

### Doesn't do

- Doesn't change adapter wrappers (canonical-reference inheritance — adapters point at phase specs).
- Doesn't require any script changes, manifest changes, or installer changes. Implementation lives entirely in phase spec documentation; the agent executes the documented flow.
- Doesn't change the inline-review path. The inline iteration path stays default; external-review is opt-in per session.
- Doesn't ship cleanup automation for `.harness/transfer/` — that's manual until MemoryVault's idle-time hook (plan #7a) lands, at which point the same 30-day GC pass handles transfer artifacts alongside crash-recovery markers.

### Tracked as

This patch ships a single new option in the `/plan` phase; not flagged in `features.json` (features.json tracks net-new user-visible features; this is an alternative path for an existing feature).

### Related

- [crickets v0.8.1](https://github.com/alexherrero/crickets/releases/tag/v0.8.1) — paired release; ships the same option in `/design author` + `/design translate` + the transfer-context template
- [crickets ADR 0004 amendment (2026-05-16)](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0004-design-skill.md#amendment--2026-05-16-v081-external-review-handoff-option) — shared design rationale + 4 load-bearing assumptions with re-audit triggers
- [v2.3.1](https://github.com/alexherrero/agentm/releases/tag/v2.3.1) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)

## 2026-05-15 — v2.3.0: `/release` + `/setup` integration for crickets's `/design` skill

**Commit range:** `v2.2.0..v2.3.0` (2 commits on `main`). Release notes: [v2.3.0](https://github.com/alexherrero/agentm/releases/tag/v2.3.0). Paired with [crickets v0.8.0](https://github.com/alexherrero/crickets/releases/tag/v0.8.0).

**What shipped:**

- **`/release` §1b "Design-doc lifecycle check (crickets)"** — new section between §1 (Verify plan completion) and §2 (Re-run gates). Three cases: (A) silent no-op when plan isn't design-sourced; (B) archive completed plan + promote next queued plan + halt /release when more parts remain in the design; (C) archive + transition parent design Status `final → launched` + update `wiki/Home.md` + `_Sidebar.md` to surface launched published designs + continue release flow when this was the LAST queued part. Graceful-skip silent no-op when no design-doc origin signal present.
- **`/setup` §7 (Populate the wiki scaffold)** extended with `wiki/explanation/designs/` landing dir bullet — cross-refs the crickets `/design` skill how-to + the §1b /release lifecycle.
- **`templates/wiki/explanation/designs/`** — NEW scaffold dir installed by `install.sh`'s per-file walk. Contents: `.gitkeep` + `README.md` (one-paragraph explanation of visibility routing rules, Status lifecycle, wiki surfacing trigger, toolkit dependency).
- **`scripts/check-references.py`** — `EXTERNAL_CUSTOMIZATIONS` extended with `design` entry (currently forward-compatibility documentation because phase specs use slash-command phrasing `` `/design` `` which doesn't match `INVOKE_SKILL_RE` — inline comment captures this honestly).
- **`/work` Step 11 summary template enhancement** for ROADMAP-driven multi-plan projects — opt-in via the `.harness/ROADMAP.md` signal. Adds roadmap context lead-in + ✅/⬜ chart + link block to `.harness/` state files + explicit handoff phrase. Universal applicability — any harness install with a roadmap benefits.

**Why it shipped this shape:**

The harness needed integration hooks for the crickets `/design` skill's per-part PLAN.md workflow. The toolkit-side skill writes PLAN.md files to `.harness/` (active + queued); without the harness lifecycle hook, operators would have to manually promote queued plans after each part's `Status: done` completion. v2.3.0's §1b automates that promotion + handles the design `final → launched` transition + wiki surfacing for published designs.

The split between toolkit (skill body) and harness (lifecycle integration) follows the customization-vs-phase pattern from agentm ADR 0006: customizations live in toolkit; phase-gated workflow lives in harness; the two integrate via well-defined hand-off points. v2.3.0 ships the harness-side hand-off; v0.8.0 of the toolkit ships the skill that writes to it.

The `/work` Step 11 enhancement came out of the dev-flow codification work (separate from plan #6 but shipping in the same release window). It's the universal good — opt-in via `.harness/ROADMAP.md` signal so single-plan installs stay minimal; multi-plan projects get the navigation aids that match their scale.

**What it doesn't do:**

- Doesn't ship the `/design` skill itself. That lives in [crickets v0.8.0](https://github.com/alexherrero/crickets/releases/tag/v0.8.0).
- Doesn't auto-detect orphan queued plans without `parent_design_doc:` frontmatter. The §1b detection signal is the frontmatter field; hand-authored plans without it skip §1b entirely.
- Doesn't surface confidential designs in the wiki. The visibility check is hard — `confidential` designs at `.harness/designs/` never appear in `wiki/Home.md` even at `launched` Status.
- Doesn't add a new harness ADR. The design decision lives in [crickets ADR 0004](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0004-design-skill.md) since the customization itself lives there.

**Tracked as:**

- Task 5 of plan #6 (design skill v1) in `.harness/PLAN.md`. Plan #6 is a 7-task project spanning both repos: tasks 1-4 + 6 in `crickets` (template + 3 sub-command bodies + docs); task 5 in harness (this release); task 7 is the coordinated release pair.
- [v2.3.0](https://github.com/alexherrero/agentm/releases/tag/v2.3.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)
- Paired release: [crickets v0.8.0](https://github.com/alexherrero/crickets/releases/tag/v0.8.0)

**Related pages:**

- [crickets ADR 0004 — Design skill design](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0004-design-skill.md)
- [crickets how-to: Use-The-Design-Skill](https://github.com/alexherrero/crickets/blob/main/wiki/how-to/Use-The-Design-Skill.md) — three worked scenarios end-to-end
- [crickets /design skill spec](https://github.com/alexherrero/crickets/blob/main/skills/design/SKILL.md)
- [crickets 10-section design-doc template](https://github.com/alexherrero/crickets/blob/main/skills/design/templates/design-doc.md)

## 2026-05-14 — v2.2.0: `/work` + `/release` augmentable with crickets's base hooks

**Commit range:** `v2.1.0..v2.2.0` (1 commit on `main`). Release notes: [v2.2.0](https://github.com/alexherrero/agentm/releases/tag/v2.2.0). Paired with [crickets v0.7.0](https://github.com/alexherrero/crickets/releases/tag/v0.7.0).

**What shipped:**

- **New section in `/work` phase spec** (`harness/phases/03-work.md`): "Long-running `/work` — operator-control hooks (crickets)". 20-line section between "When to invoke /review" and "Failure modes to avoid". Reference table for all three hooks ([kill-switch](https://github.com/alexherrero/crickets/blob/main/hooks/kill-switch/hook.md) / [steer](https://github.com/alexherrero/crickets/blob/main/hooks/steer/hook.md) / [commit-on-stop](https://github.com/alexherrero/crickets/blob/main/hooks/commit-on-stop/hook.md)); when-they-earn-their-keep framing (runaway loop / mid-task redirect / crashed session); alphabetical-ordering invariant (kill-switch fires before steer in PreToolUse); graceful-skip framing.
- **New section in `/release` phase spec** (`harness/phases/05-release.md`): "Optional: `commit-on-stop` safety net (crickets)". Shorter 4-line section focused on commit-on-stop as the backstop for interrupted release flows (mid-CHANGELOG-edit, mid-tag-prep); cross-refs the `/work` section for the full hook lineup.
- **`scripts/check-references.py` `EXTERNAL_CUSTOMIZATIONS` extended** with `kill-switch`, `steer`, `commit-on-stop`. Inline-commented as forward-compatibility documentation — the existing regexes don't currently match hook phrasings.
- **No adapter edits.** All six `/work` + `/release` wrappers (3 hosts × 2 phases) reference the canonical specs at `harness/phases/0{3,5}-*.md` exactly once; the new sections inherit via the existing canonical-reference pattern.

**Why it shipped this shape:**

The three base hooks lifted from cwc-long-running-agents give the operator precision that didn't exist before:

- **Runaway loop**: today, the only way to halt is closing the session. `touch .harness/STOP` is precise — the next `PreToolUse` blocks the tool call without ending the session.
- **Mid-run redirect**: today, the only way to redirect is interrupt-and-restart. Writing `.harness/STEER.md` injects the redirect into the next tool call's context; file is renamed to `.harness/STEER.consumed-<ts>.md` for audit trail.
- **Crash recovery**: today, a crashed session loses uncommitted work. `commit-on-stop` fires on Claude Code's `Stop` event (turn-end) and saves dirty trees to `auto-save/<ts>` branches automatically. Recovery via `git checkout`.

`/work` is the primary consumer because long-running iteration loops, mid-task redirects, and crashed sessions all happen there. `/release` benefits less (the release flow is typically short) but the commit-on-stop backstop reduces the cost of an interrupted CHANGELOG edit or tag prep.

Splitting the hooks into crickets (not bolting them onto the harness) keeps the harness phase-shaped: the harness owns the phase workflow + canonical sub-agents + setup-specific skills; crickets owns customizations that ride on top. Anyone (harness user or not) can install the toolkit on top to get the precision layer. Graceful-skip framing on both new sections means a harness without the toolkit installed still satisfies the phase contracts.

**What it doesn't do:**

- Doesn't auto-dispatch the hooks from any phase. The toolkit's installer registers them in Claude Code's settings.json; once installed, they fire on every relevant event. Phase specs document the convention; the firing is host-level, not phase-level.
- Doesn't add a new harness ADR. The design decisions (per-repo file location, audit-trail rename, safety-branch not current-branch, Stop-event-only for v0.7.0, alphabetical ordering, claude-code-only scope, Python helper for settings merge) live in [crickets ADR 0003](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0003-base-operator-hooks.md) since the customizations live there.
- Doesn't require the toolkit. Without `crickets` installed, both new sections graceful-skip silently and the existing flows continue to satisfy the phase contracts.
- Doesn't ship hooks for Antigravity or Gemini CLI. Both lack first-class hook surfaces today. Manual equivalents (always-on rules / operator prompts that check the trigger files between steps) are documented in crickets's how-to but no scripts ship for them.

**Tracked as:**

- Task 3 of plan #4 (base operator-control hooks) in `.harness/PLAN.md`. Plan #4 is a 5-task project spanning both repos: tasks 1, 2, 4 in `crickets` (installer + bodies + docs); task 3 in harness (this release); task 5 is the paired release.
- [v2.2.0](https://github.com/alexherrero/agentm/releases/tag/v2.2.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)
- Paired release: [crickets v0.7.0](https://github.com/alexherrero/crickets/releases/tag/v0.7.0).

**Related pages:**

- [crickets ADR 0003 — base operator-control hooks](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0003-base-operator-hooks.md)
- [crickets how-to: Use-The-Base-Hooks](https://github.com/alexherrero/crickets/blob/main/wiki/how-to/Use-The-Base-Hooks.md)
- [kill-switch hook spec](https://github.com/alexherrero/crickets/blob/main/hooks/kill-switch/hook.md)
- [steer hook spec](https://github.com/alexherrero/crickets/blob/main/hooks/steer/hook.md)
- [commit-on-stop hook spec](https://github.com/alexherrero/crickets/blob/main/hooks/commit-on-stop/hook.md)

## 2026-05-13 — v2.1.0: `/review` augmentable with crickets's `evaluator`

**Commit range:** `v2.0.0..v2.1.0` (1 commit on `main`). Release notes: [v2.1.0](https://github.com/alexherrero/agentm/releases/tag/v2.1.0). Paired with [crickets v0.6.0](https://github.com/alexherrero/crickets/releases/tag/v0.6.0).

**What shipped:**

- **New §3b in `/review` phase spec** (`harness/phases/04-review.md`) — "Optional: evaluator augmentation (crickets)". Documents how to dispatch the [`evaluator`](https://github.com/alexherrero/crickets/blob/main/agents/evaluator.md) sub-agent alongside the existing `adversarial-reviewer` flow. Covers when to add evaluator dispatch (PLAN.md Verification clause is a numbered list of falsifiable claims), when to skip (vague rubric or toolkit not installed — graceful-skip), the `ARTIFACT:` + `RUBRIC:` dispatch prompt shape, the `PASS` / `NEEDS_WORK` output shape with per-rubric-item PASS/FAIL + final Verdict, treat-as-finding semantics (NEEDS_WORK counts as the executable exit artifact the phase requires), and a comparison table laying out the complementary framings.
- **Cross-repo agent references resolve** (`scripts/check-references.py`). Renamed `EXTERNAL_SKILLS` → `EXTERNAL_CUSTOMIZATIONS`; added `evaluator` to the set; the exclusion now applies to both `DISPATCH_AGENT_RE` and `INVOKE_SKILL_RE` regexes. Inline comments name each entry's `crickets` home (`skills/dependabot-fixer/`, `skills/ship-release/`, `agents/evaluator.md`).
- **No adapter edits.** All three review adapter wrappers (claude-code/commands, antigravity/workflows, gemini/commands) already reference `harness/phases/04-review.md` exactly once, so §3b inherits via the existing canonical-reference pattern without per-adapter changes.

**Why it shipped this shape:**

The adversarial-reviewer has framed `/review` since v0.8.0: "the code under review likely contains bugs, find them." That framing works well for defect surfacing but doesn't give a binary verdict against an explicit rubric. When the `PLAN.md` task's Verification clause is precise — a numbered list of falsifiable claims, which is the typical shape — the natural verification is "did the diff satisfy claims 1–5?" not "are there bugs?" The evaluator adds that binary-judgment surface without disturbing the existing flow: it coexists, not replaces. Consumers needing precise grading dispatch the evaluator; consumers needing defect surfacing dispatch the adversarial-reviewer; both useful at the same time.

Splitting the evaluator into crickets (rather than adding `harness/agents/evaluator.md`) keeps the harness phase-shaped: the harness owns the phase workflow + canonical sub-agents needed by every adapter; crickets owns customizations that ride on top. Anyone (harness user or not) can dispatch the evaluator from any consumer context. The future design skill (#6), quality-gates bundle (#10), and ContextVault (#7) all consume the evaluator for per-step grading.

**What it doesn't do:**

- Doesn't replace `adversarial-reviewer`. Both shipped; `/review` documents the choice.
- Doesn't auto-dispatch the evaluator from `/review`. Explicit dispatch stays the caller's call; the §3b section is a documented option, not a default flow change.
- Doesn't require the toolkit. Without `crickets` installed, §3b graceful-skips silently and the adversarial-reviewer-only flow continues to satisfy the phase contract.
- Doesn't add a new harness ADR. The design decision (read-only allowlist, caller-supplied inline rubric, coexist not replace, structured output) lives in [crickets ADR 0002](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0002-evaluator-design.md) since the customization itself lives there.

**Tracked as:**

- Task 3 of plan #3 (fresh-context evaluator) in `.harness/PLAN.md`. Plan #3 is a 5-task project spanning both repos: tasks 1, 2, 4 land in `crickets` (installer + body + docs); task 3 is the harness-side wiring (this release); task 5 is the coordinated release pair (this release + crickets v0.6.0).
- [v2.1.0](https://github.com/alexherrero/agentm/releases/tag/v2.1.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)
- Paired release: [crickets v0.6.0](https://github.com/alexherrero/crickets/releases/tag/v0.6.0).

**Related pages:**

- [crickets ADR 0002 — evaluator design](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0002-evaluator-design.md)
- [crickets how-to: Use-The-Evaluator](https://github.com/alexherrero/crickets/blob/main/wiki/how-to/Use-The-Evaluator.md)
- [crickets agent spec: evaluator.md](https://github.com/alexherrero/crickets/blob/main/agents/evaluator.md)

## 2026-05-12 — v2.0.0: `crickets` repo split; `dependabot-fixer` + `ship-release` migrated

**Commit range:** `v1.0.0..v2.0.0` (9 commits on `main`). Release notes: [v2.0.0](https://github.com/alexherrero/agentm/releases/tag/v2.0.0). ADR: [0006 — crickets split](0006-crickets-split). Paired with [crickets v0.5.0](https://github.com/alexherrero/crickets/releases/tag/v0.5.0).

**What shipped:**

- **Sibling repo split.** The new [`crickets`](https://github.com/alexherrero/crickets) repo holds personal agent customizations (skills, sub-agents, hooks, MCP servers, slash commands, bundles, etc. — 11 primitive types) that ride on top of the harness's phase-gated workflow. `dependabot-fixer` and `ship-release` migrated from this repo to the toolkit (both were host-cross-cutting with no harness shape). `doctor` and `migrate-to-diataxis` stay in this repo — `doctor` is harness-setup-specific and `migrate-to-diataxis` is harness-shaped (encodes [ADR 0004](seven-section-convergence)).
- **Shared `lib/install/` byte-identical across repos.** Extracted ~80 lines of inline install primitives from `install.sh` + `install.ps1` into a new shared lib with 6 bash + 8 pwsh functions plus a CONTRACT.md documenting the caller contract (`UPDATE_MODE`/`$Update` flag + `BOUNDARY_ROOTS`/`$BoundaryRoots` array + six behavior invariants). SHA-256 manifest committed in both repos. Cross-repo updates flow through `scripts/sync-lib.sh` (canonical → sibling); `scripts/check-lib-parity.sh` asserts self-consistency in CI on every push. Cross-platform byte-identity work surfaced four real Mac + Windows bugs (locale-dependent `sort` collation, `$host` collision in PowerShell, missing `shasum` in Git Bash, autocrlf + binary-mode SHA-256 differences) — all fixed before v2.0.0 tag.
- **PII + lib-parity CI gates in this repo.** New `pii-guardrails` job in all three per-OS workflows runs `scripts/check-no-pii.sh` (regex scanner, byte-copied from the toolkit) and the official `gitleaks/gitleaks-action@v2`. New `lib-parity` job runs `scripts/check-lib-parity.sh`. Both repos now share the same PII detection surface, with the toolkit's pre-push hook layer + agent-facing `pii-scrubber` skill providing the additional remediation layers on that side.
- **Graceful-skip framing for migrated skills.** `harness/phases/05-release.md`'s ship-release suggestion and `harness/phases/03-work.md`'s feature-flip suggestion both note "install crickets to enable; otherwise cut release manually with `gh release create`". `harness/skills/doctor.md` probes 3 + 5 skip cleanly if the skills aren't installed. The harness still works on its own for the full phase-gated workflow; only the two migrated skills require the sibling install.

**Why it shipped this shape:**

The triggering observation, captured in [ADR 0006](0006-crickets-split), was that the **parity tax scales linearly with personal customizations**: every skill we'd want to add (kill-switch, fresh-context evaluator, ContextVault, a design skill, `pii-scrubber`) would need a canonical spec under `harness/skills/` + adapter copies under each of three adapter trees + parity script updates + reference checks. Past two or three skills, the tax becomes the dominant work. The harness also has a clear identity (phase-gated workflow + on-disk state + adversarial review primitive + Diátaxis convention) — mixing personal-customization scope into that identity blurs both. Splitting the repo lets the harness stay tight (six phases + canonical sub-agents + setup-specific skills) while the toolkit takes the open-ended customization scope with its own conventions (manifest schema + per-host paths dispatch + 11-primitive scope).

The byte-identical `lib/install/` is the load-bearing piece. Both repos need essentially the same install plumbing (boundary guard + per-file copy modes + true-sync wipe-and-recreate for `--update`); copying the code with drift would create silent divergence the moment one repo fixes a Windows bug the other doesn't. Byte-identity + CI gate makes drift impossible.

The cross-platform debugging work was a nice forcing function. Three of the four bugs surfaced in the Mac + Windows CI workflows on the toolkit side before any user hit them — exactly the kind of friction the three-OS matrix is meant to catch early. Each fix landed in both repos with parallel commits cross-referencing the sibling's SHA.

**What it doesn't do:**

- Doesn't ship a CLI sugar layer to invoke both tools as `harness` and `crickets` on PATH. That requires `dev-machine-setup` changes and lands in a future plan.
- Doesn't pre-install the toolkit alongside the harness. Users opt in by cloning `crickets` as a sibling directory and running its installer separately. Graceful-skip framing keeps the harness functional without it.
- Doesn't migrate `doctor` or `migrate-to-diataxis`. Both are harness-shaped; moving them would be a category error.
- Doesn't redesign `migrate-to-diataxis` into a general-purpose Diátaxis authoring skill. That's tracked as a separate roadmap item.

**Tracked as:**

- 7-task plan in `.harness/PLAN.md` (crickets-split) — all tasks `[x]`, Status `done`.
- [v2.0.0](https://github.com/alexherrero/agentm/releases/tag/v2.0.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)
- Paired release: [crickets v0.5.0](https://github.com/alexherrero/crickets/releases/tag/v0.5.0).

**Related pages:**

- [ADR 0006 — crickets split](0006-crickets-split)
- [crickets ADR 0001 — crickets purpose](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0001-crickets-purpose.md)
- [Repo-Layout](Repo-Layout) — sibling repo + `lib/install/` rows in Quick Reference

## 2026-05-11 — v1.0.0: Three-adapter scope; Codex dropped; 1.0.0 commitment

**Commit range:** `v0.9.0..v1.0.0` (13 commits on `main`). Release notes: [v1.0.0](https://github.com/alexherrero/agentm/releases/tag/v1.0.0). ADR: [0005 — Drop Codex support](0005-drop-codex-support).

**What shipped:**

- **Codex adapter removed.** Supported hosts narrow from four to three: Claude Code, Antigravity, Gemini CLI. `adapters/codex/` (15 files), `harness/agents/codex-adapter-research.md` (294 lines), and codex-specific code across five scripts all gone. Repo-public surfaces (README, AGENTS.md, adapter READMEs, 9 wiki pages) scrubbed of Codex mentions. Historical entries in CHANGELOG.md and past-release sections of this page deliberately preserved as history.
- **True-sync `--update` semantics.** Beyond removing the codex install code, `install.sh` and `install.ps1` now wipe twelve fully-harness-authored subdirs before recreating from source on `--update`. Orphan paths from prior versions (e.g. `.codex/` from v0.9.0 installs) are automatically removed and reported as `removed legacy <path>/`. User state files at `.harness/` root, merged `settings.json` files, `wiki/**`, and root `AGENTS.md`/`CLAUDE.md` are deliberately preserved. The mechanism generalizes: any future host or skill removal also auto-cleans without per-removal patches.
- **v1.0.0 commitment.** Semver becomes firm going forward: major = breaking, minor = additive, patch = fixes. The harness's pre-1.0 churn period closes; future breaking changes (e.g. dropping Antigravity, restructuring adapters) become explicit major-version events, and the planned roadmap (crickets repo split, ContextVault, design skill, base-skill primitives, evidence-tracking) becomes clear minor bumps.

**Why it shipped this shape:**

The Codex adapter had been paying real ongoing costs without offsetting workflow value: Codex's built-in `/plan` and `/review` collisions forced a `harness-` prefix on every Codex phase-command (the only adapter with that divergence); the codex install block was a side-channel for delivering Gemini's shared skills (`.agents/skills/`), which made the codex removal load-bearing for two hosts; and the personal-dev-env scope had narrowed to Claude Code + Antigravity + Gemini CLI. Combined with the harness's maturity, this was the right moment to graduate to v1.0.0 — the host-scope reduction is a breaking change anyway, and a firm-semver 1.0 floor better communicates the stability commitment than another 0.x point release.

The `--update` true-sync amendment is worth noting separately. The user surfaced it during task 2 closeout: "removing the codex install code stops new installs from creating codex paths, but `--update` on an existing v0.9.0 install leaves orphan files." Rather than ship a codex-specific cleanup patch, the fix was generalized: declare `MANAGED_PARENTS` (12 subdirs that are fully harness-authored), wipe them on `--update`, then run the normal install flow which recreates from source. Codex becomes the first user of a mechanism that handles all future removals.

**What it doesn't do:**

- Anyone running agentm through Codex has no harness adapter post-v1.0.0. The phase-gated workflow is host-agnostic, but they must migrate to one of the three remaining adapters.
- Codex's built-in `/plan` and `/review` semantics will not be harness-aware in a Codex session against a harness-installed project.
- `cross-review.sh` previously listed `codex` as an option for true cross-vendor review; that fallback drops to `claude` only.

**Tracked as:**

- 5-task plan in `.harness/PLAN.md` (Codex-removal sweep) — all tasks `[x]`, plus a task-2 amendment for the true-sync `--update` semantics.
- [v1.0.0](https://github.com/alexherrero/agentm/releases/tag/v1.0.0) — release notes, [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)

**Related pages:**

- [ADR 0005 — Drop Codex support; three-adapter scope](0005-drop-codex-support)
- [Update-Installed-Harness](Update-Installed-Harness) — v1.0.0+ sync semantics section
- [Installer-CLI](Installer-CLI) — `--update` flag description updated
- [Repo-Layout](Repo-Layout) — three-adapter table

## 2026-04-23 — v0.9.0: Diátaxis documentation spec + `/doctor` skill

**Commit range:** `v0.8.7..v0.9.0` (10 commits on `main`). Release notes: [v0.9.0](https://github.com/alexherrero/agentm/releases/tag/v0.9.0).

**What shipped:**

- **Diátaxis four-mode wiki convention end-to-end.** [ADR 0004](seven-section-convergence) supersedes ADR 0002's audience-based layout (`wiki/{development,operational,design,architecture}/`) with the four Diátaxis modes — `tutorials/`, `how-to/`, `reference/`, `explanation/` (with `explanation/decisions/` for ADRs). The rollout landed as a 7-task plan: (1) `scripts/check-wiki.py` shipped as a structural lint with 11 rules (a–k); (2) `templates/wiki/` reshaped so new installs land directly in the four-mode layout; (3) this repo's own `wiki/` migrated file-by-file with `git mv` to preserve blame; (4) the `documenter` sub-agent rewired to write to mode-specific targets per phase; (5) a new `migrate-to-diataxis` skill for one-shot conversion of already-installed projects; (6) [`check-wiki.py`](CI-Gates) flipped from warn-only to `--strict` in CI; (7) harness phase specs retargeted to the new mode dirs.
- **`/doctor` skill** — companion to `telemetry.sh` for post-install correctness. Default mode runs structural discovery only: verifies expected phase commands, sub-agents, skills, state files, and hooks are present and parseable in the detected adapter (<5s, no tokens). `--live` adds six real probes — `explorer` dispatch on a trivial prompt, `adversarial-reviewer` dispatch requiring an executable artifact (not prose), `ship-release --dry-run`, `migrate-to-diataxis` preview on an already-migrated tree, `dependabot-fixer` no-match path, and a hook synthetic trigger. Never mutates repo state. Canonical spec at [`harness/skills/doctor.md`](https://github.com/alexherrero/agentm/blob/main/harness/skills/doctor.md); adapter wrappers for claude-code, antigravity, and codex (Gemini reuses the Codex delivery).
- **`CONTRIBUTING.md` extracted** from the README (which dropped 126 → 64 lines). CI matrix, invariant list, and local-gate command set now live at their natural home.

**Why it shipped this shape:**

Diátaxis gives readers a clear mental model before they open a page — "learn / do / look up / understand" is more durable than "dev / ops / design / architecture" (which conflates audience with intent, and blurs when a page serves multiple audiences). The migration skill exists because ADR changes that break installed projects are a tax the harness should pay, not the user. `/doctor` exists because file-presence smoke tests answer "is it there" but not "does it work", and several install regressions over the past quarter could have been caught pre-use by a cheap structural + live-probe check.

**First dogfood of `/doctor`** caught a real spec bug: the initial skill required a `name:` frontmatter field on every surface, but Claude Code phase commands, Antigravity workflows, and Gemini TOML commands intentionally have no `name:` field (name is implicit from filename). Spec and all three adapter wrappers were corrected inside the same release ([d078485](https://github.com/alexherrero/agentm/commit/d078485)). Live probes (`explorer` and `adversarial-reviewer`) both matched their pass criteria — adversarial returned a `file:line` pointer plus a failing pytest body in 10.8s rather than prose.

**What it doesn't do:**

- No auto-install on top of a half-installed tree — `/doctor` reports gaps and points at `install.sh`; auto-repair would mask misconfiguration.
- No CI coverage for `/doctor` itself — the skill dispatches sub-agents, which requires an LLM session and can't run in headless CI. `scripts/check-parity.sh` and `check-references.py` catch the structural facets that don't need an agent.
- No additional Diátaxis modes. Five-mode extensions (glossary, changelog) were explicitly rejected in `harness/documentation.md` §Non-goals — glossaries live under `reference/`, changelogs under `reference/Completed-Features.md`.

**Tracked as:**

- v0.9.0 — [release notes](https://github.com/alexherrero/agentm/releases/tag/v0.9.0), [CHANGELOG.md](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md)
- 7-task rollout plan in `.harness/PLAN.md` — Status: done

**Related pages:**

- [ADR 0004 — Diátaxis documentation spec](seven-section-convergence)
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

- [`feat-gh-projects-integration`](https://github.com/alexherrero/agentm/blob/main/.harness/features.json) (currently `passes: false` — gated on dogfood observation)
- [`feat-documenter-subagent`](https://github.com/alexherrero/agentm/blob/main/.harness/features.json) (this sweep is the dogfood of the spec)

**Related pages:**

- [GitHub-Projects-Integration](GitHub-Projects-Integration) — feature design + implementation.
- [ADR 0003: ProjectsV2 ownership and linking](0003-ProjectsV2-Ownership-And-Linking) — the two-call create-plus-link decision.
- [Cut-A-Release](Cut-A-Release) — the procedure that invokes `ship-release`.
