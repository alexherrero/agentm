# ADR 0007: Auto context storage & recall integrated into harness phases

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-05-22
> **Related:** [ROADMAP item #8](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) · [crickets ADR 0007 — MemoryVault Discovery + Mining](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0007-memoryvault-discovery.md) · [crickets Cross-Repo Memory Protocol](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/Cross-Repo-Memory-Protocol.md) · [Use Auto-Context how-to](../../how-to/Use-Auto-Context-In-Harness-Phases.md)

## Context

ROADMAP item #8 sits as the natural follow-on to #3 (MemoryVault, shipped via #7a + #7b through toolkit v0.10.0): once the durable memory store exists, the harness should *use* it automatically at phase boundaries rather than relying on the agent or human to remember to invoke `/memory search` or `/memory save`.

The gap pre-#8: manual `context-recall` invocation is theater — agents forget; humans forget; the value of a memory system that nobody loads is zero. Phase boundaries are the *natural* recall/save points — they're already deliberate moments where the agent stops to think. Automated load keeps the manual surface (`/memory search` explicit invocation) for "I want context X right now" use cases, not the routine ones.

Five open design questions surfaced at plan time:
1. **Recall budget per phase** — too generous = context bloat + cost; too tight = useless.
2. **Vault project slug discovery** — explicit field, convention-match, or auto-detect?
3. **Graceful-skip when MemoryVault not installed** — error, prompt, or silent?
4. **Auto-save mode default** — offer-and-ask, silent, or off?
5. **`progress.md` ↔ MemoryVault promotion model** — when does ephemeral content become durable?

The skill-side AgentMemory integration shipped in `diataxis-author` (toolkit ADR 0008) settled the convention-read pattern at the skill level; this ADR settles the phase-boundary pattern at the harness level. Together they form the operator-facing auto-context surface for `/setup` `/plan` `/work` `/review` `/release` `/bugfix`.

## Decision

**Ship phase-boundary auto-context via a single stdlib-only Python dispatcher** (`scripts/harness_memory.py`) that all 5 phases + the bugfix pipeline call at predictable boundaries. Toolkit dependency is **soft** — graceful-skip when `crickets/skills/memory/` isn't sibling-cloned or `MEMORY_VAULT_PATH` env is unset. Harness runs identically with or without the toolkit installed.

Five locked design calls Q1–Q5 (resolved at plan time; operator confirmed Q4 + Q5 revisions inline):

### Q1 — Recall budget: per-phase env caps + entry caps

Defaults (tokens, approx via chars/4): `setup`=4k, `plan`=6k, `work`=6k, `review`=4k, `release`=6k, `bugfix`=6k. Override per-phase via `HARNESS_RECALL_BUDGET_<PHASE>` env. Entry cap is 5 per phase by default.

**Why not a single global budget**: phases have legitimately different needs (`/setup` needs only conventions; `/release` needs decisions across the whole project). Asymmetry justifies the per-phase env surface.

**Why not unbounded**: context-window pressure is real; capping protects against vault bloat over months of accumulation. Defaults are tunable post-ship without a release.

### Q2 — Vault project slug: explicit field + 3-tier auto-detect

New `vault_project` field in `.harness/project.json`. Read fallback chain in `scripts/vault_project.py`:
1. Explicit `vault_project` field (operator-set; highest signal).
2. `github.repo` field's basename (operator linked the repo to GH Projects, signaling intent).
3. `git remote get-url origin` → strip `.git` → basename (no operator signal beyond having a remote).

**Why not pure convention-match**: monorepos and projects where the vault entry name diverges from repo name (e.g. internal "scratchpad-mvp" → vault entry "Mobile Platform Q3") need explicit override.

**Why not require manual config**: auto-detect covers 95% of cases; explicit override available when needed. Friction at /setup is real.

### Q3 — Graceful-skip when MemoryVault not installed: silent + unconditional invoke

Phase specs invoke `harness_memory.py` unconditionally; the dispatcher itself handles graceful-skip:
- `MEMORY_VAULT_PATH` env unset or directory missing → `recall` exits 0 with empty payload; `offer-save` + `plan-done-promotion` no-op with exit 0; `available` exits 1.
- Toolkit memory scripts not found via 3-tier discovery (`HARNESS_MEMORY_TOOLKIT_PATH` env > sibling-clone > `~/Antigravity/crickets/`) → save calls record intent only with a stderr notice; recall still returns empty.

**Why this pattern**: matches existing `gh project` graceful-skip in `/release` § GitHub-Projects + `commit-on-stop` graceful-skip in `/work`. Operators upgrading harness without toolkit see zero behavior change.

**Why not error loudly**: ignores the "harness works standalone" constraint stated in plan #8's brief. Forcing toolkit install breaks operators on systems where MemoryVault wasn't yet adopted.

### Q4 — Auto-save mode: self-modulating ask + confidence threshold (revised 2026-05-22 per operator)

Default mode `HARNESS_AUTO_SAVE_MODE=ask`, but `ask` is **confidence-modulated**, not flat. Each `offer-save` invocation from a phase spec passes a `--confidence <0-1>` flag populated by the agent's self-evaluation of the candidate (rubric: high = direct decision/quote recorded this session + matches existing convention pattern; low = inferred-from-context + first-of-its-kind). Behavior:
- `confidence ≥ HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD` (default 0.8) → silent save with `[auto-saved high-confidence]` stderr notice.
- `confidence < threshold` OR `--confidence` omitted → preview-and-ask prompt fires.
- Outer envelope (`HARNESS_AUTO_SAVE_MODE=silent|off`) overrides: `silent` always skips prompt regardless of confidence; `off` never saves.

**Why this design**: flat `ask` produces fatigue and trains operators to reflex-confirm without reading; flat `silent` produces vault bloat early before the heuristic is trustworthy. Self-modulating gets both — high-signal saves go through frictionlessly; ambiguous cases get human judgment.

**Why not silent default**: operator trust in the agent's confidence calibration has to be earned over time; first weeks of dogfood need feedback signal from confirmed prompts.

**Why not off default**: would render the feature dead-on-arrival — nobody would discover it via the env override.

### Q5 — `progress.md` ↔ MemoryVault promotion: dual-trigger + cursor-tracked (revised 2026-05-22 per operator)

`.harness/progress.md` stays per-project, ephemeral, append-only (unchanged). Tail-scan promotion runs at **two triggers**:
1. When a `/work` task `[x]` flip transitions `Status: in-progress → done` (catches end-of-plan even when no immediate release follows).
2. At `/release` (existing release-cut moment).

Both triggers invoke the same `harness_memory.py plan-done-promotion` sub-command. Cursor-tracked via `.harness/.promoted-progress-cursor` (records last-promoted byte offset). Re-invocation is idempotent — second trigger no-ops because cursor advanced.

**Why two triggers and not just `/release`**: plan-done is when items have actually settled (the last task locked the decisions); waiting for `/release` could be days later or never (internal refactors, doc-only plans don't ship as releases).

**Why cursor-tracked**: avoids re-summarizing the same progress.md entries each trigger; cheap implementation; recoverable on cursor-file deletion (worst case: re-prompts a few already-saved candidates which the toolkit's `save.py` deduplicates).

## Consequences

### Positive

- **Operators get phase-boundary recall + save without remembering** to invoke `/memory search` or `/memory save`. The feature is opt-in via `MEMORY_VAULT_PATH` env but no per-phase opt-in needed once that's set.
- **Five phase specs share one dispatcher** — adding a new phase means one new `_PHASE_PROJECT_DIRS[phase]` entry + one new spec amendment, not new copy-pasted recall/save code.
- **Sub-letter section pattern** (`§1b` / `§4c` / `§7b` / `§7c` / `§5b` / `§5c`) preserves integer §-numbering — incoming wiki refs that cite "§N" stay valid; only line-range anchors need updating. 5 wiki anchors updated in this plan; zero §-number references broken.
- **Confidence-modulated ask** sidesteps both the fatigue failure mode (flat `ask` trains reflex-confirm) and the noise failure mode (flat `silent` pollutes the vault before heuristic earns trust).
- **Shared cursor between `/work` plan-done + `/release` tail-scan** means promotion fires exactly once per plan-window — never doubled, never missed.
- **Stdlib-only Python + cross-platform smoke tests on 3 OS CI** continues the established quality bar (ADR 0001 D7 / crickets ADR 0007 D7).

### Negative

- **First non-doc-only paired pair in the run** — harness ships v2.5.0 MINOR (real new phase behavior); toolkit ships v0.11.1 PATCH (wiki additions only). Operators relying on phase-spec stability will see a behavioral change; CHANGELOG documents the change clearly.
- **Recall-budget defaults are educated guesses** — may need adjustment after a week of dogfood. Acceptable risk; env-var-driven so operators can tune without a release.
- **Confidence-rubric miscalibration** — agent may over-confident itself into silently saving noise. Mitigations: rubric in phase spec is short + checkable; threshold env (default 0.8) tunable; silent-saves emit a stderr notice so operators can scan + flag false-positives.
- **Cursor-file drift at plan-done promotion** — if progress.md is edited (not just appended), offsets become stale. Mitigation: progress.md is append-only by convention; cursor-file deletion recovery worst-case re-prompts a few already-saved candidates.

### Load-bearing assumptions (re-audit triggers)

1. **Operator maintains 1–5 harness-installed projects** that share a single vault. Re-audit if managed project count grows to ~10+ AND per-project context-divergence becomes painful (consider per-project budget overrides or vault-level project isolation).

2. **`MEMORY_VAULT_PATH` env reliably identifies vault location across shells + hosts** (Claude Code, Antigravity, Gemini-CLI removed in v2.4.0). Re-audit if a new host doesn't propagate env consistently OR vault moves to a non-filesystem backend (cloud-native, encrypted-at-rest with key rotation, etc.).

3. **Per-phase budget defaults (4k–6k tokens) are appropriate for current-generation context windows** (200k+ tokens). Re-audit if context window economics shift dramatically (e.g. 10× cheaper → budgets too tight; 10× more expensive → budgets too generous).

4. **Cursor-file model survives append-only progress.md convention.** Re-audit if progress.md format evolves to allow mid-file edits (e.g. inline corrections, retroactive annotations) OR if multi-process write contention becomes a concern.

## Amendment 2026-05-27

**V4 #36 reorganization.** The "Toolkit dependency is soft — graceful-skip when `crickets/skills/memory/` isn't sibling-cloned" framing reflects the v3.x reality where the memory skill lived in Crickets. **As of agentm v4.0.0 (V4 #36 reorg) the memory skill moved to Agent M itself at `harness/skills/memory/`** per [ADR 0012 (device-wide-by-default)](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0012-device-wide-by-default.md).

The `scripts/harness_memory.py` `toolkit_scripts_dir()` resolver now checks four paths in order:

1. `HARNESS_MEMORY_TOOLKIT_PATH` env override.
2. **`<harness_repo>/harness/skills/memory/scripts/`** (v4.0.0+ canonical — the new home).
3. `<harness_repo>/../crickets/skills/memory/scripts/` (legacy v3.x sibling-clone fallback).
4. `~/Antigravity/crickets/skills/memory/scripts/` (legacy v3.x canonical-install fallback).

The soft-dependency contract from this ADR survives: the harness still runs identically when the memory skill is absent — `available()` returns the same exit codes, every phase still graceful-skips silently. Only the resolution path changed.

The two legacy crickets paths are kept in the resolver chain for operators on v3.x catalogs who haven't upgraded yet. Once V4 #26 (state migration) lands and v3.x is fully sunsetted, the legacy paths can be dropped.

## Amendment 2026-05-28

**V4 #35 — documenter vault-context resolution.** This ADR established the phase-boundary dispatcher (`scripts/harness_memory.py`) with the per-phase recall budgets of Q1 and the "five phase specs share one dispatcher; adding a phase is one `_PHASE_PROJECT_DIRS` entry" consequence. **As of agentm v4.6.0 (V4 #35) the same dispatcher now serves doc-writing time** — the documenter and the doc-touching skills read their conventions and decisions from the vault instead of re-deriving them from the repo.

A new `documenter` recall phase joins `_VALID_PHASES`, `_DEFAULT_BUDGETS`, `_RECALL_QUERIES`, and `_PHASE_PROJECT_DIRS["documenter"] = ("_index.md", "decisions", "wiki-style")`. It is **not** one of the six lifecycle phases you invoke — it's a recall-context pseudo-phase consumed by doc-touching customizations. Adding it cost exactly one `_PHASE_PROJECT_DIRS` entry, which is the cheap-extension consequence this ADR predicted.

What V4 #35 wired up:

- **`resolve_documenter_context(slug)`** — a structured-bundle helper returning `{slug, registered, operator_conventions, project_decisions, project_anchor, wiki_style}`. Returns `None` when the vault is unavailable.
- **`documenter-context` CLI subcommand** — `--slug`, `--budget`, `--format text|json`. Exit codes: `0` = bundle rendered; `1` = vault unavailable; `2` = vault reachable but slug not registered.
- **Three doc-touching primitives now consume the bundle**: the `documenter` sub-agent runs the pre-flight before scanning `wiki/`; the `wiki-author` skill surfaces the bundle in its preview-before-write step; and the `diataxis-author` skill routes its operator-convention read through the same resolver. This closes the documenter side of the V4 #26 state-migration loop — project state moved to `<vault>/projects/<slug>/`, and now the doc tooling reads its conventions and decisions from there too.

The **graceful-skip contract** carries over from Q3: on rc `1` (vault unreachable) all three primitives emit a one-line stderr notice and fall back to pre-v4.6.0 repo-local behavior. A missing vault is never a hard failure — the same soft-dependency framing this ADR locked for the six lifecycle phases.

One **budget revision supersedes a sliver of Q1's defaults table for this phase**: the documenter budget shipped at 4k, but the dogfood showed 4k truncated away the project decisions (31 always-load conventions total roughly 27k tokens). It was raised to 10k, and the documenter recall now emits project context (decisions / `_index`) *before* the always-load conventions via `phase_recall(project_first=True)`, so project decisions survive truncation. Override with `HARNESS_RECALL_BUDGET_DOCUMENTER`.

Commits: `da63046` (resolver + CLI), `fbb5b89` (primitive wiring), `6090fc4` (budget + project-first tuning). This amendment was itself authored by the documenter sub-agent through the new resolver — a dogfood of the feature it documents.

## Amendment 2026-05-31

**Re-audit trigger for assumption #2 fired.** Load-bearing assumption #2 above held that `MEMORY_VAULT_PATH` env reliably identifies vault location across hosts. It does not for **SessionStart hooks on user-scope installs**: Claude Code does not inject `MEMORY_VAULT_PATH` into the hook environment (it is not in shell profiles or `settings.json` env either), so a hook that reads only the env var silently exits 0 on every real session boot.

This bit twice. `memory-recall-session-start` hit it first and resolved it by porting a `_resolve_vault_path()` fallback (`env → .agentm-config.json::vault_path → none`). `conflict-merger-session-start` then shipped reading only the env var and was functionally inert at boot until the same fallback was ported in (`64acaa6`); its V4 #26 cross-agent / cross-device conflict detection was structurally installed and wired but never ran. The recurrence across two hooks promotes this from an incident to a **convention**: every vault-aware SessionStart hook (and its pwsh twin) resolves the vault via `env → .agentm-config.json::vault_path → none`, never the env var alone. Regression coverage: `scripts/test_conflict_merger_hook.py` drives the bash hook with `MEMORY_VAULT_PATH` unset + a fixture config and asserts the fallback path resolves.

This does not invalidate assumption #2 for the six lifecycle phases (the harness dispatcher runs in the foreground session where the env var *is* present) — it narrows it: the env-var assumption holds for in-session dispatcher calls but **not** for hook environments, which must always carry the config fallback.

## Related

- [ROADMAP item #8](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) — the roadmap entry that triggered this work.
- [crickets ADR 0007 — MemoryVault Discovery + Mining](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0007-memoryvault-discovery.md) — upstream for the vault layout (`personal-private/_always-load/`, `projects/<slug>/decisions/` — renamed from `personal-projects/<slug>/` in V4 #26, etc.) this dispatcher reads + writes.
- [crickets Cross-Repo Memory Protocol](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/Cross-Repo-Memory-Protocol.md) — toolkit-side companion documenting the harness↔toolkit-memory contract.
- [Use Auto-Context how-to](../../how-to/Use-Auto-Context-In-Harness-Phases.md) — operator-facing per-phase walkthrough + env-var matrix + troubleshooting.
- [crickets ADR 0001 — crickets purpose](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0001-crickets-purpose.md) — stdlib-only / no-new-third-party-deps convention all dispatcher code follows.
- [harness ADR 0006 — crickets split](0006-crickets-split) — original split decision; this ADR continues the soft-dependency pattern (toolkit absent → harness runs unchanged).
- Phase specs amended: [01-setup](../../../harness/phases/01-setup.md) §1b + §8b · [02-plan](../../../harness/phases/02-plan.md) §1b + §4c · [03-work](../../../harness/phases/03-work.md) §1b + §7b + §7c · [04-review](../../../harness/phases/04-review.md) §2b · [05-release](../../../harness/phases/05-release.md) §1c + §5b + §5c · [pipelines/bugfix](../../../harness/pipelines/bugfix.md) §2b + §4b.
