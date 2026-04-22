# Completed Features

Reverse-chronological log of feature deliveries in this repo. One line in the overview table per plan; a dated section below with branch/commit ref and a short summary. Maintained by the `documenter` sub-agent at `/release` time.

This page is **narrative**, not a changelog — the authoritative version log is [`CHANGELOG.md`](https://github.com/alexherrero/agentic-harness/blob/main/CHANGELOG.md). Use this page when you want to understand *what shipped and why* without reading every commit; use `CHANGELOG.md` when you want the semver history.

## ⚡ Overview

| Date | Plan / release | Features flipped | Notes |
|---|---|---|---|
| 2026-04-21 | GitHub Projects wiring + documenter end-to-end dogfood | `feat-gh-projects-integration` (pending — gated on offer-cycle observation); `feat-documenter-subagent` (this sweep is the dogfood) | 4 commits (`801dbd7..HEAD`), 23 files; new [ADR 0003](0003-ProjectsV2-Ownership-And-Linking), new [Feature page](GitHub-Projects-Integration) |

## 2026-04-21 — GitHub Projects wiring + documenter end-to-end dogfood

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
- [Runbook](Runbook) — operational procedures including `ship-release`.
