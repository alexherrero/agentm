# Completed Features

Reverse-chronological log of feature deliveries in this repo. One line in the overview table per plan; a dated section below with branch/commit ref and a short summary. Maintained by the `documenter` sub-agent at `/release` time.

This page is **narrative**, not a changelog — the authoritative version log is [`CHANGELOG.md`](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md). Use this page when you want to understand *what shipped and why* without reading every commit; use `CHANGELOG.md` when you want the semver history.

## ⚡ Overview

| Date | Plan / release | Features flipped | Notes |
|---|---|---|---|
| 2026-04-23 | [v0.9.0](https://github.com/alexherrero/agentic-harness/releases/tag/v0.9.0) — Diátaxis documentation spec + `/doctor` skill | Diátaxis rollout (ADR 0004, 7-task plan); `migrate-to-diataxis` skill; mode-aware `documenter` writes; `/doctor` skill for post-install verification | 10 commits (`v0.8.7..v0.9.0`); new [ADR 0004](0004-diataxis-documentation-spec); two new shared skills; `scripts/check-wiki.py` shipped + flipped to `--strict`; wiki dogfood reshaped with `git mv` for blame |
| 2026-04-21 | [v0.8.7](https://github.com/alexherrero/agentic-harness/releases/tag/v0.8.7) — GitHub Projects wiring + documenter end-to-end dogfood | `feat-gh-projects-integration` (pending — gated on offer-cycle observation); `feat-documenter-subagent` (this sweep is the dogfood) | 4 commits (`801dbd7..HEAD`), 23 files; new [ADR 0003](0003-ProjectsV2-Ownership-And-Linking), new [Feature page](GitHub-Projects-Integration) |

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
