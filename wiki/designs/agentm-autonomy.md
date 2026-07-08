---
title: Autonomy — observability ledger and console
status: launched
kind: design
scope: feature
area: agentm/autonomy
governs: []
parent: agentm-hld.md
seeded: 2026-07-07
approved: 2026-07-07
---

> [!NOTE]
> **LAUNCHED (2026-07-07, AA2).** child-design — the Autonomy arc's telemetry substrate: a device-local spend ledger, a deterministic console, and a digest ladder into the vault. A **cross-cutting** design, agentm-anchored, the same way [model + effort routing](agentm-model-effort-routing.md) is — points *up* at the [agentm HLD](agentm-hld.md), touches no single pillar. Evolved from the Autonomy arc's front-loaded budget-governor draft (`<vault>/_harness/designs/post-ag-frontload/BUDGET-GOVERNOR-DESIGN-DRAFT.md`, superseded) after operator review dropped its enforcement.

# Autonomy — observability ledger and console

## Objective

Multi-session autonomy only works if you can see what it cost and what it produced without watching it live. This design gives agentm one telemetry ledger — spend and run events, attributed to plan, task, and model — and a console over it: a local dashboard plus periodic digest notes. The subscription window's own rate limit is the spend backstop; this design gives visibility into subscription use and efficiency across projects.

## Background

This design evolves the budget-governor draft from the Autonomy arc's front-load. The governor paired a spend ledger with enforcement — budget envelopes, spawn denial, an automatic kill path. At review the design kept the ledger and let the host carry the enforcement: on a subscription plan the window rate limit already bounds a runaway, it resets on its own, and the harness's recoverability doctrine files recoverable outcomes under proceed-with-announcement. The arc's standing lock — an orchestrator blind to cost repeats the runaway-token incident at machine speed — is retired by making cost visible: the Mythos fleet failed because no estimate was ever put in front of the operator, and the ledger, the announcements, and the console close exactly that gap.

The evolution also re-formed the ledger's home. The session-cost capture that shipped in crickets' Wave D writes markdown into the Obsidian vault; this design moves telemetry to a device-local event log built for machine records, and the vault keeps the human-facing digest notes. The capability audit's findings carry forward: the substrate decision (resolved to Agent View by `PLAN-autonomy-control-plane`'s opening task — see Risks) and attribution tags are stamped by the harness itself, since the audit confirmed no host carries a plan field for us.

## Overview

Sessions and hooks append small JSON records to a monthly log file on the device. On a schedule, the runner folds those events into a small SQLite rollup with per-plan, per-task, per-model, and per-window tables. Everything downstream reads the rollup: a static HTML console page, a ladder of digest notes into the vault, the fan-out advisory, and — once it ships — dreaming's efficiency trend.

The whole read path is deterministic. The one AI touch is a scheduled analysis pass that writes its findings into the store as another dated artifact; the page renders whatever the store holds, the same way every time.

An unattended run announces its spawns with cost estimates, parks tidily if the window runs out, and ends with a morning report naming what it did, what it spent, and why it ended. The launch-time authorization grade a run states is a control-plane concern (below), recorded here only as one field on its `run-start` event.

## Design

### The event log

One JSON line per event, appended to `~/.agentm/telemetry/events-YYYYMM.jsonl` — outside both the repo and the vault, rotated monthly. An append can never block a session or raise; if a write fails, the session carries on and the line is simply missing. This is the same graceful contract the shipped session-cost capture holds today.

```json
{"ts": "...", "schema_version": 1, "device": "...", "session_id": "...", "parent_id": "...",
 "event": "session-cost | spawn | run-start | run-end | window-park",
 "model": "...", "tokens_by_kind": {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0},
 "cost_usd": 0.0, "tags": {"plan": "...", "task": "...", "arc": "...", "grade": "..."}}
```

Every line carries `device` and `schema_version` from day one — if a second machine ever joins, the aggregator merges logs by device id, and a cheap field now saves a migration later. The harness stamps the plan and task tags at write time from its own active-plan marker and dispatch context. A `run-start` line also records the authorization grade the run was launched under, so the console and the morning report can show what authority it claimed — the grade policy itself lives with the control-plane plan.

### The rollup

A scheduled runner job folds the raw events into a small SQLite file with per-plan, per-task, per-model, and per-window tables, reusing the five-hour bucketing the token-audit analyzer already has. The log is the source of truth and the rollup is derived from it — if the aggregation logic changes, the rollup rebuilds from the events and loses nothing. The health dashboard's history file and the GitHub board mirror stay where they are; the console reads them alongside the rollup, and one read surface is enough.

### The console

A local static HTML page over the rollup, built the way the health dashboard already is. It shows spend by plan, task, and model tier, how full the current window is, and cost per merged PR as a first productivity measure. A shared, published version (a locked-down site, Cloudflare or similar) goes on the roadmap as a backlog note; everything here being static derived data keeps that path simple when its time comes.

### Digests

Each horizon reads the rollup at its own window and lands as a note in the vault's `_inbox/`:

| Cadence | Content |
|---|---|
| Daily | spend and run summary |
| Every 3 days | 3-day rollup |
| Weekly | weekly rollup |
| Monthly | trends across all of the above |

Email delivery can come later, once the notes prove worth pushing — it needs a connector, and that decision can wait.

### When the window runs out

The window is the backstop, so hitting it should be tidy. When rate-limit errors arrive mid-run, the run parks: state written to disk, progress recorded, and a park note saying where it stopped, when, and the exact command to resume. Morning resume is one paste, by the operator — a run never resumes itself, because a loop that ate one window would simply eat the next one at reset. The morning report names why the run ended — plan finished, gates green, an escalation parked, or the window ran out — with the spend attached.

### The fan-out gate's advisory role

In an interactive session the gate keeps its shipped behavior: it asks the operator before a big dispatch, and they're there to answer. In an unattended run it announces the estimate, logs it as a ledger event, and proceeds. Nothing agentm-side stops, denies, or kills a run over spend; the kill switch stays a lever the operator pulls themselves.

## Alternatives considered

- **The enforcing governor** (the predecessor draft). A subscription has no surprise bill, the window limit already bounds a runaway, and a hard stop keyed to an estimated meter would either halt healthy runs or sit too loose to fire. Retained as the reopening path if metered spend ever arrives — see Risks.
- **Keeping the ledger in the vault.** Telemetry and memory are different things; the vault is curated knowledge on a Google Drive mount with known sync-conflict behavior, and fleet-scale machine records would flood it.
- **One physical store for all observability.** One read surface gives everything a merged store would, without disturbing the shipped health history or board mirror.
- **Auto-resume after window exhaustion.** It would amplify the exact failure the window bounds.
- **A live dashboard service.** Static derived data needs no daemon, and staying static keeps the future shared publish simple.
- **AI in the read path.** The analysis runs on a schedule and writes rows; the rendering stays deterministic.

## Dependencies

- **The shipped session-cost capture and analyzer** (crickets `src/tokens/`) — the writer, reader, five-hour window math, and fan-out gate this design retargets.
- **The runner** — hosts the aggregator and digest jobs as scheduled tasks.
- **The health dashboard** — the pattern (and scorecard machinery) the console page reuses.
- **The harness's active-plan marker** — the attribution source the event writer stamps tags from.
- **The capability audit** (`agent-teams-audit.md`, vault `_harness/designs/post-ag-frontload/`) — the substrate decision it reserved stays with the control-plane plan; its plan-binding finding is answered here by harness-side stamping.

## Migrations

**The session-cost capture moves off the vault.** The Wave D capture writes one markdown entry per model per session into the vault via the memory engine; it retargets to the event log, and the vault write retires. What it touches:

- `session_cost_writer.py` targets the event log; its tests keep the same behavior contract against the new sink.
- `session_cost_reader.py` and the fan-out gate read the rollup.
- The `session-cost` memory kind (agentm's [memory index](agentm-memory-index.md)) goes vestigial.
- Dreaming's efficiency trend is still an unbuilt stub, so pointing it at the rollup is a doc edit.

## Risks & open questions

- **The ledger is an estimate.** The hosts expose no quota API, so accounting works from local transcripts and dispatch announcements, and undercounts mid-session burn. Re-audit trigger: undercount beyond 15% of observable reality moves estimation to transcript-tail sampling.
- **An overnight loop can burn the rest of a window before anyone sees it.** Accepted at review: the loss is bounded, the window resets, and the morning report shows what happened.
- **Enforcement reopens if auto-refilling metered spend ever arrives** — a plan change or an API-keyed fleet. The question reopens from this design's record; until then it stays closed.
- **The substrate choice is resolved: Agent View.** Decided at `PLAN-autonomy-control-plane`'s opening task (2026-07-08), live-verified against Claude Code 2.1.193 (no drift from the capability audit's own baseline). Agent Teams was rejected — one team per session, no nested teams, and no session resumption at all for in-process teammates, all disqualifying for an unattended overnight fleet. Agent View's background sessions (`claude --bg` / `claude agents`) are process-supervised, survive machine sleep and restart, auto-isolate into worktrees, and (as of v2.1.198) auto-commit/push/open a draft PR on completion. See the amendment log for the full record.
- **The Antigravity half of the capability audit rests on third-party coverage.** Re-run it when Google's own docs come up.
- **The shared dashboard needs a publish gate designed at pickup**: aggregates only, personal detail scrubbed, access locked down.
- **Email delivery is undecided** — connector choice deferred until the digest notes prove worth pushing.
- Everything in the Design section is `[PENDING-IMPL]` — built by `PLAN-observability-ledger` and `PLAN-observability-console`, staged in the vault queues.

## References

- Code: `session_cost_writer.py` · `session_cost_reader.py` · `analyzer.py` · `fanout_cost_gate.py` (crickets `src/tokens/`) · the health dashboard (`scripts/health/`)
- Designs: [token-audit](https://github.com/alexherrero/crickets/wiki/crickets-token-audit) · [memory index](agentm-memory-index.md) · [model + effort routing](agentm-model-effort-routing.md) · the [HLD](agentm-hld.md) this seeds under
- Predecessor: `BUDGET-GOVERNOR-DESIGN-DRAFT.md` (v1 + v2, both superseded 2026-07-07, vault `_harness/designs/post-ag-frontload/`) · `agent-teams-audit.md` (the capability audit, same directory)
- Built by: `PLAN-observability-ledger` → `PLAN-observability-console` → `PLAN-autonomy-control-plane` (carries the launch-time grade statement + substrate decision) → `PLAN-board-tracking-model` (gated on the control-plane plan's first real team run) — staged in the vault `_harness/queued-plans/`

## Amendment log

**2026-07-08 — Acceptance demo closed: the N1 rerun ran for real (`PLAN-autonomy-control-plane` task 6).** `PLAN-autonomy-control-plane` merged (#251) with task 6 escalated rather than simulated — a sandboxed build session's real dispatch test found `claude --bg` sessions couldn't authenticate there, and the plan's own constraints forbade faking the demo. The operator ran it for real afterward, from their own terminal, against a clean `origin/main` worktree: `n1_run.run_n1_sequence()` dispatched two real Agent View background sessions tagged `plan: n1-demo`, declared the launch grade (`G-ship`) on a real `run-start` event, and both sessions completed. The harvest step surfaced the actual root cause behind the earlier sandbox finding — not a sandbox artifact at all, but the base `claude` CLI never having a persistent login (`claude auth status` read `loggedIn: false`); the operator's regular usage runs through a session-managing host that injects auth per session, which a `--bg`-spawned process doesn't inherit. `claude auth login` fixed it for the base CLI same as it would for any machine in this state. Once fixed: the aggregator built a real rollup, the console rendered it, and the morning report read *"Ended because the plan finished (plan-finished). Spend: \$0.4607"* — matched exactly by an independent grep across the raw event log (`$0.1672 + $0.2935 = $0.4607`, plus four `$0.00` `<synthetic>`-model rows from the pre-fix failed attempts, priced at zero by the pricing table's own graceful-unknown-model fallback rather than crashing). Morning report, console rollup, and raw ledger all agree — the plan's own acceptance criterion, met. `PLAN-board-tracking-model`'s gate ("the control-plane plan's first real team run") is now satisfied. *Re-audit trigger:* none outstanding for this plan; the auth-setup step (`claude auth login`) is worth naming in onboarding docs for any other machine that dispatches through Agent View for the first time.

**2026-07-08 — R1 resolved: Agent View (`PLAN-autonomy-control-plane` task 1).** The capability audit (`agent-teams-audit.md`, vault `_harness/designs/post-ag-frontload/`) reserved the fleet-substrate decision rather than resolving it. This plan's opening task made it explicit: re-verified live against the installed Claude Code (2.1.193, unchanged from the audit's own baseline — no version drift to re-check for) that `claude --bg` / `claude agents` (Agent View's background-session primitive) is real, current, and behaves as the audit described (process-supervised; `claude agents --json` confirms the live session schema is `{pid, cwd, kind, startedAt, sessionId, name}` — no plan-ID field, confirming the audit's R2 finding independently). Agent Teams was rejected on the audit's own findings: one team per session, no nested teams, and — the disqualifying one — no session resumption at all for in-process teammates, which a fleet meant to survive an unattended overnight stretch cannot tolerate. Antigravity's equivalent stays out of scope for this decision; the control plane is Claude-Code-first, matching the "Claude-first scheduling" precedent already set elsewhere in this harness (crickets' wiki-watch) — Antigravity support is a forward reference, not a rejected option. *Re-audit trigger:* if Antigravity's own docs become reachable (the audit's own named gap) and the arc later needs cross-host dispatch, re-open this decision for that host specifically; it does not reopen the Claude Code half.

**2026-07-07** — Seeded at AA2 as the evolution of the budget-governor draft, lifted from the approved vault draft (`OBSERVABILITY-DESIGN-DRAFT.md`, `status: final`, `approved: 2026-07-07`) after two operator review passes: the first dropped enforcement (subscription billing has no surprise bill; the window limit is the host-run backstop), the second homed the authorization-ladder remnant — the launch-time grade statement — to `PLAN-autonomy-control-plane`, leaving this design only the grade recorded on `run-start` events.
