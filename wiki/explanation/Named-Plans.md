<!-- mode: explanation -->
# Named plans (multi-plan state)

> [!NOTE]
> **Status:** pending — declared by [`.harness/PLAN.md`](https://github.com/alexherrero/agentm/blob/main/.harness/PLAN.md) "V5-10 part 1 — Multi-plan state (agentm substrate slice)". The substrate has **partially shipped** — the round-trip lock, `resolve_active_plan`, the `check-multi-plan-naming` gate, **and the named-plan-aware session-start hooks + `doctor`** are in `main`. The **crickets behavioral half has since shipped** (the `developer-workflows` `/work`·`/plan`·`/review` `--name <slug>` flag → scoped `PLAN-<slug>.md` / `progress-<slug>.md`, plan "Multi-plan writers", 2026-06-12), so the page now stays **pending** on a **single** remaining dependency — the `.harness/active-plan` marker **writer** (V5-10 component (2)'s worktree-spawn helper) — after which it flips to **implemented** with a full implementation trace.

Why the harness can hold *more than one active plan* in a single shared vault — `PLAN-<name>.md` / `progress-<name>.md` alongside the unnamed singleton — and the resolution model that binds a session to exactly the plan it owns. This is the keystone of V5-10 (the coordinator-directed worker team): it is the substrate that lets N workers each own a distinct plan without colliding on harness state.

## Why this exists

Through V5-0, harness state assumed **one active plan per project**: `read_state_file` / `write_state_file` resolved a fixed `PLAN.md` and `progress.md` under the project's `_harness/`, and every phase spec, hook, and the `doctor` skill looked for *the* `PLAN.md`. That is fine for a solo session, but V5's direction is **concurrent agents — one session per worker** — and several workers driving one singleton plan file would serialize the whole team onto one document.

Named plans remove that bottleneck. A plan can be named — `PLAN-foo.md` with its own `progress-foo.md` — so two workers touch **different files** and never contend on the same document. The contract is strictly **additive**: the unnamed `PLAN.md` / `progress.md` path is unchanged, so a solo session keeps working exactly as before. There is no data migration.

This page is the agentm **substrate** slice only. The behavioral half — the `/work <named-plan>` phase argument, the writer that appends `progress-<name>.md`, the staging UX that emits a named plan — lives in the companion crickets **developer-workflows** plugin (the V5-unbundling boundary: agentm owns the durable state, crickets owns the phase loop). See [The agentm/crickets seam](#the-agentmcrickets-seam) below.

## The naming contract

Named plan files are **flat at the `_harness/` root** — not a per-plan subdirectory, not a generalized `queued-plans/` staging tier (that staging tier is a separate, crickets-side concept).

| File | Style | Written by |
|---|---|---|
| `PLAN.md` (unnamed) | replace (content-hash CAS) | the singleton path — unchanged, back-compatible |
| `PLAN-<name>.md` | replace (content-hash CAS) | the named-plan path |
| `progress.md` (unnamed) | append-only | the singleton path — unchanged |
| `progress-<name>.md` | append-only | the named-plan writer (crickets-side) |

### Optional YAML frontmatter on plan files

Plan files may carry a YAML frontmatter block (delimited by `---`) with two optional coordinator fields introduced in V5-11:

| Field | Type | Purpose |
|---|---|---|
| `depends_on` | list[string] | Slugs of plans that must complete before this one is ready to start. Used by `scripts/readiness.py` for dependency-readiness checks and `scripts/merge_order.py` for topo-sort ordering. |
| `touches` | list[string] | Glob patterns of files this plan modifies (e.g. `scripts/foo*.py`, `wiki/reference/*.md`). Used by `scripts/readiness.py` to detect safe-to-run-together overlap. Plans **without** `touches:` are loudly degraded by the readiness checker — never silently assumed safe to run concurrently. |

Both fields are purely additive — absent fields mean "no declared deps" and "no declared file scope" respectively. A solo-session plan without frontmatter is unchanged by this convention.

The replace-vs-append split is load-bearing. `PLAN-*` files are **replace-style** and go through the vault-write protocol's content-hash compare-and-swap, exactly like the unnamed `PLAN.md`. `progress-*` files are **append-only** — they are never CAS-replaced, because two workers appending to one progress file is naturally mergeable by Drive's append handling, whereas a replace-style write of a progress file would reintroduce the contention this whole design avoids. The append discipline for `progress-<name>.md` is enforced where the *writer* lives (crickets-side); this substrate slice locks the round-trip read contract and documents the append-contract so the writer cannot drift.

## Why this is a naming convention, not a dispatcher rewrite

The state resolver is **already filename-agnostic** — this was verified against the code, not assumed. `vault_state_path(resolution, filename)` is pure path construction, and `safe_write_replace_style` does its content-hash CAS on an arbitrary path, keying on no literal `"PLAN.md"` string. Named plans therefore fall out of the existing resolver for free: a named file is just a different `filename` argument threading through the same path-construction and the same atomic-write-under-mutex machinery (see [Vault write protocol](Vault-Write-Protocol)).

What the substrate work *adds* is not new resolution logic but two guards that lock the contract:

- a test suite that **proves** the round-trip — `PLAN-foo.md` resolves under `_harness/`, named content reads and writes back, content-hash CAS raises on a stale hash exactly as for the singleton, and the conflict janitor flags `PLAN-foo (conflicted copy …).md`. This round-trip contract is now locked by [`scripts/test_harness_memory_named_plans.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory_named_plans.py) (9 tests, no production-code change — the resolver was already filename-agnostic);
- a `check-multi-plan-naming` gate (CI gate #13) that asserts the resolver still exposes the named-plan entry point (`resolve_active_plan` + `harness_state_dir`) and that no curated harness doc silently re-asserts a singleton. This gate shipped in "V5-10 part 1" task 4 — [`scripts/check-multi-plan-naming.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-multi-plan-naming.sh), locked by [`scripts/test_check_multi_plan_naming.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_check_multi_plan_naming.py) (8 tests, including the mandatory negative tests that fail on a re-introduced singleton assertion and on a missing resolver surface).

> [!NOTE]
> **Contract lock landed; feature still pending.** The round-trip half above is test-locked as of "V5-10 part 1" task 1, `resolve_active_plan` (the session→plan binder, [below](#binding-a-session-to-its-plan--resolve_active_plan)) is test-locked as of task 2, the `check-multi-plan-naming` gate shipped as of task 4, and the **named-plan-aware session-start hooks + `doctor`** shipped as of task 5 ([trace below](#what-has-shipped-so-far)). The crickets behavioral half **has since shipped** (the `--name <slug>` flag across `/work`·`/plan`·`/review`, 2026-06-12). The **one** remaining piece of this substrate — the `.harness/active-plan` marker *writer* (component (2)'s worktree-spawn helper) — is **not** shipped; this page stays **pending** until it does.

If any of that work surfaces a *caller* somewhere that hard-codes `"PLAN.md"` (the resolver itself is clean; a caller might not be), the fix is to close it minimally — not to expand scope.

## Binding a session to its plan — `resolve_active_plan`

A worker must end up bound to **exactly one** plan, and a mis-binding (a worker silently working the wrong plan) is the foot-gun this design most wants to prevent. The [`resolve_active_plan`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py) helper makes the binding explicit with a strict precedence:

```
explicit argument
  → worktree-local .harness/active-plan marker
    → legacy PLAN.md / progress.md
```

It returns the resolved plan filename and its matching progress filename. The marker pattern mirrors the existing `.project-mode` repo-local marker (reachable on disk without consulting the vault).

The load-bearing rule: **it errors loudly, it never silently falls back to the singleton.** When `.harness/active-plan` exists but names a plan whose `PLAN-<name>.md` is absent, empty, or ambiguous, `resolve_active_plan` raises rather than quietly degrading to `PLAN.md`. A silent fallback there is precisely how a worker would end up editing the wrong plan; raising turns a mis-binding into an immediate, observable failure. Only the *unset* case — no argument and no marker — resolves to the legacy singleton, which is the intended back-compat path.

> [!NOTE]
> **This helper is now real (substrate-only).** `resolve_active_plan` and its four helpers (`_read_active_plan_marker`, `_normalize_plan_name`, `_is_safe_plan_slug`, `_plan_pair`) plus a dedicated `ActivePlanError` ship in [`scripts/harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py) as of "V5-10 part 1" task 2, locked by [`scripts/test_resolve_active_plan.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_resolve_active_plan.py) (13 tests across all three precedence branches + the loud-error edges). What is locked: the **precedence order** above, an explicit-arg traversal guard (`ValueError` on a slug that is not a single path component), and `ActivePlanError` on every present-but-unresolvable marker (blank, malformed, traversal, or naming an absent/empty plan). The page stays **pending** because this is the *reader* only — the marker *writer* (worktree-spawn helper) is still unbuilt. (The named-plan-aware hooks shipped in task 5; the crickets behavioral half shipped 2026-06-12.)

> [!NOTE]
> **A bash bridge now reaches this reader.** `resolve_active_plan` is a Python function not otherwise callable from a bash phase spec, so a thin [`resolve-active-plan` CLI verb](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L1714) ([dispatch at `#L1884`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L1884)) wraps it: it emits the resolved `<plan_path>\t<progress_path>` pair on stdout so the crickets **developer-workflows** phase specs can target named plans without reimplementing precedence. Exit codes mirror the reader's stance — `0` resolved, `1` no resolvable `_harness/` dir (vault-mode, no vault → graceful-skip), `2` **loud** on a dangling `.harness/active-plan` marker (Risk #7) or an unsafe slug, **never** a silent singleton fallback. This verb shipped **not** as one of the original tasks 1–5 (that plan is done) but as the **consumption bridge** for the separate crickets "multi-plan writers" plan; it is read-only. Locked by the `ResolveActivePlanCLI` class in [`scripts/test_resolve_active_plan.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_resolve_active_plan.py) (8 CLI tests — bare→singleton, `--plan` named, filename-form, valid-marker, dangling-marker exit 2 with no singleton in stdout, unsafe-slug exit 2, no-`_harness/` exit 1, read-only — bringing the file to 21 tests: 13 function + 8 CLI). It does **not** move the page off **pending**: it is one more shipped *reader/consumption* surface, not the marker writer. (The crickets behavioral half that consumes this bridge shipped 2026-06-12.)

`resolve_active_plan` is a **reader** of the marker. The marker is *written* by component (2)'s worktree-spawn helper, which is a later V5-10 plan — so the substrate ships the reader of a file whose writer does not exist yet. That is by design (the seam is structural, below); the reader's tests create the marker by hand.

## The agentm/crickets seam

The split between this substrate and its behavioral half is **structural, not stylistic**:

| Owned by agentm (this slice) | Owned by crickets (sibling plan) |
|---|---|
| the named-plan resolver contract + its test lock | the `/work <named-plan>` phase argument |
| `resolve_active_plan` (reads `.harness/active-plan`) | the `progress-<name>.md` append writer |
| the `resolve-active-plan` **CLI verb** — the bash entry point the crickets phase specs shell to | the phase specs that *consume* the emitted `(PLAN, progress)` pair |
| `list_plan_files(harness_dir)` public function + `list-plans` CLI verb ✅ (shipped, V5-5 task 3) | the session-start hook callers (hooks delegate *to* this verb) |
| the `queue_status_lite` read logic | the `/queue-status-lite` command surface |
| the `check-multi-plan-naming` gate | `/plan` + `/design sequence` emitting `PLAN-<name>.md` |
| named-plan-aware session-start hooks + `doctor` ✅ (shipped, task 5; hooks updated V5-5 task 3) | the two-tier staging → activation UX |

**Crickets-side status (2026-06-12):** the named-plan **writers** shipped — `/work`, `/plan`, and `/review` take a uniform `--name <slug>` flag (the right column's first three rows), resolving the pair through a `resolve_plan.py` bridge that shells to the `resolve-active-plan` verb rather than reimplementing precedence. Still crickets-side pending: the `/queue-status-lite` command surface, `/design sequence` emitting `PLAN-<name>.md`, and the two-tier staging → activation UX.

The seam point: this plan's `resolve_active_plan` **reads** `.harness/active-plan`; component (2)'s worktree-spawn helper **writes** it. Because a single `/work` session is single-repo and reads only its own repo's `_harness/`, the split is forced by the architecture — the substrate can be built and verified independently, but an end-to-end "a worker runs `/work --name foo`" demo needs both the crickets sibling plan (**shipped 2026-06-12**) and the marker writer (still pending).

## What has shipped so far

This substrate is landing task-by-task under "V5-10 part 1". What is in `main` today:

| Surface | Task | Where it lives |
|---|---|---|
| Filename-agnostic round-trip lock (`PLAN-<name>.md` reads/writes back through the same CAS) | 1 | [`scripts/test_harness_memory_named_plans.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory_named_plans.py) (9 tests, no production-code change) |
| `resolve_active_plan` — the session→plan binder (reader of `.harness/active-plan`) | 2 | [`scripts/harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py), locked by [`scripts/test_resolve_active_plan.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_resolve_active_plan.py) (13 tests) |
| `resolve-active-plan` **CLI verb** — the bash bridge that emits the `(PLAN, progress)` pair so crickets phase specs can consume the reader (read-only; exit 0/1/2) | bridge (not a tasks-1–5 deliverable — shipped for the crickets "multi-plan writers" plan) | [`scripts/harness_memory.py#L1714`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L1714) ([dispatch `#L1884`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L1884)), locked by the `ResolveActivePlanCLI` class in [`scripts/test_resolve_active_plan.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_resolve_active_plan.py) (8 CLI tests → file now 21: 13 function + 8 CLI) |
| `check-multi-plan-naming` gate (CI gate #13) | 4 | [`scripts/check-multi-plan-naming.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-multi-plan-naming.sh), locked by [`scripts/test_check_multi_plan_naming.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_check_multi_plan_naming.py) |
| **Named-plan-aware session-start hooks + `doctor`** | **5** | see below |
| `list_plan_files(harness_dir)` public function + `list-plans` CLI verb — plan enumeration via the bridge (V5-5 task 3, commit `a7e3bee`) | V5-5 task 3 | [`scripts/harness_memory.py#L518`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L518) (function) + [`#L1540`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L1540) (parser) + [`#L1743`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L1743) (dispatch); locked by `TestListPlanFiles` + `TestListPlansCLI` in [`scripts/test_harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory.py) (13 tests) |

**Task 5 — named-plan-aware discovery (readers only).** Session boot and `doctor` now *see* named plans, while remaining strict **readers** of the (still-unwritten) `.harness/active-plan` marker:

- **Session-start hooks (both twins).** Before the locked DC-7 singleton check, each hook delegates plan discovery to the `list-plans` CLI verb in `harness_memory.py` (V5-5 task 3, commit `a7e3bee`). The verb enumerates `PLAN*.md` in the `_harness/` dir via `harness_state_dir()` (V5-6-compatible) and emits the `.harness/active-plan` binding when set. When ≥1 named plan exists the hook emits a **named-plan block** listing every `PLAN*.md` and the binding; when zero exist it falls through to the **byte-identical** locked singleton block (back-compat — a solo repo is unchanged). A present marker naming an absent `PLAN-<name>.md` is surfaced as `DANGLING`, never fatal. Bash: [`harness-context-session-start.sh#L69`](https://github.com/alexherrero/agentm/blob/main/harness/hooks/harness-context-session-start/harness-context-session-start.sh#L69) (`list-plans` call) + [`#L87`](https://github.com/alexherrero/agentm/blob/main/harness/hooks/harness-context-session-start/harness-context-session-start.sh#L87) (named-plan branch); PowerShell twin: [`harness-context-session-start.ps1#L50`](https://github.com/alexherrero/agentm/blob/main/harness/hooks/harness-context-session-start/harness-context-session-start.ps1#L50) (`list-plans` call) + [`#L72`](https://github.com/alexherrero/agentm/blob/main/harness/hooks/harness-context-session-start/harness-context-session-start.ps1#L72) (named-plan branch). Covered by 5 named-plan methods in [`scripts/test_harness_context_hook.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_context_hook.py). 13 new tests for `list_plan_files` + `list-plans` CLI in [`scripts/test_harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory.py) (`TestListPlanFiles` 6, `TestListPlansCLI` 7).
- **`doctor` item 4.** Reports the full named-plan set (e.g. `2 named plans: PLAN-foo.md, PLAN-bar.md`), treats a named-only repo (named plans present, unnamed singleton absent) as healthy, and flags a dangling `.harness/active-plan` as **`[WARN]`** — never FAIL, mirroring the hook's non-fatal surfacing. [`harness/skills/doctor.md#L50`](https://github.com/alexherrero/agentm/blob/main/harness/skills/doctor.md#L50).
- **Gate assertion 3.** `check-multi-plan-naming` now also asserts **both** hook twins keep the `PLAN-*.md` glob, so the twins cannot drift apart and silently lose named-plan discovery. [`scripts/check-multi-plan-naming.sh#L89`](https://github.com/alexherrero/agentm/blob/main/scripts/check-multi-plan-naming.sh#L89), with 3 assertion-3 methods in [`scripts/test_check_multi_plan_naming.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_check_multi_plan_naming.py).
- **Conflict-merger operator message.** The GDrive conflict-janitor's notice now names `PLAN-<name>.md` / `progress-<name>.md` alongside the singletons. [`conflict-merger-session-start.sh#L111`](https://github.com/alexherrero/agentm/blob/main/harness/hooks/conflict-merger-session-start/conflict-merger-session-start.sh#L111).

Still **pending** (why the page is not yet `implemented`): the `.harness/active-plan` marker **writer** (component (2)'s worktree-spawn helper — these readers surface a marker nothing writes yet). The crickets behavioral half (`/work`·`/plan`·`/review` `--name <slug>`, the `progress-<name>.md` append writer) **shipped 2026-06-12**, leaving the marker writer as the sole remaining blocker. See [The agentm/crickets seam](#the-agentmcrickets-seam).

## What this slice does not do

Stated plainly so the gap is read as scope, not as unfinished work:

- It creates **no worktrees** — worktree-per-worker is a later V5-10 plan. This slice ships only the *reader* of the `.harness/active-plan` marker, not the writer.
- It ships **no integration / merge command** — combining two workers' output is a later plan.
- It defines **no role agent-defs** (researcher / project-manager / tech-lead / worker) — also later.
- It does **not** retire the `worktrees-never-auto` convention — that changes behavior for concurrent sessions and lands with the worktree component, not here.
- It does **not** arbitrate claims or leases between workers. The read-model ([Queue status lite](Queue-Status-Lite)) is informational only; **the human is the arbiter**.

## Related

- [Queue status lite](Queue-Status-Lite) — the read-only coordinator dashboard that lists every active plan and its status.
- [Vault write protocol](Vault-Write-Protocol) — the per-vault mutex + content-hash CAS + atomic writer that every `PLAN-*` replace-style write goes through.
- [ADR 0012 — The vault-write protocol](memory-storage-seam) — the concurrency floor (N≥2 writers) that named plans rely on; the hard dependency this slice builds on.
- [ADR 0011 — V5 unbundling of the dev loop](0011-v5-unbundling-dev-loop) — why the phase behavior lives in crickets and the state substrate lives here.
- [Single-repo state mode](Single-Repo-State-Mode) — the related repo-local `.project-mode` marker pattern that `resolve_active_plan`'s `.harness/active-plan` marker mirrors.
- [CI gates](CI-Gates) — the `check-multi-plan-naming` gate (#13) that locks the naming contract.
