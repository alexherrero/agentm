<!-- mode: explanation -->
# Named plans (multi-plan state)

> [!NOTE]
> **Status:** pending — declared by [`.harness/PLAN.md`](https://github.com/alexherrero/agentm/blob/main/.harness/PLAN.md) "V5-10 part 1 — Multi-plan state (agentm substrate slice)". The substrate has **partially shipped** — the round-trip lock, `resolve_active_plan`, and the `check-multi-plan-naming` gate are in `main`; the page stays **pending** until the marker writer, named-plan-aware hooks, and the crickets behavioral half land, when it flips to **implemented** with a full implementation trace.

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

The replace-vs-append split is load-bearing. `PLAN-*` files are **replace-style** and go through the vault-write protocol's content-hash compare-and-swap, exactly like the unnamed `PLAN.md`. `progress-*` files are **append-only** — they are never CAS-replaced, because two workers appending to one progress file is naturally mergeable by Drive's append handling, whereas a replace-style write of a progress file would reintroduce the contention this whole design avoids. The append discipline for `progress-<name>.md` is enforced where the *writer* lives (crickets-side); this substrate slice locks the round-trip read contract and documents the append-contract so the writer cannot drift.

## Why this is a naming convention, not a dispatcher rewrite

The state resolver is **already filename-agnostic** — this was verified against the code, not assumed. `vault_state_path(resolution, filename)` is pure path construction, and `safe_write_replace_style` does its content-hash CAS on an arbitrary path, keying on no literal `"PLAN.md"` string. Named plans therefore fall out of the existing resolver for free: a named file is just a different `filename` argument threading through the same path-construction and the same atomic-write-under-mutex machinery (see [Vault write protocol](Vault-Write-Protocol)).

What the substrate work *adds* is not new resolution logic but two guards that lock the contract:

- a test suite that **proves** the round-trip — `PLAN-foo.md` resolves under `_harness/`, named content reads and writes back, content-hash CAS raises on a stale hash exactly as for the singleton, and the conflict janitor flags `PLAN-foo (conflicted copy …).md`. This round-trip contract is now locked by [`scripts/test_harness_memory_named_plans.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory_named_plans.py) (9 tests, no production-code change — the resolver was already filename-agnostic);
- a `check-multi-plan-naming` gate (CI gate #13) that asserts the resolver still exposes the named-plan entry point (`resolve_active_plan` + `harness_state_dir`) and that no curated harness doc silently re-asserts a singleton. This gate shipped in "V5-10 part 1" task 4 — [`scripts/check-multi-plan-naming.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-multi-plan-naming.sh), locked by [`scripts/test_check_multi_plan_naming.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_check_multi_plan_naming.py) (8 tests, including the mandatory negative tests that fail on a re-introduced singleton assertion and on a missing resolver surface).

> [!NOTE]
> **Contract lock landed; feature still pending.** The round-trip half above is test-locked as of "V5-10 part 1" task 1, `resolve_active_plan` (the session→plan binder, [below](#binding-a-session-to-its-plan--resolve_active_plan)) is test-locked as of task 2, and the `check-multi-plan-naming` gate shipped as of task 4. The rest of this substrate — the `.harness/active-plan` marker *writer*, named-plan-aware hooks, and the crickets behavioral half — is **not** shipped; this page stays **pending** until it does.

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
> **This helper is now real (substrate-only).** `resolve_active_plan` and its four helpers (`_read_active_plan_marker`, `_normalize_plan_name`, `_is_safe_plan_slug`, `_plan_pair`) plus a dedicated `ActivePlanError` ship in [`scripts/harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py) as of "V5-10 part 1" task 2, locked by [`scripts/test_resolve_active_plan.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_resolve_active_plan.py) (13 tests across all three precedence branches + the loud-error edges). What is locked: the **precedence order** above, an explicit-arg traversal guard (`ValueError` on a slug that is not a single path component), and `ActivePlanError` on every present-but-unresolvable marker (blank, malformed, traversal, or naming an absent/empty plan). The page stays **pending** because this is the *reader* only — the marker *writer* (worktree-spawn helper), the named-plan-aware hooks, and the crickets behavioral half are still unbuilt.

`resolve_active_plan` is a **reader** of the marker. The marker is *written* by component (2)'s worktree-spawn helper, which is a later V5-10 plan — so the substrate ships the reader of a file whose writer does not exist yet. That is by design (the seam is structural, below); the reader's tests create the marker by hand.

## The agentm/crickets seam

The split between this substrate and its behavioral half is **structural, not stylistic**:

| Owned by agentm (this slice) | Owned by crickets (sibling plan) |
|---|---|
| the named-plan resolver contract + its test lock | the `/work <named-plan>` phase argument |
| `resolve_active_plan` (reads `.harness/active-plan`) | the `progress-<name>.md` append writer |
| the `queue_status_lite` read logic | the `/queue-status-lite` command surface |
| the `check-multi-plan-naming` gate | `/plan` + `/design sequence` emitting `PLAN-<name>.md` |
| named-plan-aware session-start hooks + `doctor` | the two-tier staging → activation UX |

The seam point: this plan's `resolve_active_plan` **reads** `.harness/active-plan`; component (2)'s worktree-spawn helper **writes** it. Because a single `/work` session is single-repo and reads only its own repo's `_harness/`, the split is forced by the architecture — the substrate can be built and verified independently, but an end-to-end "a worker runs `/work foo`" demo needs the crickets sibling plan to exist.

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
- [ADR 0012 — The vault-write protocol](0012-vault-write-protocol) — the concurrency floor (N≥2 writers) that named plans rely on; the hard dependency this slice builds on.
- [ADR 0011 — V5 unbundling of the dev loop](0011-v5-unbundling-dev-loop) — why the phase behavior lives in crickets and the state substrate lives here.
- [Single-repo state mode](Single-Repo-State-Mode) — the related repo-local `.project-mode` marker pattern that `resolve_active_plan`'s `.harness/active-plan` marker mirrors.
- [CI gates](CI-Gates) — the `check-multi-plan-naming` gate (#13) that locks the naming contract.
