# How the pieces fit

How the phases, adapters, templates, scripts, and this wiki interact — the narrative behind the on-disk map. For the map itself see [Repo layout](Repo-Layout); for *why* the phase gates and the doc convention exist, see the [AgentM HLD](agentm-hld) and the [Foundations HLD](agentm-foundations-hld).

## Two sources of truth → adapters → target project

Since the V5 unbundling there are two canonical sources, not one: crickets' developer-workflows plugin owns the phase specs (`/plan` · `/work` · `/review` · `/release` · `/bugfix`); this repo's `harness/agents/*.md` and `harness/skills/*.md` own the memory-engine sub-agents and skills (`adapt-evaluator`, `memory-idea-researcher`, `doctor`, `memory`, `design`, `wiki-author`). Each adapter is a thin shim that *points* at whichever canonical spec it needs rather than restating it.

```
         ┌────────────────────────────┐   ┌────────────────────────────┐
         │  crickets                  │   │  agentm                    │
         │  developer-workflows       │   │  harness/agents/*.md       │
         │  (phase specs — source     │   │  harness/skills/*.md       │
         │   of truth for /plan etc.) │   │  (memory-engine — source   │
         │                            │   │   of truth for sub-agents  │
         │                            │   │   and skills)              │
         └──────────────┬─────────────┘   └──────────────┬─────────────┘
                         │ referenced-by                  │ referenced-by
                         ▼                                ▼
                  ┌──────────────────────────────────────────┐
                  │  adapters/                                │
                  │  claude-code/  antigravity/                │
                  └────────────────────┬───────────────────────┘
                                       │ copied-by
                                       ▼
         ┌───────────────────────────────────────────┐
         │  install.sh / install.ps1                  │
         │  reads ONLY from templates/ + adapters/    │
         │  (NEVER from wiki/ — installer boundary)   │
         └────────┬───────────────────────────────────┘
                  │ drops into
                  ▼
         ┌───────────────────────────────────────────┐
         │  target-project/                           │
         │    .harness/  .claude/  .agents/           │
         │    AGENTS.md  CLAUDE.md                    │
         │    wiki/  (empty scaffold from templates/) │
         │    .github/workflows/wiki-sync.yml         │
         └───────────────────────────────────────────┘
```

**Why it holds together:** every adapter file is expected to cite either a crickets developer-workflows phase-spec path or an agentm `harness/<agents|skills>/` path, and [`scripts/check-references.py`](https://github.com/alexherrero/agentm/blob/main/scripts/check-references.py) fails CI if an adapter points at a spec that doesn't exist. That's what keeps the two supported adapters — [Claude Code and Antigravity](Compatibility) — in lockstep: they are pointers at the same canonical text, not parallel rewrites. Adding a host is a matter of writing pointers, not re-authoring the workflow.

## The installer boundary

`install.sh` and `install.ps1` read from **only** two roots:

1. `$HARNESS_ROOT/templates/` — the scaffold every project gets (state files, hooks, wiki scaffold, the wiki-sync workflow).
2. `$HARNESS_ROOT/adapters/` — the per-host commands, agents, and skills.

They **never** read from `wiki/` (the dogfood docs for *this* repo), `scripts/` (this repo's test infra), or `.github/workflows/tests-*.yml` (this repo's CI). That boundary is what keeps the harness's own documentation from leaking into every project it installs into.

The boundary is enforced in three layers: the top-of-file comment in [`install.sh`](https://github.com/alexherrero/agentm/blob/main/install.sh), the runtime `ensure_boundary_src` guard inside `cp_managed`, and the byte-for-byte assertions in [`scripts/test-install.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/test-install.sh) and [`scripts/smoke-install-bash.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/smoke-install-bash.sh). The full rationale is in the [Foundations HLD](agentm-foundations-hld).

## Verification runs before any agent

CI runs on Linux, macOS, and Windows in parallel, and every gate is deterministic and blocking — they run *before* any agentic review, because the harness treats typecheckers and tests as the truth and LLM reviews as augmentation. The full list is in [CI gates](CI-Gates).

## Related

- [Product intent](Product-Intent) — the problem the harness solves.
- [Repo layout](Repo-Layout) — the on-disk map this narrative describes.
- [CI gates](CI-Gates) — what each CI workflow proves.
- [Host adapters](Host-Adapters) — how a single canonical spec reaches each host.
- [Phase-gated workflow design](agentm-hld) · [Documentation convention design](agentm-foundations-hld).
