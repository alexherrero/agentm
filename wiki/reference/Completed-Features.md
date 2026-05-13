# Completed Features

Reverse-chronological log of feature deliveries in this repo. One line in the overview table per plan; a dated section below with branch/commit ref and a short summary. Maintained by the `documenter` sub-agent at `/release` time.

This page is **narrative**, not a changelog — the authoritative version log is [`CHANGELOG.md`](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md). Use this page when you want to understand *what shipped and why* without reading every commit; use `CHANGELOG.md` when you want the semver history.

## ⚡ Overview

| Date | Plan / release | Features flipped | Notes |
|---|---|---|---|
| 2026-05-12 | [v2.0.0](https://github.com/alexherrero/agentic-harness/releases/tag/v2.0.0) — `agent-toolkit` repo split: `dependabot-fixer` + `ship-release` moved out | **BREAKING**: two skills migrated to [`agent-toolkit`](https://github.com/alexherrero/agent-toolkit); new shared `lib/install/` byte-identical across repos; PII + lib-parity CI gates added | 9 commits (`v1.0.0..v2.0.0`); new [ADR 0006](0006-agent-toolkit-split); paired with [agent-toolkit v0.5.0](https://github.com/alexherrero/agent-toolkit/releases/tag/v0.5.0); harness shared-skills narrow 4→2; cross-platform byte-identity work surfaced + fixed four real Mac/Windows bugs |
| 2026-05-11 | [v1.0.0](https://github.com/alexherrero/agentic-harness/releases/tag/v1.0.0) — Three-adapter scope; Codex dropped; 1.0.0 commitment | **BREAKING**: Codex adapter removed; true-sync `--update` semantics; firm-semver 1.0.0 floor | 13 commits (`v0.9.0..v1.0.0`); new [ADR 0005](0005-drop-codex-support); ~1300 lines net removed; first major version; parity invariant simplified to three adapters |
| 2026-04-23 | [v0.9.0](https://github.com/alexherrero/agentic-harness/releases/tag/v0.9.0) — Diátaxis documentation spec + `/doctor` skill | Diátaxis rollout (ADR 0004, 7-task plan); `migrate-to-diataxis` skill; mode-aware `documenter` writes; `/doctor` skill for post-install verification | 10 commits (`v0.8.7..v0.9.0`); new [ADR 0004](0004-diataxis-documentation-spec); two new shared skills; `scripts/check-wiki.py` shipped + flipped to `--strict`; wiki dogfood reshaped with `git mv` for blame |
| 2026-04-21 | [v0.8.7](https://github.com/alexherrero/agentic-harness/releases/tag/v0.8.7) — GitHub Projects wiring + documenter end-to-end dogfood | `feat-gh-projects-integration` (pending — gated on offer-cycle observation); `feat-documenter-subagent` (this sweep is the dogfood) | 4 commits (`801dbd7..HEAD`), 23 files; new [ADR 0003](0003-ProjectsV2-Ownership-And-Linking), new [Feature page](GitHub-Projects-Integration) |

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
