---
title: runner — design
status: launched
kind: design
scope: feature
area: agentm/runner
governs:
  - scripts/runner/**
  - scripts/agentm-runner.sh
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
window:    "02:00-06:00"                          # the hours it may start in, local time; due outside it, it waits for the window's next opening
order:     0                                       # position within one cycle — lower first, ties by name
```

The runner reads every manifest each cycle, decides which jobs are due (`schedule` + last-run + `lookback` + any `gate`, gated further by `window` when the manifest carries one), runs the due ones in `order` within the cycle (lower first, ties by name), keeps each within `budget`, and routes `output` by the target's ownership tier (T3 free · T2 reported in the digest · T1 never a job target). **As-built (2026-07-06):** the schema, the due-decision cycle, dry-run-until-promoted, ownership-tier write routing, and the throttle-pause-stop watchdog all ship in `scripts/runner/` — see Migrations. The host-trigger wiring (Claude Desktop / Antigravity Scheduled Tasks, OS cron) fires via `scripts/agentm-runner.sh`, and the hands-on Antigravity 2.0 verification pass confirmed the trigger itself is real — cron/launchd is the trigger to rely on, since Antigravity's own Scheduled Tasks primitive neither can be installed by a shipped plugin nor persists across app restart (Risks). **That verification covered the trigger firing, not job discovery** — a CWD-relative path bug meant every launchd cycle discovered zero jobs until 2026-07-17 (see Amendment log). **`window` + `order` (2026-09-11):** two fields joined the schema after the as-built date above — see Amendment log.

### Who runs on it (the consumers, one-way-up)

| Consumer (capability) | What runs | Expected schedule *(default, operator-tunable)* |
|---|---|---|
| **Forward learning** (experience) | scan operator-approved sources since a watermark, mine ideas, surface to the watchlist | daily |
| **Dreaming** (experience) | whole-corpus consolidation → a derived layer, staged for operator apply (the heaviest job) | activity-gated (~weekly) |
| **Health check** (diagnostics) | an idempotent health snapshot; doubles as the fleet watchdog | daily / idle |
| **Research learn-forward** (research) | `learn-forward` + `codebase-improvement` — the crickets caller of the experience pipeline | weekly |
| **Persona loop / goal** (personas) | Maintainer / Researcher upkeep loops; Engineer / Researcher / Architect / Designer run-until-done goals (the [goal contract](agentm-goal-contract)) | per-persona |

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

A job can also carry a `window` — the hours of day it may start in, local time. A job due outside its window is not late; it waits for the window's next opening, and the lookback above counts from that opening rather than from the moment it fell due. This is how the nightly jobs (enrichment, the dreaming binary, the Python dreaming cycle, the corpus scorecard) stay inside `02:00-06:00` regardless of what hour the runner first happens to see them due.

### The cycle — one idempotent pass, then exit
The runner is a single-cycle program: one invocation is one cooldown-gated, cursor-backed, idempotent poll → run → advance cycle, then exit. It keeps no state beyond a cursor and a per-job marker on a local, non-synced path, so re-running it is safe — the cursor and the dispatched-set make a repeat a no-op. Crash-recovery falls out of the same two pieces: a crashed cycle re-runs whole on the next invocation (idempotency makes that safe), and a job whose start-marker never reached its done-state is retried (the orphan-marker pattern the idle-hook already uses).

### The vault-write contract — the third writer
The runner is the **third writer** to the vault, alongside the CLI (writer #1) and the MCP server (writer #2), and it invents no coordination of its own: every write goes through the existing `vault_lock.py` surface — `vault_mutex` + `atomic_write` + `content_hash` CAS. On lock timeout it backs off and does not write un-serialized; on a concurrent modification it re-reads, re-applies, and retries. The runner's own state — the cursor and per-job markers — lives on a local, non-synced path (`~/.cache/agentm/…`, never inside the synced vault, the lockdir rule).

**Routed by ownership tier.** The runner writes by the target's tier in the [memory-system model](agentm-memory-system): **T3** (the agent's own `Agent/Memory/`) it writes freely; **T2** (curated — designs, plans, roadmaps) it writes as needed and emits a digest entry so the operator sees the change and can revert; **T1** (the operator's personal space) it never writes — that takes the separate explicit seam call, which a scheduled job does not make. The one move the runner *proposes* rather than applies is archiving or pruning curated (T2) content: it stages the proposal to `_dream-staging/` and the digest carries it for the operator to confirm, since a cold-stored design is harder to notice gone than a normal edit. Dreaming's ~20–30-minute pass acquires the mutex around each atomic stage rather than holding it for the whole pass, so concurrent sessions are never starved.

### The token contract — no overspend
Background jobs spend tokens unattended, so two rules govern the spend:
- **A hard budget ceiling, gated to what a night actually spent.** `.harness/budget.yaml` carries a daily USD ceiling shared across all jobs ($5 when unset); a pre-flight checks it before a run starts and skips-and-logs when the envelope can't cover the next run, on a throttle → pause → stop ladder, so an over-budget run never starts. The ceiling sums only the cost each job last reported **in the past 24 hours** (`_spend_so_far`, `scripts/runner/cycle.py:120-142`) — summed forever, the first job to report a real cost would have held every job past the ceiling for good, the spender included, since nothing ever aged its own number out. The pre-flight also gates only a job that declares a `budget:` (`_spends`, `cycle.py:144-152`): a job that makes no model call can never be over budget, so the hourly sweep or a shepherd never waits on another job's spend. Each spending job also has its own per-run `budget`; the fleet ceiling is the global, operator-tunable cap. The runner tracks spend from each run's reported `total_cost_usd` — `templates/jobs/enrich-nightly.yaml` is the first job to declare one, `budget: tokens: 1000000`, and it stays `enabled: false` until the operator has read the first supervised batch.
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

- **Done (2026-07-06).** The runner shipped (`scripts/runner/`) — a consumer design blocked on the runner *existing* is unblocked; what remains for forward learning, dreaming, diagnostics, and research is each consumer's own build, not this substrate. Their `[PENDING-IMPL]` markers stay (nothing about them shipped), but the framing changes: read "blocked on the runner" as closed, not as still-open.
- **Name the single-cycle idiom as a `conventions` shape** (cooldown-gated, cursor-backed, idempotent, opt-in, surface-don't-adopt) and point diagnostics / research / wiki-drift at it — done; see [crickets-conventions](https://github.com/alexherrero/crickets/wiki/crickets-conventions) § The single-cycle shape.
- **Done.** Lifted the V6-11 spec into `wiki/designs/` — see [agentm-memory-index](agentm-memory-index), authored + finalized 2026-06-26, ahead of this bullet ever needing to act on it.
- **Done (2026-07-06, hands-on).** Re-assessed `crickets/wiki/reference/Antigravity-Limitations.md` row #1 against the real Antigravity 2.0 app: confirmed, not stale — a shipped plugin still cannot register a scheduled task (no manifest primitive kind, no installer hook, triggers are Python-SDK-registered at agent-creation time only). No doc edit was needed; the hands-on check verified the existing claim. A second hands-on experiment surfaced a further, distinct finding — see Risks.

## Risks & open questions

- **The app-off precondition — handled by the lookback, for triggers that actually re-fire.** The lookback covers a job overdue from an off device: it runs on the first cycle after wake, within its `lookback` window — a missed window is recovered, not lost. This holds for OS cron/launchd (survives app state entirely) and for on-demand calls. **It does not hold for Antigravity's own native Scheduled Tasks primitive**, verified hands-on 2026-07-06: a task scheduled to fire while the app is closed is silently transitioned to `CANCELED` on restart rather than firing late or deferring — the runner cycle is never invoked, so lookback never gets a chance to run. Moot in practice since row #1 already forecloses a shipped plugin registering that trigger at all, but it forecloses relying on Antigravity Scheduled Tasks even for an operator-created one. A job that must fire at an exact wall-clock time, or must survive the device sleeping, needs the OS-cron/launchd trigger.
- **No mid-cycle resume.** A crashed cycle re-runs whole on the next invocation; idempotency makes that safe. Re-audit if the job count or per-run cost grows enough that whole-cycle re-runs hurt.
- **Antigravity scheduling reliability — verified, not just suspected.** Two independent gaps, both hands-on confirmed 2026-07-06: (a) no plugin-installable trigger path (row #1); (b) native Scheduled Tasks don't persist across app restart/sleep — they silently cancel rather than catch up. Scheduled-task agents are also pinned to Gemini 3.5 Flash (model selection "being explored"), with a reported error on missing key/model mappings. Net effect: Antigravity Scheduled Tasks is not a reliable heartbeat for the runner today; cron/launchd is the trigger to rely on for any device that may sleep or restart.
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

**2026-09-11 — the daily ceiling ages out, and gates only what spends (agentm-vault plan 04, task 2).** The fleet's daily USD ceiling used to sum every job's last-recorded cost forever, which cost nothing while no job had ever reported a real one; the nightly enrichment batch now does, and summed without a window it would have deadlocked every job past the ceiling for good — the batch included, which could then never run again to replace its own number. `_spend_so_far` now sums only the last-recorded cost of a job that ran in the past 24 hours (`scripts/runner/cycle.py:120-142`), matching the ceiling it is compared against. The pre-flight gate also now checks `_spends(job)` — whether the manifest declares a `budget:` — before counting a job against the ceiling at all, so a job that makes no model call (the hourly sweep, a shepherd) is never held back by another job's spend (`cycle.py:144-152,414-419`). `templates/jobs/enrich-nightly.yaml` is the first manifest to declare one, `budget: tokens: 1000000`; it stays `enabled: false` until the operator has read the first supervised batch. Why not keep summing forever: a ceiling that never ages is a deadlock wearing a budget's name, and the failure only became reachable once a job actually spent. Why not skip the budget-only gate: without it, a job that spends nothing would wait on a heavy night's cost the same as a job that does, which is exactly the coupling `_spends` exists to cut. Re-audit trigger: a job carrying a real `budget:` that the ceiling never gates because `_spends` reads a field the manifest didn't set, or a spending job the ceiling never catches because its marker's last-run clock disagrees with wall time.

**2026-09-11 — a window, and an order within the night (agentm-vault plan 04, task 1).** Two fields joined the job-manifest schema. `window` (`"HH:MM-HH:MM"`, local time, hyphen or en dash, may wrap midnight) names the hours a job may start in; a job due outside it waits for the window's next opening rather than running at whatever hour its interval happened to land on, and nothing touches its marker while it waits, so the lookback re-anchor below never mistakes waiting for missed. An orphaned start also waits for its window. A windowed job with an interval of a day or more is due at the window's opening rather than the minute it last started, so a long first step does not push later nights later; a sub-day interval keeps its own clock inside the window. `order` (default 0) fixes a job's position within one cycle, lower first, ties by name — jobs used to run in filename order. The four nightly manifests (`enrich-nightly`, `dreaming`, `dream`, `corpus-scorecard`) now carry `window: "02:00-06:00"` and `order: 1`–`4` in that sequence, the night's order the § Dreaming section of the agentm-vault design names: enrichment, the binary, the Python cycle, the scorecards. Why not a cron-style single-hour field: a window says when a job *may* start rather than naming the one minute it must, so a job due at 13:07 still runs that night at 02:00 instead of missing its slot outright, and a slow first step cannot push every later step's start later until two nightly jobs no longer meet in one cycle. Re-audit trigger: a nightly job skips a night because a step ahead of it in the order ran past 06:00 and pushed it outside the window.

**2026-09-05 — a refused manifest is data, not a halt (filing-v2 remainders task 1, [#560](https://github.com/alexherrero/agentm/pull/560)).** The loader used to raise on the first malformed `.harness/jobs/*.yaml` and the cycle aborted, so one bad manifest stopped every scheduled job on the machine with a traceback in a launchd log as the only trace — twice on 2026-09-05 (a `tier: T1`, a half-quoted command). `load_manifests_lenient` keeps every manifest that loads and names each it refused; the cycle runs what loaded, carries `refused`/`loaded` on its report, and leaves its account at `~/.cache/agentm/runner/last-cycle.json`; the CLI exits 3 only when nothing loaded, `--strict` restores the old load. The refusal reaches the operator where they look: the session brief's `⚠ runner refused N manifest(s)` line and the doctor's `runner-cycle` row. Why not keep all-or-nothing: loud-to-a-log is not loud, and a typo in one manifest is not a reason to skip the nightly dream cycle. Re-audit trigger: a refused manifest that goes unnoticed for a week despite the brief and the doctor — then the surfacing, not the contract, is what needs work.

**2026-07-17 — the host-trigger wiring never actually discovered a job ([#319](https://github.com/alexherrero/agentm/issues/319) / [#320](https://github.com/alexherrero/agentm/pull/320)).** The 2026-07-06 As-built note above called the host-trigger wiring "fully wired" on the strength of the hands-on Antigravity verification pass — but that pass proved the *trigger* fires, not that a fired cycle finds any jobs. `agentm-runner.sh` has to `cd` into `scripts/` so `runner.cli`'s sibling import of `vault_lock` resolves; `runner.cli`'s own `--jobs-dir`/`--harness-dir` default to CWD-relative paths (`.harness/jobs`, `.harness`) that only resolve from the repo root, so under `cwd=scripts/` they silently resolved to a directory that never existed. `manifest.load_manifests()` treats a missing jobs directory as "fresh install, no jobs configured" and returns `[]`, not an error — so every launchd-triggered cycle since the runner was first built (2026-07-05) ran clean and exited 0 having discovered zero jobs. The job-manifest contract this design specifies (`.harness/jobs/<name>.yaml`, read every cycle) was correct on paper and had never once been exercised by the actual background scheduler. Fixed by passing both paths explicitly, anchored at the repo root regardless of cwd. The same pass closed a related honesty gap behind the Safety section's liveness claim: `cycle.py`'s lookback re-anchor (unchanged, still deliberate) writes the identical `"status": "done"` marker a real completion does, so a job that had never actually run read as healthy; `scripts/runner/state.py` now carries a `last_real_run` field (`mark_missed()`, `last_real_run_epoch()`) a reader can trust regardless of how many silent re-anchors have happened since — extending "liveness tracked separately from completion" from the health-check watchdog's own record to every job's marker. Why this isn't a redesign: the job-manifest schema, the due-decision cycle, and the trigger wiring are all unchanged — this was a path-resolution bug in the launcher script, not a substrate defect. See [Known Issues](../reference/Known-Issues) for the general lesson (a launcher's `cd` silently invalidating a CWD-relative CLI default). *Re-audit trigger:* if `agentm-runner.sh` or any sibling launcher gains a new `cd` for a future import, re-check every CWD-relative default the invoked CLI carries before trusting the trigger wiring end-to-end.

**2026-07-06 — hands-on Antigravity verification closes the last open item.** The operator ran both hands-on checks this design's Migrations/Risks sections had been waiting on. (1) A shipped plugin still cannot register a scheduled task in Antigravity 2.0 — confirms `Antigravity-Limitations.md` row #1 exactly as written, so no doc edit was needed there. (2) A new, distinct finding: Antigravity's native Scheduled Tasks primitive does not persist across app restart or sleep — a task due to fire while the app was closed transitions silently to `CANCELED` on reopen rather than firing late or catching up, so the runner's lookback recovery never gets invoked via that specific trigger path. Reconciled into Migrations (item closed) and Risks (the app-off bullet narrowed to name which triggers the lookback guarantee actually covers; the Antigravity-reliability bullet folds in the cancellation finding). Why this isn't a redesign: cron/launchd and on-demand calls are unaffected (neither depends on Antigravity's app-lifecycle state), and row #1 already meant a shipped plugin couldn't use the Antigravity path regardless — this closes an open verification, it doesn't change the shipped design. Re-audit trigger: re-test if Antigravity ships a persistence guarantee for its scheduled-task primitive (would make it viable for the on-demand/manual-configuration case even though a plugin still can't install it).

**2026-07-06 — core build lands (AG Wave B leader 1/5).** `scripts/runner/` ships: the job-manifest schema + loader (`manifest.py`), the due-decision cycle with orphan-start crash recovery (`cycle.py`), local per-job state (`state.py`), dry-run-until-promoted, T2/T3 ownership-tier write routing through `vault_lock.py` as the third writer, a daily-USD budget ceiling, and a throttle-pause-stop watchdog (`watchdog.py`) — plus `scripts/agentm-runner.sh`, the uniform entry point all three triggers (host scheduled task, on-demand, OS cron) invoke identically. 22 tests. `governs:` now points at `scripts/runner/**`. Two items stay open: the host-trigger hands-on Antigravity verification (no session so far has had access to the app), and naming the single-cycle idiom as a `conventions` shape in crickets (done same day, see Migrations). Re-audit trigger: flip the host-trigger row once someone verifies it hands-on.

**2026-06-28 — lock-down sweep (operator review).** All standing fixes clean (diagram sized; no mermaid; no ADR mentions; log already newest-first). Confirmed the DBOS reversal (rides the host scheduler; reverses locked-decision #4; no resident daemon) and the tiered `vault_lock` write discipline. The ROADMAP-MASTER scheduler→runner / “rides DBOS” reconciliation stays a queued roadmap-session followup. No content change. Locked as a v5–v8 guidepost.

**2026-06-26 — authored, reviewed, and finalized.** The runner is agentm's standalone background-job executor. A host built-in scheduled task (Claude Desktop Scheduled Tasks · Antigravity Scheduled Tasks), an on-demand call, or OS cron fires one idempotent cycle that reads the `.harness/jobs/` manifests, runs the due and past-due (lookback) jobs within a hard fleet budget, writes through `vault_lock` routed by memory-system ownership tier (T3 free · T2 autonomous + reported in the digest, revertable · T1 only via the separate seam call), and reports to the digest. It was reshaped from a custom DBOS durable-execution scheduler to riding the host feature — reversing locked-decision-#4's DBOS choice (ship-less-where-native; no resident daemon, so the no-daemon principle holds intact); renamed scheduler→runner and sidecar→job; run-often-plus-lookback replaced a separate catch-up; the Agent-SDK credit pool was dropped for overspend risk; and the approve-before review-inbox became the digest (report-after, with propose-confirm only for archiving or pruning curated content).

**Re-audit triggers:** confirm a shipped plugin can install a host scheduled task on Antigravity 2.0 and verify its app-open precondition hands-on; re-pin the host-scheduler facts on a host release (via content-refresh); the scheduler→runner rename across the consumer designs + roadmap docs rides the Bucket-B voice-sweep; author the digest in the crickets reporting capability; lift the V6-11 spec (sibling).
