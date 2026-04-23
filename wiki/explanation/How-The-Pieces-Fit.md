# How the pieces fit

Narrative of how phases, adapters, templates, scripts, and this wiki interact. For the on-disk map, see [Repo-Layout](Repo-Layout); for the *why* of phase gates and the doc convention, see [ADR 0001](0001-phase-gated-workflow) and [ADR 0002](0002-documentation-convention).

## 🏗 Phases → adapters → templates → target project

```
         ┌──────────────────────────────────────────┐
         │   harness/phases/*.md   (source of truth) │
         │   harness/agents/*.md                     │
         │   harness/skills/*.md                     │
         └────────┬─────────────────────────┬────────┘
                  │                         │
                  │ referenced-by            │ referenced-by
                  ▼                         ▼
         ┌──────────────────┐      ┌──────────────────┐
         │  adapters/       │      │  wiki/           │
         │  claude-code/    │      │  (THIS repo's    │
         │  antigravity/    │      │   own docs only) │
         │  codex/          │      │                  │
         │  gemini/         │      └──────────────────┘
         └────────┬─────────┘
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
         │    .harness/  .claude/  .agent/            │
         │    .agents/  .codex/  .gemini/             │
         │    AGENTS.md  CLAUDE.md                    │
         │    wiki/  (empty scaffold from templates/) │
         │    .github/workflows/wiki-sync.yml          │
         └────────────────────────────────────────────┘
```

**Key property:** the phase specs in `harness/` are authoritative. Every adapter file is expected to cite a `harness/<phases|agents|skills>/` path; [`scripts/check-references.py`](https://github.com/alexherrero/agentic-harness/blob/main/scripts/check-references.py) fails CI if an adapter references a spec that doesn't exist. That's what keeps the four adapters in sync — they're all pointers at the same canonical text. Adding a new adapter is a matter of writing pointers, not re-writing the workflow.

## 📁 The installer boundary

`install.sh` and `install.ps1` read **only** from two roots:

1. `$HARNESS_ROOT/templates/` — the scaffold every project gets (state files, hooks, wiki scaffold, wiki-sync workflow).
2. `$HARNESS_ROOT/adapters/` — tool-specific commands / agents / skills.

They **never** read from:

- `$HARNESS_ROOT/wiki/` — dogfood docs for the harness repo (this one).
- `$HARNESS_ROOT/scripts/` — test infra for the harness repo.
- `$HARNESS_ROOT/.github/workflows/tests-*.yml` — CI for the harness repo.

The boundary is enforced in three layers: the top-of-file comment in [`install.sh`](https://github.com/alexherrero/agentic-harness/blob/main/install.sh#L23-L28); the runtime guard `ensure_boundary_src` in the `cp_managed` function; and the byte-for-byte assertions in [`scripts/test-install.sh`](https://github.com/alexherrero/agentic-harness/blob/main/scripts/test-install.sh) and [`scripts/smoke-install-bash.sh`](https://github.com/alexherrero/agentic-harness/blob/main/scripts/smoke-install-bash.sh). See [ADR 0002](0002-documentation-convention) for the full rationale.

## ⚙️ Verification infrastructure

CI runs on Linux, macOS, and Windows in parallel. All gates are documented in [CI-Gates](CI-Gates). The gates are deterministic and blocking — they run before any agentic review — because of the principle that typecheckers and tests are the truth and LLM reviews augment. For the full list of why, see [`harness/principles.md`](https://github.com/alexherrero/agentic-harness/blob/main/harness/principles.md).

## Related

- [Product-Intent](Product-Intent) — what problem the harness solves.
- [Repo-Layout](Repo-Layout) — the on-disk map this narrative describes.
- [CI-Gates](CI-Gates) — what each CI workflow proves.
- [ADR 0001](0001-phase-gated-workflow) — why phase gates.
- [ADR 0002](0002-documentation-convention) — why this wiki is never installed into target projects.
