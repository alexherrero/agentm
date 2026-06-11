# ADR 0002: Documentation convention

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-04-01

## Context

A harness-installed project accumulates two kinds of knowledge:

- **Current-work state** — what's being done right now, next task, latest test result. Lives in `.harness/PLAN.md`, `features.json`, `progress.md`. Already well-solved.
- **Durable knowledge** — what the system is, who it's for, how to operate it, why it's shaped the way it is. Not solved.

The canonical failure modes for durable knowledge in AI-assisted projects:

1. **Docs alongside code.** The agent implementing a feature also writes the docs. It reliably biases toward confirming the plan (defensive documentation) rather than describing what shipped. It also bloats the implementer's context.
2. **Single `DOCS.md`.** Doesn't scale; hard to browse; conflates audiences.
3. **Auto-generated from code.** Humans can't freely edit; docs become schema, not narrative.
4. **YAML front-matter for status.** Overhead that nobody updates.
5. **Docs generated dynamically from code annotations.** Bias toward what the code currently says, not what the code should say — makes review useless.

Additionally: **this repo ships a harness into other repos**. The harness repo has its own docs. Without a hard rule, `install.sh` would eventually grow a copy path that reads from `$HARNESS_ROOT/wiki/` and propagates this repo's "how agentm works" docs into every target project — which is useless to the target project and confusing.

## Decision

Four-part convention (full spec in [`harness/documentation.md`](https://github.com/alexherrero/agentm/blob/main/harness/documentation.md)):

### 1. Narrative wiki, four sections

`wiki/` at repo root, with four subdirs and fixed jobs:

- `development/` — how to build, install, contribute
- `operational/` — how to run, release, debug in production
- `design/` — product intent, features, rationale
- `architecture/` — subsystems, data flow, decisions (ADRs)

Templates: "Page" (default), "Status" (pending → implemented → deprecated), "ADR". Three templates, no more — every extra template is a decision the sub-agent gets wrong. Filenames are `CamelCase-With-Dashes.md` (matches GitHub Wiki URL convention).

### 2. Phase-boundary updates, not inline

A dedicated `documenter` sub-agent ([`harness/agents/documenter.md`](https://github.com/alexherrero/agentm/blob/main/harness/agents/documenter.md)) owns writes to `wiki/**`. It runs **only** at phase boundaries (`/setup`, `/plan`, `/work`'s commit step, `/release`, `/bugfix`). **Never during `/work`'s implement step.**

This is the hard rule that prevents "defensive documentation". The implementer writes the code; a separate pass writes the docs after gates are green.

### 3. GitHub Wiki as a dumb mirror

A `.github/workflows/wiki-sync.yml` workflow mirrors `wiki/**` to `${REPO}.wiki.git` on push to the default branch. The repo is the source of truth; the GitHub Wiki is a read-only browser-friendly view.

### 4. Installer boundary

`install.sh` and `install.ps1` copy **only** from `$HARNESS_ROOT/templates/` and `$HARNESS_ROOT/adapters/`. They never read from:

- `$HARNESS_ROOT/wiki/` — this repo's dogfood docs
- `$HARNESS_ROOT/scripts/` — this repo's test infra
- `$HARNESS_ROOT/.github/workflows/tests-*.yml` — this repo's CI

Target projects get the *empty* scaffold from [`templates/wiki/`](https://github.com/alexherrero/agentm/tree/main/templates/wiki). They never receive this repo's own documentation. The boundary is enforced in two layers — runtime and test — because post-hoc assertions alone can silently pass once an out-of-boundary source becomes byte-identical to a legitimate template (see [#1](https://github.com/alexherrero/agentm/issues/1) Defect 2).

**Runtime guard (copy time):**

- [`install.sh`](https://github.com/alexherrero/agentm/blob/main/install.sh#L92-L113) defines `ensure_boundary_src`, called from `cp_user`, `cp_managed`, and `cp_managed_dir` (and transitively from `cp_user_walk`). Every copy operation asserts the source path starts with `$HARNESS_ROOT/templates/` or `$HARNESS_ROOT/adapters/`; anything else exits with a loud `installer-boundary violation` message before the copy happens.
- [`install.ps1`](https://github.com/alexherrero/agentm/blob/main/install.ps1#L84-L102) defines `Ensure-BoundarySrc`, called from `Copy-UserFile`, `Copy-ManagedFile`, and `Copy-ManagedDir`. Same semantics, using `Resolve-Path` + `DirectorySeparatorChar` for cross-platform path normalization.

**Test-time assertions:**

- Documented in the top-of-file comment block of [`install.sh`](https://github.com/alexherrero/agentm/blob/main/install.sh#L23-L28).
- Asserted by [`scripts/smoke-install-bash.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/smoke-install-bash.sh) (and its pwsh twin): after `install.sh` runs, none of the files from `$HARNESS_ROOT/wiki/` or `$HARNESS_ROOT/scripts/` appear in the scratch install.
- Tightened by [`scripts/test-install.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/test-install.sh) and [`scripts/test-install.ps1`](https://github.com/alexherrero/agentm/blob/main/scripts/test-install.ps1): `diff -r templates/wiki/ <scratch>/wiki/` byte-for-byte, hash-based check that no content from `$HARNESS_ROOT/wiki/` appears in the scratch install, *plus* check (e) which mutates `install.sh` / `install.ps1` in place to rewrite the `wiki-sync.yml` copy source to the source-repo mirror and asserts the runtime guard fires with the boundary-violation message. Runs in Linux CI (bash) and Windows CI (pwsh) on every PR.

## Consequences

**Positive**

- **Docs reflect what shipped.** Writing them after gates are green, in a separate session, with no implementer reasoning in context, forces a from-scratch synthesis of the actual behavior.
- **Four subdirs map to four real audiences.** Development is for contributors, Operational is for on-call, Design is for product, Architecture is for future maintainers. Mixed-audience docs become single-audience pages.
- **ADRs record load-bearing decisions** in the format a future auditor expects — Context/Decision/Consequences. This is what `/principle 6` ("re-audit on every model bump") chews on.
- **The installer boundary is invariant, not convention — and enforced at copy time, not only asserted by post-hoc tests.** You cannot accidentally ship this repo's docs into a target project because `ensure_boundary_src` / `Ensure-BoundarySrc` refuses the copy the moment the source path falls outside `templates/` or `adapters/`. A drive-by PR to `install.sh` that adds `cp_managed "$HARNESS_ROOT/.github/workflows/wiki-sync.yml" ...` — the exact regression that would have been silent under hash-only smoke checks once the source and template are byte-identical — fails loudly at copy time on all three OSes, with the negative test (`test-install.sh` / `test-install.ps1` check (e)) proving the guard actually fires.
- **The GitHub Wiki sync is a dumb mirror.** No merge logic, no third-party action, no hidden state. `rsync -a --delete`.

**Negative**

- **Docs lag behind code.** Between the end of `/work` and the end of `/release`, the wiki does not describe the new behavior. Mitigation: `/work`'s commit step runs `documenter` to flip pending → implemented on the relevant Feature/Subsystem page. It's not zero-lag, but it's bounded.
- **The `documenter` sub-agent adds adapter surface.** Every adapter must expose documenter at the same phase boundaries. Mitigated by [`scripts/check-parity.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-parity.sh) enforcing the canonical sub-agent set.
- **Dogfood freshness is manual.** This repo's `wiki/` references specific line ranges in `install.sh` and `scripts/`; those can drift. Mitigation: pre-release check in [Cut-A-Release](Cut-A-Release) "Dogfood-freshness check"; the installer-boundary smoke test ensures drift never leaks into target projects.
- **No machine-checkable quality score.** `/release`'s documenter pass is adversarial-framed ("find what wasn't documented") but deliberately not an LLM-as-judge quality score — see [principle 4](https://github.com/alexherrero/agentm/blob/main/harness/principles.md#4-deterministic-verification-before-llm-judgment). Structural checks (cross-links resolve, required pages exist) run in CI; "is this page good" is a human call.

**Load-bearing assumptions**

- Target projects want a wiki-shaped narrative, not a single `DOCS.md`. If a small project finds four subdirs overweight, the convention supports collapsing unused sections (each subdir can be empty other than a `README.md` pointer) — but the four-section frame itself is load-bearing for cross-project navigability.
- The GitHub Wiki feature remains available in the repo settings. The sync workflow gracefully skips if the wiki is disabled (the `wiki/` folder stays authoritative locally either way).
- The installer boundary is worth the cost of keeping `wiki/`, `scripts/`, and `tests-*.yml` at the repo root instead of nested under `templates/`. This trades a slightly flatter repo structure for a hard guarantee that drive-by changes cannot leak.
