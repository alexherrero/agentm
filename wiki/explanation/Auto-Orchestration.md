# Auto-orchestration

Why the memory skills became a *push* surface instead of a *pull* one — and how the SessionStart briefing, the idle chain, and the phase-boundary dispatches surface pending work without ever blocking a session or nagging. For the operator knobs, see [Tune auto-orchestration](Tune-Auto-Orchestration); for every config key, see [Auto-orchestration config](Auto-Orchestration-Config).

## The gap it closes

The memory skills — recall, reflect, discover-skills, adapt-skills, the watchlist — were already powerful, but you had to *remember* to use them. Pending work piled up unseen: an inbox over threshold, watchlist patterns waiting for review, incubator ideas that never got researched, stale idea-ledger entries that should have been collected. The skills sat there until you thought to ask.

Auto-orchestration closes that gap on three surfaces, and the *posture* is the whole point — it is plumbing and nudges, never an autonomous actor:

- **At session start**, a briefing tells you what needs attention — *"3 watchlist patterns to review · inbox over threshold · 2 incubator ideas pending research"* — in one tight block, plus two nudges (ideas you keep having that are worth promoting, and watchlist patterns you said you'd author but haven't).
- **During idle time**, a bounded discover → adapt chain runs itself in passes, staging candidates so you stop hand-invoking `adapt-skills`.
- **At the phase boundaries**, a finished `/work` session gets reflected and a finished `/release` refreshes the skill surfaces — without you running `reflect` or `index-skills` by hand. The entry point for these dispatches is `phase_dispatch()` in [`scripts/harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py) (V5-5 / LC-3); the valid phases are locked in `_BRIDGE_PHASES` at line 1541.

Every surface is non-blocking and graceful-skips on any failure, so a broken script never fails your session boot or wedges a phase. Cooldowns plus a "only fire when state has shifted since last shown" guard keep the briefing from nagging. And nothing is autonomous: the system proposes and nudges, but every adoption, every fork, every write outside the permeable boundary still waits for you.

## Trigger ownership (V5-5)

Each loop's *trigger* — the *when* — lives with the owner of that boundary; the *memory operations* and *cadence state* stay kernel-resident (single-writer: only `auto_orchestration.py` writes the state file). Three owners:

- **Kernel** — config/state core, the idle chain, and all memory operations (reflect, discover, adapt, index). Always agentm-resident.
- **Developer plugin** (`crickets/developer-workflows`) — the phase-boundary trigger. The formalized bridge entry point is `phase_dispatch()` in `scripts/harness_memory.py` (V5-5/LC-3); the plugin calls in through it rather than re-implementing the orchestration logic.
- **PM plugin** (`crickets/github-projects`) — the session-start briefing and nudge trigger. *Gate status: lifted* — the plugin exists in crickets dist — but the relocation is a separate crickets plan. Until that plan ships, the briefing fires from the kernel `memory-recall-session-start` hook exactly as before. The failure mode is *non-relocation*, never a broken briefing.

## The load-bearing design calls

- **The mechanism is hook/file-based and cross-host.** It extends the existing idle and SessionStart hooks plus file-based state and config — deliberately *not* the Anthropic Workflow SDK primitive, which is Claude-tier-gated and would lose Antigravity parity. The Workflow hybrid stays a post-V4 research follow-up.
- **Full scope shipped, nothing autonomous deferred.** The state + config core, the briefing, the SessionStart wiring, the idle chain, the phase-integration dispatch, and both nudges all landed.
- **Everything is harness-native.** The scripts, hooks, and phase wiring all live in `agentm` — there is no crickets crossover and no paired release; the push-surface ships entirely from the harness.
- **Pass-2 is not a hook step.** A hook fires outside the agent loop and cannot dispatch a sub-agent, so the idle chain stops after *staging* Pass-1 candidates and surfaces the count. The evaluator hand-off happens via phase-dispatch or a nudge, where sub-agent dispatch is legitimate and operator-gated.
- **Never blocks, never nags.** Every surface exits clean on any failure; cooldowns and the shifted-since-last-shown check guard notification fatigue. The idle chain is bounded by construction — each step no-ops on empty input, reflect caps at five unseen sessions per pass, adapt runs with a hard limit, and a fired chain is launched detached so its results surface on the *next* briefing rather than stalling the current boot.
- **The permeable boundary holds.** The system never auto-adopts a skill, never auto-forks to the toolkit, and never writes outside the contract without an operator gate.

## The named risk

**Notification fatigue** is the risk that only calibrates under real use — the cooldowns, the shifted-since-last-shown guard, and the operator-tunable thresholds are the mitigations, but the real acceptance gate is the dogfood on the operator's own vault, not deterministic tests alone. If a step proves too costly to auto-fire, it gets demoted to a SessionStart "you could run X" nudge.

## Related

- [Tune auto-orchestration](Tune-Auto-Orchestration) — the operator recipe for thresholds, cooldowns, and chain toggles.
- [Auto-orchestration config](Auto-Orchestration-Config) — the config-key and state-file reference.
- [Use auto-context in harness phases](Use-Auto-Context-In-Harness-Phases) — the phase-boundary pull surface this push-surface complements.
- [Orchestration and auto-detection](Orchestration-And-Auto-Detection) — where the memory hooks sit in the architecture.
