---
title: goal contract — design
status: launched
kind: design
scope: feature
area: agentm/personas
governs: []
parent: agentm-hld.md
seeded: 2026-06-26
approved: 2026-06-26
---

> **A /goal is a persona pursuing an objective on the host's built-in autonomous run — Claude Code's `/goal`, Antigravity's Agent Manager — under the contract no host provides: a checkable definition of *done* and clear stop conditions.** The host runs the loop; this design adds the guardrails — parent [agentm HLD](agentm-hld).

# AgentM Goal Contract Design

## Objective

Both hosts already run goals autonomously. Claude Code has a built-in `/goal [condition]` that loops across turns until a separate evaluator says the condition holds; Antigravity's Agent Manager drives a multi-step agent through a plan until it reports finished. The loop, the cross-turn persistence, even — on Claude — an evaluator-checked completion string are the host's. What no host provides is the *contract* that makes an unattended run trustworthy, and that gap is what this design fills.

The persona goal mode is named in four roster rows of the [personas](agentm-personas) design and listed as a [runner](agentm-runner) consumer, but the contract is specified nowhere — the design-critique rated that absence the highest runtime risk. The contract is what the host loop is blind to: how a run decides it is **done** (a checkable success criterion, deterministic gates first), what **stops** it, and what keeps it from **grading its own work too leniently**. agentm adds these on top of the host's run, the way the [runner](agentm-runner) adds vault-safe writes on top of the host's built-in scheduler.

## Overview

A goal run is the host's autonomous loop wrapped in agentm's contract: a falsifiable objective and its stop bounds go in, the host iterates, each turn is gated by a success check the agent cannot edit, and there are exactly four ways out.

| The host provides | agentm adds — the contract |
|---|---|
| the autonomous loop + cross-turn persistence (Claude Code's `/goal [condition]`; Antigravity's Agent Manager + plan/task-list) | a **falsifiable objective** at intake, and the [`done`](agentm-opinions-and-gates) opinion as the **success criterion** (deterministic gates first) |
| a turn-or-condition stop (Claude evaluates a typed string; Antigravity demonstrates completion via a Walkthrough) | **anti-gaming** — an uneditable done-check, and completion confirmed by a cold [`/review`](https://github.com/alexherrero/crickets/wiki/crickets-code-review) |
| cost + turn limits (`max_turns`, `max_budget_usd`, advisory `task_budget`; Antigravity's Sprint quota) | a **convergence bound** — an iteration cap + an optional deadline — and a session sentinel for crash-resume |
| per-agent model pinning (a dropdown, or a `/model` nudge) | **persona → model + effort** binding applied at activation |
| — | the **four-exit** stop taxonomy and durable on-disk **self-replanning** state |

![A goal run is the host's autonomous loop wrapped by agentm's contract: a falsifiable objective + a stop bound (iterations, deadline) feed the host's run (Claude /goal or Antigravity Agent Manager); agentm wraps each turn with a limit check, a safety pre-check, and a verify against an uneditable done-check, exiting on one of four conditions — done (confirmed by a cold /review), limit-reached, stuck, or needs-operator-decision](diagrams/agentm-goal-contract.svg)

*The host runs the loop; agentm wraps each turn — a limit check, a success check the agent cannot edit, and four ways out, one confirmed by a cold `/review`.*

## Design

### Riding the host run — two ways

agentm ships no `/goal` command: the roadmap records one as not-added, and Claude Code already owns `/goal`. agentm's contribution is the **goal launch mode** a persona declares in its `modes:` axis ([persona activation](agentm-persona-activation)), which rides the host's autonomous run two sanctioned ways:

- **In a session** — an interactive session puts on a goal-mode persona and pursues the objective without waiting per-turn, until it stops. On Claude this can hand the falsifiable condition to the native `/goal` and let its evaluator-loop be the heartbeat; on Antigravity it rides the Agent-Manager loop. The model is advisory here (the host can nudge `/model`). This path inherits `/work`'s safety pre-check and iteration cap but not the runner's watchdog, so an in-session goal is the less-guarded realization — bounded by the iteration cap and operator attention.
- **As a runner job** — an unattended goal-run rides the [runner](agentm-runner) (`.harness/jobs/<name>.yaml`), under its watchdog and crash-resume. ([persona activation](agentm-persona-activation)'s "/goal runner" is this realization.)

The plumbing also allows a sub-agent dispatch, but a one-shot return fights open-ended pursuit, so the in-session and runner-job paths are the two sanctioned realizations.

### The contract — seven terms

- **Objective** — the operator states a falsifiable goal directly: the un-decomposed analogue of a `PLAN.md`. An objective whose success cannot be made executable is refused at intake, the way [`/work`](https://github.com/alexherrero/crickets/wiki/crickets-development-lifecycle) halts on an unverifiable task.
- **Bound** — a convergence ceiling: an **iteration cap** (a whole-goal `--max-iterations` plus the inherited five-per-gate cap) and an optional **deadline**. Cost is the host's account-level concern (`max_budget_usd`, the Sprint quota); a goal carries no per-goal token budget. A session sentinel makes a crash resumable, so it re-runs the in-flight iteration safely.
- **Success** — the [`done`](agentm-opinions-and-gates) opinion by default (its deterministic check battery plus the written completeness conventions), or an operator `--accept` executable test. Deterministic gates run first; the judgment augments them.
- **Stop** — exactly four exits (below).
- **The loop** — one iteration (below).
- **Report** — the [digest](https://github.com/alexherrero/crickets/wiki/crickets-reporting) when the runner backgrounds it; a ≤5-bullet end-of-run summary in a live session, whose required line is *why it stopped*.
- **Persona + tier** — a goal is always a persona in goal mode; the tier resolves through [model + effort routing](agentm-model-effort-routing) at adoption, and the brain ([memory system](agentm-memory-system)) composes beneath every run.

### The loop — one iteration

One turn of the host's autonomous run, wrapped by the contract (single-threaded always — coherence-critical work never fans out). The host drives **plan** and **act**; agentm wraps each turn with the rest:

1. **Limit check** *(agentm)* — stop if the iteration cap or the deadline is reached.
2. **Safety pre-check** *(agentm)* — the `/work` go/no-go (safe · recoverable · in-scope · verifiable); a failure here is the needs-operator-decision exit, evaluated every pass.
3. **Plan / re-plan** *(host loop; agentm governs sizing)* — a goal owns its plan and revises the remaining work against the last verify result each pass. The *how-we-engineer* opinion governs decomposition sizing.
4. **Act** *(host loop)* — single-threaded implementation; read-only fan-out (the explorer sub-agent) gathers context, and implementation stays single-threaded.
5. **Verify** *(agentm)* — deterministic gates first, then the success criterion; feed the full error output back on failure so the next pass sees it.
6. **Persist to disk** — append `progress.md` and update the goal's own plan; state lives on disk, so a resumed goal is recoverable.
7. **Decide** — done → exit; otherwise loop to step 1.

The cycle is designed to be cursor-backed and idempotent, like the runner's poll → run → advance, so a crashed goal the runner resumes re-runs the in-flight iteration safely.

### The four exits

- **Done** — the success criterion passes behind green deterministic gates, and a cold [`/review`](https://github.com/alexherrero/crickets/wiki/crickets-code-review) confirms it.
- **Limit-reached** — `--max-iterations` or the deadline is reached.
- **Stuck** — the runner's watchdog auto-pauses on no progress (spiking revert churn, no gate movement), or the five-per-gate cap exhausts. These are deterministic signals. The watchdog is runner-only; an in-session goal falls back to the cap alone, which catches a stuck gate but not a no-progress spin with green gates.
- **Needs-operator-decision** — the safety pre-check fires: an unrecoverable action, an unsettled decision the objective never resolved, scope drift, or an unverifiable prerequisite.

These are the graceful terminations. An uncaught crash with no resume is out of contract: with the runner, its session sentinel re-runs the in-flight iteration; without it, an in-session goal simply ends.

### Anti-gaming — two invariants, locked before the runner builds

The host loop pursues the objective but is blind to whether the success check is honest: an autonomous run that grades its own work could weaken its own check to pass. No host guards this. Two invariants close it (operator ruling):

- **The done-check is uneditable by the running agent.** The running agent reads the success criterion and cannot rewrite it — the same rule that forbids a runner editing a test to make it pass.
- **Completion routes through `/review`.** A goal reports done only after a cold [Reviewer](agentm-personas) sub-agent confirms it.

The done-check binds the runner; the cold `/review` rides the persona-activation sub-agent dispatch. Until that chain ships, both are a design contract the runner build must honor, not yet a runtime guard — except for the Decide step itself (loop step 7 below), which now enforces both invariants mechanically in isolation, and now has a real caller in the N1 overnight sequence (`scripts/control_plane/n1_run.py`): see the amendment log.

### The boundary

- **vs the host autonomous run** (Claude `/goal`, Antigravity Agent Manager) — the host runs the loop and, on Claude, checks a typed completion string; agentm supplies the falsifiable objective, the deterministic-gates-first success check, the convergence bound, and the persona binding. The goal mode rides the host run; it does not reimplement it.
- **vs [`/work`](https://github.com/alexherrero/crickets/wiki/crickets-development-lifecycle)** — `/work` is bounded by its finite task list (done = the last task checked); a goal owns and re-plans its work and decides done by this contract. A goal reuses `/work`'s autonomy doctrine, safety pre-check, five-per-gate cap, and verify shape, and adds self-planning plus the convergence bound.
- **vs `/loop`** — both are open-ended, in different shapes: a `/loop` repeats a fixed step on a cadence and never finishes; a goal converges and stops when the objective is met. A backgrounded `/loop` needs a live session; a backgrounded goal rides the runner.
- **vs the [runner](agentm-runner)** — the runner is the substrate a goal run consumes one-way (its watchdog, sentinel, and digest); the goal mode is the consumer.
- **vs interactive** — interactive waits for the human each turn; a goal runs until it stops.
- **crickets stays the consumer** — `/work`'s "runnable one-off / loop / goal" framing references this agentm contract; it does not own it (the one-way crickets → agentm dependency).

## Dependencies

- **composes the [runner](agentm-runner)** — the watchdog · sentinel · digest for an unattended goal-run.
- **requests [`done`](agentm-opinions-and-gates)** — the success criterion, resolved through the [opinion registry](agentm-opinion-registry).
- **routes completion through crickets [`/review`](https://github.com/alexherrero/crickets/wiki/crickets-code-review)** — the cold adversarial confirm.
- **runs as a persona in goal mode** — the `modes:` axis + the tier binding ([persona activation](agentm-persona-activation), [model + effort routing](agentm-model-effort-routing)); the brain ([memory system](agentm-memory-system)) beneath.
- **generalizes crickets [`/work`](https://github.com/alexherrero/crickets/wiki/crickets-development-lifecycle)** — reuses its autonomy doctrine, safety pre-check, and verify shape.
- Points up at the [agentm HLD](agentm-hld) §Personas.

## Migrations

- **At lift (docs):** add a `goal-contract` seeding row under the `agentm/personas` area in the area-taxonomy (no new area — it joins personas / persona-activation there).
- **Reconcile the runner consumer table:** `agentm-runner.md` attributes "run-until-done goals" to "Maintainer / Researcher," but the roster gives the Maintainer the loop-only mode. Drop the Maintainer from the goal attribution — the goal-declaring personas are Engineer, Researcher, Architect, and Designer.
- **At build:** the contract is buildable only behind the runner + persona-activation + model-effort-routing, so it is a late adopter of that substrate. Lock the two anti-gaming invariants before the runner builds (operator ruling), since the runner is the path that makes an unattended goal possible.

## Risks & open questions

- **Buildable only behind a chain.** A goal run leans on the runner + persona-activation + model-effort-routing, all designed-only. Until that chain ships, the goal mode is a declared `modes:` value with no executor; no design may claim a working `/goal`.
- **Anti-gaming.** Without the uneditable done-check + mandatory `/review`, an autonomous goal could weaken its own success criterion to pass; the two invariants close that. The cold-`/review` invariant is itself enforceable only once the persona-activation sub-agent dispatch ships.
- **Cost is the host's to bound.** A goal carries no per-goal token budget (operator call); it is bounded by the iteration cap + deadline, and cost is governed by the host's account-level limits (`max_budget_usd`, the Sprint quota) and, for a runner job, the runner's fleet ceiling. The convergence bound is what stops a goal.
- **Headless feasibility.** A `/loop` needs a live session, so a backgrounded goal-run rides the runner + the host's Scheduled Tasks, not `/loop`. Build the in-session path first (advisory model, `/work`-style summary), the runner-backed path second.
- **Host capabilities move fast.** The host-provided column (Claude's `/goal` evaluator + the autonomous billing pool; Antigravity's quota + completion mechanics) shifts with each release; the contract agentm adds is the stable part. Treat the host column as a re-audit trigger, per the harness's re-audit-when-the-model-ships rule.
- **Re-audit triggers:** lock the two invariants before the runner builds; reconcile the runner's Maintainer-goal table at this lift; re-audit the host-provided column on a host release; flip `[PENDING-IMPL]` when the runner + activation chain ships.

## Locked design calls

- **Ride the host's loop; add the contract.** The host provides the autonomous run (Claude `/goal`, Antigravity Agent Manager); agentm provides the success check, the convergence bound, the anti-gaming, the persona binding, and the four exits — the same reframe as the runner riding host-native scheduled tasks.
- **No agentm `/goal` command** (roadmap not-added; Claude already owns `/goal`) — the goal mode lives in the `modes:` axis; an in-session adoption and a runner job are the two realizations.
- **Completion routes through `/review`** (operator ruling) — a goal never self-certifies.
- **The done-check is uneditable** (operator ruling) — the second anti-gaming invariant alongside completion-via-`/review`, locked before the runner builds.
- **No per-goal token budget** (operator call) — a goal is bounded by the iteration cap + deadline; cost is the host's account-level concern (`max_budget_usd`, the Sprint quota).
- **Single-threaded** — a goal's loop never fans out parallel implementers; read-only fan-out is for context only.
- **Four exits** — done · limit-reached · stuck · needs-operator-decision; every *graceful* termination is one of these, and the summary names which. An uncaught crash with no resume is out of contract (the runner's sentinel handles it).
- **Reuses `/work` wholesale** — autonomy doctrine, safety pre-check, five-per-gate cap, verify-first; it adds self-planning and the convergence bound.

## Open decisions (recommended defaults — confirm at review)

1. **Watchdog STUCK** — **pause-for-operator, resumable** *(recommended)*, vs a hard stop. The runner watchdog already has pause as a distinct rung and the sentinel makes resume safe; a no-progress goal is often one unblock away, so a pause preserves the in-flight session. Reserve hard-stop for the limit-reached exit.
2. **Default persona** when `--persona` is omitted and the objective is ambiguous — **Engineer/T1** *(recommended)*, the cheapest long-stretch (opusplan), which leans on *done* (the success criterion). Inference still wins when the objective clearly implies the Researcher, Architect, or Designer.

## References

- **The precedent to generalize:** crickets [`/work`](https://github.com/alexherrero/crickets/wiki/crickets-development-lifecycle) — the plan-driven autonomous loop (autonomy doctrine, safety pre-check, five-per-gate cap, verify-first)
- **Composes:** [runner](agentm-runner) (watchdog · sentinel · digest) · [`done`](agentm-opinions-and-gates) via the [opinion registry](agentm-opinion-registry) · crickets [`/review`](https://github.com/alexherrero/crickets/wiki/crickets-code-review) (the cold confirm) · [model + effort routing](agentm-model-effort-routing) (the tier) · the brain ([memory system](agentm-memory-system))
- **Up:** [agentm HLD](agentm-hld) §Personas · [persona activation](agentm-persona-activation) (goal is one of its four launch modes)
- **Driver:** crickets [development-lifecycle](https://github.com/alexherrero/crickets/wiki/crickets-development-lifecycle) (references this contract; does not own it)
- DESIGN-CRITIQUE **W10** (the anti-gaming gap) · ROADMAP-DELTA **research-goal-semantics**

## Amendment log

*Newest first. Collapses to one ≤2-paragraph entry at finalization; git holds the granular history.*

- **2026-07-14 — the Decide step gets its first real caller: the N1 overnight sequence (proving-ledger item 19).** `scripts/control_plane/n1_run.py`'s `run_n1_sequence()` now consults `goal_contract.decide()` for the run's own done determination instead of leaving it unstated. When `N1Config.done_check_path` is set, the done-check is fingerprinted before dispatch runs (goal start) and re-checked at decide time (after dispatch); dispatch returncodes stand in for `gates_green`; `cold_review_confirmed` defaults to `False` and must come from an actual external confirmation, never derived from the run itself. `report.decision` carries the result (`None` when no done-check is configured — no contract, no guess). A new CLI surface (`--done-check`, `--cold-review-confirmed`) makes this reachable from an unattended overnight invocation, not just from a test. Verified by six new tests in `scripts/control_plane/test_n1_run.py`, including one that simulates a running dispatcher weakening its own done-check mid-run and confirms `decide()` still refuses it (`needs-operator-decision`, not `done`) even with green dispatch and `cold_review_confirmed=True`. **Still not wired:** the other six loop steps (limit check, safety pre-check, plan/act, verify, persist) and the actual cold-`/review` sub-agent dispatch that would produce a real `cold_review_confirmed` signal — this closes the "no caller" gap only, not the full seven-step loop.

- **2026-07-07 — Decide step (loop step 7) ships in isolation (AG Wave E, PLAN-wave-e-scheduled-surfaces task 3).** `scripts/goal_contract.py` implements the Decide step alone — not the full seven-step loop — plus the done-check integrity check it depends on: `snapshot_done_check` / `done_check_tampered` fingerprint the done-check (the `done` opinion or an operator `--accept` test) at goal start and re-check it at decide time, and `decide(...)` returns `done` only when the done-check is untampered **and** gates are green **and** `cold_review_confirmed=True` is passed in explicitly — it can never reach `done` from green gates alone. This is the first concrete, testable form of both locked anti-gaming invariants (uneditable done-check; completion routes through `/review`), enforced as tamper-detection-and-refusal rather than OS-level prevention, mirroring AGENTS.md rule 5's treatment of a test the agent might weaken. **Still not built:** the other six loop steps (limit check, safety pre-check, plan/act, verify, persist) — the design still points these at `/work`'s own machinery — and nothing yet wires this module into an actual host loop (Claude's `/goal`, Antigravity's Agent Manager). Nothing yet *dispatches* a cold `/review` sub-agent and feeds its result into `cold_review_confirmed`; this module only enforces that the gate can't be bypassed once that signal exists, not how the signal gets produced. The runner + persona-activation chain this section names remains unshipped. Verified by `scripts/test_goal_contract.py` (6 tests: tamper detection, tampered-rejected-first, green-gates-alone-insufficient, gates-not-green-insufficient, positive path).

- **2026-06-28 — lock-down sweep (operator review).** All standing fixes clean (diagram sized; no mermaid; no ADR mentions; log already newest-first). Confirmed rides-the-host-engine + adds-only-the-contract (no `/goal` command; token budget removed) and the locked anti-gaming invariants (uneditable done-check + cold `/review`). The two open-for-review items (STUCK = pause-resumable; default persona Engineer/T1) and the Maintainer over-list reconcile stay flagged for the runner/activation build. No content change. Locked as a v5–v8 guidepost.

- **2026-06-26 — authored, reframed, and budget-removed; approved + launched.** The fifth Bucket-A substrate sub-design, writing the contract behind the persona **goal launch mode** — named in four roster rows + a runner consumer, but specified nowhere. **Reframed (operator):** both hosts ship the autonomous goal *engine* (Claude Code's built-in `/goal [condition]` evaluator-loop; Antigravity's Agent Manager), so this design rides the host run and adds only the contract no host provides — done-determination, anti-gaming, the convergence bound, persona binding — mirroring the runner's reframe onto host-native scheduled tasks; agentm ships no `/goal` command (Claude owns it). **Token budget removed (operator):** a goal carries no per-goal token budget — it is bounded by the iteration cap + deadline, and cost is the host's account-level concern (`max_budget_usd`, the Sprint quota). A goal is a persona in goal mode under the contract (objective · bound · success · stop · loop · report · persona+tier): a falsifiable objective; a single-threaded loop (limit check → safety pre-check → plan/re-plan → act → verify → persist → decide); *done* via the `done` opinion (or an `--accept` test) behind deterministic gates; four exits (done · limit-reached · stuck · needs-operator-decision). It reuses `/work`'s autonomy doctrine wholesale and adds self-planning + the convergence bound. **Anti-gaming (operator ruling):** the done-check is uneditable and completion routes through a cold `/review` — two invariants locked before the runner builds. **Open for review:** watchdog STUCK = pause-resumable; default persona = Engineer/T1. **Reconcile at lift:** the runner consumer table over-lists the Maintainer as a goal persona (it is loop-only). *Re-audit:* lock the two invariants before the runner builds; flip `[PENDING-IMPL]` when the runner + activation chain ships.
