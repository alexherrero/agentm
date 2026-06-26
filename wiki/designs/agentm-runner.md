---
title: runner — design
status: launched
kind: design
scope: feature
area: agentm/runner
governs: []
parent: agentm-hld.md
seeded: 2026-06-26
approved: 2026-06-26
---

> **agentm's background jobs run on the hosts' built-in schedulers (Claude Desktop Scheduled Tasks, Antigravity Scheduled Tasks); a thin agentm runner handles budget, vault-safe writes, and catching up jobs the device missed while it was off.** Sibling to [model + effort routing](agentm-model-effort-routing); parent [AgentM HLD](agentm-hld).

# AgentM Runner Design

## Objective

The runner runs agentm's background jobs — forward-learning, dreaming, health checks, the persona loop/goal modes — without anyone at the keyboard. The host's scheduler fires the runner on a simple heartbeat; the runner decides which jobs are actually due, runs the ones that are (including any the device missed while it was off), keeps them within budget, and writes their results through the vault.

## Overview

The runner runs **jobs**: jobs are registered by capabilities (crickets plugins), each declared by a manifest. A job names what to run, on what schedule, what it may spend, and where its output lands. Each capability owns its job's logic — forward-learning, dreaming, a health check; the runner owns the running: deciding which jobs are due, the budget gate, the write path, the audit record.

**The schedule is the host's; the runner is standalone.** Both supported hosts ship a built-in scheduled-task feature that runs a command on the user's machine on a cadence, with local file access — Claude Code's **Desktop Scheduled Tasks** and Antigravity's **Scheduled Tasks**. agentm registers one that fires the runner on a frequent heartbeat, and the runner does one cycle and exits. The same runner also runs **on demand** or from **OS cron / launchd** — the host scheduler is one caller of three.

**What the runner adds over the bare host scheduler:** it catches a past-due job that never ran because the device was off (a lookback on each cycle), enforces a token budget before spending, writes vault-safely (routing each write by its memory-system ownership tier), and reports every change in the digest. The host keeps the heartbeat; the runner makes the work safe, budgeted, and resilient to a machine that isn't always on.

![How a job runs: a host scheduled task (Claude Desktop Scheduled Tasks or Antigravity Scheduled Tasks), OS cron, or an on-demand call invokes the agentm runner, which runs one idempotent cycle — reading the job manifests in .harness/jobs/, deciding which jobs are due (including past-due ones missed while the device was off), checking the fleet budget, running them, then writing through vault_lock as the third writer (routed by ownership tier) and reporting each change to the digest](diagrams/agentm-runner.svg)

Nothing of the runner is built today. Each consumer's on-demand half exists — the manually-invocable seed (the import watchlist for forward-learning, the thin `/dream`, `/diagnose`) — so every consumer degrades to "run it by hand" until the runner lands. This is the spec for the substrate; the individual jobs are designed by their own capabilities.

### The job (the unit)
A job is a manifest at `.harness/jobs/<name>.yaml`. The schema:

```yaml
run:       <command or capability entry point>   # what to execute
schedule:  daily | weekly | "0 3 * * *"          # how often it should run
lookback:  7d                                     # still run it if overdue within this window (catch a missed run)
budget:    50000                                  # per-run token ceiling
output:    <vault path | digest>                    # where results land; the runner applies the target's ownership tier
gate:      <optional>                             # only run if a condition holds, e.g. ≥N new entries since last run
```

The runner reads every manifest each cycle, decides which jobs are due (`schedule` + last-run + `lookback` + any `gate`), runs them within `budget`, and routes `output` by the target's ownership tier (T3 free · T2 reported in the digest · T1 never a job target). [PENDING-IMPL — the schema ships with the runner; the documenter flips this to as-built when `scripts/runner/` lands.]

### Who runs on it (the consumers, one-way-up)

| Consumer (capability) | What runs | Expected schedule *(default, operator-tunable)* |
|---|---|---|
| **Forward learning** (experience) | scan operator-approved sources since a watermark, mine ideas, surface to the watchlist | daily |
| **Dreaming** (experience) | whole-corpus consolidation → a derived layer, staged for operator apply (the heaviest job) | activity-gated (~weekly) |
| **Health check** (diagnostics) | an idempotent health snapshot; doubles as the fleet watchdog | daily / idle |
| **Research learn-forward** (research) | `learn-forward` + `codebase-improvement` — the crickets caller of the experience pipeline | weekly |
| **Persona loop / goal** (personas) | Maintainer / Researcher upkeep loops + run-until-done goals | per-persona |

`content-refresh` is **not** a consumer — model-currency rides a CI drift-detector on CI cron. The one remaining maintenance touchpoint is the V7-6 security-vuln-watch job.

## Design

### Invocation — host scheduler, on demand, or cron

| Trigger | What it is | When |
|---|---|---|
| **Host scheduled task** | Claude Desktop Scheduled Tasks / Antigravity Scheduled Tasks fire the runner on a heartbeat | the usual path — local file access, no open session |
| **On demand** | the operator runs the runner directly | a manual pass or testing |
| **OS cron / launchd** | the OS fires the runner | the always-on path when the host app isn't running |

Every trigger calls the same runner; it reads the manifests, runs the due jobs, and exits. The runner is the cross-host piece (stdlib); the trigger is host-specific. On Claude this is specifically **Desktop Scheduled Tasks** — Routines run on a cloud clone with no local vault, and `/loop` needs a live session.

### Run often, look back
The runner is deterministic and idempotent, and a cycle with nothing due is cheap — so the host fires it **often** (a frequent heartbeat, not one fire per job). Each cycle the runner reads every job's `schedule` and last-run and runs the ones that are due. The same pass covers a **device that was off**: a job whose scheduled time passed while the machine slept is overdue, and the runner runs it on the first cycle after wake, as long as it's still within the job's `lookback` window. A daily job missed over a long weekend runs once on Monday — the runner decides per job; the host just keeps the heartbeat going. This is the whole catch-up story: a function inside the runner, not a separate mechanism.

### The cycle — one idempotent pass, then exit
The runner is a single-cycle program: one invocation is one cooldown-gated, cursor-backed, idempotent poll → run → advance cycle, then exit. It keeps no state beyond a cursor and a per-job marker on a local, non-synced path, so re-running it is safe — the cursor and the dispatched-set make a repeat a no-op. Crash-recovery falls out of the same two pieces: a crashed cycle re-runs whole on the next invocation (idempotency makes that safe), and a job whose start-marker never reached its done-state is retried (the orphan-marker pattern the idle-hook already uses).

### The vault-write contract — the third writer
The runner is the **third writer** to the vault, alongside the CLI (writer #1) and the MCP server (writer #2), and it invents no coordination of its own: every write goes through the existing `vault_lock.py` surface — `vault_mutex` + `atomic_write` + `content_hash` CAS. On lock timeout it backs off and does not write un-serialized; on a concurrent modification it re-reads, re-applies, and retries. The runner's own state — the cursor and per-job markers — lives on a local, non-synced path (`~/.cache/agentm/…`, never inside the synced vault, the lockdir rule).

**Routed by ownership tier.** The runner writes by the target's tier in the [memory-system model](agentm-memory-system): **T3** (the agent's own `Agent/Memory/`) it writes freely; **T2** (curated — designs, plans, roadmaps) it writes as needed and emits a digest entry so the operator sees the change and can revert; **T1** (the operator's personal space) it never writes — that takes the separate explicit seam call, which a scheduled job does not make. The one move the runner *proposes* rather than applies is archiving or pruning curated (T2) content: it stages the proposal to `_dream-staging/` and the digest carries it for the operator to confirm, since a cold-stored design is harder to notice gone than a normal edit. Dreaming's ~20–30-minute pass acquires the mutex around each atomic stage rather than holding it for the whole pass, so concurrent sessions are never starved.

### The token contract — no overspend
Background jobs spend tokens unattended, so two rules govern the spend:
- **A hard budget ceiling.** `.harness/budget.yaml` carries a daily/weekly token ceiling shared across all jobs; a pre-flight checks it before a run starts and skips-and-logs when the envelope can't cover the next run, on a throttle → pause → stop ladder, so an over-budget run never starts. Each job also has its own per-run `budget`; the fleet ceiling is the global, operator-tunable cap. The runner tracks spend from each run's reported `total_cost_usd`.
- **No double-spend on a crash.** A job's expensive `claude -p` call would re-run in full if the cycle crashed and re-fired. So a job records a **session sentinel** and resumes with `claude -p --resume <session_id>` — idempotent at the spend boundary, not only the write boundary.

### Safety
- **A new job starts in dry-run** — it renders what it *would* write and reports that to the digest, writing nothing until the operator promotes it; so a job can't silently mutate the vault on its first run, and it's the safe way to tune a dreaming prompt.
- **A watchdog + circuit-breaker** — the diagnostics health-check job doubles as fleet watchdog: a machine-readable health + liveness record per run, and an auto-pause (throttle → pause → stop) when a job trips a threshold (a ballooning staging dir, spiking revert churn, a repeatedly-tripped budget). A finished run is not necessarily a successful one, so liveness (last-successful-run) is tracked separately from completion.
- **The digest** — every cycle's work lands in **the digest** (the reporting capability): what ran, the T2 changes it made (each with a revert pointer), fleet health, and any archive/prune proposals to confirm. It reports autonomous changes after the fact and asks only for the destructive ones. (The digest is its own design in the reporting capability; the runner feeds it.)
- **Boundary-crossing writes** route through deterministic checks first, then an adversarial-reviewer-agent gate (crickets), and only the residue reaches the human inbox.

## Dependencies & composition

- **Host built-in scheduled tasks** — Claude Code Desktop Scheduled Tasks and Antigravity Scheduled Tasks invoke the runner on a cadence; OS cron/launchd and an on-demand call are the other two triggers. agentm provides the runner they invoke.
- **`vault_lock.py`** (the V5-0 write floor) — composed as the third writer; the runner does not reimplement coordination.
- **The consumers** (experience, research, diagnostics, personas, maintenance) lean on the runner **one-way-up** — they register a job and name the interface; the runner never depends on a capability.
- **memory-system** — the runner routes every write by its [ownership tiers](agentm-memory-system) (T3 free · T2 report-in-digest · T1 separate seam call).
- **The digest** (the reporting capability, forward-referenced) — the report surface jobs feed; it carries the T2-change reports + revert pointers + the archive proposals.
- **V6-11 metadata table** (sibling Wave-B substrate) — referenced only for the health-check report and the dreaming `session-cost` review; the runner does not own it.
- **The wiki-watch single-cycle idiom** — the runner generalizes it; named as a `conventions` shape so the consumers share one implementation.

## Migrations

- **Downgrade the consumers' status language** from "designed" to "blocked on the runner" until it ships, so no design reads as independently buildable when it depends on an unbuilt substrate.
- **Name the single-cycle idiom as a `conventions` shape** (cooldown-gated, cursor-backed, idempotent, opt-in, surface-don't-adopt) and point diagnostics / research / wiki-drift at it.
- **Lift the V6-11 spec** into `wiki/designs/` (sibling task; the runner only references it).
- **Re-assess `crickets/wiki/reference/Antigravity-Limitations.md` row #1** (the "no installable trigger path for a shipped plugin" gap) against Antigravity 2.0 Scheduled Tasks — the operator-facing feature now exists; confirm hands-on whether a *shipped plugin* can install a scheduled task, and update the row.

## Risks & open questions

- **The app-off precondition — handled by the lookback.** The host scheduled task fires only while the host app is running and the machine is awake. The lookback covers this: a job overdue from an off device runs on the first cycle after wake, within its `lookback` window — a missed window is recovered, not lost. A job that must fire at an exact wall-clock time regardless of device state is the exception — use the OS-cron/launchd trigger, or accept next-wake. Verify the Antigravity precondition hands-on.
- **No mid-cycle resume.** A crashed cycle re-runs whole on the next invocation; idempotency makes that safe. Re-audit if the job count or per-run cost grows enough that whole-cycle re-runs hurt.
- **Antigravity scheduling reliability.** Scheduled-task agents are currently pinned to Gemini 3.5 Flash (model selection "being explored"), with a reported error on missing key/model mappings — verify before relying on Antigravity scheduling.
- **The digest is a separate design** (in the reporting capability). This design feeds it but does not specify it; if it slips, job output falls back to per-feature staging dirs — workable, but without the single report surface.
- **A non-revertable T2 change.** The runner reports T2 edits so the operator can revert, but a change that can't be reverted (the vault isn't git-backed yet) is the one case that would warrant asking before rather than after. Accepted as a known limitation until agent-memory is git-backed (backlogged); the destructive ops (archive/prune) already take the propose-confirm path, which bounds the exposure.

## References

- R03 — scheduled / background / autonomous agents (the 2026-06 roadmap research pass) — the SOTA scan and the safety primitives (staging-default, fleet ceiling, dry-run, watchdog, inbox).
- Locked decision #4 (ship-less-where-native) — satisfied by riding the hosts' built-in scheduled tasks.
- Claude Code Desktop Scheduled Tasks · Antigravity Scheduled Tasks — the host scheduling features the runner is invoked by.
- [model + effort routing](agentm-model-effort-routing) — sibling cross-cutting agentm design.
- [Experience & dreaming](agentm-experience-and-dreaming) — the primary consumer (forward-learning, dreaming).
- [AgentM HLD](agentm-hld) — parent.
- `vault_lock.py` (V5-0) — the write floor the runner composes as the third writer.
- the wiki-watch cycle (crickets) — the single-cycle idiom the runner generalizes.

## Amendment log

**2026-06-26 — authored, reviewed, and finalized.** The runner is agentm's standalone background-job executor. A host built-in scheduled task (Claude Desktop Scheduled Tasks · Antigravity Scheduled Tasks), an on-demand call, or OS cron fires one idempotent cycle that reads the `.harness/jobs/` manifests, runs the due and past-due (lookback) jobs within a hard fleet budget, writes through `vault_lock` routed by memory-system ownership tier (T3 free · T2 autonomous + reported in the digest, revertable · T1 only via the separate seam call), and reports to the digest. It was reshaped from a custom DBOS durable-execution scheduler to riding the host feature — reversing locked-decision-#4's DBOS choice (ship-less-where-native; no resident daemon, so the no-daemon principle holds intact); renamed scheduler→runner and sidecar→job; run-often-plus-lookback replaced a separate catch-up; the Agent-SDK credit pool was dropped for overspend risk; and the approve-before review-inbox became the digest (report-after, with propose-confirm only for archiving or pruning curated content).

**Re-audit triggers:** confirm a shipped plugin can install a host scheduled task on Antigravity 2.0 and verify its app-open precondition hands-on; re-pin the host-scheduler facts on a host release (via content-refresh); the scheduler→runner rename across the consumer designs + roadmap docs rides the Bucket-B voice-sweep; author the digest in the crickets reporting capability; lift the V6-11 spec (sibling).
