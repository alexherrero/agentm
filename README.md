<p align="center">
  <img src="assets/agent-m/banner-1600.png" alt="Agent M — The structural backend harness you wished you had">
</p>

<p align="center"><em>The agent harness that gives you the assistant you want — part Star Trek Computer, part J.A.R.V.I.S.</em></p>

<!--
  Badge convention (plan #15 task 6 v2) — apply uniformly across the brand-system:
    labelColor = 0a0a0a (ink, brand)
    color      = auto (semantic green/red on CI; semver-colored on release)
                 OR f4efe6 (paper) for state-less metadata (e.g. LICENSE)
    style      = for-the-badge (brutalist, ALL CAPS, sharp corners — matches banner motif)
    logo       = github (logoColor f4efe6) on CI + release badges
  CI badge points at the dedicated `ci-all.yml` aggregator workflow which waits
  for the 3 per-OS workflows on the same commit and reports a combined status.
  This insulates the badge from other apps' check suites (e.g. installed GitHub Apps
  that queue but never complete checks). Compatibility lives at wiki/reference/Compatibility.md.
  Mirrored on the Crickets README via task 7. Documented in PLAN.md task 7.
-->

<p align="center">
  <a href="https://github.com/alexherrero/agentm/actions/workflows/ci-all.yml"><img src="https://img.shields.io/github/actions/workflow/status/alexherrero/agentm/ci-all.yml?branch=main&style=for-the-badge&label=CI&labelColor=0a0a0a&logo=github&logoColor=f4efe6" alt="CI"></a>
  <a href="https://github.com/alexherrero/agentm/releases/latest"><img src="https://img.shields.io/github/v/release/alexherrero/agentm?label=LATEST&labelColor=0a0a0a&logo=github&logoColor=f4efe6&style=for-the-badge" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/LICENSE-MIT-f4efe6?labelColor=0a0a0a&style=for-the-badge" alt="License: MIT"></a>
</p>

<p align="center"><sub>Works with Claude Code + Antigravity — <a href="https://github.com/alexherrero/agentm/wiki/Compatibility">see compatibility</a></sub></p>

Think of **Agent M** as the structural backend harness you wished you had—part Star Trek Computer, part J.A.R.V.I.S.-level contextual autonomy, engineered to manage your projects, memory, and persistent knowledge across any modern agent surface, gaining experience and self-improving as it goes.

Imagine those workflows you saw in the movies. You're talking to your agent, *"open a new project file for M"* and off you go. Agent M remembers your projects and files together, talks with you about them, and learns and grows with you as you work. The context is self-maintaining — no time spent curating your own knowledge graph, and it can help with your personal notes too when **you** want it to.

This repo is the **harness** — the phase-gated workflow, auto-recall hooks, sub-agents, and on-disk state that make Agent M a system instead of a folder of files. It pairs with [**Crickets**](https://github.com/alexherrero/crickets) — a tactical suite of agent primitives (skills, hooks, sub-agents, bundles) that acts as the execution engine the harness installs into your target projects.

> **Latest:** v4.15.0 (2026-06-03) — **Hardening I: single-repo first-class + e2e breadth** (single-repo MINOR). The harness now runs on a **single repo with zero vault / Obsidian / Google Drive dependency** — opt in with `install.sh --local-state` (or `agentm_config.py --state-mode local`) and harness state lives in `<repo>/.harness/`. State-mode config is now **on-host only** (`state_mode` in `.agentm-config.json`; the vault holds data, never config — DC-8). Retired the redundant `install-state-sync` hook (the live "N fragment(s) skipped" boot-error source) + the last of the crickets install coupling. Backed by a first **end-to-end test batch** — whole-phase lifecycle (both state modes), every memory hook's firing/graceful-skip, and the memory-engine round-trip — wired into `check-all.sh` (11 gates) + CI.  
> **Prior:** v4.14.0 (2026-06-02) — **Decouple from crickets: agentm stands alone** (single-repo MINOR). crickets v3.0 retired its bespoke per-host installer in favor of native Claude Code / Antigravity plugins; this is agentm's side of the clean break — `install.sh`/`install.ps1` no longer clone or bootstrap crickets, and agentm owns its `lib/install/` outright. Also folded in the **V4 verification battery** (`verify-v4.sh` + `check-all.sh`).  
> [Release notes](https://github.com/alexherrero/agentm/releases/latest) · [Agent M Evolution HLD](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md) · [Device-Wide Architecture HLD](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/device-wide-architecture.md) · [CHANGELOG](CHANGELOG.md)

## What's where

| Piece | What it is |
|---|---|
| **Agent M** | The system as a whole — this repo + Crickets + your AgentMemory vault folder, working together |
| **Harness** (this repo) | Phase-gated workflow (`/setup` `/plan` `/work` `/review` `/release` `/bugfix`) + auto-recall + sub-agents + scripts |
| **Crickets** ([`crickets`](https://github.com/alexherrero/crickets)) | Skills, hooks, sub-agents, bundles — the primitives you install into your projects |
| **AgentMemory vault** | Your Obsidian markdown folder (synced via Google Drive / Dropbox / etc.) — agent reads at session start, writes under controlled conditions |

Agent M is opinionated — small, not a 150-agent supermarket. It works with YOLO mode and other fully automated coding workflows, but it's designed for the ones that keep a human in the loop.

## Why Agent M?

|  | Vanilla Claude Code | Claude Code + Agent M |
|---|---|---|
| **Session continuity** | Memory ends with the session; the next prompt starts blank | Vault-backed; new sessions auto-recall the entries relevant to where you left off |
| **Per-phase auto-context** | You re-explain conventions every time, or rely on a static `CLAUDE.md` | Each phase (`/setup` `/plan` `/work` `/review` `/release`) recalls phase-scoped entries within a token budget |
| **Evidence-tracked task closeouts** | Tasks close when the agent says they're done | `evidence-tracker` hook blocks `[ ] → [x]` flips in `PLAN.md` unless the agent actually read the spec/test files first |
| **Paired-release coordination** | Manual cross-repo coordination per release | Locked release-order convention + URL-linked sibling release notes + paired CI verification on both repos |
| **Cross-project memory** | Each project's `CLAUDE.md` lives in isolation | Vault holds operator-wide conventions + per-project sub-trees; the same locked decisions surface across every project you work in |

Agent M doesn't replace Claude Code — it gives it persistence, structure, and the kind of accumulating context that turns a fresh session into a continuation.

## Get started

Once both repos are cloned and the vault folder exists, Agent M is operational.

**1. Install both repos as siblings**

```bash
git clone https://github.com/alexherrero/agentm.git ~/Antigravity/agentm
git clone https://github.com/alexherrero/crickets.git    ~/Antigravity/crickets
```

**2. Point the vault at your existing Obsidian + sync setup**

```bash
mkdir -p "<sync-root>/AgentMemory/personal-private/_always-load"
mkdir -p "<sync-root>/AgentMemory/projects"
mkdir -p "<sync-root>/AgentMemory/_meta"
export MEMORY_VAULT_PATH="<sync-root>/AgentMemory"
```

> [!NOTE]
> Pre-v4.1.0 vaults used `personal-projects/` (renamed to `projects/` in V4 #26). Existing operators should run `bash agentm/scripts/rename-vault-personal-projects.sh` after upgrading. The resolver chain transparently handles both layouts during transition — recall + save keep working either way.

Any sync layer works (Google Drive, Dropbox, syncthing).

**3. Install the harness + Crickets plugins**

```bash
# Harness (this repo) — slash commands, sub-agents, .harness/ state, AGENTS.md / CLAUDE.md, wiki/ scaffold
bash ~/Antigravity/agentm/install.sh [--hooks] /path/to/your-project

# Crickets — native host plugins (developer base: evaluator + kill-switch / steer /
# commit-on-stop, plus github-ci / pii / wiki). Installs onto Claude Code + Antigravity.
curl -fsSL https://raw.githubusercontent.com/alexherrero/crickets/main/bootstrap.sh | bash
```

Installations are idempotent; `--hooks` is opt-in for verification hooks. Windows: use `install.ps1` with PowerShell 7+; same flag shape with `-Hooks` and `-Update`.

**Running without a vault (single-repo mode).** agentm defaults to vault-resident state (your AgentMemory MemoryVault). To run a repo with **zero vault / Obsidian / Google Drive dependency**, install with `--local-state`:

```bash
bash ~/Antigravity/agentm/install.sh --local-state /path/to/your-project
```

This writes `"state_mode": "local"` to the on-host `.agentm-config.json` (the single config file — the vault holds data, config is on-host only) and skips vault wiring. Harness state (`PLAN.md`, `progress.md`, `features.json`, …) then lives in `<repo>/.harness/`. Flip an existing install with `python3 ~/Antigravity/agentm/scripts/agentm_config.py --state-mode local`; a per-repo `<repo>/.harness/.project-mode` marker overrides the device default for a single repo. Windows: `install.ps1 -LocalState`.

<details>
<summary>More install detail — seed your always-load entries + verify</summary>

**4. Seed your always-load entries**

Capture your locked conventions, coding-style rules, project invariants under `<vault>/personal-private/_always-load/`. One entry per concern. The first pass is co-created — you and the agent walk through it together; you approve each entry.

**5. Verify**

```bash
python3 ~/Antigravity/agentm/scripts/harness_memory.py recall --phase setup
```

Should print your always-load entries within the 4000-token budget.

</details>

Full install detail: [wiki/how-to/Install-Into-Project.md](wiki/how-to/Install-Into-Project.md).

## How it works

```mermaid
flowchart LR
    U([You])
    H[Host<br/>Claude Code · Antigravity]
    A[Adapter<br/>commands · agents · skills]
    S[Canonical specs<br/>harness/]
    ST[(.harness/<br/>state)]
    W[(wiki/<br/>→ GitHub Wiki)]
    V[(AgentMemory<br/>vault — synced)]

    U -->|/slash command| H
    H --> A
    A --> S
    S --> ST
    S --> W
    S --> V
    V --> A
```

## Phases

| Command | Purpose |
|---|---|
| `/setup` | First-time project init — scaffold, `init.sh`, feature list, vault recall |
| `/plan` | Turn a brief into `.harness/PLAN.md` — tasks with pass/fail criteria |
| `/work` | Execute one task from the plan; evidence-tracked; update progress; stop |
| `/review` | Adversarial critique of the change — must produce executable artifact |
| `/release` | Pre-merge gate — clean tree, verification passes, changelog, paired-release coordination |
| `/bugfix` | Report → Analyze → Fix → Verify pipeline with GitHub Issue as posterity record |

Every phase auto-recalls relevant entries from your AgentMemory vault at start, and offers to save new durable knowledge at exit. Self-modulating offer-save (confidence-thresholded) and cursor-tracked promotion keep the vault current without nagging you.

## Skills shipped with the harness

Legacy single-file canonical skills (delivered via the per-host `adapters/` pipeline):

| Skill | What it does |
|---|---|
| [`migrate-to-diataxis`](harness/skills/migrate-to-diataxis.md) | One-shot migration of an already-installed project's `wiki/` to the Diátaxis four-mode layout. Preview-first, `git mv` for blame, non-destructive. (Superseded by crickets' `wiki-maintenance` plugin for new work; kept for legacy migration.) |
| [`doctor`](harness/skills/doctor.md) | User-invoked (`/doctor`). Verifies the install is correctly wired up in this host — structural by default, `--live` adds real sub-agent dispatches and skill dry-runs. |

Compound skills imported from Crickets in v4.0.0 (V4 #36) — delivered via the manifest-walking dispatcher in `install.sh` / `install.ps1`:

| Skill | What it does |
|---|---|
| [`memory`](harness/skills/memory/SKILL.md) | The Agent M memory skill itself. `/memory save` / `evolve` / `reflect` / `search` / `index-skills` / `discover-skills` / `adapt-skills` / `watchlist` / `promote`. Permeable A3 write boundary; collision-checked; supersession-not-deletion. Powers the recall + reflect hook loop. |
| [`design`](harness/skills/design/SKILL.md) | Human-facing design pipeline → agent execution handoff. `/design author` walks a 10-section template; `/design translate` splits the approved design into structural parts; `/design sequence` generates a `PLAN.md` per part for `/work` + `/release` flow. |
| [`ship-release`](harness/skills/ship-release/SKILL.md) | Cut a tagged GitHub release with semver-driven version bumps from conventional commits. Writes CHANGELOG, tags, pushes, creates the release. |

> **Wiki authoring lives in crickets now.** The `diataxis-author` skill was retired from agentm in the seven-section convergence ([ADR 0004 Amendment 2026-06-11](wiki/decisions/0004-diataxis-documentation-spec.md)) — it is canonical in [crickets' `wiki-maintenance` plugin](https://github.com/alexherrero/crickets/tree/main/src/wiki-maintenance). The harness defers to it with the [ADR 0006](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0006-crickets-split.md) graceful-skip (suggest-then-skip when crickets is not installed); the surviving seven-folder `scripts/check-wiki.py` is agentm's wiki gate.

Hooks (claude-code only per [ADR 0009](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0009-evidence-tracker-hook.md)):

| Hook | What it does |
|---|---|
| [`memory-recall-session-start`](harness/hooks/memory-recall-session-start/hook.md) | SessionStart event → loads always-load entries from your vault into the agent's context (deduped, status-filtered, ~500ms budget). |
| [`memory-recall-prompt-submit`](harness/hooks/memory-recall-prompt-submit/hook.md) | UserPromptSubmit event → keyword + vector-search recall of relevant entries based on the current prompt (~300ms budget; never blocks the prompt). |
| [`memory-reflect-stop`](harness/hooks/memory-reflect-stop/hook.md) | Stop event → mines the session transcript for durable-knowledge candidates (preferences, workflows, fixes, ideas); HIGH-confidence auto-saves to canonical paths, MEDIUM/LOW + ideas land in `_inbox/`. |
| [`memory-reflect-idle`](harness/hooks/memory-reflect-idle/hook.md) | SessionStart event → recovers orphan reflection markers from crashed sessions, processes deferred reflection candidates. |
| [`evidence-tracker`](harness/hooks/evidence-tracker/hook.md) | Default-FAIL evidence enforcement on `/work` task closeouts. Blocks `[ ]` → `[x]` flips in `PLAN.md` unless the agent demonstrably `Read` the spec/test files first. Hybrid resolver (heuristic + per-task override + explicit opt-out). |

Sub-agents (imported in v4.0.0; existing 4 legacy agents at `harness/agents/`):

| Sub-agent | What it does |
|---|---|
| [`memory-idea-researcher`](harness/agents/memory-idea-researcher.md) | Read-only deep-research worker for `_idea-incubator/` skeletons. Bounded wall-time / web-fetch / token budgets enforced from the skeleton's frontmatter. |

Plugins (Antigravity 2.0 / agy v1.0.2+):

| Plugin | What it does |
|---|---|
| [`example-plugin`](harness/plugins/example-plugin/) | Reference plugin showing the Antigravity 2.0 plugin manifest format. Install via `bash scripts/install-plugin.sh example-plugin`. |

Base primitives + the 2 evaluator sub-agents (`evaluator`, `diataxis-evaluator`) + 3 operator-control hooks (`kill-switch`, `steer`, `commit-on-stop`) + 2 utility skills (`pii-scrubber`, `dependabot-fixer`) live in **Crickets**. (The memory-flow `adapt-evaluator` sub-agent moved to agentm in V4 #23 — memory primitives are agentm-native.) See [ADR 0012](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0012-device-wide-by-default.md) for the device-wide-by-default split rationale and [ADR 0006](wiki/decisions/0006-crickets-split.md) for the original split decision.

## Telemetry

`.harness/progress.md` accumulates evidence of whether the harness is working. Run `.harness/scripts/telemetry.sh` for a per-project report or `--all` for multi-project. Signal definitions in [harness/telemetry.md](harness/telemetry.md).

## Repo structure

<details>
<summary>Top-level layout</summary>

```text
agentm/
├── harness/          # canonical phase specs + harness-shipped skills (doctor, migrate-to-diataxis) + telemetry doc + principles
├── adapters/         # per-host wiring (claude-code/, antigravity/) — thin shims that point back at the canonical specs in harness/
├── wiki/             # Diátaxis-shaped docs (tutorials/ + how-to/ + reference/ + explanation/) — published as the GitHub Wiki
├── scripts/          # install helpers + smoke tests + harness_memory.py + manifest validators
├── templates/        # scaffolding (PLAN.md template, init.sh template) installed into target projects
├── assets/           # Agent M brand assets — logo, monogram, brand preview
├── lib/              # shared install plumbing — byte-identical to Crickets' lib/install/
├── AGENTS.md         # universal instructions for any AGENTS.md-aware host
├── CLAUDE.md         # Claude Code entry point — points back at AGENTS.md
├── install.sh        # POSIX installer (Linux + Mac)
└── install.ps1       # Windows installer (PowerShell 7+)
```

</details>

## Architecture history

Agent M has grown over time across paired releases of `agentm` and `crickets`. The full V1→V4 evolution — what shipped, what's deferred, where the design is going — lives in [Agent Memory Evolution](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md) on the Crickets side. [V3 Retrospective](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/v3-retrospective.md) covers what shipped, what we learned, what's deferred.

For the harness's design rationale, see [harness/principles.md](harness/principles.md) and the architecture decisions under [wiki/decisions/](wiki/decisions/).

## Status

Currently shipping **v4.15.0** — **Hardening I: single-repo first-class + e2e breadth** (single-repo MINOR). The harness now runs on a single repo with **zero vault dependency** (`install.sh --local-state` → state in `<repo>/.harness/`; state-mode config is on-host only, DC-8), the redundant `install-state-sync` hook + the last crickets install coupling are gone, and a first end-to-end test batch (whole-phase lifecycle in both state modes · every memory hook's firing/graceful-skip · the memory-engine round-trip) is wired into `check-all.sh` (11 gates) + CI. Built on **v4.14.0** (decoupled from crickets — agentm is a self-contained standalone install owning its `lib/install/` outright). The current major capability remains **v4.13.0's auto-orchestration push-surface** (V4 #23; single-repo MINOR), which turns the Agent M memory skills from a *pull surface* (you had to remember to run them) into a *push surface*: a SessionStart **briefing** surfaces pending state (inbox over threshold · HIGH skill-watchlist · incubator ideas in research · GC-eligible idea-ledger items) in one tight 1–3 line block, emitting only when state shifted since you last saw it and the cooldown allows; an **idle-time chain** (`orchestration_idle.py`) runs reflect-corpus → discover-skills → adapt Pass-1 itself, detached so it never blocks the hook's 30s timeout and bounded by `--max-batches 1`/`--limit 3`; **phase-integration** (`orchestration_phase.py`) reflects the just-finished session after `/work` (dedup-guarded vs the `memory-reflect-stop` hook via the `.reflected` marker, cross-host incl. Antigravity) and refreshes skill sources after `/release`; and two **nudges** suggest `/memory promote` for an idea surfaced ≥3× and flag `_skill-watchlist/` entries `promoted` >30d without action. All gated by an operator-tunable config (`<vault>/personal-private/auto-orchestration-config.md`) + cooldown/shifted-state guard (`<vault>/_meta/auto-orchestration-state.json`); never blocks a session, never nags, never auto-adopts. Entirely **agentm-native** (DC-3); hook/file-based + cross-host (DC-1). **This is the last open V4 item — the V4 foundation is complete.** Each code task passed an adversarial review that caught a real defect (non-UTF-8 hook crash · clear-then-refill suppression · wrong-session-under-concurrency reflect), each fixed + regression-tested. 84 new tests (549 suite, 4-OS). The default thresholds/cooldowns calibrate under the operator's real-use dogfood. Single-repo release; the paired crickets `adapt-evaluator` de-crossover already merged (ships with crickets' next release). See [CHANGELOG.md](CHANGELOG.md) and the [latest release](https://github.com/alexherrero/agentm/releases/latest).

Prior: **v4.12.0** — Cross-surface Agent M vault access (V4 #22; single-repo MINOR). Made the AgentMemory vault readable natively from every agent surface (Claude.ai · Claude Desktop · Antigravity), not just Claude Code, via one paste-anywhere context payload + thin per-surface wiring (configure-don't-build, no new MCP server/API/daemon). Read/write surface-scoped (chat surfaces read-only; filesystem working agents may write). Migrated the Antigravity adapter `.agent/` → `.agents/` (2.0 default) and made `/doctor` host-aware. All surfaces operator-dogfood-validated. See [CHANGELOG.md](CHANGELOG.md) and the [latest release](https://github.com/alexherrero/agentm/releases/latest).

## Contributing

Self-tested on every push by three per-OS workflows (Linux, Mac, Windows) running in parallel. Run the same gates locally with `bash scripts/smoke-install-bash.sh`. Details and the full invariant list in [CONTRIBUTING.md](CONTRIBUTING.md).
